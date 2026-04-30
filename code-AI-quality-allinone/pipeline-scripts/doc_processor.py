# ==================================================================================
# File: doc_processor.py
# Version: 2.1 (Full Commented Version)
#
# [Overview]
# This script preprocesses and uploads documents in various formats
# (PDF, PPTX, DOCX, XLSX, TXT) by converting them to Markdown for ingestion
# into Dify, the RAG (Retrieval Augmented Generation) system.
#
# [Core architecture: 2-Pass Hybrid Pipeline]
# Goes through the steps below to avoid data loss for complex documents
# (PDF, PPTX).
#
# 1. Pass 1 (Structure Detection & Extraction):
#    - PyMuPDF detects the document skeleton (text, table regions, image regions).
#    - In particular, it secures the table region coordinates first and skips
#      text extraction in those regions to keep tables from being broken
#      during text extraction.
#
# 2. Pass 2 (Vision Refinement):
#    - Tables and charts/images that are hard to express as text are cropped
#      from the original.
#    - A local LLM (Ollama Llama 3.2 Vision) is asked to convert them into
#      a detailed description or Markdown table.
#
# 3. Merge (Reconstruction):
#    - The plain text (OCR) and the vision analysis output are reassembled
#      in Y-axis (reading-order) sequence to produce a single Markdown
#      document that follows the human reading order.
#
# [Run modes]
# 1. convert: read source documents and convert them to Markdown locally.
# 2. upload:  send the converted Markdown files to the Dify Knowledge Base API.
# ==================================================================================

import os
import sys
import json
import time
import shutil
import subprocess
import base64
import io
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from operator import itemgetter

# External library dependencies
import requests  # HTTP API calls
import fitz      # PyMuPDF for PDF processing

# ============================================================================
# [Config] Environment variables and fixed paths
# ============================================================================

# Directory of source documents (Jenkins volume mount path)
SOURCE_DIR = "/var/knowledges/docs/org"

# Directory where converted Markdown files are saved
RESULT_DIR = "/var/knowledges/docs/result"

# Dify API endpoint
# The Jenkins container talks to the Dify API container ("api") internally.
DIFY_API_BASE = os.getenv("DIFY_API_BASE", "http://api:5001/v1")

# Ollama Vision API config
# Jenkins runs inside a container; Ollama runs on the host machine (Mac, etc.).
# Mac Docker Desktop: host.docker.internal resolves automatically.
# Linux: docker-compose extra_hosts mapping required. Override with the
# OLLAMA_API_URL env var if the environment differs.
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://host.docker.internal:11434/api/generate")

# Vision model name to use (overridable)
# The model must be pulled on the host beforehand: `ollama pull llama3.2-vision`.
VISION_MODEL = os.getenv("VISION_MODEL", "llama3.2-vision:latest")

# Stability tuning for Dify text-document upload
# Repos with many chunks (e.g. NodeGoat) sometimes have the reverse
# proxy / API drop the connection mid-upload, so we apply a short throttle
# and retry.
DOC_UPLOAD_RETRIES = int(os.getenv("DOC_UPLOAD_RETRIES", "3"))
DOC_UPLOAD_RETRY_BACKOFF_SEC = float(os.getenv("DOC_UPLOAD_RETRY_BACKOFF_SEC", "2"))
DOC_UPLOAD_THROTTLE_MS = int(os.getenv("DOC_UPLOAD_THROTTLE_MS", "150"))

# ============================================================================
# [Utility] Common helpers
# ============================================================================

def log(msg: str) -> None:
    """
    Helper to make logs stand out in Jenkins Console Output.
    Uses flush=True for unbuffered immediate output.
    """
    print(f"[DocProcessor] {msg}", flush=True)

def safe_read_text(path: Path, max_bytes: int = 5_000_000) -> str:
    """
    Avoid encoding errors and memory issues when reading a file.

    Args:
        path: file path to read
        max_bytes: maximum read bytes (default 5MB).
                   Very large text files are inefficient during RAG chunking,
                   so we cap them.

    Returns:
        UTF-8 decoded string (empty string on error)
    """
    try:
        data = path.read_bytes()
        # Apply file size limit
        data = data[:max_bytes]
        # errors='ignore' avoids aborting on emoji or special-character corruption.
        return data.decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"[Warn] file read failed: {path.name} / {e}")
        return ""

def write_text(path: Path, content: str) -> None:
    """
    Write a string to file.
    Creates the parent directory automatically if it does not exist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

# ============================================================================
# [Core 1] Vision Analysis Logic (Ollama integration)
# ============================================================================

def analyze_image_region(image_bytes: bytes, prompt: str) -> str:
    """
    Send image data to the Ollama Vision model (Llama 3.2 Vision) for analysis.
    Used for regions where text extraction is hard, such as tables and charts.

    Args:
        prompt: instruction for the Vision model (e.g. "convert this table to markdown")

    Returns:
        Text description generated by the model (Markdown format)
    """
    try:
        # 1. Base64-encode the image bytes for API transport.
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # 2. Build the Ollama API request payload.
        payload = {
            "model": VISION_MODEL,
            "prompt": prompt,
            "stream": False,    # disable streaming, receive the full response at once
            "images": [img_b64],
            "options": {
                # Low temperature (0.1) suppresses model creativity.
                # Document conversion is about extracting factual data.
                "temperature": 0.1,
                # Image analysis can produce long text — secure the context window.
                "num_ctx": 2048
            }
        }

        # 3. API call (3 minute timeout).
        # Vision models are inference-heavy, so responses can take a while.
        r = requests.post(OLLAMA_API_URL, json=payload, timeout=180)
        r.raise_for_status()  # raise on HTTP 4xx/5xx

        # 4. Extract the result.
        data = r.json()
        return data.get("response", "").strip()

    except Exception as e:
        log(f"!! [Vision Error] analysis failed: {e}")
        # Even on failure, return an error message so the whole process keeps going.
        return "(Vision analysis failed for this region)"

# ============================================================================
# [Core 2] Hybrid PDF Converter (the main conversion engine)
# ============================================================================

def pdf_to_markdown_hybrid(pdf_path: Path) -> str:
    """
    Hybrid conversion engine combining text extraction and vision analysis.
    1. Plain text is extracted quickly via PyMuPDF.
    2. Tables and images are captured and analyzed precisely via Ollama Vision.
    3. All elements are reordered by their original Y-axis position to restore
       the reading order.
    """
    start_time = time.time()
    # Open the document with PyMuPDF.
    doc = fitz.open(str(pdf_path))
    full_doc = []  # collected results across all pages

    total_pages = len(doc)
    log(f"[Hybrid] Processing Start: {pdf_path.name} (Total Pages: {total_pages})")

    # Iterate through each page (1-indexed).
    for page_num, page in enumerate(doc, start=1):
        log(f"  Processing Page {page_num}/{total_pages}...")

        # List of elements extracted from this page.
        # Shape: {'y': y_coord, 'type': 'text'|'table'|'image', 'content': 'markdown content'}
        page_content = []

        # --------------------------------------------------------------------
        # Pass 1-1: Detect tables (Priority 1)
        # --------------------------------------------------------------------
        # Reason: tables are the most likely to lose their row/column structure
        # during text extraction. We use PyMuPDF's `find_tables` to grab the
        # table region coordinates (bbox) first.
        tables = page.find_tables()
        table_rects = [tab.bbox for tab in tables]
        log(f"    [Step 1] Detected {len(table_rects)} tables.")

        for rect in table_rects:
            # Crop the detected table region as an image.
            pix = page.get_pixmap(clip=rect)
            img_bytes = pix.tobytes("png")

            v_start = time.time()
            log(f"    -> [Vision:Table] Analyzing region at Y={rect[1]:.1f}...")

            # Ask the Vision model "look at the image and produce a markdown table".
            md_table = analyze_image_region(
                img_bytes,
                "Convert this table image into a Markdown table format. Only output the table, no description."
            )
            log(f"    <- [Vision:Table] Done ({time.time() - v_start:.2f}s)")

            # Store the result (with coordinates).
            page_content.append({
                "y": rect[1],    # top Y coordinate used for sorting
                "type": "table",
                "content": f"\n{md_table}\n"
            })

        # --------------------------------------------------------------------
        # Pass 1-2: Extract text and image blocks (Priority 2)
        # --------------------------------------------------------------------
        # get_text("dict") mode returns the page contents as block-level structures.
        # The blocks list mixes text blocks and image blocks.
        blocks = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]

        text_count = 0
        image_count = 0

        for block in blocks:
            # Read the block's coordinates (bbox).
            bbox = fitz.Rect(block["bbox"])

            # [Important: dedup filter]
            # Check whether the current block sits inside one of the previously
            # detected table regions. Any text inside a table has already been
            # turned into a table by the Vision model, so extracting it again
            # would duplicate content — skip it.
            is_inside_table = False
            for t_rect in table_rects:
                # Compute the intersection of the two regions.
                intersect = bbox.intersect(fitz.Rect(t_rect))
                # Treat the block as part of the table when ≥80% of its area overlaps.
                if intersect.get_area() > 0.8 * bbox.get_area():
                    is_inside_table = True
                    break

            if is_inside_table:
                continue

            # ----------------------------------------------------------------
            # Case A: Text block (type=0)
            # ----------------------------------------------------------------
            if block["type"] == 0:
                text = ""
                # A text block is composed of multiple lines and spans.
                for line in block["lines"]:
                    for span in line["spans"]:
                        text += span["text"] + " "
                    text += "\n"

                # Append only when we have content.
                if text.strip():
                    page_content.append({
                        "y": bbox.y0,
                        "type": "text",
                        "content": text
                    })
                    text_count += 1

            # ----------------------------------------------------------------
            # Case B: Image block (type=1)
            # ----------------------------------------------------------------
            elif block["type"] == 1:
                # [Noise filter]
                # Documents contain many small meaningless images: icons,
                # decorative lines, backgrounds. Ignore images smaller than
                # 50px in either dimension — not worth analyzing.
                width = bbox[2] - bbox[0]
                height = bbox[3] - bbox[1]
                if width < 50 or height < 50:
                    continue

                img_bytes = block["image"]

                v_start = time.time()
                log(f"    -> [Vision:Image] Analyzing region at Y={bbox.y0:.1f}...")

                # Ask the Vision model for an image description.
                desc = analyze_image_region(
                    img_bytes,
                    "Describe this image in detail. If it's a chart, summarize the data trends."
                )
                log(f"    <- [Vision:Image] Done ({time.time() - v_start:.2f}s)")

                # Insert the image as a blockquote (>) so it stands out in markdown.
                page_content.append({
                    "y": bbox.y0,
                    "type": "image",
                    "content": f"\n> **[Image Analysis]**\n> {desc}\n"
                })
                image_count += 1

        log(f"    [Step 2] Extracted {text_count} text blocks and {image_count} images (filtered).")

        # --------------------------------------------------------------------
        # Pass 2: Merge & Sort
        # --------------------------------------------------------------------
        # Sort all collected elements (text, tables, image descriptions) by
        # Y-axis (top → bottom) order. This restores the document's original
        # reading order.
        page_content.sort(key=itemgetter("y"))

        # Concatenate the contents of the sorted elements.
        page_md = f"## Page {page_num}\n\n"
        page_md += "\n".join([item["content"] for item in page_content])
        full_doc.append(page_md)
        log(f"    [Step 3] Page {page_num} reconstruction complete.")

    doc.close()
    log(f"[Hybrid] Finished: {pdf_path.name} (Elapsed: {time.time() - start_time:.2f}s)")

    # Join page contents with horizontal rules.
    title = pdf_path.name
    body = "\n\n---\n\n".join(full_doc)
    return f"# {title}\n\n{body}\n"

# ============================================================================
# [Utility] Generic document format converters (legacy support)
# ============================================================================

def docx_to_markdown(docx_path: Path) -> str:
    """Convert a DOCX file to Markdown by extracting text only."""
    try:
        from docx import Document
        d = Document(str(docx_path))
        # Pull every non-empty paragraph.
        lines = [p.text.strip() for p in d.paragraphs if p.text.strip()]
        if not lines: return ""
        return f"# {docx_path.name}\n\n" + "\n\n".join(lines) + "\n"
    except Exception as e:
        log(f"[Warn] DOCX conversion failed: {e}")
        return ""

def excel_to_markdown(xls_path: Path) -> str:
    """Convert each Excel sheet to a Markdown table."""
    try:
        import pandas as pd
        # Read every sheet.
        sheets = pd.read_excel(str(xls_path), sheet_name=None)
        if not sheets: return ""

        out = [f"# {xls_path.name}\n"]
        for sheet_name, df in sheets.items():
            out.append(f"## Sheet: {sheet_name}\n")
            # Use pandas to_markdown.
            out.append(df.to_markdown(index=False))
            out.append("\n")
        return "\n".join(out).strip() + "\n"
    except Exception as e:
        log(f"[Warn] Excel conversion failed: {e}")
        return ""

def pptx_to_pdf(pptx_path: Path, out_dir: Path) -> Optional[Path]:
    """
    Convert a PPTX file to PDF.

    Why:
    Pulling text directly from a PPTX library produces a jumbled layout order
    and tends to miss diagram/chart data. Using LibreOffice to "print" to PDF
    locks the layout in place, putting the file in the best state for the
    Hybrid Pipeline downstream.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # Expected path of the converted PDF.
    expected_pdf = out_dir / (pptx_path.stem + ".pdf")

    # LibreOffice headless conversion command.
    cmd = [
        "soffice", "--headless", "--nologo", "--nolockcheck", "--norestore",
        "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx_path),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        log(f"[Warn] PPTX -> PDF conversion failed (check LibreOffice): {e}")
        return None

    if expected_pdf.exists():
        return expected_pdf
    return None

# ============================================================================
# [Run mode 1] Convert
# ============================================================================

def convert_one(src_path: Path) -> None:
    """Inspect a single file's extension and dispatch to the right converter."""
    start_time = time.time()
    ext = src_path.suffix.lower()

    # 1. PDF (Hybrid Vision applied)
    if ext == ".pdf":
        log(f"[Target] PDF Detected: {src_path.name}")
        md = pdf_to_markdown_hybrid(src_path)
        if md:
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Success] {src_path.name} -> {out.name} ({time.time() - start_time:.2f}s)")
        return

    # 2. PPTX (PDF conversion → Hybrid Vision)
    # Visual elements matter for PPTX, so convert to PDF first and then run
    # the Hybrid engine.
    if ext == ".pptx":
        log(f"[Target] PPTX Detected: {src_path.name} -> Converting to PDF first...")
        pdf = pptx_to_pdf(src_path, Path(RESULT_DIR))
        if pdf:
            # Run Hybrid logic on the converted PDF.
            md = pdf_to_markdown_hybrid(pdf)
            if md:
                out = Path(RESULT_DIR) / f"{src_path.name}.md"
                write_text(out, md)
                log(f"[Success] {src_path.name} -> {out.name} ({time.time() - start_time:.2f}s)")
        return

    # 3. Other text formats (simple extraction)
    if ext == ".docx":
        md = docx_to_markdown(src_path)
        if md:
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Success] {src_path.name} -> {out.name} ({time.time() - start_time:.2f}s)")
        return

    if ext in [".xlsx", ".xls"]:
        md = excel_to_markdown(src_path)
        if md:
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Success] {src_path.name} -> {out.name} ({time.time() - start_time:.2f}s)")
        return

    if ext == ".txt":
        text = safe_read_text(src_path)
        if text.strip():
            md = f"# {src_path.name}\n\n{text.strip()}\n"
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Success] {src_path.name} -> {out.name} ({time.time() - start_time:.2f}s)")
        return

    # 4. Already markdown (just copy/verify)
    if ext == ".md":
        text = safe_read_text(src_path)
        if text.strip():
            out = Path(RESULT_DIR) / src_path.name
            write_text(out, text)
            log(f"[Success] {src_path.name} -> {out.name} ({time.time() - start_time:.2f}s)")
        return

def convert_all() -> None:
    """Main loop that walks every file under SOURCE_DIR and converts it."""
    log("=== [Hybrid Doc Processor] Convert Start ===")

    # Create the result directory if missing.
    os.makedirs(RESULT_DIR, exist_ok=True)
    src_root = Path(SOURCE_DIR)

    # Recursive file walk.
    for root, _, files in os.walk(src_root):
        for name in files:
            p = Path(root) / name
            # Filter on supported extensions (.md included).
            if p.suffix.lower() in [".pdf", ".docx", ".xlsx", ".xls", ".txt", ".pptx", ".md"]:
                convert_one(p)

    log("=== [Hybrid Doc Processor] Convert Done ===")

# ============================================================================
# [Run mode 2] Upload
# ============================================================================

def dify_headers(api_key: str) -> dict:
    """Build the auth headers for Dify API calls."""
    return {"Authorization": f"Bearer {api_key}"}

def get_dataset_doc_form(api_key: str, dataset_id: str) -> str:
    """
    Fetch the Dify dataset configuration (document form).
    Prevents the user from putting Q&A-style data into a 'document' dataset
    by accident.
    """
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}"
    r = requests.get(url, headers=dify_headers(api_key), timeout=60)
    if r.status_code == 200:
        return str(r.json().get("doc_form", ""))
    else:
        log(f"[Warn] could not fetch dataset info. (Status: {r.status_code})")
        return ""

def ensure_doc_form_matches(api_key: str, dataset_id: str, expected_doc_form: str) -> None:
    """
    Verify the dataset's actual configuration matches the format we are uploading.
    If we cannot read the configuration we skip validation and continue.
    """
    try:
        actual = get_dataset_doc_form(api_key, dataset_id)
        if actual and actual != expected_doc_form:
            log(f"[Warn] dataset config mismatch (DB={actual} / requested={expected_doc_form}). proceeding anyway.")
    except Exception as e:
        log(f"[Warn] error during pre-check: {e}. proceeding without validation.")

def _get_dataset_indexing_technique(api_key: str, dataset_id: str) -> str:
    """Read indexing_technique from the dataset. Either high_quality or economy. Falls back to economy."""
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}"
    try:
        r = requests.get(url, headers=dify_headers(api_key), timeout=30)
        if r.status_code == 200:
            val = r.json().get("indexing_technique", "")
            if val in ("high_quality", "economy"):
                return val
    except Exception:
        pass
    return "economy"


def upload_text_document(
    api_key: str,
    dataset_id: str,
    name: str,
    text: str,
    doc_form: str,
    doc_language: str,
    indexing_technique: str = None,
) -> Tuple[bool, str]:
    """Call Dify's 'create document by text' API.

    When indexing_technique is unspecified, read it from the dataset config
    (supports high_quality / economy).
    """
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}/document/create-by-text"

    if not indexing_technique:
        indexing_technique = _get_dataset_indexing_technique(api_key, dataset_id)

    payload = {
        "name": name,
        "text": text,
        "indexing_technique": indexing_technique,
        "doc_form": doc_form,
        "doc_language": doc_language,
        "process_rule": {
            "mode": "automatic",
            "rules": {"remove_extra_spaces": True},
            "remove_urls_emails": False,
        },
    }

    headers = {**dify_headers(api_key), "Content-Type": "application/json"}
    last_err = None

    for attempt in range(DOC_UPLOAD_RETRIES + 1):
        try:
            r = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=300,
            )
            if r.status_code >= 500:
                last_err = f"HTTP {r.status_code} - {r.text[:200]}"
            elif r.status_code >= 400:
                return False, f"HTTP {r.status_code} - {r.text[:200]}"
            else:
                try:
                    res_data = r.json()
                    doc_id = (
                        res_data.get("document", {}).get("id")
                        or res_data.get("id", "Unknown ID")
                    )
                    if DOC_UPLOAD_THROTTLE_MS > 0:
                        time.sleep(DOC_UPLOAD_THROTTLE_MS / 1000.0)
                    return True, f"OK (ID: {doc_id})"
                except Exception:
                    if DOC_UPLOAD_THROTTLE_MS > 0:
                        time.sleep(DOC_UPLOAD_THROTTLE_MS / 1000.0)
                    return True, "OK"
        except requests.RequestException as e:
            last_err = str(e)

        if attempt < DOC_UPLOAD_RETRIES:
            wait_s = DOC_UPLOAD_RETRY_BACKOFF_SEC * (attempt + 1)
            log(
                f"[Upload:RETRY] {name} | attempt {attempt + 1}/{DOC_UPLOAD_RETRIES} "
                f"| reason: {last_err} | wait={wait_s:.1f}s"
            )
            time.sleep(wait_s)

    return False, f"upload failed after retries: {last_err}"


# Fix B — Dify's default automatic_mode segmentation (~1024 tokens, ≈ 3-4KB)
# splits long chunks across multiple segments, with the footer landing on a
# single segment while the others end up code-only. context_filter's
# parse_footer then cannot extract path/symbol and renders them as `?::?`.
# We cap the code body to keep it on one segment.
# head 70% + tail 30% strategy — preserves the signature/early flow plus the
# return/late flow so most analysis context is retained. A truncation marker
# is inserted in the middle.
MAX_CODE_CHARS_PER_CHUNK = int(os.environ.get("DOC_PROCESSOR_MAX_CODE_CHARS", "2200"))


def _truncate_code_for_single_segment(code: str, cap: int = MAX_CODE_CHARS_PER_CHUNK) -> str:
    if not code or len(code) <= cap:
        return code
    head_budget = int(cap * 0.7)
    tail_budget = cap - head_budget - 40  # leave room for the truncation marker line
    if tail_budget < 0:
        tail_budget = 0
    trimmed_middle = len(code) - head_budget - tail_budget
    marker = f"\n... [middle {trimmed_middle} chars truncated for single-segment upload] ...\n"
    return code[:head_budget] + marker + code[-tail_budget:]


def _chunk_to_document(chunk: dict) -> Tuple[str, str]:
    """JSONL chunk → (document_name, document_text).

    document_name: "<path>::<symbol>" (groups well by file in the Dify UI)
    document_text: chunk's code body + metadata footer (broadens the
    BM25/embedding match surface during retrieval)
    """
    path = chunk.get("path", "unknown")
    symbol = chunk.get("symbol", "?")
    kind = chunk.get("kind", "")
    lang = chunk.get("lang", "")
    lines = chunk.get("lines", "")
    commit_sha = chunk.get("commit_sha", "")
    callers = chunk.get("callers") or []
    callees = chunk.get("callees") or []
    test_paths = chunk.get("test_paths") or []
    test_for = chunk.get("test_for") or ""
    is_test = bool(chunk.get("is_test"))
    doc = (chunk.get("doc") or "").strip()

    name = f"{path}::{symbol}"
    # Handle Dify document name length limit (~100 chars).
    if len(name) > 120:
        name = name[:117] + "..."

    # Append metadata to the body footer — broadens the retrieval match surface.
    # Dify BM25 search tokenizes metadata, so is_test / callers / test_paths
    # match the structured queries produced by build_kb_query (e.g.
    # "callers: loginUser").
    meta_lines = [
        "",
        "---",
        f"path: {path}",
        f"symbol: {symbol}",
        f"kind: {kind}",
        f"lang: {lang}",
        f"lines: {lines}",
    ]
    if commit_sha:
        meta_lines.append(f"commit_sha: {commit_sha[:12]}")
    if doc:
        # P2 K-4 — leading docstring/comment. Exposes natural-language intent
        # to both BM25 and dense embeddings, complementing the weak semantic
        # match of code identifiers alone.
        meta_lines.append(f"doc: {doc}")
    if is_test:
        meta_lines.append("is_test: true")
        # D3 — inject natural-language synonyms into the test-chunk footer.
        # The vocabulary distance between cypress e2e (`cy.get`) and DAO
        # (`parseInt`) makes the tests bucket fall to 0% (observed). One
        # natural-language token line lets BM25 and dense both match the
        # caller-side intent words ("test", "verification", "spec", ...).
        meta_lines.append("tags: test verification scenario spec assertion test e2e cypress unit integration")
    if test_for:
        meta_lines.append(f"test_for: {test_for}")
    if callees:
        meta_lines.append(f"callees: {', '.join(callees[:20])}")
    if callers:
        meta_lines.append(f"callers: {', '.join(callers[:20])}")
    if test_paths:
        meta_lines.append(f"test_paths: {', '.join(test_paths[:10])}")
    # D — decorator info. Exposes security/auth/route intent on the chunk match surface.
    decorators = chunk.get("decorators") or []
    if decorators:
        meta_lines.append(f"decorators: {' '.join(decorators[:5])}")
    # C — endpoint (HTTP route). Extracted from decorator or imperative registration.
    endpoint = (chunk.get("endpoint") or "").strip()
    if endpoint:
        meta_lines.append(f"endpoint: {endpoint}")
    # H — structured docstring. Tokenizing params/returns/throws lets both
    # dense and BM25 match. Caller-side queries like "what parameters does
    # this function take" gain a search signal.
    doc_params = chunk.get("doc_params") or []
    if doc_params:
        # tokenize only the name from [(type, name, desc), ...]
        param_names = [p[1] for p in doc_params if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1]]
        if param_names:
            meta_lines.append(f"params: {' '.join(param_names[:10])}")
    doc_returns = chunk.get("doc_returns")
    if doc_returns and isinstance(doc_returns, (list, tuple)) and len(doc_returns) >= 2:
        rt_type, rt_desc = doc_returns[0], doc_returns[1]
        if rt_type or rt_desc:
            ret_str = (rt_type + " " + rt_desc).strip()
            if ret_str:
                meta_lines.append(f"returns: {ret_str[:80]}")
    doc_throws = chunk.get("doc_throws") or []
    if doc_throws:
        thr_names = []
        for t in doc_throws:
            if isinstance(t, (list, tuple)):
                thr_names.append((t[0] or t[1] or "").strip())
        thr_names = [t for t in thr_names if t]
        if thr_names:
            meta_lines.append(f"throws: {' '.join(thr_names[:5])}")
    # D1 — expose _context_summary (the LLM-generated chunk summary produced
    # by enricher) in the footer. For cases where identifier matching is weak
    # (cypress / e2e, etc.), this expands the dense embedding's semantic
    # match surface — caller-side dense queries like "what is this function's
    # behavior" can match it.
    summary = (chunk.get("_context_summary") or "").strip()
    if summary:
        # cap at 240 chars to avoid bloating the footer
        meta_lines.append(f"summary: {summary[:240]}")
    # A1 (lite) — for e2e/cypress chunks, extract describe(...) / it(...)
    # natural-language descriptions from the body and surface them in the
    # footer. Uses a regex instead of tree-sitter parsing.
    if is_test and lang in ("javascript", "typescript", "tsx"):
        import re as _re
        descs = []
        body = chunk.get("code") or ""
        # describe("...", ...) / it("...", ...) / context("...", ...) patterns
        for m in _re.finditer(
                r"\b(?:describe|it|context|test)\s*\(\s*['\"]([^'\"]{4,140})['\"]",
                body):
            descs.append(m.group(1).strip())
            if len(descs) >= 3:
                break
        if descs:
            meta_lines.append(f"test_descriptions: {' / '.join(descs)}")

    # Fix B — apply the code body cap, then add the footer
    code_body = _truncate_code_for_single_segment(chunk.get("code", ""))
    text = code_body + "\n" + "\n".join(meta_lines)
    return name, text


# ─ Fix A — Dataset purge helper ──────────────────────────────────────────
# When 02 pre-training runs in --mode full, leftover chunks from previous
# projects/runs accumulate in the same dataset; retrieval then mixes
# unrelated project chunks and RAG quality plummets (observed: nodegoat +
# ttc-sample-app mixed → citation 2.1%). Delete every existing document
# before upload to guarantee project-level isolation.
def purge_dataset_documents(api_key: str, dataset_id: str) -> int:
    """Delete every document in the dataset. Returns the number deleted."""
    log("[Purge] start deleting all existing dataset documents")
    import urllib.request
    import urllib.error
    headers = dify_headers(api_key)
    deleted = 0
    # pagination — list and delete up to 100 at a time
    while True:
        list_url = f"{DIFY_API_BASE}/datasets/{dataset_id}/documents?page=1&limit=100"
        req = urllib.request.Request(list_url, headers=headers, method="GET")
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            log(f"[Purge:WARN] document list HTTP {e.code}")
            break
        except Exception as e:
            log(f"[Purge:WARN] document list failed: {e}")
            break
        items = data.get("data") or []
        if not items:
            break
        for doc in items:
            doc_id = doc.get("id")
            if not doc_id:
                continue
            del_url = f"{DIFY_API_BASE}/datasets/{dataset_id}/documents/{doc_id}"
            del_req = urllib.request.Request(del_url, headers=headers, method="DELETE")
            try:
                urllib.request.urlopen(del_req, timeout=30).read()
                deleted += 1
                if deleted % 20 == 0:
                    log(f"[Purge] {deleted} deleted...")
            except urllib.error.HTTPError as e:
                log(f"[Purge:WARN] {doc.get('name','?')} delete failed HTTP {e.code}")
            except Exception as e:
                log(f"[Purge:WARN] {doc.get('name','?')} delete failed: {e}")
        # After deleting a page, list again from page=1 — once deletes go through, the next 100 surface
        if len(items) < 100:
            break
    log(f"[Purge] done — deleted {deleted} total")
    return deleted


def upload_jsonl_chunks(
    api_key: str,
    dataset_id: str,
    doc_form: str,
    doc_language: str,
    indexing_technique: str,
) -> Tuple[int, int]:
    """For each line of every *.jsonl file under RESULT_DIR, upload one Dify document.

    Returns: (success_count, fail_count)
    """
    result_root = Path(RESULT_DIR)
    jsonl_files = sorted(result_root.glob("*.jsonl"))
    if not jsonl_files:
        return 0, 0

    import json as _json
    success = 0
    fail = 0
    for jp in jsonl_files:
        try:
            lines = jp.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            log(f"[JSONL:READ-FAIL] {jp.name}: {e}")
            continue
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                chunk = _json.loads(ln)
            except Exception as e:
                log(f"[JSONL:PARSE-FAIL] {jp.name}: {e}")
                fail += 1
                continue

            name, text = _chunk_to_document(chunk)
            ok, detail = upload_text_document(
                api_key, dataset_id, name, text, doc_form, doc_language, indexing_technique
            )
            if ok:
                success += 1
                # Avoid log flood — only print every 10th
                if success % 10 == 1 or success <= 5:
                    log(f"[Upload:SUCCESS] {name} | {detail}")
            else:
                fail += 1
                log(f"[Upload:FAIL] {name} | {detail}")
    return success, fail


def _write_kb_manifest(dataset_id: str, doc_count: int, path: str = "/data/kb_manifest.json") -> None:
    """Phase 1.5 — manifest used to assess KB freshness.

    Stage 0 of P2/P3 reads the commit_sha from this file and compares it
    against the currently requested SHA. All fields are read from env vars
    (set by the Jenkinsfile):
      KB_MANIFEST_REPO_URL, KB_MANIFEST_BRANCH, KB_MANIFEST_COMMIT_SHA, KB_MANIFEST_ANALYSIS_MODE
    """
    try:
        payload = {
            "repo_url":      os.getenv("KB_MANIFEST_REPO_URL", ""),
            "branch":        os.getenv("KB_MANIFEST_BRANCH", ""),
            "commit_sha":    os.getenv("KB_MANIFEST_COMMIT_SHA", ""),
            "analysis_mode": os.getenv("KB_MANIFEST_ANALYSIS_MODE", "full"),
            "uploaded_at":   int(time.time()),
            "document_count": int(doc_count),
            "dataset_id":    dataset_id,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log(f"[KB-Manifest] written: {path} commit={payload['commit_sha'][:12]} docs={doc_count}")
    except Exception as e:
        log(f"[KB-Manifest:WARN] write failed: {e}")


def upload_all(api_key: str, dataset_id: str, doc_form: str, doc_language: str,
               purge_first: bool = False) -> int:
    """Upload *.jsonl (AST chunks) under RESULT_DIR plus fallback *.md (legacy) to Dify.

    When purge_first=True, delete every existing document in the dataset
    before uploading — used in 02 --mode full to keep chunks of different
    projects from getting mixed (Fix A).
    """
    log("=== [Hybrid Doc Processor] Upload Start ===")
    ensure_doc_form_matches(api_key, dataset_id, doc_form)

    if purge_first:
        purge_dataset_documents(api_key, dataset_id)

    result_root = Path(RESULT_DIR)
    if not result_root.exists():
        log("[FAIL] no conversion result directory. run convert/repo_context_builder first.")
        return 1

    indexing_technique = _get_dataset_indexing_technique(api_key, dataset_id)
    log(f"Dataset indexing_technique: {indexing_technique}")

    # 1) Prefer JSONL (AST chunk) mode — multiple documents per file produced by repo_context_builder
    jsonl_success, jsonl_fail = upload_jsonl_chunks(
        api_key, dataset_id, doc_form, doc_language, indexing_technique
    )

    # 2) Fallback: legacy *.md upload (only when no JSONL is present)
    md_success = 0
    md_fail = 0
    if jsonl_success == 0 and jsonl_fail == 0:
        log("[Upload] no JSONL — falling back to legacy *.md upload")
        for p in sorted(result_root.glob("*.md")):
            text = safe_read_text(p)
            if not text:
                continue
            log(f"Attempting upload: {p.name} (Dataset: {dataset_id})")
            ok, detail = upload_text_document(
                api_key, dataset_id, p.name, text, doc_form, doc_language, indexing_technique
            )
            if ok:
                log(f"[Upload:SUCCESS] {p.name} | {detail}")
                md_success += 1
            else:
                log(f"[Upload:FAIL] {p.name} | Reason: {detail}")
                md_fail += 1

    total_success = jsonl_success + md_success
    total_fail = jsonl_fail + md_fail
    log(
        f"=== [Hybrid Doc Processor] Upload Summary: "
        f"Success={total_success} (jsonl={jsonl_success}, md={md_success}), "
        f"Fail={total_fail} ==="
    )

    # Phase 1.5: write the kb_manifest after a successful upload (basis for
    # the P2/P3 freshness assert). Even partial failures count, so use
    # total_success > 0 as the trigger.
    if total_success > 0:
        _write_kb_manifest(dataset_id, total_success)

    log("=== [Hybrid Doc Processor] Upload Done ===")
    return total_fail

# ============================================================================
# [Main] Program entry point (CLI parser)
# ============================================================================

def main() -> None:
    """
    Parse CLI args and run convert or upload mode.
    Usage:
      1. Convert: python3 doc_processor.py convert
      2. Upload:  python3 doc_processor.py upload <API_KEY> <DATASET_ID> <FORM> <LANG>
    """
    if len(sys.argv) < 2:
        raise SystemExit("usage: doc_processor.py [convert|upload] ...")

    cmd = sys.argv[1].strip().lower()

    # 1. Convert mode
    if cmd == "convert":
        convert_all()
        return

    # 2. Upload mode
    if cmd == "upload":
        # Requires at least 2 args (API_KEY, DATASET_ID). Supports the --purge flag.
        # Usage:
        #   doc_processor.py upload <API_KEY> <DATASET_ID> [doc_form] [doc_language] [--purge]
        argv = sys.argv[2:]
        purge_first = False
        if "--purge" in argv:
            purge_first = True
            argv = [a for a in argv if a != "--purge"]
        if len(argv) < 2:
            raise SystemExit("usage: doc_processor.py upload <API_KEY> <DATASET_ID> [doc_form] [doc_language] [--purge]")

        api_key = argv[0]
        dataset_id = argv[1]
        # When omitted, fall back to defaults (preserves backward compatibility).
        doc_form = argv[2] if len(argv) > 2 else "text_model"
        doc_language = argv[3] if len(argv) > 3 else "Korean"

        fail_count = upload_all(api_key, dataset_id, doc_form, doc_language, purge_first=purge_first)
        if fail_count > 0:
            sys.exit(1) # any failure → return error code, fails the Jenkins build
        return

if __name__ == "__main__":
    main()
