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
    """Raised on Dify API communication failure."""


class DifyClient:
    """
    Communication layer for the Dify Chatflow API.
    - /v1/files/upload : upload documents in Doc mode
    - /v1/chat-messages : scenario generation and healing requests (blocking)
    """

    # HTTP status codes to retry on transient errors
    _RETRYABLE_STATUS_CODES = {502, 503, 504}

    def __init__(self, config: Config):
        self.base_url = config.dify_base_url
        self.headers = {"Authorization": f"Bearer {config.dify_api_key}"}
        self.heal_timeout_sec = getattr(config, "heal_timeout_sec", 60)
        self.scenario_timeout_sec = getattr(config, "scenario_timeout_sec", 300)
        # Path for dumping the raw response on parse failure (post-mortem diagnosis)
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
        """Send an HTTP request, retrying with exponential backoff on transient errors.

        Retried on:
            - ``requests.ConnectionError`` (connection refused, DNS failures, etc.)
            - ``requests.Timeout`` (read/connect timeouts)
            - HTTP 502, 503, 504 (upstream transient errors)

        4xx client errors are returned immediately; the caller handles them.

        Args:
            method: HTTP method (e.g. ``"POST"``).
            url: request URL.
            max_retries: max retry count, not counting the first attempt.
            backoff_base: wait time for the first retry (seconds). Doubles thereafter.
            timeout: request timeout (seconds).
            **kwargs: extra arguments passed to ``requests.request()``.

        Returns:
            The successful ``requests.Response``.

        Raises:
            requests.RequestException: when all retries are exhausted.
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
                    "[Retry] %s %s — retry %d/%d (after %.0fs). cause: %s",
                    method, url, attempt + 1, max_retries, wait, last_exc,
                )
                time.sleep(wait)

        raise last_exc  # type: ignore[misc]

    # ── Doc mode: upload document file ──
    def upload_file(self, file_path: str) -> str:
        """Upload a document to the Dify Files API and return the upload_file_id.

        Args:
            file_path: path to the document (PDF, etc.) to upload.

        Returns:
            The file ID string assigned by Dify.

        Raises:
            DifyConnectionError: on HTTP error or network failure.
        """
        log.info("[Doc] uploading document... (%s)", file_path)
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
                timeout=60,
            )
            res.raise_for_status()
        except requests.RequestException as e:
            raise DifyConnectionError(f"file upload failed: {e}") from e

        file_id = res.json().get("id")
        log.info("[Doc] document upload done (ID: %s)", file_id)
        return file_id

    # ── Doc mode: extract file as text for LLM input ──
    def extract_text_from_file(self, file_path: str) -> str:
        """Convert the uploaded file into **plain/markdown text** that the LLM can read directly.

        The Dify Chatflow Planner node is wired with ``context.enabled: false``,
        so the LLM does not receive the uploaded file's content as input. This
        function extracts the text on the client side and merges it into
        ``srs_text`` — the LLM gets to see the document content without
        modifying the Chatflow structure.

        File type is **detected via magic bytes** — even when the Jenkins
        Pipeline always saves DOC_FILE as ``upload.pdf``, content that is
        markdown / plain text is handled accordingly:

        - PDF (starts with ``%PDF-``): extract page-by-page text via ``pymupdf``
          and join with ``## Page N`` separators (markdown style)
        - Otherwise: read directly as UTF-8 (``errors="replace"`` — tolerant
          of non-conforming bytes)

        When the cap (``DIFY_DOC_MAX_CHARS`` env, default 12000 chars) is
        exceeded, keep only the prefix + ``[... truncated at N chars ...]``
        marker. Safe within the token budget under the
        ``OLLAMA_CONTEXT_SIZE=16384`` assumption (~12k chars → ~3k tokens).

        Args:
            file_path: path to the file to extract (typically the
                ``$AGENT_HOME/upload.pdf`` saved by the Pipeline).

        Returns:
            Extracted text (UTF-8 string). Empty string if the file is empty.

        Raises:
            FileNotFoundError: when the file does not exist.
            ImportError: when the file is a PDF but ``pymupdf`` is not installed.
        """
        max_chars = int(os.getenv("DIFY_DOC_MAX_CHARS", "12000"))
        with open(file_path, "rb") as f:
            head = f.read(8)
        if head.startswith(b"%PDF-"):
            import pymupdf  # lazy import — no dependency needed for non-PDF inputs
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

        # If structured doc step markers are present, preserve them before truncation.
        # When the PDF body is long enough to cut the trailing marker block, the local
        # deterministic parser in doc mode breaks and we fall back to the non-deterministic LLM path.
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
        log.info("[Doc] document text extracted: %d chars (%s)", len(text), os.path.basename(file_path))
        return text

    # ── Scenario generation (chat / doc mode) ──
    def generate_scenario(
        self,
        run_mode: str,
        srs_text: str,
        target_url: str,
        api_docs: str = "",
        file_id: str | None = None,
        enable_grounding: bool = False,
    ) -> list[dict]:
        """Ask the Dify Chatflow to generate a scenario; return the DSL step array.

        Args:
            run_mode: execution mode (``"chat"`` or ``"doc"``).
            srs_text: natural-language requirements text.
            target_url: URL under test.
            api_docs: summary text of API endpoints, used as a hint for network mocking.
            file_id: file ID returned by ``upload_file()`` in Doc mode. None if absent.
            enable_grounding: when True, prepend the DOM inventory of target_url
                in front of srs_text (Phase 1 T1.5). The default can be toggled
                via env ``ENABLE_DOM_GROUNDING=1``.

        Returns:
            List of DSL step dicts.

        Raises:
            DifyConnectionError: on API failure or JSON parse failure.
        """
        # Phase 1 grounding: prepend the actual DOM inventory of target_url.
        # Graceful degradation on extraction failure — keep the existing path.
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
            "query": "Please run the scenario.",
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

        # The Planner call can exceed the 120s default timeout on slow models like e4b.
        # Use scenario_timeout_sec (default 300s) + 1 retry (for network blips).
        # Slow-model latency does not improve on retry, so cap at max_retries=1.
        answer = self._call(
            payload,
            timeout=self.scenario_timeout_sec,
            max_retries=1,
            call_kind="planner",
            extra_metric=grounding_meta,
        )
        log.info("Dify response length: %d chars, contains <think>: %s", len(answer), "<think>" in answer)
        scenario = extract_json_safely(answer)
        if not scenario or not isinstance(scenario, list):
            # On failure, dump the raw response to artifacts (post-mortem diagnosis)
            dump_path = self._dump_raw_response(answer)
            import re
            cleaned = re.sub(r"<think>.*?</think>", "[THINK_BLOCK_REMOVED]", answer, flags=re.S)
            cleaned = re.sub(r"<think>.*", "[UNCLOSED_THINK_REMOVED]", cleaned, flags=re.S)
            dump_msg = f"\n  raw response dump: {dump_path}" if dump_path else ""
            raise DifyConnectionError(
                f"scenario parse failed.\n"
                f"  response length: {len(answer)} chars\n"
                f"  content after stripping <think> blocks (first 500 chars):\n{cleaned[:500]}"
                + dump_msg
            )
        return scenario

    def _dump_raw_response(self, answer: str) -> str | None:
        """Save the raw Dify response that failed to parse, with a timestamp, into the artifacts directory."""
        if not self.artifacts_dir:
            return None
        try:
            os.makedirs(self.artifacts_dir, exist_ok=True)
            fname = f"dify-raw-response-{time.strftime('%Y%m%dT%H%M%S')}.txt"
            path = os.path.join(self.artifacts_dir, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(answer)
            log.warning("[Dify] dumped raw response: %s (%d chars)", path, len(answer))
            return path
        except OSError as e:
            log.warning("[Dify] failed to dump raw response: %s", e)
            return None

    # ── Healing request (heal mode) ──
    def request_healing(
        self,
        error_msg: str,
        dom_snapshot: str,
        failed_step: dict,
        strategy_trace: list[dict] | None = None,
    ) -> dict | None:
        """Ask the LLM to heal a failed step; return new target info.

        Args:
            error_msg: error message describing the failure.
            dom_snapshot: HTML DOM of the current page (truncated length).
            failed_step: the failed DSL step dict.
            strategy_trace: list of multi-strategy attempts the executor made.
                Each item is ``{"strategy": <name>, "error": <msg or "ok">}``.
                Lets the healer learn things like "swapping selectors only would
                hit the same timeout".

        Returns:
            Dict containing new target/value/condition. ``None`` on parse failure.
        """
        # B: inject strategy_trace into the chatflow inputs. The healer node in the
        # chatflow yaml consumes it via the ``{{strategy_trace}}`` placeholder.
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
            "query": "Please run the scenario.",
            "response_mode": "blocking",
            "user": "mac-agent",
        }
        # On heal calls, user wait time is cost. If the model is slow, retrying
        # only multiplies the wait, so use max_retries=0 + a short timeout.
        answer = self._call(
            payload,
            timeout=self.heal_timeout_sec,
            max_retries=0,
            call_kind="healer",
        )
        return extract_json_safely(answer)

    # ── Internal: Chatflow API call ──
    def _call(
        self,
        payload: dict,
        *,
        timeout: int = 120,
        max_retries: int = 3,
        call_kind: str = "unknown",
        extra_metric: dict | None = None,
    ) -> str:
        """Send a blocking request to Dify /chat-messages and return the answer.

        Args:
            payload: request body.
            timeout: per-request timeout (seconds).
            max_retries: retry count. 0 for heal calls (slow models are not transient errors).
            call_kind: metric tag. ``planner`` or ``healer``.

        Raises:
            DifyConnectionError: on HTTP error, timeout, or network failure.
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
            raise DifyConnectionError(f"Dify API communication failed: {e}") from e
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
            log.warning("[Metrics] failed to record LLM call metric: %s", e)

    # ── DOM Grounding (Phase 1 T1.5) ──
    def _prepend_dom_inventory(
        self, srs_text: str, target_url: str,
    ) -> tuple[str, dict]:
        """Phase 1 grounding: prepend the inventory of target_url before srs_text.

        Graceful degradation on failure — return the original srs_text and put the reason in meta.
        """
        try:
            from .grounding import fetch_inventory, serialize_block
            from .grounding.pruner import prune
            from .grounding.budget import fit_to_budget, estimate_tokens, DEFAULT_TOKEN_BUDGET
        except ImportError as e:
            log.warning("[grounding] module import failed: %s", e)
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
            "[grounding] %s inventory prepended (elements=%d, tokens=%d, truncated=%s)",
            target_url, len(inv.elements), tokens, inv.truncated,
        )
        return merged, {
            "used": True,
            "target_url": target_url,
            "grounding_inventory_tokens": tokens,
            "grounding_element_count": len(inv.elements),
            "grounding_truncated": inv.truncated,
        }
