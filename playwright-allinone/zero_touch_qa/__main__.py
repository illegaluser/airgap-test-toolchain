"""
DSCORE Zero-Touch QA v4.0 — CLI 엔트리포인트.

사용법:
  python3 -m zero_touch_qa --mode chat
  python3 -m zero_touch_qa --mode doc --file upload.pdf
  python3 -m zero_touch_qa --mode convert --file recorded.py
  python3 -m zero_touch_qa --mode convert --convert-only --file recorded.py
  python3 -m zero_touch_qa --mode execute --scenario scenario.json
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time

from . import __version__
from .config import Config
from .converter import convert_playwright_to_dsl
from .dify_client import DifyClient, DifyConnectionError
from .executor import QAExecutor
from .metrics import aggregate_llm_sla
from .report import build_html_report, save_run_log, save_scenario
from .regression_generator import generate_regression_test
from .utils import parse_structured_doc_steps

log = logging.getLogger("zero_touch_qa")


def main():
    parser = argparse.ArgumentParser(
        description=f"DSCORE Zero-Touch QA v{__version__}"
    )
    parser.add_argument(
        "--mode",
        choices=["chat", "doc", "convert", "execute"],
        required=True,
        help="chat: 자연어, doc: 기획서 업로드, convert: Playwright 녹화 변환, execute: 기존 시나리오 재실행",
    )
    parser.add_argument("--file", default=None, help="기획서 또는 Playwright .py 파일 경로")
    parser.add_argument("--scenario", default=None, help="기존 scenario.json 경로 (execute 모드)")
    parser.add_argument("--target-url", default=None, help="테스트 시작 URL")
    parser.add_argument("--srs-text", default=None, help="자연어 요구사항 (chat 모드)")
    parser.add_argument("--api-docs", default=None, help="API 엔드포인트 힌트 텍스트 (선택)")
    parser.add_argument("--headed", action="store_true", default=True, help="실제 브라우저 표시 (기본값)")
    parser.add_argument("--headless", action="store_true", help="헤드리스 모드")
    parser.add_argument(
        "--convert-only",
        action="store_true",
        help=(
            "convert 모드에서 변환 + 검증 + scenario.json 저장 후 즉시 종료 (executor 미실행). "
            "Recording 서비스 같은 외부 호출자가 변환 결과만 필요할 때 사용."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="상세 로그 출력")
    # T-D / P0.1 — storage_state dump/restore (인증 후 세션 재사용).
    # env AUTH_STORAGE_STATE_IN/OUT 도 동작 — CLI 인자가 우선.
    parser.add_argument(
        "--storage-state-in", default=None,
        help="시작 시 복원할 storage_state JSON 경로 (인증 스킵용)",
    )
    parser.add_argument(
        "--storage-state-out", default=None,
        help="종료 후 덤프할 storage_state JSON 경로 (인증 결과 보존)",
    )
    args = parser.parse_args()

    # 로깅 설정
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Recording 서비스 계약상 convert-only 오용은 Dify retry 전에 즉시 실패해야 한다.
    if args.convert_only and args.mode != "convert":
        log.error("--convert-only 는 --mode convert 와 함께만 사용한다.")
        sys.exit(1)

    config = Config.from_env()
    headed = not args.headless

    # 환경변수 폴백 (Jenkins에서 env로 전달하는 경우)
    target_url = args.target_url or os.getenv("TARGET_URL", "")
    srs_text = args.srs_text or os.getenv("SRS_TEXT", "")
    api_docs = args.api_docs or os.getenv("API_DOCS", "")

    try:
        scenario = _prepare_scenario(args, config, target_url, srs_text, api_docs)
    except DifyConnectionError as e:
        log.error("Dify 연결 실패: %s", e)
        _generate_error_report(config.artifacts_dir, str(e))
        sys.exit(1)
    except ScenarioValidationError as e:
        log.error("시나리오 구조 검증 실패: %s", e)
        _generate_error_report(
            config.artifacts_dir,
            f"시나리오 구조 검증 실패: {e}",
        )
        sys.exit(1)
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)

    if not scenario:
        log.error("시나리오가 비어 있습니다.")
        sys.exit(1)

    # --convert-only: Recording 서비스(또는 외부 호출자) 가 변환 결과만 필요한 경우
    # 여기서 즉시 종료한다. executor 호출·navigate prepend·HTML 리포트 생성 전부 스킵.
    # convert 분기에서 이미 _validate_scenario 통과한 시나리오만 도달하므로
    # scenario.json 만 저장하고 빠진다.
    if args.convert_only:
        save_scenario(scenario, config.artifacts_dir)
        log.info(
            "[convert-only] %d 스텝 변환 + 검증 완료 → %s/scenario.json",
            len(scenario),
            config.artifacts_dir,
        )
        sys.exit(0)

    # 방어: Planner LLM 이 step 1 navigate 를 drop 한 경우 자동 prepend.
    # gemma4:e4b 같은 작은 모델이 Chatflow 의 navigate-first 지시를 무시할 때
    # 브라우저가 about:blank 에서 시작해 모든 후속 step 이 실패하는 것을 막는다.
    if target_url and scenario[0].get("action") != "navigate":
        log.info("[Guard] scenario[0].action != navigate — TARGET_URL 로 navigate step 자동 prepend")
        scenario.insert(0, {
            "step": 1,
            "action": "navigate",
            "target": "",
            "value": target_url,
            "description": "대상 페이지 로드 (엔진 자동 보강)",
        })
        # prepend 후 step 번호 1..N 으로 재정렬 — _validate_scenario 가 수행하는
        # renumber 가 prepend 이전에 끝나므로, 여기서도 동일 정책을 다시 적용해
        # 리포트에 "Step 1 navigate, Step 1 hover" 같이 중복 번호가 노출되지 않게 한다.
        for idx, st in enumerate(scenario):
            st["step"] = idx + 1

    # 원본 시나리오 저장
    save_scenario(scenario, config.artifacts_dir)

    # 업로드 원본 파일 (기획서 / Playwright 녹화 / scenario.json) 을 artifacts 로
    # 복사해 HTML 리포트에서 참조 가능하게 한다. 리포트를 공유받은 사람이
    # "어떤 입력으로 이 결과가 나왔는지" 를 리포트 한 폴더로 전부 추적할 수 있다.
    upload_source = args.scenario if args.mode == "execute" else args.file
    uploaded_name = _copy_upload_to_artifacts(upload_source, config.artifacts_dir)

    # 실행
    log.info("시나리오 실행 시작 (%d스텝, headed=%s)", len(scenario), headed)
    executor = QAExecutor(config)
    results = executor.execute(
        scenario,
        headed=headed,
        storage_state_in=args.storage_state_in,
        storage_state_out=args.storage_state_out,
    )

    # 산출물 생성
    save_run_log(results, config.artifacts_dir)
    save_scenario(scenario, config.artifacts_dir, suffix=".healed")
    # llm_calls.jsonl → llm_sla.json 집계 (S4C-05). 빌드별 LLM SLA 가
    # archiveArtifacts 와 HTML 리포트의 운영 지표 섹션에 자동 노출된다.
    aggregate_llm_sla(config.artifacts_dir)
    build_html_report(
        results,
        config.artifacts_dir,
        version=__version__,
        uploaded_file=uploaded_name,
        run_mode=args.mode,
    )
    generate_regression_test(scenario, results, config.artifacts_dir)

    # 결과 요약
    passed = sum(1 for r in results if r.status in ("PASS", "HEALED"))
    failed = sum(1 for r in results if r.status == "FAIL")
    log.info("실행 완료 — PASS: %d, FAIL: %d", passed, failed)

    if failed > 0:
        sys.exit(1)


class ScenarioValidationError(ValueError):
    """Dify 가 반환한 scenario 가 구조적으로 무효."""


# [표준 액션] — 14 standard actions (Planner 가 emit) + 인증 보조 액션 auth_login.
# auth_login 은 LLM 이 emit 하지 않고 사용자가 작성한 시나리오에 직접 들어오므로
# dify-chatflow.yaml Planner prompt 와는 동기화하지 않는다 (executor 만 처리).
_VALID_ACTIONS = frozenset(
    {
        "navigate",
        "click",
        "fill",
        "press",
        "select",
        "check",
        "hover",
        "wait",
        "verify",
        "upload",
        "drag",
        "scroll",
        "mock_status",
        "mock_data",
        "auth_login",
    }
)

# verify.condition 화이트리스트 — executor._perform_action 의 분기와 1:1 동기.
# "" (빈 문자열) 은 "값이 들어 있으면 contains, 아니면 visible" 로 해석됨 → 허용.
_VALID_VERIFY_CONDITIONS = frozenset(
    {
        "",
        "visible",
        "hidden",
        "disabled",
        "enabled",
        "checked",
        "value",
        "text",
        "contains_text",
        "contains",
    }
)


_TARGET_OPTIONAL_ACTIONS = ("navigate", "wait", "press")
# auth_login: target=mode (form/totp/oauth) 필수, value=credential alias 필수.
_VALUE_REQUIRED_ACTIONS = frozenset(
    {"fill", "press", "select", "upload", "drag", "auth_login"}
)
_SCROLL_VALID_VALUES = frozenset({"into_view", "into-view", "into view"})
_AUTH_LOGIN_MODES = frozenset({"form", "totp", "oauth"})


def _check_mock_times(i: int, step: dict) -> None:
    """mock_* 의 선택적 ``times`` 가 양의 정수인지 검증한다."""
    if "times" not in step:
        return
    try:
        n = int(step["times"])
    except (TypeError, ValueError) as e:
        raise ScenarioValidationError(
            f"step[{i}] action={step['action']} 의 times 가 정수 아님: {step['times']!r}"
        ) from e
    if n < 1:
        raise ScenarioValidationError(
            f"step[{i}] action={step['action']} 의 times 는 1 이상이어야 함 (={n})"
        )


def _check_step_shape(i: int, step) -> dict:
    """list[dict] 가정과 action 화이트리스트만 검사하고 step 을 반환한다."""
    if not isinstance(step, dict):
        raise ScenarioValidationError(
            f"step[{i}] 가 dict 아님 (타입={type(step).__name__})"
        )
    action = step.get("action")
    if isinstance(action, str):
        normalized = action.strip().strip("`'\" ").lower()
        if normalized != action:
            step["action"] = normalized
            action = normalized
    if action not in _VALID_ACTIONS:
        raise ScenarioValidationError(f"step[{i}].action 이 유효하지 않음: {action!r}")
    return step


def _check_target_value_contract(i: int, step: dict) -> None:
    """action 별 target/value 필수 여부를 검사한다."""
    action = step["action"]
    if action not in _TARGET_OPTIONAL_ACTIONS and not step.get("target"):
        raise ScenarioValidationError(
            f"step[{i}] action={action} 인데 target 이 비어 있음"
        )
    if action == "press" and not (step.get("target") or step.get("value")):
        raise ScenarioValidationError(
            f"step[{i}] action=press 인데 target/value 가 모두 비어 있음"
        )
    if action in _VALUE_REQUIRED_ACTIONS and not str(step.get("value", "")).strip():
        raise ScenarioValidationError(
            f"step[{i}] action={action} 인데 value 가 비어 있음"
        )


def _check_action_specific(i: int, step: dict) -> None:
    """scroll/mock_*/verify 등 액션별 추가 계약을 검사한다."""
    action = step["action"]
    if action == "scroll":
        scroll_value = str(step.get("value", "")).strip().lower()
        if scroll_value not in _SCROLL_VALID_VALUES:
            raise ScenarioValidationError(
                f"step[{i}] action=scroll 인데 value 는 'into_view' 여야 함"
            )
        return
    if action == "mock_status":
        try:
            int(str(step.get("value", "")).strip())
        except ValueError as e:
            raise ScenarioValidationError(
                f"step[{i}] action=mock_status 인데 value 가 정수 아님"
            ) from e
        _check_mock_times(i, step)
        return
    if action == "mock_data":
        if step.get("value") in ("", None):
            raise ScenarioValidationError(
                f"step[{i}] action=mock_data 인데 value 가 비어 있음"
            )
        _check_mock_times(i, step)
        return
    if action == "auth_login":
        # target = "form" | "totp" | "oauth" — 콤마 뒤에 ", email_field=#x, ..." 같은
        # explicit selector 모디파이어가 따라올 수 있다. 첫 토큰만 모드로 취급.
        head = str(step.get("target", "")).split(",", 1)[0].strip().lower()
        if head not in _AUTH_LOGIN_MODES:
            raise ScenarioValidationError(
                f"step[{i}] action=auth_login 의 target 은 "
                f"{sorted(_AUTH_LOGIN_MODES)} 중 하나여야 함 (={head!r})"
            )
        return
    if action == "verify":
        condition = str(step.get("condition", "")).strip().lower()
        if condition not in _VALID_VERIFY_CONDITIONS:
            # LLM 이 화이트리스트 밖의 자유 condition (empty / present / exists 등) 을 emit 하면
            # reject 하지 말고 빈 문자열로 강등 — executor 의 default fallback ("value 있으면
            # contains, 없으면 visible") 으로 안전 매핑한다. 시나리오 전체 폐기를 막는다.
            step["condition"] = ""


def _sanitize_scenario(scenario):
    """LLM 비결정성 1차 흡수 — action 누락/invalid 한 step 은 drop 후 반환.

    Planner LLM 이 14스텝 중 1개 step 의 action 키를 누락하거나 typos 가 섞이는 케이스
    가 빈번. 시나리오 전체를 reject + retry 하는 비용 (gemma4:26b 추론 ~30s+) 보다
    invalid step 만 drop 하고 진행하는 게 결정적이고 빠르다. 단, drop 사유는 WARNING
    으로 남겨 사용자가 추적 가능.

    빈 시나리오는 그대로 반환 — _validate_scenario 가 reject 처리.
    """
    if not isinstance(scenario, list):
        return scenario
    keep = []
    for i, st in enumerate(scenario):
        if not isinstance(st, dict):
            log.warning("[Sanitize] step[%d] 가 dict 아님 — drop: %r", i, st)
            continue
        action = st.get("action")
        if not isinstance(action, str):
            log.warning("[Sanitize] step[%d] action 누락/None — drop: %r", i, st)
            continue
        normalized = action.strip().strip("`'\" ").lower()
        if normalized not in _VALID_ACTIONS:
            # LLM 이 action 필드에 meta-reasoning 을 섞어 emit 하는 케이스 회복.
            # 예: "verify, target: id=status, value: ..." 또는 "`verify`, ..."
            # 앞쪽 첫 토큰이 valid action 이면 그것을 채택, 그 외는 drop.
            head = re.split(r"[\s,;:()`'\"*]", normalized, maxsplit=1)[0]
            if head in _VALID_ACTIONS:
                log.warning(
                    "[Sanitize] step[%d] action=%r → 첫 토큰 %r 로 회복 (LLM meta-reasoning leak)",
                    i, action, head,
                )
                st = {**st, "action": head}
            else:
                log.warning("[Sanitize] step[%d] 미지원 action=%r — drop", i, action)
                continue
        keep.append(st)
    if len(keep) != len(scenario):
        log.warning("[Sanitize] %d/%d step 유지", len(keep), len(scenario))
    return keep


def _validate_scenario(scenario) -> None:
    """Dify 응답 scenario 의 구조적 유효성 검증. 실패 시 ScenarioValidationError.

    LLM 비결정성으로 인해 드물게 발생하는 다음 케이스를 조기에 탐지:
    - 빈 배열 / list 아닌 타입
    - step 요소가 dict 아님 (문자열 / null 혼입)
    - action 이 14대 표준 밖이거나 누락
    - navigate/wait/press 이외 action 에서 target 이 비어 있음 (실행 시 locator 실패 확정)
    - 신규 액션의 최소 입력 계약(value/condition 등) 위반

    이 검증을 통과해도 시맨틱상 잘못된 시나리오 (예: SRS 와 무관한 작업) 는 막을 수
    없다. 그 경우는 executor 레벨의 Healer / Guard 가 후단에서 대응.
    """
    if not isinstance(scenario, list) or not scenario:
        raise ScenarioValidationError("시나리오 배열이 비어 있음")
    for i, raw_step in enumerate(scenario):
        step = _check_step_shape(i, raw_step)
        _check_target_value_contract(i, step)
        _check_action_specific(i, step)
        # LLM 이 emit 한 step 번호는 비순차·누락이 잦다 (예: 1, 18). list 순서 자체가
        # 진짜 ordering 이므로 1..N 으로 강제 정렬해 리포트 가독성을 보장한다.
        step["step"] = i + 1


def _prepare_scenario(
    args, config: Config, target_url: str, srs_text: str, api_docs: str
) -> list[dict]:
    """모드에 따라 시나리오를 준비한다."""
    if args.mode == "execute":
        if not args.scenario:
            raise FileNotFoundError("execute 모드에는 --scenario 인자가 필요합니다.")
        with open(args.scenario, "r", encoding="utf-8") as f:
            scenario = json.load(f)
        # 외부에서 들어온 시나리오도 chat/doc 과 동일한 14대 DSL 계약을 강제한다.
        # 손으로 작성한 scenario.json 의 mock_status value 정수성 같은 계약 위반이
        # 런타임 ValueError 로 흘러들어가 자가치유에 의해 가려지는 것을 막는다.
        _validate_scenario(scenario)
        log.info("[Scenario] %s 로드 (%d스텝)", args.scenario, len(scenario))
        return scenario

    if args.mode == "convert":
        if not args.file:
            raise FileNotFoundError("convert 모드에는 --file 인자가 필요합니다.")
        scenario = convert_playwright_to_dsl(args.file, config.artifacts_dir)
        # 14대 DSL 계약 검증을 convert 경로에서도 강제 — 기존엔 누락되어
        # 손상된 DSL 이 executor 단계에서 ValueError 로 흘러들었다.
        # Recording 서비스(--convert-only) 도 이 검증으로 즉시 실패를 받음.
        _validate_scenario(scenario)
        return scenario

    # chat / doc 모드: Dify 호출
    dify = DifyClient(config)
    file_id = None

    if args.mode == "doc":
        if not args.file:
            log.warning("[Doc] --file 인자가 없습니다. SRS_TEXT로 대체합니다.")
        else:
            # 클라이언트 측 파일 → 텍스트 추출 후 srs_text 에 prepend.
            # Dify Chatflow 의 Planner 노드가 context.enabled=false 상태라 파일 업로드
            # 경로로는 LLM 이 내용을 못 읽는다. 텍스트로 직접 넣어야 gemma4:e4b 가
            # 문서를 보면서 시나리오를 생성할 수 있다.
            try:
                doc_text = dify.extract_text_from_file(args.file)
                if doc_text:
                    structured = parse_structured_doc_steps(doc_text)
                    if structured:
                        log.info(
                            "[Doc] 구조화된 step marker 감지 — Dify 생략, 로컬 파서 결과 사용 (%d스텝)",
                            len(structured),
                        )
                        return structured
                    if srs_text:
                        srs_text = (
                            f"[첨부 문서 내용]\n{doc_text}\n\n"
                            f"[추가 요구사항]\n{srs_text}"
                        )
                    else:
                        srs_text = f"[첨부 문서 내용]\n{doc_text}"
                    log.info("[Doc] 문서에서 %d 자 추출, srs_text 에 병합", len(doc_text))
                else:
                    log.warning("[Doc] 문서에서 추출된 텍스트가 비어있습니다.")
            except Exception as e:
                log.warning(
                    "[Doc] 파일 추출 실패 (%s) — 기존 upload_file 경로로 폴백", e
                )
                file_id = dify.upload_file(args.file)

    # LLM 비결정성 대비 — Dify 응답을 구조 검증 후, 무효면 최대 3 회까지 재생성.
    # 가장 흔한 실패: (1) scenario 배열이 비었거나, (2) step.action 이 9대 표준 밖,
    # (3) fill/click 등인데 target 이 비어있음. 이 조건들은 executor 로 넘기면
    # selector 실패로 귀결되므로 여기서 조기 차단하고 재시도.
    for attempt in range(1, 4):
        try:
            scenario = dify.generate_scenario(
                run_mode=args.mode,
                srs_text=srs_text,
                target_url=target_url,
                api_docs=api_docs,
                file_id=file_id,
                enable_grounding=os.getenv("ENABLE_DOM_GROUNDING", "0") == "1",
            )
            # LLM 비결정성 1차 흡수 — action 누락/invalid 한 step 은 drop 후 검증.
            # 정상 step 만 ≥1개 남으면 retry 비용을 아끼고 그대로 진행.
            scenario = _sanitize_scenario(scenario)
            _validate_scenario(scenario)
            log.info(
                "[Dify] 시나리오 수신 (%d스텝) — attempt %d/3 성공",
                len(scenario), attempt,
            )
            return scenario
        except (DifyConnectionError, ScenarioValidationError) as e:
            if attempt < 3:
                backoff = 5 * attempt  # 5s, 10s, 15s
                log.warning(
                    "[Retry %d/3] 시나리오 수신/검증 실패 — %s (다음 시도까지 %ds 대기)",
                    attempt, e, backoff,
                )
                time.sleep(backoff)
            else:
                log.error("[Dify] 3 회 시도 모두 실패. 마지막 오류: %s", e)
                raise


def _copy_upload_to_artifacts(source_path: str | None, artifacts_dir: str) -> str | None:
    """사용자가 업로드한 원본 파일 (기획서 / Playwright 녹화 / scenario.json) 을
    artifacts 디렉토리로 복사해 HTML 리포트의 "첨부 문서" 섹션에서 참조 가능하게 한다.

    doc / convert / execute 세 모드 공통 적용. chat 모드는 업로드 없음 → None 반환.

    Args:
        source_path: Pipeline 이 저장한 업로드 파일 경로. None 이거나 실제 파일이
            없으면 아무 것도 하지 않고 None 반환.
        artifacts_dir: 저장 디렉토리. 없으면 생성.

    Returns:
        artifacts 에 복사된 파일의 basename (예: ``upload.pdf``).
        원본이 없으면 None.
    """
    if not source_path or not os.path.isfile(source_path):
        return None
    os.makedirs(artifacts_dir, exist_ok=True)
    basename = os.path.basename(source_path)
    dest = os.path.join(artifacts_dir, basename)
    try:
        if os.path.abspath(source_path) == os.path.abspath(dest):
            return basename
    except OSError:
        pass
    shutil.copy2(source_path, dest)
    log.info("[Upload] 원본 파일 artifacts 에 복사: %s", basename)
    return basename


def _generate_error_report(artifacts_dir: str, error_msg: str):
    """Dify 연결 실패 시 최소한의 에러 리포트를 생성한다."""
    os.makedirs(artifacts_dir, exist_ok=True)
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><title>Zero-Touch QA Error</title></head>
<body style="font-family: sans-serif; margin: 40px; color: #991b1b;">
  <h1>Zero-Touch QA 실행 실패</h1>
  <p style="background: #fee2e2; padding: 16px; border-radius: 8px;">
    <strong>Dify 연결 실패:</strong> {error_msg}
  </p>
  <p>Dify 서비스 상태를 확인하십시오.</p>
</body>
</html>"""
    path = os.path.join(artifacts_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("[Error Report] %s 생성", path)


if __name__ == "__main__":
    main()
