"""
DSCORE Zero-Touch QA v4.0 — CLI entry point.

Usage:
  python3 -m zero_touch_qa --mode chat
  python3 -m zero_touch_qa --mode doc --file upload.pdf
  python3 -m zero_touch_qa --mode convert --file recorded.py
  python3 -m zero_touch_qa --mode convert --convert-only --file recorded.py
  python3 -m zero_touch_qa --mode execute --scenario scenario.json

  python3 -m zero_touch_qa auth seed --name <name> --seed-url <url> ...
  python3 -m zero_touch_qa auth list [--json]
  python3 -m zero_touch_qa auth verify --name <name> [--json]
  python3 -m zero_touch_qa auth delete --name <name>
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
    # ── auth subcommand routing ─────────────────────────────────────────
    # For compatibility with the existing ``--mode`` CLI, branch into a separate
    # entry point when the first positional arg is 'auth'. (External callers like
    # replay_proxy that use ``--mode execute`` continue to work as before.)
    if len(sys.argv) >= 2 and sys.argv[1] == "auth":
        sys.exit(_run_auth_cli(sys.argv[2:]))

    parser = argparse.ArgumentParser(
        description=f"DSCORE Zero-Touch QA v{__version__}"
    )
    parser.add_argument(
        "--mode",
        choices=["chat", "doc", "convert", "execute"],
        required=True,
        help="chat: natural language, doc: upload spec, convert: Playwright recording → DSL, execute: re-run an existing scenario",
    )
    parser.add_argument("--file", default=None, help="path to a spec or Playwright .py file")
    parser.add_argument("--scenario", default=None, help="path to an existing scenario.json (execute mode)")
    parser.add_argument("--target-url", default=None, help="test start URL")
    parser.add_argument("--srs-text", default=None, help="natural-language requirements (chat mode)")
    parser.add_argument("--api-docs", default=None, help="API endpoint hint text (optional)")
    parser.add_argument("--headed", action="store_true", default=True, help="show the actual browser (default)")
    parser.add_argument("--headless", action="store_true", help="headless mode")
    parser.add_argument(
        "--convert-only",
        action="store_true",
        help=(
            "In convert mode, exit immediately after converting + validating + saving scenario.json "
            "(do not run the executor). Used when external callers like the Recording service only need the conversion result."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose log output")
    # T-D / P0.1 — storage_state dump/restore (reuse session after auth).
    # env AUTH_STORAGE_STATE_IN/OUT also work — CLI args take precedence.
    parser.add_argument(
        "--storage-state-in", default=None,
        help="path to storage_state JSON to restore at startup (skip auth)",
    )
    parser.add_argument(
        "--storage-state-out", default=None,
        help="path to dump storage_state JSON after run (preserve auth result)",
    )
    args = parser.parse_args()

    # Logging setup
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Per the Recording-service contract, misuse of convert-only must fail before Dify retries.
    if args.convert_only and args.mode != "convert":
        log.error("--convert-only must be used only with --mode convert.")
        sys.exit(1)

    config = Config.from_env()
    headed = not args.headless

    # Env-var fallback (when Jenkins passes them via env)
    target_url = args.target_url or os.getenv("TARGET_URL", "")
    srs_text = args.srs_text or os.getenv("SRS_TEXT", "")
    api_docs = args.api_docs or os.getenv("API_DOCS", "")

    try:
        scenario = _prepare_scenario(args, config, target_url, srs_text, api_docs)
    except DifyConnectionError as e:
        log.error("Dify connection failed: %s", e)
        _generate_error_report(config.artifacts_dir, str(e))
        sys.exit(1)
    except ScenarioValidationError as e:
        log.error("scenario structure validation failed: %s", e)
        _generate_error_report(
            config.artifacts_dir,
            f"scenario structure validation failed: {e}",
        )
        sys.exit(1)
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)

    if not scenario:
        log.error("scenario is empty.")
        sys.exit(1)

    # --convert-only: when an external caller (Recording service, etc.) only needs the
    # conversion result, exit here. Skip executor, navigate prepend, and HTML report.
    # The convert branch only reaches this point with scenarios that already passed
    # _validate_scenario, so we just save scenario.json and exit.
    if args.convert_only:
        save_scenario(scenario, config.artifacts_dir)
        log.info(
            "[convert-only] %d steps converted + validated → %s/scenario.json",
            len(scenario),
            config.artifacts_dir,
        )
        sys.exit(0)

    # Defense: when the Planner LLM drops the step-1 navigate, prepend it automatically.
    # Prevents small models like gemma4:e4b from ignoring the Chatflow's navigate-first
    # instruction and starting the browser on about:blank, which would fail every subsequent step.
    if target_url and scenario[0].get("action") != "navigate":
        log.info("[Guard] scenario[0].action != navigate — auto-prepending a navigate step to TARGET_URL")
        scenario.insert(0, {
            "step": 1,
            "action": "navigate",
            "target": "",
            "value": target_url,
            "description": "load target page (engine auto-injected)",
        })
        # Renumber steps to 1..N after prepend — the renumber inside _validate_scenario
        # ran before prepend, so reapply the same policy here to avoid duplicate numbers
        # like "Step 1 navigate, Step 1 hover" in the report.
        for idx, st in enumerate(scenario):
            st["step"] = idx + 1

    # Save the original scenario
    save_scenario(scenario, config.artifacts_dir)

    # Copy the uploaded original file (spec / Playwright recording / scenario.json) into
    # artifacts so the HTML report can reference it. Anyone receiving the report can
    # trace "which input produced these results" entirely from the report folder.
    upload_source = args.scenario if args.mode == "execute" else args.file
    uploaded_name = _copy_upload_to_artifacts(upload_source, config.artifacts_dir)

    # Run
    log.info("starting scenario execution (%d steps, headed=%s)", len(scenario), headed)
    executor = QAExecutor(config)
    results = executor.execute(
        scenario,
        headed=headed,
        storage_state_in=args.storage_state_in,
        storage_state_out=args.storage_state_out,
    )

    # Produce artifacts
    save_run_log(results, config.artifacts_dir)
    save_scenario(scenario, config.artifacts_dir, suffix=".healed")
    # llm_calls.jsonl → llm_sla.json aggregation (S4C-05). The per-build LLM SLA
    # is automatically exposed in archiveArtifacts and the operations-metrics
    # section of the HTML report.
    aggregate_llm_sla(config.artifacts_dir)
    build_html_report(
        results,
        config.artifacts_dir,
        version=__version__,
        uploaded_file=uploaded_name,
        run_mode=args.mode,
    )
    generate_regression_test(scenario, results, config.artifacts_dir)

    # Result summary
    passed = sum(1 for r in results if r.status in ("PASS", "HEALED"))
    failed = sum(1 for r in results if r.status == "FAIL")
    log.info("execution complete — PASS: %d, FAIL: %d", passed, failed)

    if failed > 0:
        sys.exit(1)


class ScenarioValidationError(ValueError):
    """Scenario returned by Dify is structurally invalid."""


# [Standard actions] — the 14 Planner-emitted standard actions + auxiliary
# actions (auth_login, reset_state). Auxiliary actions are not emitted by the LLM;
# they appear only in user-authored scenarios, so we do not sync them with the
# Planner prompt in dify-chatflow.yaml (executor only).
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
        "reset_state",
    }
)

# verify.condition whitelist — 1:1 with the branches in executor._perform_action.
# "" (empty) is interpreted as "contains if value present, else visible" → allowed.
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


_TARGET_OPTIONAL_ACTIONS = ("navigate", "wait", "press", "reset_state")
# auth_login: target=mode (form/totp/oauth) required, value=credential alias required.
# reset_state: target ignored, value=scope (cookie/storage/indexeddb/all) required.
_VALUE_REQUIRED_ACTIONS = frozenset(
    {"fill", "press", "select", "upload", "drag", "auth_login", "reset_state"}
)
_SCROLL_VALID_VALUES = frozenset({"into_view", "into-view", "into view"})
_AUTH_LOGIN_MODES = frozenset({"form", "totp", "oauth"})
# T-B (P0.3-A) — value whitelist for reset_state.
# cookie    → context.clear_cookies()
# storage   → page.evaluate("localStorage.clear(); sessionStorage.clear();")
# indexeddb → page.evaluate(deleteAllIDB)
# all       → all three above
_RESET_STATE_VALID_VALUES = frozenset({"cookie", "storage", "indexeddb", "all"})


def _check_mock_times(i: int, step: dict) -> None:
    """Verify the optional ``times`` of mock_* is a positive integer."""
    if "times" not in step:
        return
    try:
        n = int(step["times"])
    except (TypeError, ValueError) as e:
        raise ScenarioValidationError(
            f"step[{i}] action={step['action']} times is not an integer: {step['times']!r}"
        ) from e
    if n < 1:
        raise ScenarioValidationError(
            f"step[{i}] action={step['action']} times must be >= 1 (={n})"
        )


def _check_step_shape(i: int, step) -> dict:
    """Check the list[dict] assumption and the action whitelist; return the step."""
    if not isinstance(step, dict):
        raise ScenarioValidationError(
            f"step[{i}] is not a dict (type={type(step).__name__})"
        )
    action = step.get("action")
    if isinstance(action, str):
        normalized = action.strip().strip("`'\" ").lower()
        if normalized != action:
            step["action"] = normalized
            action = normalized
    if action not in _VALID_ACTIONS:
        raise ScenarioValidationError(f"step[{i}].action is invalid: {action!r}")
    return step


def _check_target_value_contract(i: int, step: dict) -> None:
    """Check whether target/value are required per action."""
    action = step["action"]
    if action not in _TARGET_OPTIONAL_ACTIONS and not step.get("target"):
        raise ScenarioValidationError(
            f"step[{i}] action={action} but target is empty"
        )
    if action == "press" and not (step.get("target") or step.get("value")):
        raise ScenarioValidationError(
            f"step[{i}] action=press but target and value are both empty"
        )
    if action in _VALUE_REQUIRED_ACTIONS and not str(step.get("value", "")).strip():
        raise ScenarioValidationError(
            f"step[{i}] action={action} but value is empty"
        )


def _check_action_specific(i: int, step: dict) -> None:
    """Per-action extra contract checks (scroll / mock_* / verify, etc.)."""
    action = step["action"]
    if action == "scroll":
        scroll_value = str(step.get("value", "")).strip().lower()
        if scroll_value not in _SCROLL_VALID_VALUES:
            raise ScenarioValidationError(
                f"step[{i}] action=scroll but value must be 'into_view'"
            )
        return
    if action == "mock_status":
        try:
            int(str(step.get("value", "")).strip())
        except ValueError as e:
            raise ScenarioValidationError(
                f"step[{i}] action=mock_status but value is not an integer"
            ) from e
        _check_mock_times(i, step)
        return
    if action == "mock_data":
        if step.get("value") in ("", None):
            raise ScenarioValidationError(
                f"step[{i}] action=mock_data but value is empty"
            )
        _check_mock_times(i, step)
        return
    if action == "auth_login":
        # target = "form" | "totp" | "oauth" — explicit selector modifiers like
        # ", email_field=#x, ..." can follow after the comma. Only the first token
        # is treated as the mode.
        head = str(step.get("target", "")).split(",", 1)[0].strip().lower()
        if head not in _AUTH_LOGIN_MODES:
            raise ScenarioValidationError(
                f"step[{i}] action=auth_login target must be one of "
                f"{sorted(_AUTH_LOGIN_MODES)} (={head!r})"
            )
        return
    if action == "reset_state":
        # value = "cookie" | "storage" | "indexeddb" | "all". target is ignored.
        scope = str(step.get("value", "")).strip().lower()
        if scope not in _RESET_STATE_VALID_VALUES:
            raise ScenarioValidationError(
                f"step[{i}] action=reset_state value must be one of "
                f"{sorted(_RESET_STATE_VALID_VALUES)} (={scope!r})"
            )
        return
    if action == "verify":
        condition = str(step.get("condition", "")).strip().lower()
        if condition not in _VALID_VERIFY_CONDITIONS:
            # When the LLM emits a free-form condition outside the whitelist
            # (empty / present / exists / etc.), do not reject — downgrade to
            # empty string and let the executor's default fallback ("contains
            # if value present, else visible") map it safely. Prevents
            # discarding the entire scenario.
            step["condition"] = ""


def _sanitize_scenario(scenario):
    """First-pass absorption of LLM non-determinism — drop steps with missing/invalid actions and return.

    The Planner LLM frequently drops the action key on 1 of 14 steps or mixes in typos.
    Dropping invalid steps and proceeding is more deterministic and faster than rejecting
    + retrying the whole scenario (gemma4:26b inference is ~30s+). Drop reasons are logged
    at WARNING so users can trace them.

    Empty scenarios are returned as-is — _validate_scenario rejects them.
    """
    if not isinstance(scenario, list):
        return scenario
    keep = []
    for i, st in enumerate(scenario):
        if not isinstance(st, dict):
            log.warning("[Sanitize] step[%d] is not a dict — drop: %r", i, st)
            continue
        action = st.get("action")
        if not isinstance(action, str):
            log.warning("[Sanitize] step[%d] action missing / None — drop: %r", i, st)
            continue
        normalized = action.strip().strip("`'\" ").lower()
        if normalized not in _VALID_ACTIONS:
            # Recover the case where the LLM emits meta-reasoning inside the action field.
            # e.g. "verify, target: id=status, value: ..." or "`verify`, ..."
            # If the first token is a valid action, adopt it; otherwise drop.
            head = re.split(r"[\s,;:()`'\"*]", normalized, maxsplit=1)[0]
            if head in _VALID_ACTIONS:
                log.warning(
                    "[Sanitize] step[%d] action=%r → recovered as first token %r (LLM meta-reasoning leak)",
                    i, action, head,
                )
                st = {**st, "action": head}
            else:
                log.warning("[Sanitize] step[%d] unsupported action=%r — drop", i, action)
                continue
        keep.append(st)
    if len(keep) != len(scenario):
        log.warning("[Sanitize] kept %d/%d steps", len(keep), len(scenario))
    return keep


def _validate_scenario(scenario) -> None:
    """Validate the structural integrity of a Dify-returned scenario. Raise ScenarioValidationError on failure.

    Catches the rare cases caused by LLM non-determinism early:
    - empty array / not a list
    - step element is not a dict (strings / nulls mixed in)
    - action is missing or outside the 14 standard actions
    - target is empty for actions other than navigate/wait/press
      (which guarantees a locator failure at runtime)
    - new actions violating their minimum input contract (value/condition/etc.)

    Passing this check does not catch semantically wrong scenarios (e.g. work
    unrelated to the SRS). Those are handled later by the executor-level Healer / Guard.
    """
    if not isinstance(scenario, list) or not scenario:
        raise ScenarioValidationError("scenario array is empty")
    for i, raw_step in enumerate(scenario):
        step = _check_step_shape(i, raw_step)
        _check_target_value_contract(i, step)
        _check_action_specific(i, step)
        # LLM-emitted step numbers are often non-sequential / missing (e.g. 1, 18).
        # The list order itself is the real ordering, so force-renumber to 1..N
        # for report readability.
        step["step"] = i + 1


def _prepare_scenario(
    args, config: Config, target_url: str, srs_text: str, api_docs: str
) -> list[dict]:
    """Prepare the scenario for the selected mode."""
    if args.mode == "execute":
        if not args.scenario:
            raise FileNotFoundError("execute mode requires the --scenario argument.")
        with open(args.scenario, "r", encoding="utf-8") as f:
            scenario = json.load(f)
        # Apply the same 14-DSL contract as chat/doc to scenarios coming from outside.
        # Prevents contract violations like a non-integer mock_status value in a
        # hand-written scenario.json from leaking into runtime ValueError and being
        # masked by self-healing.
        _validate_scenario(scenario)
        log.info("[Scenario] loaded %s (%d steps)", args.scenario, len(scenario))
        return scenario

    if args.mode == "convert":
        if not args.file:
            raise FileNotFoundError("convert mode requires the --file argument.")
        scenario = convert_playwright_to_dsl(args.file, config.artifacts_dir)
        # Enforce the 14-DSL contract on the convert path too — previously it was
        # missing, so corrupted DSL leaked into the executor stage as ValueError.
        # The Recording service (--convert-only) also gets immediate failure via this check.
        _validate_scenario(scenario)
        return scenario

    # chat / doc mode: call Dify
    dify = DifyClient(config)
    file_id = None

    if args.mode == "doc":
        if not args.file:
            log.warning("[Doc] no --file argument. Falling back to SRS_TEXT.")
        else:
            # Client-side file → extract text and prepend to srs_text.
            # The Dify Chatflow Planner node has context.enabled=false, so the LLM
            # cannot read content via the file-upload path. Inserting the text
            # directly is what lets gemma4:e4b see the document while generating
            # the scenario.
            try:
                doc_text = dify.extract_text_from_file(args.file)
                if doc_text:
                    structured = parse_structured_doc_steps(doc_text)
                    if structured:
                        log.info(
                            "[Doc] structured step markers detected — skipping Dify, using local parser result (%d steps)",
                            len(structured),
                        )
                        return structured
                    if srs_text:
                        srs_text = (
                            f"[Attached document content]\n{doc_text}\n\n"
                            f"[Additional requirements]\n{srs_text}"
                        )
                    else:
                        srs_text = f"[Attached document content]\n{doc_text}"
                    log.info("[Doc] extracted %d chars from document, merged into srs_text", len(doc_text))
                else:
                    log.warning("[Doc] extracted document text is empty.")
            except Exception as e:
                log.warning(
                    "[Doc] file extraction failed (%s) — falling back to upload_file", e
                )
                file_id = dify.upload_file(args.file)

    # Defense against LLM non-determinism — validate Dify response structure and
    # regenerate up to 3 times on invalid output.
    # Most common failures: (1) empty scenario array, (2) step.action outside the
    # 9 standard actions, (3) empty target on fill/click etc. Passing these to the
    # executor inevitably ends in selector failure, so block early and retry here.
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
            # First-pass absorption of LLM non-determinism — drop missing/invalid
            # action steps and validate. If at least 1 valid step remains, save
            # the retry cost and proceed.
            scenario = _sanitize_scenario(scenario)
            _validate_scenario(scenario)
            log.info(
                "[Dify] received scenario (%d steps) — attempt %d/3 succeeded",
                len(scenario), attempt,
            )
            return scenario
        except (DifyConnectionError, ScenarioValidationError) as e:
            if attempt < 3:
                backoff = 5 * attempt  # 5s, 10s, 15s
                log.warning(
                    "[Retry %d/3] scenario receive/validate failed — %s (waiting %ds before next attempt)",
                    attempt, e, backoff,
                )
                time.sleep(backoff)
            else:
                log.error("[Dify] all 3 attempts failed. Last error: %s", e)
                raise


def _copy_upload_to_artifacts(source_path: str | None, artifacts_dir: str) -> str | None:
    """Copy the user-uploaded original file (spec / Playwright recording / scenario.json)
    into the artifacts directory so the HTML report's "Attached document" section can reference it.

    Applied across the doc / convert / execute modes. chat mode has no upload → returns None.

    Args:
        source_path: path to the uploaded file saved by the Pipeline. Returns None
            (no-op) if None or the file does not exist.
        artifacts_dir: save directory; created if absent.

    Returns:
        Basename of the file copied into artifacts (e.g. ``upload.pdf``).
        None if there is no original.
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
    log.info("[Upload] copied original file into artifacts: %s", basename)
    return basename


def _generate_error_report(artifacts_dir: str, error_msg: str):
    """Generate a minimal error report on Dify connection failure."""
    os.makedirs(artifacts_dir, exist_ok=True)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Zero-Touch QA Error</title></head>
<body style="font-family: sans-serif; margin: 40px; color: #991b1b;">
  <h1>Zero-Touch QA execution failed</h1>
  <p style="background: #fee2e2; padding: 16px; border-radius: 8px;">
    <strong>Dify connection failed:</strong> {error_msg}
  </p>
  <p>Check Dify service status.</p>
</body>
</html>"""
    path = os.path.join(artifacts_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("[Error Report] wrote %s", path)


# ─────────────────────────────────────────────────────────────────────────
# auth subcommand — auth-profile catalog CLI (P2)
# ─────────────────────────────────────────────────────────────────────────
#
# Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.5
#
# Invocation format:
#   python3 -m zero_touch_qa auth seed --name <name> --seed-url <url> ...
#   python3 -m zero_touch_qa auth list [--json]
#   python3 -m zero_touch_qa auth verify --name <name> [--json] [--no-naver-probe]
#   python3 -m zero_touch_qa auth delete --name <name>


def _build_auth_parser() -> argparse.ArgumentParser:
    """auth subcommand argparse tree. seed/list/verify/delete sub-sub branches."""
    parser = argparse.ArgumentParser(
        prog="python3 -m zero_touch_qa auth",
        description="Auth Profile catalog (for E2E testing services that integrate with Naver OAuth)",
    )
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")

    # seed
    p_seed = sub.add_parser(
        "seed",
        help="Seed a new auth session (a human logs in directly + passes 2FA)",
    )
    p_seed.add_argument("--name", required=True, help="profile identifier")
    p_seed.add_argument(
        "--seed-url",
        required=True,
        help="⚠️ entry URL of the *service* under test (not the Naver login URL!)",
    )
    p_seed.add_argument(
        "--verify-service-url",
        required=True,
        help="URL of a service page only visible to logged-in users",
    )
    p_seed.add_argument(
        "--verify-service-text",
        default="",
        help="Optional: text that appears on the verify URL only when logged in (empty to check URL access only)",
    )
    p_seed.add_argument(
        "--no-naver-probe",
        action="store_true",
        help="Disable Naver-side weak probe (default: enabled)",
    )
    p_seed.add_argument(
        "--service-domain",
        default=None,
        help="auto-extracted from seed-url when not specified",
    )
    p_seed.add_argument(
        "--ttl-hint-hours", type=int, default=12,
        help="UI-displayed expiry hint (default 12)",
    )
    p_seed.add_argument("--notes", default="", help="free-form notes")
    p_seed.add_argument(
        "--timeout-sec", type=int, default=600,
        help="user-input wait timeout in seconds (default 600 = 10 min)",
    )

    # list
    p_list = sub.add_parser("list", help="list registered profiles")
    p_list.add_argument(
        "--json", dest="as_json", action="store_true",
        help="JSON output (for script integration)",
    )

    # verify
    p_verify = sub.add_parser(
        "verify",
        help="Verify a profile — service authoritative + naver weak probe (optional)",
    )
    p_verify.add_argument("--name", required=True)
    p_verify.add_argument(
        "--no-naver-probe", action="store_true",
        help="skip the naver probe (service-only verification)",
    )
    p_verify.add_argument("--timeout-sec", type=int, default=30)
    p_verify.add_argument(
        "--json", dest="as_json", action="store_true",
        help="JSON output",
    )

    # delete
    p_delete = sub.add_parser("delete", help="delete profile + storage file")
    p_delete.add_argument("--name", required=True)

    return parser


def _run_auth_cli(argv: list) -> int:
    """auth subcommand entry point. 0 on success / 1 on failure."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = _build_auth_parser()
    args = parser.parse_args(argv)

    # Import auth_profiles only at this function — importing the module pulls
    # in POSIX dependencies (fcntl, etc.), so we keep the legacy --mode path unaffected.
    from . import auth_profiles as ap

    handlers = {
        "seed": _auth_handle_seed,
        "list": _auth_handle_list,
        "verify": _auth_handle_verify,
        "delete": _auth_handle_delete,
    }
    handler = handlers.get(args.action)
    if handler is None:
        log.error("[auth] unknown action: %s", args.action)
        return 1
    try:
        return handler(args, ap)
    except KeyboardInterrupt:
        log.warning("[auth] user-interrupted")
        return 130


def _auth_handle_seed(args, ap_module) -> int:
    """auth seed handler."""
    from .auth_profiles import (
        AuthProfileError,
        NaverProbeSpec,
        VerifySpec,
    )
    verify = VerifySpec(
        service_url=args.verify_service_url,
        service_text=args.verify_service_text,
        naver_probe=None if args.no_naver_probe else NaverProbeSpec(),
    )
    print(f"# seed start — name={args.name}")
    print(f"#   seed_url    = {args.seed_url}")
    print(f"#   service     = {args.verify_service_url}")
    print("#   ⚠ A separate browser window will open. A human logs in directly and passes 2FA, then")
    print(f"#     verifies their name on the service → close the window. (timeout {args.timeout_sec}s)")
    try:
        prof = ap_module.seed_profile(
            name=args.name,
            seed_url=args.seed_url,
            verify=verify,
            service_domain=args.service_domain,
            ttl_hint_hours=args.ttl_hint_hours,
            notes=args.notes,
            timeout_sec=args.timeout_sec,
        )
    except AuthProfileError as e:
        log.error("[auth seed] failed — %s", e)
        return 1
    except Exception as e:  # noqa: BLE001
        log.exception("[auth seed] unexpected error")
        return 1
    print(f"# ✅ seed complete — name={prof.name}")
    print(f"#   storage  = {prof.storage_path}")
    print(f"#   verified = {prof.last_verified_at}")
    print(f"#   chips    = {prof.chips_supported}")
    return 0


def _auth_handle_list(args, ap_module) -> int:
    """auth list handler. With --json, prints one-line JSON."""
    profiles = ap_module.list_profiles()
    if args.as_json:
        out = [
            {
                "name": p.name,
                "service_domain": p.service_domain,
                "last_verified_at": p.last_verified_at,
                "ttl_hint_hours": p.ttl_hint_hours,
                "chips_supported": p.chips_supported,
                "session_storage_warning": p.session_storage_warning,
            }
            for p in profiles
        ]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if not profiles:
        print("# (no profiles registered)")
        return 0
    print(f"# {len(profiles)} profiles")
    print(f"{'NAME':<30} {'SERVICE':<32} {'LAST VERIFIED':<28} {'TTL':>4}")
    for p in profiles:
        last = p.last_verified_at or "-"
        print(f"{p.name:<30} {p.service_domain:<32} {last:<28} {p.ttl_hint_hours:>3}h")
    return 0


def _auth_handle_verify(args, ap_module) -> int:
    """auth verify handler. service-side authoritative + (optional) naver weak probe."""
    from .auth_profiles import AuthProfileError
    # Handle ProfileNotFoundError + other AuthProfileError together (all exit code 1).
    try:
        prof = ap_module.get_profile(args.name)
        ok, detail = ap_module.verify_profile(
            prof,
            naver_probe=not args.no_naver_probe,
            timeout_sec=args.timeout_sec,
        )
    except AuthProfileError as e:
        log.error("[auth verify] %s", e)
        return 1
    except Exception:  # noqa: BLE001
        log.exception("[auth verify] unexpected error")
        return 1
    if args.as_json:
        print(json.dumps({"ok": ok, **detail}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    status = "✓ OK" if ok else "✗ FAIL"
    print(f"# {status} — name={args.name}")
    print(f"#   service_ms     = {detail.get('service_ms')}")
    print(f"#   naver_probe_ms = {detail.get('naver_probe_ms')}")
    print(f"#   naver_ok       = {detail.get('naver_ok')}")
    if detail.get("fail_reason"):
        print(f"#   fail_reason    = {detail['fail_reason']}")
    return 0 if ok else 1


def _auth_handle_delete(args, ap_module) -> int:
    """auth delete handler. Cleans up both the catalog entry and the storage file."""
    from .auth_profiles import AuthProfileError
    # ProfileNotFoundError is a subclass of AuthProfileError, so handle them together.
    try:
        ap_module.delete_profile(args.name)
    except AuthProfileError as e:
        log.error("[auth delete] %s", e)
        return 1
    print(f"# delete complete — name={args.name}")
    return 0


if __name__ == "__main__":
    main()
