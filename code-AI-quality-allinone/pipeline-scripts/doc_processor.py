# ==================================================================================
# 파일명: doc_processor.py
# 버전: 2.1 (Full Commented Version)
#
# [시스템 개요]
# 이 스크립트는 다양한 포맷의 문서(PDF, PPTX, DOCX, XLSX, TXT)를 
# RAG(검색 증강 생성) 시스템인 Dify에 적재하기 위해 Markdown 포맷으로 변환하는
# 전처리(Preprocessing) 및 업로드 자동화 도구입니다.
#
# [핵심 아키텍처: 2-Pass Hybrid Pipeline]
# 복잡한 문서(PDF, PPTX)의 데이터 유실을 막기 위해 아래 과정을 거칩니다.
#
# 1. Pass 1 (Structure Detection & Extraction):
#    - PyMuPDF를 사용하여 문서의 뼈대(텍스트, 표 영역, 이미지 영역)를 감지합니다.
#    - 특히 '표(Table)'가 텍스트 추출 과정에서 깨지는 것을 막기 위해, 
#      표 영역 좌표를 우선 확보하고 해당 영역의 텍스트 추출을 건너뜁니다.
#
# 2. Pass 2 (Vision Refinement):
#    - 텍스트로 표현하기 힘든 '표'와 '차트/이미지'는 원본 그대로 캡처(Crop)합니다.
#    - 로컬 LLM(Ollama Llama 3.2 Vision)에게 이미지를 보내 상세한 설명이나
#      Markdown 표 포맷으로 변환을 요청합니다.
#
# 3. Merge (Reconstruction):
#    - 일반 텍스트(OCR)와 Vision 분석 결과를 Y축 좌표(Reading Order) 순서대로
#      재배치하여, 사람이 읽는 순서와 동일한 하나의 Markdown 문서를 완성합니다.
#
# [실행 모드]
# 1. convert: 원본 문서를 읽어 Markdown으로 변환하여 로컬에 저장합니다.
# 2. upload: 변환된 Markdown 파일을 Dify Knowledge Base API로 전송합니다.
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

# 외부 라이브러리 의존성
import requests  # API 호출용
import fitz      # PDF 처리를 위한 PyMuPDF 라이브러리

# ============================================================================
# [설정] 환경 변수 및 고정 경로 설정
# ============================================================================

# 원본 문서가 위치한 디렉터리 (Jenkins 볼륨 마운트 경로)
SOURCE_DIR = "/var/knowledges/docs/org"

# 변환된 Markdown 파일이 저장될 디렉터리
RESULT_DIR = "/var/knowledges/docs/result"

# Dify API 접속 주소
# Jenkins 컨테이너 내부에서 Dify API 컨테이너("api")로 접속합니다.
DIFY_API_BASE = os.getenv("DIFY_API_BASE", "http://api:5001/v1")

# Ollama Vision API 설정
# Jenkins는 컨테이너 내부에서 돌고, Ollama는 호스트 머신(Mac 등)에서 실행 중.
# Mac Docker Desktop: host.docker.internal 자동 해석.
# Linux: docker-compose 의 extra_hosts 로 매핑 필요. 환경 상이 시 OLLAMA_API_URL env 로 override.
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://host.docker.internal:11434/api/generate")

# 사용할 Vision 모델명 (override 가능)
# 사전에 호스트 머신에서 'ollama pull llama3.2-vision' 명령어로 모델을 받아둬야 함.
VISION_MODEL = os.getenv("VISION_MODEL", "llama3.2-vision:latest")

# Dify text document upload 안정성 튜닝
# NodeGoat 같이 청크 수가 많은 레포는 연속 업로드 도중 reverse proxy/API 가
# 간헐적으로 연결을 끊는 경우가 있어 짧은 throttle + retry 를 적용한다.
DOC_UPLOAD_RETRIES = int(os.getenv("DOC_UPLOAD_RETRIES", "3"))
DOC_UPLOAD_RETRY_BACKOFF_SEC = float(os.getenv("DOC_UPLOAD_RETRY_BACKOFF_SEC", "2"))
DOC_UPLOAD_THROTTLE_MS = int(os.getenv("DOC_UPLOAD_THROTTLE_MS", "150"))

# ============================================================================
# [유틸리티] 공통 헬퍼 함수
# ============================================================================

def log(msg: str) -> None:
    """
    Jenkins Console Output에서 로그를 명확하게 보기 위한 함수입니다.
    flush=True를 사용하여 버퍼링 없이 즉시 출력합니다.
    """
    print(f"[DocProcessor] {msg}", flush=True)

def safe_read_text(path: Path, max_bytes: int = 5_000_000) -> str:
    """
    파일을 읽을 때 발생할 수 있는 인코딩 오류와 메모리 문제를 방지합니다.
    
    Args:
        path: 읽을 파일의 경로
        max_bytes: 최대 읽기 허용 바이트 (기본 5MB). 
                   너무 큰 텍스트 파일은 RAG 청킹 과정에서 비효율적이므로 제한합니다.
    
    Returns:
        UTF-8로 디코딩된 문자열 (오류 발생 시 빈 문자열 반환)
    """
    try:
        data = path.read_bytes()
        # 파일 크기 제한 적용
        data = data[:max_bytes]
        # errors='ignore'로 설정하여 이모지나 특수문자 깨짐으로 인한 중단을 방지합니다.
        return data.decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"[Warn] 파일 읽기 실패: {path.name} / {e}")
        return ""

def write_text(path: Path, content: str) -> None:
    """
    문자열을 파일로 저장합니다. 
    저장 경로의 부모 디렉터리가 없으면 자동으로 생성합니다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

# ============================================================================
# [Core 1] Vision Analysis Logic (Ollama 연동)
# ============================================================================

def analyze_image_region(image_bytes: bytes, prompt: str) -> str:
    """
    이미지 데이터를 Ollama Vision 모델(Llama 3.2 Vision)에게 보내 분석 결과를 받습니다.
    표(Table)나 차트처럼 텍스트 추출이 어려운 영역을 처리하는 데 사용됩니다.
    
    Args:
        prompt: Vision 모델에게 지시할 명령어 (예: "이 표를 마크다운으로 바꿔줘")
        
    Returns:
        모델이 생성한 텍스트 설명 (Markdown 형식)
    """
    try:
        # 1. API 전송을 위해 이미지 바이트를 Base64 문자열로 인코딩합니다.
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        
        # 2. Ollama API 요청 페이로드를 구성합니다.
        payload = {
            "model": VISION_MODEL,
            "prompt": prompt,
            "stream": False,    # 스트리밍을 끄고 전체 응답을 한 번에 받습니다.
            "images": [img_b64],
            "options": {
                # temperature를 낮게 설정(0.1)하여 모델의 창의성을 억제합니다.
                # 문서 변환은 사실적인 데이터 추출이 중요하기 때문입니다.
                "temperature": 0.1,
                # 이미지 분석은 텍스트가 길어질 수 있으므로 컨텍스트 윈도우를 확보합니다.
                "num_ctx": 2048
            }
        }
        
        # 3. API 호출 (타임아웃 3분)
        # Vision 모델은 추론 연산량이 많아 응답 시간이 오래 걸릴 수 있습니다.
        r = requests.post(OLLAMA_API_URL, json=payload, timeout=180)
        r.raise_for_status()  # HTTP 4xx/5xx 에러 발생 시 예외 처리
        
        # 4. 결과 추출
        data = r.json()
        return data.get("response", "").strip()
        
    except Exception as e:
        log(f"!! [Vision Error] 분석 실패: {e}")
        # 실패하더라도 전체 프로세스가 죽지 않도록 에러 메시지를 반환합니다.
        return "(Vision analysis failed for this region)"

# ============================================================================
# [Core 2] Hybrid PDF Converter (핵심 변환 엔진)
# ============================================================================

def pdf_to_markdown_hybrid(pdf_path: Path) -> str:
    """
    텍스트 추출과 비전 분석을 결합한 하이브리드 변환 엔진입니다.
    1. 일반 텍스트는 PyMuPDF로 빠르게 추출합니다.
    2. 표와 이미지는 캡처하여 Ollama Vision으로 정밀 분석합니다.
    3. 모든 요소를 원래 문서의 좌표(Y축) 순서대로 재배치하여 읽기 순서를 복원합니다.
    """
    start_time = time.time()
    # PyMuPDF로 문서를 엽니다.
    doc = fitz.open(str(pdf_path))
    full_doc = []  # 전체 페이지의 변환 결과를 담을 리스트
    
    total_pages = len(doc)
    log(f"[Hybrid] Processing Start: {pdf_path.name} (Total Pages: {total_pages})")

    # 각 페이지 순회 (1페이지부터 시작)
    for page_num, page in enumerate(doc, start=1):
        log(f"  Processing Page {page_num}/{total_pages}...")
        
        # 페이지 내 추출된 요소들을 저장할 리스트
        # 구조: {'y': Y축좌표, 'type': 'text'|'table'|'image', 'content': 'Markdown내용'}
        page_content = []
        
        # --------------------------------------------------------------------
        # Pass 1-1: 표(Table) 영역 감지 (Priority 1)
        # --------------------------------------------------------------------
        # 이유: 표는 텍스트 추출 시 행/열 구조가 깨지기 가장 쉽습니다.
        # 따라서 PyMuPDF의 'find_tables' 기능을 이용해 표 영역 좌표(bbox)를 먼저 찾습니다.
        tables = page.find_tables()
        table_rects = [tab.bbox for tab in tables]
        log(f"    [Step 1] Detected {len(table_rects)} tables.")
        
        for rect in table_rects:
            # 감지된 표 영역을 이미지로 잘라냅니다 (Crop).
            pix = page.get_pixmap(clip=rect)
            img_bytes = pix.tobytes("png")
            
            v_start = time.time()
            log(f"    -> [Vision:Table] Analyzing region at Y={rect[1]:.1f}...")
            
            # Vision 모델에게 "이미지만 보고 마크다운 표를 만들어달라"고 요청합니다.
            md_table = analyze_image_region(
                img_bytes, 
                "Convert this table image into a Markdown table format. Only output the table, no description."
            )
            log(f"    <- [Vision:Table] Done ({time.time() - v_start:.2f}s)")
            
            # 결과 저장 (좌표 포함)
            page_content.append({
                "y": rect[1],    # 정렬 기준이 될 상단 Y 좌표
                "type": "table",
                "content": f"\n{md_table}\n"
            })

        # --------------------------------------------------------------------
        # Pass 1-2: 텍스트 및 이미지 블록 추출 (Priority 2)
        # --------------------------------------------------------------------
        # get_text("dict") 모드는 페이지 내용을 블록 단위 구조체로 반환합니다.
        # blocks 리스트에는 텍스트 블록과 이미지 블록이 섞여 있습니다.
        blocks = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]
        
        text_count = 0
        image_count = 0
        
        for block in blocks:
            # 블록의 좌표(bbox)를 가져옵니다.
            bbox = fitz.Rect(block["bbox"])
            
            # [중요: 중복 방지 필터링]
            # 현재 처리 중인 블록이 앞서 감지한 '표 영역' 안에 포함되는지 확인합니다.
            # 표 영역 안에 있는 텍스트는 이미 Vision 모델이 표로 변환했습니다.
            # 따라서 여기서 또 추출하면 내용이 중복되므로 건너뛰어야(Skip) 합니다.
            is_inside_table = False
            for t_rect in table_rects:
                # 두 영역의 교차 영역을 계산합니다.
                intersect = bbox.intersect(fitz.Rect(t_rect))
                # 블록 면적의 80% 이상이 표 영역과 겹치면 표의 일부로 간주합니다.
                if intersect.get_area() > 0.8 * bbox.get_area():
                    is_inside_table = True
                    break
            
            if is_inside_table:
                continue

            # ----------------------------------------------------------------
            # Case A: 텍스트 블록 처리 (type=0)
            # ----------------------------------------------------------------
            if block["type"] == 0: 
                text = ""
                # 텍스트 블록은 여러 라인(lines)과 스팬(spans)으로 구성됩니다.
                for line in block["lines"]:
                    for span in line["spans"]:
                        text += span["text"] + " "
                    text += "\n"
                
                # 내용이 있는 경우에만 추가합니다.
                if text.strip():
                    page_content.append({
                        "y": bbox.y0,
                        "type": "text",
                        "content": text
                    })
                    text_count += 1

            # ----------------------------------------------------------------
            # Case B: 이미지 블록 처리 (type=1)
            # ----------------------------------------------------------------
            elif block["type"] == 1:
                # [노이즈 필터링]
                # 문서에는 아이콘, 장식선, 배경 등 의미 없는 작은 이미지가 많습니다.
                # 가로/세로가 50px 미만인 이미지는 분석 가치가 없다고 판단하여 무시합니다.
                width = bbox[2] - bbox[0]
                height = bbox[3] - bbox[1]
                if width < 50 or height < 50:
                    continue
                
                img_bytes = block["image"]
                
                v_start = time.time()
                log(f"    -> [Vision:Image] Analyzing region at Y={bbox.y0:.1f}...")
                
                # Vision 모델에게 이미지 설명을 요청합니다.
                desc = analyze_image_region(
                    img_bytes,
                    "Describe this image in detail. If it's a chart, summarize the data trends."
                )
                log(f"    <- [Vision:Image] Done ({time.time() - v_start:.2f}s)")
                
                # 이미지는 인용구(>) 형식으로 마크다운에 삽입하여 구분합니다.
                page_content.append({
                    "y": bbox.y0,
                    "type": "image",
                    "content": f"\n> **[Image Analysis]**\n> {desc}\n"
                })
                image_count += 1

        log(f"    [Step 2] Extracted {text_count} text blocks and {image_count} images (filtered).")

        # --------------------------------------------------------------------
        # Pass 2: 병합 (Merge & Sort)
        # --------------------------------------------------------------------
        # 수집된 모든 요소(텍스트, 표, 이미지 설명)를 Y축 좌표(문서 위->아래) 순서로 정렬합니다.
        # 이를 통해 문서의 원래 읽는 순서(Reading Order)를 복원합니다.
        page_content.sort(key=itemgetter("y"))
        
        # 정렬된 요소들의 내용을 하나로 합칩니다.
        page_md = f"## Page {page_num}\n\n"
        page_md += "\n".join([item["content"] for item in page_content])
        full_doc.append(page_md)
        log(f"    [Step 3] Page {page_num} reconstruction complete.")

    doc.close()
    log(f"[Hybrid] Finished: {pdf_path.name} (Elapsed: {time.time() - start_time:.2f}s)")
    
    # 전체 페이지 내용을 구분선으로 연결하여 반환합니다.
    title = pdf_path.name
    body = "\n\n---\n\n".join(full_doc)
    return f"# {title}\n\n{body}\n"

# ============================================================================
# [유틸리티] 일반 문서 포맷 변환기 (Legacy Support)
# ============================================================================

def docx_to_markdown(docx_path: Path) -> str:
    """DOCX 파일을 텍스트만 추출하여 Markdown으로 변환합니다."""
    try:
        from docx import Document
        d = Document(str(docx_path))
        # 빈 줄을 제외하고 모든 문단을 추출합니다.
        lines = [p.text.strip() for p in d.paragraphs if p.text.strip()]
        if not lines: return ""
        return f"# {docx_path.name}\n\n" + "\n\n".join(lines) + "\n"
    except Exception as e:
        log(f"[Warn] DOCX 변환 실패: {e}")
        return ""

def excel_to_markdown(xls_path: Path) -> str:
    """Excel 파일의 각 시트를 Markdown 표 형태로 변환합니다."""
    try:
        import pandas as pd
        # 모든 시트를 읽어옵니다.
        sheets = pd.read_excel(str(xls_path), sheet_name=None)
        if not sheets: return ""
        
        out = [f"# {xls_path.name}\n"]
        for sheet_name, df in sheets.items():
            out.append(f"## Sheet: {sheet_name}\n")
            # pandas의 to_markdown 기능을 활용합니다.
            out.append(df.to_markdown(index=False))
            out.append("\n")
        return "\n".join(out).strip() + "\n"
    except Exception as e:
        log(f"[Warn] Excel 변환 실패: {e}")
        return ""

def pptx_to_pdf(pptx_path: Path, out_dir: Path) -> Optional[Path]:
    """
    PPTX 파일을 PDF로 변환합니다.
    
    이유: 
    PPTX 라이브러리로 텍스트를 직접 추출하면 레이아웃 순서가 뒤죽박죽이 되고,
    도표나 차트 데이터를 놓치기 쉽습니다.
    대신 LibreOffice를 사용해 PDF로 '인쇄'하듯 변환하면 레이아웃이 고정되므로,
    이후 Hybrid Pipeline으로 처리하기 최적의 상태가 됩니다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # 변환될 PDF 파일의 예상 경로
    expected_pdf = out_dir / (pptx_path.stem + ".pdf")
    
    # LibreOffice Headless 모드 실행 명령어
    cmd = [
        "soffice", "--headless", "--nologo", "--nolockcheck", "--norestore",
        "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx_path),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        log(f"[Warn] PPTX -> PDF 변환 실패 (LibreOffice 확인 필요): {e}")
        return None
    
    if expected_pdf.exists():
        return expected_pdf
    return None

# ============================================================================
# [실행 모드 1] 변환 로직 (Convert)
# ============================================================================

def convert_one(src_path: Path) -> None:
    """단일 파일에 대해 파일 확장자를 확인하고 적절한 변환기를 호출합니다."""
    start_time = time.time()
    ext = src_path.suffix.lower()
    
    # 1. PDF 처리 (Hybrid Vision 적용)
    if ext == ".pdf":
        log(f"[Target] PDF Detected: {src_path.name}")
        md = pdf_to_markdown_hybrid(src_path)
        if md:
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Success] {src_path.name} -> {out.name} ({time.time() - start_time:.2f}s)")
        return
    
    # 2. PPTX 처리 (PDF 변환 -> Hybrid Vision 적용)
    # PPTX는 시각적 요소가 중요하므로 PDF로 1차 변환 후 Hybrid 엔진을 태웁니다.
    if ext == ".pptx":
        log(f"[Target] PPTX Detected: {src_path.name} -> Converting to PDF first...")
        pdf = pptx_to_pdf(src_path, Path(RESULT_DIR))
        if pdf:
            # 변환된 PDF를 대상으로 Hybrid 로직 실행
            md = pdf_to_markdown_hybrid(pdf)
            if md:
                out = Path(RESULT_DIR) / f"{src_path.name}.md"
                write_text(out, md)
                log(f"[Success] {src_path.name} -> {out.name} ({time.time() - start_time:.2f}s)")
        return

    # 3. 기타 텍스트 포맷 처리 (단순 추출)
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

    # 4. 이미 마크다운인 경우 (단순 복사 및 검증)
    if ext == ".md":
        text = safe_read_text(src_path)
        if text.strip():
            out = Path(RESULT_DIR) / src_path.name
            write_text(out, text)
            log(f"[Success] {src_path.name} -> {out.name} ({time.time() - start_time:.2f}s)")
        return

def convert_all() -> None:
    """SOURCE_DIR의 모든 파일을 검색하여 변환을 수행하는 메인 루프입니다."""
    log("=== [Hybrid Doc Processor] Convert Start ===")
    
    # 결과 디렉터리가 없으면 생성
    os.makedirs(RESULT_DIR, exist_ok=True)
    src_root = Path(SOURCE_DIR)
    
    # 재귀적으로 파일 탐색
    for root, _, files in os.walk(src_root):
        for name in files:
            p = Path(root) / name
            # 지원하는 확장자 필터링 (.md 포함)
            if p.suffix.lower() in [".pdf", ".docx", ".xlsx", ".xls", ".txt", ".pptx", ".md"]:
                convert_one(p)
                
    log("=== [Hybrid Doc Processor] Convert Done ===")

# ============================================================================
# [실행 모드 2] 업로드 로직 (Upload)
# ============================================================================

def dify_headers(api_key: str) -> dict:
    """Dify API 호출을 위한 인증 헤더를 생성합니다."""
    return {"Authorization": f"Bearer {api_key}"}

def get_dataset_doc_form(api_key: str, dataset_id: str) -> str:
    """
    Dify Dataset의 설정(문서 형식)을 조회합니다.
    사용자가 '문서형' Dataset에 'Q&A' 데이터를 넣으려는 실수를 방지하기 위함입니다.
    """
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}"
    r = requests.get(url, headers=dify_headers(api_key), timeout=60)
    if r.status_code == 200:
        return str(r.json().get("doc_form", ""))
    else:
        log(f"[Warn] 데이터셋 정보를 가져올 수 없습니다. (Status: {r.status_code})")
        return ""

def ensure_doc_form_matches(api_key: str, dataset_id: str, expected_doc_form: str) -> None:
    """
    Dataset의 실제 설정과 업로드하려는 데이터 형식이 일치하는지 검증합니다.
    정보를 가져올 수 없는 경우 검증을 건너뛰고 진행합니다.
    """
    try:
        actual = get_dataset_doc_form(api_key, dataset_id)
        if actual and actual != expected_doc_form:
            log(f"[Warn] Dataset 설정 불일치 감지 (DB={actual} / 요청={expected_doc_form}). 업로드를 강행합니다.")
    except Exception as e:
        log(f"[Warn] 사전 검증 중 오류 발생: {e}. 검증 없이 진행합니다.")

def _get_dataset_indexing_technique(api_key: str, dataset_id: str) -> str:
    """Dataset 에서 indexing_technique 을 조회. high_quality/economy 중 하나. 실패 시 economy."""
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
    """Dify 의 '텍스트로 문서 만들기' API 호출.

    indexing_technique 미지정 시 Dataset 설정에서 조회 (high_quality / economy 지원).
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


# Fix B — Dify 기본 automatic_mode segmentation (~1024 tokens, ≈ 3~4KB) 보다
# 긴 청크는 여러 segment 로 쪼개지며, footer 가 한 segment 에만 몰리고 다른
# segment 는 code-only 로 저장돼 context_filter 의 parse_footer 가 path/symbol
# 을 못 뽑아 `?::?` 로 표기되는 문제 관측. code 본문을 상한으로 자른다.
# head 70% + tail 30% 전략 — signature / 초반 흐름과 return / 후반 흐름을
# 동시에 보존해 대부분의 분석 맥락을 유지. 가운데 잘림 표시를 남긴다.
MAX_CODE_CHARS_PER_CHUNK = int(os.environ.get("DOC_PROCESSOR_MAX_CODE_CHARS", "2200"))


def _truncate_code_for_single_segment(code: str, cap: int = MAX_CODE_CHARS_PER_CHUNK) -> str:
    if not code or len(code) <= cap:
        return code
    head_budget = int(cap * 0.7)
    tail_budget = cap - head_budget - 40  # 잘림 표시 라인 여백
    if tail_budget < 0:
        tail_budget = 0
    trimmed_middle = len(code) - head_budget - tail_budget
    marker = f"\n... [middle {trimmed_middle} chars truncated for single-segment upload] ...\n"
    return code[:head_budget] + marker + code[-tail_budget:]


def _chunk_to_document(chunk: dict) -> Tuple[str, str]:
    """JSONL 청크 → (document_name, document_text).

    document_name: "<path>::<symbol>" (Dify UI 에서 파일별 그룹핑 보기 좋음)
    document_text: 청크의 code 본문 + metadata footer (retrieval 시 BM25/임베딩 매칭 대상 확장)
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
    # Dify document name 길이 제한 대비 (100자 내외)
    if len(name) > 120:
        name = name[:117] + "..."

    # 본문 footer 에 metadata 추가 — retrieval 쿼리 매칭 면적 확대.
    # is_test / callers / test_paths 는 Dify BM25 검색이 metadata 도 토큰화하므로
    # build_kb_query 가 생성하는 구조화 쿼리 ("callers: loginUser" 등) 와 매칭된다.
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
        # P2 K-4 — leading docstring/comment. 자연어 의도가 BM25/dense 양쪽
        # 임베딩에 노출되어 코드 식별자만으로는 약한 의미 매칭 보완.
        meta_lines.append(f"doc: {doc}")
    if is_test:
        meta_lines.append("is_test: true")
        # D3 — test 청크 footer 에 자연어 동의어 강제 주입.
        # cypress e2e (`cy.get`) vs DAO (`parseInt`) 처럼 vocabulary 가 멀어
        # tests bucket 0% 가 되는 문제 (관측). 자연어 토큰 한 줄로 BM25 / dense
        # 양쪽에서 caller 코드 측의 "테스트", "검증", "spec" 같은 의도 자연어와
        # 매칭 가능하게.
        meta_lines.append("tags: 테스트 검증 시나리오 spec assertion test e2e cypress unit integration")
    if test_for:
        meta_lines.append(f"test_for: {test_for}")
    if callees:
        meta_lines.append(f"callees: {', '.join(callees[:20])}")
    if callers:
        meta_lines.append(f"callers: {', '.join(callers[:20])}")
    if test_paths:
        meta_lines.append(f"test_paths: {', '.join(test_paths[:10])}")
    # D — decorator 정보. 보안/auth/route 의도가 청크 매칭 면적에 노출.
    decorators = chunk.get("decorators") or []
    if decorators:
        meta_lines.append(f"decorators: {' '.join(decorators[:5])}")
    # C — endpoint (HTTP route). decorator 또는 명령형 등록에서 추출됨.
    endpoint = (chunk.get("endpoint") or "").strip()
    if endpoint:
        meta_lines.append(f"endpoint: {endpoint}")
    # H — docstring 구조화. params/returns/throws 토큰화로 dense + BM25 양쪽
    # 매칭 가능. caller 코드 측의 "이 함수는 어떤 매개변수를 받는가" 검색 신호.
    doc_params = chunk.get("doc_params") or []
    if doc_params:
        # [(type, name, desc), ...] 의 name 만 토큰으로
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
    # D1 — _context_summary (enricher 가 LLM 으로 생성한 청크 자연어 요약) 를
    # footer 에 노출. 코드 식별자 매칭에 약한 케이스 (cypress / e2e 등) 에서
    # 자연어 임베딩 의미 매칭의 면적을 확대 — caller 코드 측의 "이 함수의
    # 동작 의도" 와 dense 매칭 가능.
    summary = (chunk.get("_context_summary") or "").strip()
    if summary:
        # 너무 길면 footer 비대 — 240자 제한
        meta_lines.append(f"summary: {summary[:240]}")
    # A1 (간소판) — e2e/cypress 청크 본문에서 describe(...) / it(...) 의 자연어
    # 설명을 추출해 footer 에 노출. tree-sitter 파싱 대신 정규식.
    if is_test and lang in ("javascript", "typescript", "tsx"):
        import re as _re
        descs = []
        body = chunk.get("code") or ""
        # describe("...", ...) / it("...", ...) / context("...", ...) 패턴
        for m in _re.finditer(
                r"\b(?:describe|it|context|test)\s*\(\s*['\"]([^'\"]{4,140})['\"]",
                body):
            descs.append(m.group(1).strip())
            if len(descs) >= 3:
                break
        if descs:
            meta_lines.append(f"test_descriptions: {' / '.join(descs)}")

    # Fix B — code 본문 상한 적용 후 footer 추가
    code_body = _truncate_code_for_single_segment(chunk.get("code", ""))
    text = code_body + "\n" + "\n".join(meta_lines)
    return name, text


# ─ Fix A — Dataset purge 헬퍼 ─────────────────────────────────────────────
# 02 사전학습을 --mode full 로 돌릴 때 이전 프로젝트/이전 실행의 잔재 청크가
# 같은 Dataset 에 누적되면 retrieve 결과에 무관한 프로젝트 청크가 섞여 RAG
# 품질이 급락한다 (관측: nodegoat + ttc-sample-app 혼재로 citation 2.1%).
# 업로드 전에 기존 문서를 모두 삭제해 프로젝트 간 격리를 보장.
def purge_dataset_documents(api_key: str, dataset_id: str) -> int:
    """Dataset 의 모든 document 를 삭제. 삭제 건수 반환."""
    log("[Purge] 기존 Dataset 문서 전수 삭제 시작")
    import urllib.request
    import urllib.error
    headers = dify_headers(api_key)
    deleted = 0
    # pagination — 한 번에 최대 100 건씩 조회해 삭제
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
            log(f"[Purge:WARN] document list 실패: {e}")
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
                    log(f"[Purge] {deleted} 건 삭제됨...")
            except urllib.error.HTTPError as e:
                log(f"[Purge:WARN] {doc.get('name','?')} 삭제 실패 HTTP {e.code}")
            except Exception as e:
                log(f"[Purge:WARN] {doc.get('name','?')} 삭제 실패: {e}")
        # 한 페이지 삭제 후 다음 루프에서 다시 list (page=1) — 삭제됐으면 다음 100 건이 올라옴
        if len(items) < 100:
            break
    log(f"[Purge] 완료 — 총 {deleted} 건 삭제")
    return deleted


def upload_jsonl_chunks(
    api_key: str,
    dataset_id: str,
    doc_form: str,
    doc_language: str,
    indexing_technique: str,
) -> Tuple[int, int]:
    """RESULT_DIR 내 *.jsonl 각 line → 하나의 Dify document 로 업로드.

    반환: (success_count, fail_count)
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
                # 긴 로그 방지 — 10 의 배수만 출력
                if success % 10 == 1 or success <= 5:
                    log(f"[Upload:SUCCESS] {name} | {detail}")
            else:
                fail += 1
                log(f"[Upload:FAIL] {name} | {detail}")
    return success, fail


def _write_kb_manifest(dataset_id: str, doc_count: int, path: str = "/data/kb_manifest.json") -> None:
    """Phase 1.5 — KB freshness 판정용 manifest.

    P2/P3 의 Stage 0 가 이 파일의 commit_sha 를 읽어 현재 요청된 SHA 와 비교한다.
    필드는 모두 env 에서 읽는다 (Jenkinsfile 가 export):
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
    """RESULT_DIR 의 *.jsonl (AST 청크) + fallback *.md (레거시) 를 Dify 로 업로드.

    purge_first=True 일 때 업로드 전에 Dataset 의 기존 문서를 모두 삭제한다.
    02 --mode full 에서 프로젝트 간 청크 혼재 방지 용도 (Fix A).
    """
    log("=== [Hybrid Doc Processor] Upload Start ===")
    ensure_doc_form_matches(api_key, dataset_id, doc_form)

    if purge_first:
        purge_dataset_documents(api_key, dataset_id)

    result_root = Path(RESULT_DIR)
    if not result_root.exists():
        log("[FAIL] 변환 결과 디렉터리가 없습니다. 먼저 convert/repo_context_builder 를 실행하세요.")
        return 1

    indexing_technique = _get_dataset_indexing_technique(api_key, dataset_id)
    log(f"Dataset indexing_technique: {indexing_technique}")

    # 1) JSONL (AST 청크) 모드 우선 — repo_context_builder 가 만든 파일당 여러 document
    jsonl_success, jsonl_fail = upload_jsonl_chunks(
        api_key, dataset_id, doc_form, doc_language, indexing_technique
    )

    # 2) fallback: 레거시 *.md 업로드 (JSONL 이 없을 때만)
    md_success = 0
    md_fail = 0
    if jsonl_success == 0 and jsonl_fail == 0:
        log("[Upload] JSONL 없음 — 레거시 *.md 업로드로 폴백")
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

    # Phase 1.5: 업로드 성공 후 kb_manifest 기록 (P2/P3 freshness assert 근거).
    # 실패도 일부 있을 수 있으니 total_success > 0 기준.
    if total_success > 0:
        _write_kb_manifest(dataset_id, total_success)

    log("=== [Hybrid Doc Processor] Upload Done ===")
    return total_fail

# ============================================================================
# [메인] 프로그램 진입점 (CLI 파서)
# ============================================================================

def main() -> None:
    """
    명령행 인자를 파싱하여 convert 또는 upload 모드를 실행합니다.
    사용법:
      1. 변환: python3 doc_processor.py convert
      2. 업로드: python3 doc_processor.py upload <API_KEY> <DATASET_ID> <FORM> <LANG>
    """
    if len(sys.argv) < 2:
        raise SystemExit("usage: doc_processor.py [convert|upload] ...")
    
    cmd = sys.argv[1].strip().lower()
    
    # 1. 변환 모드 실행
    if cmd == "convert":
        convert_all()
        return
    
    # 2. 업로드 모드 실행
    if cmd == "upload":
        # 최소 2개의 인자(API_KEY, DATASET_ID)가 필요함. --purge 플래그 지원.
        # 사용법:
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
        # 인자가 없으면 기본값 사용 (하위 호환성 유지)
        doc_form = argv[2] if len(argv) > 2 else "text_model"
        doc_language = argv[3] if len(argv) > 3 else "Korean"

        fail_count = upload_all(api_key, dataset_id, doc_form, doc_language, purge_first=purge_first)
        if fail_count > 0:
            sys.exit(1) # 실패가 있으면 에러 코드를 반환하여 Jenkins 빌드를 실패 처리함
        return

if __name__ == "__main__":
    main()
