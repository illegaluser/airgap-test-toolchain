import json
import logging
import os
import time

import requests

from .config import Config
from .metrics import append_jsonl
from .utils import extract_json_safely

log = logging.getLogger(__name__)


class DifyConnectionError(Exception):
    """Dify API 통신 실패 시 발생한다."""


def _format_dify_error(base_url: str, exc: requests.RequestException) -> str:
    """Dify API 호출 실패 메시지를 운영자 친화적으로 정리.

    ``ConnectionError`` (TCP 연결 거부 / DNS 실패 등) 는 dscore 컨테이너
    미가동 또는 ``DIFY_BASE_URL`` 오설정이 90 % 이상이라 가이드를 prepend.
    원본 메시지는 디버깅용으로 보존.
    """
    raw = str(exc)
    if isinstance(exc, requests.ConnectionError):
        return (
            "Dify API 연결 실패 — dscore.ttc.playwright 컨테이너가 떠 있는지, "
            f"또는 DIFY_BASE_URL ({base_url}) 가 맞는지 확인하세요. "
            f"원본: {raw}"
        )
    return f"Dify API 통신 실패: {raw}"


class DifyClient:
    """
    Dify Chatflow API 통신 계층.
    - /v1/files/upload : Doc 모드 문서 업로드
    - /v1/chat-messages : 시나리오 생성 및 치유 요청 (blocking)
    """

    # 일시적 오류 시 재시도할 HTTP 상태 코드
    _RETRYABLE_STATUS_CODES = {502, 503, 504}

    def __init__(self, config: Config):
        self.base_url = config.dify_base_url
        self.headers = {"Authorization": f"Bearer {config.dify_api_key}"}
        # 기본 None = 무제한. env (HEAL_TIMEOUT_SEC / SCENARIO_TIMEOUT_SEC) 양의
        # 정수일 때만 한도 적용. 운영 원칙 — timeout 강제중단은 사용자 명시 옵트인.
        self.heal_timeout_sec = getattr(config, "heal_timeout_sec", None)
        self.scenario_timeout_sec = getattr(config, "scenario_timeout_sec", None)
        # 파싱 실패 시 raw 응답 덤프 경로 (사후 진단)
        self.artifacts_dir = getattr(config, "artifacts_dir", None)
        self.llm_calls_path = (
            os.path.join(self.artifacts_dir, "llm_calls.jsonl")
            if self.artifacts_dir
            else None
        )

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        max_retries: int = 3,
        backoff_base: float = 5.0,
        timeout: int = 120,
        **kwargs,
    ) -> requests.Response:
        """HTTP 요청을 전송하되, 일시적 오류 시 지수 백오프로 재시도한다.

        재시도 대상:
            - ``requests.ConnectionError`` (연결 거부, DNS 실패 등)
            - ``requests.Timeout`` (읽기/연결 타임아웃)
            - HTTP 502, 503, 504 (업스트림 일시 장애)

        4xx 클라이언트 에러는 즉시 반환하여 호출부에서 처리한다.

        Args:
            method: HTTP 메서드 (``"POST"`` 등).
            url: 요청 URL.
            max_retries: 최대 재시도 횟수. 초회 포함하지 않음.
            backoff_base: 첫 재시도 대기 시간(초). 이후 2배씩 증가.
            timeout: 요청 타임아웃(초).
            **kwargs: ``requests.request()`` 에 전달할 추가 인자.

        Returns:
            성공한 ``requests.Response`` 객체.

        Raises:
            requests.RequestException: 모든 재시도 소진 후에도 실패 시.
        """
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                res = requests.request(method, url, timeout=timeout, **kwargs)
                setattr(res, "_ztqa_retry_count", attempt)
                if res.status_code not in self._RETRYABLE_STATUS_CODES:
                    return res
                last_exc = requests.HTTPError(
                    f"HTTP {res.status_code}", response=res,
                )
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e

            if attempt < max_retries:
                wait = backoff_base * (2 ** attempt)
                log.warning(
                    "[Retry] %s %s — %d/%d 재시도 (%.0f초 후). 원인: %s",
                    method, url, attempt + 1, max_retries, wait, last_exc,
                )
                time.sleep(wait)

        raise last_exc  # type: ignore[misc]

    # ── Doc 모드: 문서 파일 업로드 ──
    def upload_file(self, file_path: str) -> str:
        """Dify Files API 에 문서를 업로드하고 upload_file_id 를 반환한다.

        Args:
            file_path: 업로드할 PDF 등 문서 파일 경로.

        Returns:
            Dify 가 부여한 파일 ID 문자열.

        Raises:
            DifyConnectionError: HTTP 에러 또는 네트워크 실패 시.
        """
        log.info("[Doc] 문서 업로드 중... (%s)", file_path)
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        try:
            res = self._request_with_retry(
                "POST",
                f"{self.base_url}/files/upload",
                headers=self.headers,
                files={"file": (filename, file_bytes, "application/pdf")},
                data={"user": "mac-agent"},
                # 파일 업로드 단일 timeout — 큰 PDF 대비 무제한 (env 미설정 시).
                # 같은 운영 원칙: timeout 강제중단은 사용자 명시 옵트인.
                timeout=self.scenario_timeout_sec,
            )
            res.raise_for_status()
        except requests.RequestException as e:
            raise DifyConnectionError(f"파일 업로드 실패: {e}") from e

        file_id = res.json().get("id")
        log.info("[Doc] 문서 업로드 완료 (ID: %s)", file_id)
        return file_id

    # ── Doc 모드: 파일을 LLM 입력용 텍스트로 추출 ──
    def extract_text_from_file(self, file_path: str) -> str:
        """업로드 파일을 LLM 이 바로 읽을 수 있는 **plain/markdown 텍스트**로 변환한다.

        Dify Chatflow 의 Planner 노드가 ``context.enabled: false`` 로 묶여 있어
        업로드된 파일의 내용을 LLM 이 입력으로 받지 못한다. 이 함수는 클라이언트
        측에서 텍스트를 추출해 ``srs_text`` 에 병합하도록 설계됐다 — Chatflow
        구조를 건드리지 않고도 LLM 이 문서 내용을 직접 보게 된다.

        파일 타입은 **magic bytes 로 감지** — Jenkins Pipeline 이 DOC_FILE 을
        항상 ``upload.pdf`` 로 저장하더라도 실제 내용이 markdown / 일반 텍스트면
        그 경로로 처리된다:

        - PDF (``%PDF-`` 시작): ``pymupdf`` 로 페이지별 텍스트 추출 후 ``## Page N``
          구분자로 결합 (markdown 스타일)
        - 그 외: UTF-8 로 직접 read (``errors="replace"`` — 비정상 바이트 내성)

        상한 (``DIFY_DOC_MAX_CHARS`` env, 기본 12000 자) 초과 시 앞부분만 +
        ``[... truncated at N chars ...]`` 주석. ``OLLAMA_CONTEXT_SIZE=16384``
        가정 하에서 토큰 예산 (~12k chars → ~3k tokens) 안전 범위.

        Args:
            file_path: 추출할 파일 경로 (보통 Pipeline 이 저장한
                ``$AGENT_HOME/upload.pdf``).

        Returns:
            추출된 텍스트 (UTF-8 문자열). 파일이 비어있으면 빈 문자열.

        Raises:
            FileNotFoundError: 파일이 존재하지 않을 때.
            ImportError: PDF 파일인데 ``pymupdf`` 패키지가 설치되지 않았을 때.
        """
        max_chars = int(os.getenv("DIFY_DOC_MAX_CHARS", "12000"))
        with open(file_path, "rb") as f:
            head = f.read(8)
        if head.startswith(b"%PDF-"):
            import pymupdf  # 지연 import — PDF 가 아닐 때 의존성 불필요
            doc = pymupdf.open(file_path)
            parts = []
            for i, page in enumerate(doc, 1):
                page_text = page.get_text().strip()
                if page_text:
                    parts.append(f"## Page {i}\n\n{page_text}")
            text = "\n\n".join(parts)
            doc.close()
        else:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()

        # 구조화된 doc step marker 가 있으면 truncation 전에 우선 보존한다.
        # PDF 본문이 길어 뒤쪽 marker 블록이 잘리면 doc 모드의 로컬 deterministic
        # 파서가 동작하지 못하고, 다시 LLM 비결정성 경로로 빠진다.
        marker_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("ZTQA_STEP|")
        ]

        if len(text) > max_chars:
            if marker_lines:
                marker_block = "\n".join(marker_lines)
                remaining = max(0, max_chars - len(marker_block) - 64)
                text = (
                    marker_block
                    + "\n\n"
                    + text[:remaining]
                    + f"\n\n[... truncated at {max_chars} chars ...]"
                )
            else:
                text = text[:max_chars] + f"\n\n[... truncated at {max_chars} chars ...]"
        log.info("[Doc] 문서 텍스트 추출: %d 자 (%s)", len(text), os.path.basename(file_path))
        return text

    # ── 시나리오 생성 (chat / doc 모드) ──
    def generate_scenario(
        self,
        run_mode: str,
        srs_text: str,
        target_url: str,
        api_docs: str = "",
        file_id: str | None = None,
        enable_grounding: bool = False,
    ) -> list[dict]:
        """Dify Chatflow 에 시나리오 생성을 요청하고 DSL 스텝 배열을 반환한다.

        Args:
            run_mode: 실행 모드 (``"chat"`` 또는 ``"doc"``).
            srs_text: 자연어 요구사항 텍스트.
            target_url: 테스트 대상 URL.
            api_docs: 네트워크 모킹 힌트용 API 엔드포인트 요약 텍스트.
            file_id: Doc 모드에서 ``upload_file()`` 이 반환한 파일 ID. 없으면 None.
            enable_grounding: True 면 target_url 의 DOM 인벤토리를 srs_text 앞에
                prepend 한다 (Phase 1 T1.5). env ``ENABLE_DOM_GROUNDING=1`` 로
                기본값 토글 가능.

        Returns:
            DSL 스텝 dict 의 리스트.

        Raises:
            DifyConnectionError: API 통신 실패 또는 JSON 파싱 실패 시.
        """
        # Phase 1 grounding: target_url 의 실제 DOM 인벤토리를 prepend.
        # 추출 실패 시 graceful degradation — 기존 경로 유지.
        grounding_meta = {"used": False}
        if enable_grounding and target_url:
            srs_text, grounding_meta = self._prepend_dom_inventory(
                srs_text, target_url,
            )

        payload = {
            "inputs": {
                "run_mode": run_mode,
                "srs_text": srs_text,
                "target_url": target_url,
                "api_docs": api_docs,
            },
            "query": "실행을 요청합니다.",
            "response_mode": "blocking",
            "user": "mac-agent",
        }
        if file_id:
            payload["files"] = [
                {
                    "type": "document",
                    "transfer_method": "local_file",
                    "upload_file_id": file_id,
                }
            ]

        # Planner 호출은 e4b 같은 느린 모델에서 120초 기본 timeout 초과 가능.
        # scenario_timeout_sec(기본 300s) 적용 + retry 1회 (network blip 대비).
        # 모델이 느린 건 재시도해도 또 느릴 뿐이라 max_retries=1 로 제한.
        answer = self._call(
            payload,
            timeout=self.scenario_timeout_sec,
            max_retries=1,
            call_kind="planner",
            extra_metric=grounding_meta,
        )
        log.info("Dify 응답 길이: %d자, <think> 포함: %s", len(answer), "<think>" in answer)
        scenario = extract_json_safely(answer)
        if not scenario or not isinstance(scenario, list):
            # 실패 시 raw 응답을 artifacts 에 덤프 (사후 진단)
            dump_path = self._dump_raw_response(answer)
            import re
            cleaned = re.sub(r"<think>.*?</think>", "[THINK_BLOCK_REMOVED]", answer, flags=re.S)
            cleaned = re.sub(r"<think>.*", "[UNCLOSED_THINK_REMOVED]", cleaned, flags=re.S)
            dump_msg = f"\n  raw 응답 덤프: {dump_path}" if dump_path else ""
            raise DifyConnectionError(
                f"시나리오 파싱 실패.\n"
                f"  응답 길이: {len(answer)}자\n"
                f"  <think> 블록 제거 후 내용(앞 500자):\n{cleaned[:500]}"
                + dump_msg
            )
        return scenario

    def _dump_raw_response(self, answer: str) -> str | None:
        """파싱 실패한 Dify 원본 응답을 artifacts 디렉토리에 타임스탬프와 함께 저장."""
        if not self.artifacts_dir:
            return None
        try:
            os.makedirs(self.artifacts_dir, exist_ok=True)
            fname = f"dify-raw-response-{time.strftime('%Y%m%dT%H%M%S')}.txt"
            path = os.path.join(self.artifacts_dir, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(answer)
            log.warning("[Dify] raw 응답을 덤프했습니다: %s (%d자)", path, len(answer))
            return path
        except OSError as e:
            log.warning("[Dify] raw 응답 덤프 실패: %s", e)
            return None

    # ── 치유 요청 (heal 모드) ──
    def request_healing(
        self,
        error_msg: str,
        dom_snapshot: str,
        failed_step: dict,
        strategy_trace: list[dict] | None = None,
    ) -> dict | None:
        """실패한 스텝의 치유를 LLM 에 요청하고 새 target 정보를 반환한다.

        Args:
            error_msg: 실패 원인 에러 메시지.
            dom_snapshot: 현재 페이지의 HTML DOM (잘린 길이).
            failed_step: 실패한 DSL 스텝 dict.
            strategy_trace: executor 가 시도한 multi-strategy 결과 리스트. 각 항목
                ``{"strategy": <name>, "error": <msg or "ok">}``. healer 가 "selector
                만 바꿔봐야 같은 timeout" 같은 정보를 알 수 있게 한다.

        Returns:
            새 target/value/condition 이 포함된 dict. 파싱 실패 시 ``None``.
        """
        # B: strategy_trace 를 chatflow inputs 에 주입. chatflow yaml 의 healer 노드
        # prompt 가 ``{{strategy_trace}}`` placeholder 로 받아 사용한다.
        payload = {
            "inputs": {
                "run_mode": "heal",
                "error": error_msg,
                "dom": dom_snapshot,
                "failed_step": json.dumps(failed_step, ensure_ascii=False),
                "strategy_trace": json.dumps(
                    strategy_trace or [], ensure_ascii=False
                ),
            },
            "query": "실행을 요청합니다.",
            "response_mode": "blocking",
            "user": "mac-agent",
        }
        # heal 호출은 사용자 대기시간이 곧 비용. 모델 추론이 느린 경우
        # 재시도해봐야 또 느릴 뿐이므로 max_retries=0 + 짧은 timeout 사용.
        answer = self._call(
            payload,
            timeout=self.heal_timeout_sec,
            max_retries=0,
            call_kind="healer",
        )
        return extract_json_safely(answer)

    # ── 내부: Chatflow API 호출 ──
    def _call(
        self,
        payload: dict,
        *,
        timeout: int = 120,
        max_retries: int = 3,
        call_kind: str = "unknown",
        extra_metric: dict | None = None,
    ) -> str:
        """Dify /chat-messages 엔드포인트에 blocking 요청을 보내고 answer 를 반환한다.

        Args:
            payload: 요청 본문.
            timeout: 단일 요청 timeout(초).
            max_retries: 재시도 횟수. heal 호출은 0 (모델 느림은 일시 장애 아님).
            call_kind: metric 구분자. ``planner`` 또는 ``healer``.

        Raises:
            DifyConnectionError: HTTP 에러, 타임아웃, 네트워크 실패 시.
        """
        started = time.time()
        status_code: int | None = None
        retry_count = 0
        answer = ""
        error_msg = ""
        timeout_hit = False
        try:
            res = self._request_with_retry(
                "POST",
                f"{self.base_url}/chat-messages",
                json=payload,
                headers={
                    **self.headers,
                    "Content-Type": "application/json",
                },
                timeout=timeout,
                max_retries=max_retries,
            )
            status_code = res.status_code
            retry_count = int(getattr(res, "_ztqa_retry_count", 0) or 0)
            res.raise_for_status()
            answer = res.json().get("answer", "")
            return answer
        except requests.RequestException as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            retry_count = max_retries
            timeout_hit = isinstance(e, requests.Timeout)
            error_msg = str(e)
            raise DifyConnectionError(
                _format_dify_error(self.base_url, e)
            ) from e
        finally:
            self._record_llm_call_metric(
                kind=call_kind,
                started_at=started,
                elapsed_ms=round((time.time() - started) * 1000, 2),
                timeout_sec=timeout,
                retry_count=retry_count,
                status_code=status_code,
                timeout=timeout_hit,
                answer_chars=len(answer),
                error=error_msg,
                extra=extra_metric,
            )

    def _record_llm_call_metric(
        self,
        *,
        kind: str,
        started_at: float,
        elapsed_ms: float,
        timeout_sec: int,
        retry_count: int,
        status_code: int | None,
        timeout: bool,
        answer_chars: int,
        error: str,
        extra: dict | None = None,
    ) -> None:
        """Append one Dify LLM call metric to artifacts/llm_calls.jsonl."""
        if not self.llm_calls_path:
            return
        record = {
            "kind": kind,
            "started_at": time.strftime(
                "%Y-%m-%dT%H:%M:%S%z", time.localtime(started_at)
            ),
            "elapsed_ms": elapsed_ms,
            "timeout_sec": timeout_sec,
            "retry_count": retry_count,
            "status_code": status_code,
            "timeout": timeout,
            "answer_chars": answer_chars,
            "error": error,
        }
        if extra:
            record.update(extra)
        try:
            append_jsonl(self.llm_calls_path, record)
        except OSError as e:
            log.warning("[Metrics] LLM 호출 metric 기록 실패: %s", e)

    # ── DOM Grounding (Phase 1 T1.5) ──
    def _prepend_dom_inventory(
        self, srs_text: str, target_url: str,
    ) -> tuple[str, dict]:
        """Phase 1 grounding: target_url 의 인벤토리를 srs_text 앞에 prepend.

        실패 시 graceful degradation — 원본 srs_text 그대로 반환 + meta 에 사유.
        """
        try:
            from .grounding import fetch_inventory, serialize_block
            from .grounding.pruner import prune
            from .grounding.budget import fit_to_budget, estimate_tokens, DEFAULT_TOKEN_BUDGET
        except ImportError as e:
            log.warning("[grounding] 모듈 import 실패: %s", e)
            return srs_text, {"used": False, "error": f"import: {e}"}

        budget = int(os.environ.get("GROUNDING_TOKEN_BUDGET", str(DEFAULT_TOKEN_BUDGET)))
        inv = fetch_inventory(target_url)
        if inv.error:
            return srs_text, {
                "used": False, "error": inv.error,
                "target_url": target_url,
            }

        prune(inv)
        fit_to_budget(inv, budget=budget)
        block = serialize_block(inv)
        if not block:
            return srs_text, {
                "used": False, "error": "empty_block",
                "target_url": target_url,
            }

        tokens = estimate_tokens(block)
        merged = block + "\n" + srs_text if srs_text else block
        log.info(
            "[grounding] %s 인벤토리 prepend (요소 %d, %d 토큰, truncated=%s)",
            target_url, len(inv.elements), tokens, inv.truncated,
        )
        return merged, {
            "used": True,
            "target_url": target_url,
            "grounding_inventory_tokens": tokens,
            "grounding_element_count": len(inv.elements),
            "grounding_truncated": inv.truncated,
        }
