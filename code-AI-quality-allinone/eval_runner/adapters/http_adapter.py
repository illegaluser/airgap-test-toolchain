"""
http_adapter.py — HTTP API 기반 AI 에이전트 평가 어댑터

평가 대상 AI가 REST API(예: Dify Chatflow, Ollama Wrapper, OpenAI 호환 API)인 경우
이 어댑터를 사용합니다.

[주요 기능]
- 다중 턴 대화 지원: 이전 대화 이력을 messages 배열로 포함하여 전송
- 다양한 API 호환: query/input/messages를 동시에 전송하여 대부분의 LLM API와 호환
- 응답 정규화: 다양한 응답 형식(answer/response/text/output/message)에서 답변을 추출
- 토큰 사용량 추출: prompt_tokens/completion_tokens를 표준화된 형식으로 변환
- 에러 처리: HTTP 4xx/5xx 응답도 구조화하여 리포트에 실패 원인이 남도록 처리
"""

import json
import os
import time
from typing import Dict, List, Optional

import requests

from .base import BaseAdapter, UniversalEvalOutput


class GenericHttpAdapter(BaseAdapter):
    """
    HTTP API 기반 AI 에이전트를 호출하고 결과를 UniversalEvalOutput으로 표준화하는 어댑터.
    대화 기록(history)을 포함한 다중 턴 요청을 지원합니다.

    Phase 6: 요청/응답 포맷 선택 지원 (TARGET_REQUEST_SCHEMA env).
    - "standard" (기본): 기존 `{messages, query, input}` → `{answer, docs, usage}` 포맷
    - "openai_compat":   OpenAI Chat Completions 호환 `{model, messages}` →
                         `{choices:[{message:{content}}], usage:{prompt_tokens,...}}`
    """

    def __init__(self, target_url: str, api_key: str = None, auth_header: str = None):
        super().__init__(target_url, api_key, auth_header)
        self.request_schema = (os.environ.get("TARGET_REQUEST_SCHEMA") or "standard").strip().lower()

    @staticmethod
    def _extract_usage(data: Dict) -> Dict[str, int]:
        """
        응답 JSON의 usage 필드를 러너 공통 포맷으로 정규화합니다.
        공급자마다 키 이름이 달라질 수 있어 여러 후보를 허용합니다.
        """
        usage_data = data.get("usage", {}) if isinstance(data, dict) else {}
        if not isinstance(usage_data, dict):
            return {}

        prompt_tokens = usage_data.get("prompt_tokens")
        completion_tokens = usage_data.get("completion_tokens")
        total_tokens = usage_data.get("total_tokens")

        if prompt_tokens is None:
            prompt_tokens = usage_data.get("input_tokens", 0)
        if completion_tokens is None:
            completion_tokens = usage_data.get("output_tokens", 0)
        if total_tokens is None:
            total_tokens = usage_data.get("total", 0) or (prompt_tokens or 0) + (completion_tokens or 0)

        return {
            "promptTokens": int(prompt_tokens or 0),
            "completionTokens": int(completion_tokens or 0),
            "totalTokens": int(total_tokens or 0),
        }

    @staticmethod
    def _extract_actual_output(data: Dict) -> str:
        """
        실제 답변 텍스트가 들어 있을 가능성이 높은 필드들을 순서대로 탐색합니다.
        가장 먼저 발견된 값을 평가용 actual_output으로 사용합니다.
        Phase 6: OpenAI 호환 응답(`choices[0].message.content`) 도 fallback 으로 탐색.
        """
        if not isinstance(data, dict):
            return ""

        for key in ("answer", "response", "text", "output", "message"):
            value = data.get(key)
            if value is not None:
                # OpenAI 의 message 필드는 dict → 그 안의 content 를 꺼낸다
                if key == "message" and isinstance(value, dict):
                    content = value.get("content")
                    if content is not None:
                        return str(content)
                    continue
                return str(value)

        # OpenAI Chat Completions: choices[0].message.content
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message")
                if isinstance(msg, dict) and msg.get("content") is not None:
                    return str(msg["content"])
                text = first.get("text")
                if text is not None:
                    return str(text)
        return ""

    @staticmethod
    def _extract_contexts(data: Dict) -> List[str]:
        """
        RAG 평가용 검색 문맥을 docs 또는 retrieval_context 필드에서 꺼냅니다.
        문자열 단일값도 리스트로 감싸 DeepEval 입력 형식을 맞춥니다.
        """
        if not isinstance(data, dict):
            return []

        docs = data.get("docs")
        if docs is None:
            docs = data.get("retrieval_context", [])

        if isinstance(docs, str):
            return [docs]
        if isinstance(docs, list):
            return [str(item) for item in docs]
        return []

    @staticmethod
    def _extract_error_detail(data: Dict, raw_response: str) -> str:
        """
        4xx/5xx 응답에서 사람이 바로 원인을 파악할 수 있도록 요약 에러 메시지를 구성합니다.
        """
        if isinstance(data, dict):
            err_obj = data.get("error")
            if isinstance(err_obj, dict):
                for key in ("message", "detail", "reason", "error"):
                    value = err_obj.get(key)
                    if value:
                        return str(value)[:500]
            elif err_obj:
                return str(err_obj)[:500]

            for key in ("message", "detail", "reason"):
                value = data.get(key)
                if value:
                    return str(value)[:500]

        compact = " ".join((raw_response or "").split())
        return compact[:500]

    def _build_headers(self) -> Dict[str, str]:
        """
        요청 헤더를 조립합니다.
        TARGET_AUTH_HEADER가 주어지면 우선 사용하고, 없을 때만 API_KEY를 Bearer 토큰으로 변환합니다.
        """
        headers = {"Content-Type": "application/json"}

        if self.auth_header:
            if ":" in self.auth_header:
                key, value = self.auth_header.split(":", 1)
                headers[key.strip()] = value.strip()
            else:
                headers["Authorization"] = self.auth_header.strip()
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        return headers

    def invoke(
        self,
        input_text: str,
        history: Optional[List[Dict]] = None,
        **kwargs,
    ) -> UniversalEvalOutput:
        """
        대상 HTTP API를 호출하고 결과를 UniversalEvalOutput으로 표준화해 반환합니다.
        멀티턴 평가에서는 history를 messages 배열로 변환해 함께 보냅니다.
        """
        start_time = time.time()
        headers = self._build_headers()

        # 이전 대화 이력을 messages에 쌓아 대상 모델이 컨텍스트를 유지할 수 있게 합니다.
        messages = []
        if history:
            for turn in history:
                messages.append({"role": "user", "content": turn["input"]})
                messages.append({"role": "assistant", "content": turn["actual_output"]})
        messages.append({"role": "user", "content": input_text})

        # Phase 6: TARGET_REQUEST_SCHEMA 에 따라 payload 포맷을 분기한다.
        if self.request_schema == "openai_compat":
            # OpenAI Chat Completions 호환 — `model` 필드는 JUDGE_MODEL 환경변수를 재사용.
            # 대상 측이 model 을 무시할 수 있으므로 default 로 "auto" 사용.
            payload = {
                "model": os.environ.get("JUDGE_MODEL") or "auto",
                "messages": messages,
            }
        else:
            # standard: eval_runner 표준 포맷. 다양한 외부 API 와의 호환성 위해 query/input/messages 병용.
            payload = {
                "messages": messages,
                "query": input_text,
                "input": input_text,
                "user": "eval-runner",
            }

        # Phase 6: adapter timeout 을 env 로 조정 가능. 로컬 Ollama 가 모델 swap 으로
        # 첫 몇 호출이 느릴 때 60s 기본값이 부족한 경우가 많다. 기본 300s, env override.
        try:
            timeout_sec = int(os.environ.get("ADAPTER_TIMEOUT_SEC") or "300")
        except (TypeError, ValueError):
            timeout_sec = 300
        try:
            response = requests.post(
                self.target_url,
                json=payload,
                headers=headers,
                timeout=timeout_sec,
            )
            latency_ms = int((time.time() - start_time) * 1000)

            try:
                # JSON 응답이면 구조화 데이터와 원문 문자열을 모두 보존합니다.
                data = response.json()
                raw_response = json.dumps(data, ensure_ascii=False)
            except json.JSONDecodeError:
                # 비JSON 응답도 정책 검사를 위해 원문 그대로 저장합니다.
                data = {}
                raw_response = response.text

            actual_output = self._extract_actual_output(data)
            usage = self._extract_usage(data)

            if response.status_code >= 400:
                # 실패 응답도 리포트에 남길 수 있도록 가능한 정보를 최대한 담아 반환합니다.
                detail = self._extract_error_detail(data, raw_response)
                error_message = f"HTTP {response.status_code}"
                if detail:
                    error_message = f"{error_message}: {detail}"
                # Phase 3.3 Q1: 5xx 는 인프라/가용성 이슈 → system, 4xx 는 요청 문제 → system 으로
                # 통일 (대부분 upstream wrapper/config 문제). 상세 구분은 Phase 5+ 에서.
                return UniversalEvalOutput(
                    input=input_text,
                    actual_output=actual_output or str(data),
                    http_status=response.status_code,
                    raw_response=raw_response,
                    error=error_message,
                    error_type="system",
                    latency_ms=latency_ms,
                    usage=usage,
                )

            return UniversalEvalOutput(
                input=input_text,
                actual_output=str(actual_output),
                retrieval_context=self._extract_contexts(data),
                http_status=response.status_code,
                raw_response=raw_response,
                latency_ms=latency_ms,
                usage=usage,
            )
        except requests.exceptions.RequestException as exc:
            # 네트워크 예외를 표준 출력 구조로 감싸면 상위 평가 로직이 동일하게 처리할 수 있습니다.
            # Phase 3.3 Q1: ConnError/Timeout 은 전부 system 에러.
            return UniversalEvalOutput(
                input=input_text,
                actual_output="",
                error=f"Connection Error: {exc}",
                error_type="system",
                latency_ms=int((time.time() - start_time) * 1000),
            )
