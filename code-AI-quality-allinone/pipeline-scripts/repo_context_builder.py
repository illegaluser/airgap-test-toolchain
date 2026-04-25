#!/usr/bin/env python3
# Tree-sitter AST 기반 코드 청커 — Dify RAG 용 파일/심볼 단위 JSONL 생성.
#
# 입력: --repo_root (분석 대상 레포 경로), --out (결과 디렉터리), --commit-sha (옵션)
# 출력: out/<repo>/<path_safe>.jsonl  (각 line = 하나의 symbol 청크)
# 추가: out/<repo>/_repo_summary.md   (기존 context_<repo>.md 대체, 사람이 보는 요약)
#
# 청크 필드:
#   path, symbol, kind (function/method/class/...), lang,
#   lines ("start-end"), code, commit_sha,
#   callees (본 청크 안 호출 — AST 로컬 추출),
#   callers (pass 2 역인덱스, "caller_path#caller_symbol" 형식),
#   test_paths (pass 2 역링크 — 이 심볼을 대상으로 하는 테스트 "test_path#test_symbol"),
#   is_test (boolean, 테스트 심볼이면 true), test_for (테스트 심볼이 가리키는 대상)
#
# 호출 그래프 구축은 2 패스로 수행:
#   pass 1 — 각 파일 AST 에서 symbol 청크 + callees (같은 파일 안 call site 이름만) 수집
#   pass 2 — 전체 청크 모인 뒤 symbol name → 청크 역인덱스 구축, callees 를 순회해
#            target 청크의 callers 역링크, test_for → test_paths 역링크 채움
# pass 2 는 동명 심볼 중의성에 대해 "모든 후보에 링크" 하는 recall 우선 전략을 취한다.
# import resolution 은 tree-sitter 만으로는 불가해 precision 은 희생된다 — impact
# analysis 맥락에서는 false-positive caller 가 false-negative 보다 덜 해롭다고 판단.
#
# 실패 정책: 파서 실패·바이너리·비지원 확장자는 조용히 skip, 전체 흐름 중단 없음.
import argparse
import json
import os
import re
import subprocess
import warnings
from pathlib import Path

# tree_sitter_languages (≥0.2) 가 내부적으로 구형 `Language(path, name)` 을 호출해
# FutureWarning 을 파일당 1~2회씩 출력, 수백 줄 로그 잡음을 만든다. 실제 동작엔
# 영향 없고 패키지 상류 수정 전까지 외부에서 억제만 가능.
warnings.filterwarnings(
    "ignore",
    message=r"Language\(path, name\) is deprecated.*",
    category=FutureWarning,
    module=r"tree_sitter(\..*)?",
)

try:
    from tree_sitter_languages import get_language, get_parser
except Exception as e:
    # 빌드 환경에 tree-sitter-languages 가 없으면 즉시 실패 (Dockerfile 이 보장)
    raise SystemExit(f"[repo_context_builder] tree_sitter_languages import 실패: {e}")


# Step R 에서 외부 스크립트 (sonar_issue_exporter) 가 enclosing_function 추출에
# 이 모듈의 청킹 로직을 재사용할 수 있도록 공개 심볼을 명시. 리팩터 없이
# `from repo_context_builder import extract_chunks_from_file, LANG_CONFIG` 가능.
__all__ = [
    "LANG_CONFIG",
    "EXCLUDE_DIRS",
    "extract_chunks_from_file",
    "scan_repo",
    "resolve_commit_sha",
]


# ─ 언어별 AST 노드 매핑 ────────────────────────────────────────────────────
# node_types: tree-sitter 노드 타입 → 논리적 "kind" (function/class/method 등)
# name_field: child_by_field_name 으로 이름을 뽑는 필드 (없으면 identifier 자식 탐색)
LANG_CONFIG = {
    ".py": {
        "lang": "python",
        "node_types": {
            "function_definition": "function",
            "class_definition": "class",
            "async_function_definition": "function",
        },
        "name_field": "name",
    },
    ".java": {
        "lang": "java",
        "node_types": {
            "method_declaration": "method",
            "constructor_declaration": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
        },
        "name_field": "name",
    },
    ".ts": {
        "lang": "typescript",
        "node_types": {
            "function_declaration": "function",
            "method_definition": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "arrow_function": "function",
            # E — type/schema 청크. TS 의 도메인 모델은 정적분석 진단의 핵심 맥락.
            "type_alias_declaration": "type",
            "enum_declaration": "enum",
        },
        "name_field": "name",
    },
    ".tsx": {
        "lang": "tsx",
        "node_types": {
            "function_declaration": "function",
            "method_definition": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "type_alias_declaration": "type",
            "enum_declaration": "enum",
        },
        "name_field": "name",
    },
    ".js": {
        "lang": "javascript",
        "node_types": {
            "function_declaration": "function",
            "method_definition": "method",
            "class_declaration": "class",
            "arrow_function": "function",
        },
        "name_field": "name",
    },
    # P2 H-3 — 추가 언어. tree_sitter_languages 패키지가 모두 포함.
    # Phase A F5 (확장됨): collect_callees() 가 go/rust/c#/kotlin/c/cpp 분기를
    # 모두 갖추므로 callers 역인덱스가 11개 언어 전부에서 동작. 언어별 비대칭
    # 우려는 해소됐고, diagnostic 리포트는 lang_breakdown 으로 분포 노출.
    ".go": {
        "lang": "go",
        "node_types": {
            "function_declaration": "function",
            "method_declaration": "method",
            "type_declaration": "type",
        },
        "name_field": "name",
    },
    ".rs": {
        "lang": "rust",
        "node_types": {
            "function_item": "function",
            "impl_item": "impl",
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "trait",
        },
        "name_field": "name",
    },
    ".cs": {
        "lang": "c_sharp",
        "node_types": {
            "method_declaration": "method",
            "constructor_declaration": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "struct_declaration": "struct",
            "enum_declaration": "enum",
        },
        "name_field": "name",
    },
    ".kt": {
        "lang": "kotlin",
        "node_types": {
            "function_declaration": "function",
            "class_declaration": "class",
            "object_declaration": "object",
        },
        "name_field": "simple_identifier",
    },
    ".c": {
        "lang": "c",
        "node_types": {
            "function_definition": "function",
        },
        "name_field": "declarator",  # function_definition.declarator → identifier
    },
    ".h": {
        "lang": "c",
        "node_types": {
            "function_definition": "function",
        },
        "name_field": "declarator",
    },
    ".cpp": {
        "lang": "cpp",
        "node_types": {
            "function_definition": "function",
            "class_specifier": "class",
            "struct_specifier": "struct",
        },
        "name_field": "declarator",
    },
    ".hpp": {
        "lang": "cpp",
        "node_types": {
            "function_definition": "function",
            "class_specifier": "class",
        },
        "name_field": "declarator",
    },
    ".cc": {
        "lang": "cpp",
        "node_types": {
            "function_definition": "function",
            "class_specifier": "class",
        },
        "name_field": "declarator",
    },
}


EXCLUDE_DIRS = {
    ".git", ".scannerwork", "node_modules", "build", "dist", "out", "target",
    ".idea", ".vscode", ".gradle", ".next", ".nuxt", ".cache", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    # P1.5 K-1 — 번들/미니파이 자산 경로. caller hub 가 1자 심볼 (`e`,`m`,`a`)
    # 로 오염되는 주된 원인. nodegoat 실측 기준 caller hub top-10 중 9개가
    # 이 경로에서 나왔음.
    "vendor", "vendors", "3rdparty", "third_party", "bower_components",
    "public/vendor", "assets/vendor",
    "min", "minified",
}


# P1.5 K-1 — 미니파이 파일명 패턴 (경로엔 vendor 없어도 파일명으로 걸러낸다).
# `.min.js / .min.css / -min.js / bundle.js / chunk-<hash>.js` 등.
# 정규식 한 번에 평가. 프로젝트 루트에 `bundle.prod.js` 같은 산출물이 들어있는
# 경우도 커버.
import re as _re
MINIFIED_FILE_PATTERNS = _re.compile(
    r"(?:"
    r"\.min\.(?:js|css|mjs)$"
    r"|-min\.(?:js|css)$"
    r"|\.bundle\.(?:js|mjs)$"
    r"|chunk-[0-9a-f]{6,}\.(?:js|mjs)$"
    r"|\.umd\.(?:js|mjs)$"
    r")",
    _re.IGNORECASE,
)


KEY_FILES = [
    "README.md", "README.txt",
    "package.json", "requirements.txt", "pyproject.toml", "Pipfile",
    "pom.xml", "build.gradle", "build.gradle.kts",
    "go.mod", "Cargo.toml", ".env.example",
]


# P1.5 K-2 — trivial chunk 감지 임계값.
# body 가 너무 짧거나 content 가 거의 식별자뿐인 경우 "1자 vendor 심볼" 같은
# 노이즈. BM25 에 문서로 들어가면 흔한 토큰으로 잘못 매칭된다.
MIN_MEANINGFUL_CODE_CHARS = int(os.environ.get("REPO_CTX_MIN_BODY_CHARS", "20"))
MIN_MEANINGFUL_NON_WS_LINES = int(os.environ.get("REPO_CTX_MIN_BODY_LINES", "2"))


def is_trivial_chunk(code: str) -> bool:
    """매우 짧거나 문법적 껍데기만 있는 청크를 True 로 판정.

    heuristic:
      - 전체 body 문자 (공백 제거) < MIN_MEANINGFUL_CODE_CHARS
      - 비-공백 라인 수 < MIN_MEANINGFUL_NON_WS_LINES
    """
    stripped = code.strip()
    if len(stripped) < MIN_MEANINGFUL_CODE_CHARS:
        return True
    non_ws_lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if len(non_ws_lines) < MIN_MEANINGFUL_NON_WS_LINES:
        return True
    return False


# ─ 언어별 "call site" 추출 — callees 필드용 ──────────────────────────────────
# Phase A F5 — 이전엔 python/java/js/ts 만 callees 추출되어 go/rust/c#/kotlin/c/cpp
# 레포는 callers 역인덱스가 구조적으로 비었다. 추가 분기로 11개 LANG_CONFIG
# 언어 모두 호출 그래프 추출 가능하게 확장.
def collect_callees(node, lang: str) -> list:
    """node 서브트리에서 호출된 symbol 이름들을 수집 (중복 제거·정렬)."""
    callees = set()

    def _trim_dotted(txt: str) -> str:
        # obj.method / pkg::func 등에서 마지막 식별자만 추출
        if "::" in txt:
            txt = txt.rsplit("::", 1)[-1]
        if "." in txt:
            txt = txt.rsplit(".", 1)[-1]
        return txt

    def _is_ident_like(txt: str) -> bool:
        # 영숫자 + underscore 만. 빈 문자열 / 연산자 / generic <...> 스킵.
        if not txt or len(txt) < 2:
            return False
        return txt.replace("_", "").isalnum()

    def walk(n):
        if lang == "python":
            if n.type == "call":
                fn = n.child_by_field_name("function")
                if fn is not None:
                    txt = _trim_dotted(fn.text.decode("utf-8", errors="replace"))
                    if txt.isidentifier():
                        callees.add(txt)
        elif lang == "java":
            if n.type == "method_invocation":
                name = n.child_by_field_name("name")
                if name is not None:
                    callees.add(name.text.decode("utf-8", errors="replace"))
            elif n.type == "object_creation_expression":
                # `new Foo()` — 생성자 호출도 호출 그래프에 포함.
                tp = n.child_by_field_name("type")
                if tp is not None:
                    txt = tp.text.decode("utf-8", errors="replace")
                    if _is_ident_like(txt):
                        callees.add(txt)
        elif lang in ("javascript", "typescript", "tsx"):
            if n.type in ("call_expression", "new_expression"):
                fn = n.child_by_field_name("function") or n.child_by_field_name("constructor")
                if fn is not None:
                    txt = _trim_dotted(fn.text.decode("utf-8", errors="replace"))
                    if _is_ident_like(txt):
                        callees.add(txt)
        elif lang == "go":
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                if fn is not None:
                    txt = _trim_dotted(fn.text.decode("utf-8", errors="replace"))
                    if _is_ident_like(txt):
                        callees.add(txt)
        elif lang == "rust":
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                if fn is not None:
                    txt = _trim_dotted(fn.text.decode("utf-8", errors="replace"))
                    # rust 의 generic <T> 가 붙어 들어올 수 있으므로 < 이전까지
                    if "<" in txt:
                        txt = txt.split("<", 1)[0]
                    if _is_ident_like(txt):
                        callees.add(txt)
            elif n.type == "macro_invocation":
                # rust 매크로 (`println!`, `assert_eq!` 등) 도 호출 그래프 신호.
                # `!` 는 식별자가 아니므로 제거.
                mac = n.child_by_field_name("macro")
                if mac is not None:
                    txt = _trim_dotted(mac.text.decode("utf-8", errors="replace")).rstrip("!")
                    if _is_ident_like(txt):
                        callees.add(txt)
            elif n.type == "method_call_expression":
                # rust `obj.method(...)` — name 이 method 의 식별자 자식
                name = n.child_by_field_name("method")
                if name is not None:
                    txt = name.text.decode("utf-8", errors="replace")
                    if _is_ident_like(txt):
                        callees.add(txt)
        elif lang == "c_sharp":
            if n.type == "invocation_expression":
                fn = n.child_by_field_name("function") or n.child_by_field_name("expression")
                if fn is not None:
                    txt = _trim_dotted(fn.text.decode("utf-8", errors="replace"))
                    if "<" in txt:
                        txt = txt.split("<", 1)[0]
                    if _is_ident_like(txt):
                        callees.add(txt)
            elif n.type == "object_creation_expression":
                # `new Foo()` — C# 생성자
                tp = n.child_by_field_name("type")
                if tp is not None:
                    txt = _trim_dotted(tp.text.decode("utf-8", errors="replace"))
                    if "<" in txt:
                        txt = txt.split("<", 1)[0]
                    if _is_ident_like(txt):
                        callees.add(txt)
        elif lang == "kotlin":
            if n.type == "call_expression":
                # kotlin tree-sitter 는 함수명을 첫 child (보통 simple_identifier
                # 또는 navigation_expression) 에 둔다. field name 이 통일돼 있지
                # 않아 휴리스틱 — 첫 자식이 식별자면 그대로, navigation 이면 마지막.
                ch0 = n.children[0] if n.children else None
                if ch0 is not None:
                    txt = _trim_dotted(ch0.text.decode("utf-8", errors="replace"))
                    if _is_ident_like(txt):
                        callees.add(txt)
        elif lang in ("c", "cpp"):
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                if fn is not None:
                    txt = fn.text.decode("utf-8", errors="replace")
                    # cpp `obj->method(...)` 처리
                    if "->" in txt:
                        txt = txt.rsplit("->", 1)[-1]
                    txt = _trim_dotted(txt)
                    # cpp `Foo<T>::bar` 처리
                    if "<" in txt:
                        txt = txt.split("<", 1)[0]
                    if _is_ident_like(txt):
                        callees.add(txt)
        for ch in n.children:
            walk(ch)

    walk(node)
    return sorted(callees)


def get_symbol_name(node, name_field: str):
    """child_by_field_name(name_field) 우선, 익명 함수면 부모 컨텍스트에서 이름 부여.

    Phase D Fix A — 익명 callback (`err => {...}`, `function(req,res){...}`) 의
    파라미터를 함수명으로 잡는 fn:err 버그 수정. 추출 우선순위:

      1. node.child_by_field_name(name_field) — 정상 명명된 함수
      2. arrow_function / function_expression 의 부모가 명명 컨텍스트면:
         - variable_declarator (`const X = () => {}`) → X
         - assignment_expression (`X.y = function(){}`) → 좌변 마지막 식별자
         - pair (`{ X: () => {} }`) → 키
         - public_field_definition (TS class field) → 필드명
      3. 그 외 익명 함수 → None (chunk skip 유도)
    """
    n = node.child_by_field_name(name_field)
    if n is not None:
        txt = n.text.decode("utf-8", errors="replace").strip()
        if txt:
            return txt

    # arrow_function / function_expression / function — 익명 가능 타입에 한해 부모 lookup.
    # 그 외 (function_declaration, method_definition, class_declaration 등) 는
    # name field 없으면 진짜 익명이므로 None.
    if node.type not in ("arrow_function", "function_expression", "function"):
        return None

    parent = node.parent
    while parent is not None:
        ptype = parent.type
        if ptype == "variable_declarator":
            # const X = () => {} 의 X
            nm = parent.child_by_field_name("name")
            if nm is not None:
                txt = nm.text.decode("utf-8", errors="replace").strip()
                if txt and txt.replace("_", "").isalnum():
                    return txt
            return None
        if ptype == "assignment_expression":
            # X.y = function(){} 의 y, 또는 X = () => {} 의 X
            left = parent.child_by_field_name("left")
            if left is not None:
                txt = left.text.decode("utf-8", errors="replace").strip()
                if "." in txt:
                    txt = txt.rsplit(".", 1)[-1]
                if txt and txt.replace("_", "").isalnum():
                    return txt
            return None
        if ptype == "pair":
            # { foo: () => {} } 의 foo
            key = parent.child_by_field_name("key")
            if key is not None:
                txt = key.text.decode("utf-8", errors="replace").strip().strip("'\"`")
                if txt and txt.replace("_", "").isalnum():
                    return txt
            return None
        if ptype in ("public_field_definition", "field_definition"):
            # TS class field — name field
            nm = parent.child_by_field_name("name") or parent.child_by_field_name("property")
            if nm is not None:
                txt = nm.text.decode("utf-8", errors="replace").strip()
                if txt and txt.replace("_", "").isalnum():
                    return txt
            return None
        # variable_declarator → lexical_declaration → program 같은 wrapper 한 단계 통과.
        if ptype in ("lexical_declaration", "variable_declaration"):
            parent = parent.parent
            continue
        break
    return None


# P2 K-4 — leading docstring / comment 추출.
# 정렬된 결과를 청크 metadata 에 `doc:` 라인으로 실어 BM25 가 자연어 의도까지
# 토큰화하도록 한다. 코드 식별자만으로는 약했던 dense retrieval 의 의미 매칭 보완.
_COMMENT_NODE_TYPES = {
    "comment", "line_comment", "block_comment", "doc_comment",
    "shebang_line",
}


def _extract_leading_doc_text(node, lang: str, source: bytes) -> str:
    """raw docstring/comment 추출 (newline 보존). Python triple-quote / 주석
    마커는 제거하지만 줄 구분은 유지 — Google/NumPy block 파서가 의미 단위로
    분리할 수 있도록.

    Phase A F7: Google-style `Args:` / `Returns:` / NumPy `----` 헤더 같은
    block 구조는 newline 위에 의존하므로 normalize 이전 단계의 텍스트가 필요.
    """
    raw = ""
    if lang == "python":
        body = node.child_by_field_name("body")
        if body is not None and body.child_count > 0:
            first = body.children[0]
            if first.type == "expression_statement" and first.child_count > 0:
                expr = first.children[0]
                if expr.type == "string":
                    raw = source[expr.start_byte:expr.end_byte].decode("utf-8", errors="replace")

    if not raw:
        collected = []
        cur = node.prev_sibling
        while cur is not None and cur.type in _COMMENT_NODE_TYPES:
            collected.append(source[cur.start_byte:cur.end_byte].decode("utf-8", errors="replace"))
            cur = cur.prev_sibling
        if collected:
            collected.reverse()
            raw = "\n".join(collected)
    if not raw:
        return ""
    return _strip_doc_markers(raw)


def extract_leading_doc(node, lang: str, source: bytes) -> str:
    """함수 정의 위쪽의 연속 comment 또는 Python docstring 을 한 줄로 요약.

    - Python: 함수 body 첫 statement 가 string literal 이면 docstring.
    - 기타 언어: 함수 정의 노드 직전 sibling 들 중 연속 comment 노드 텍스트
      를 위→아래 순으로 수집.
    """
    raw = _extract_leading_doc_text(node, lang, source)
    if not raw:
        return ""
    return _collapse_doc_to_oneline(raw)


def _strip_doc_markers(raw: str) -> str:
    """주석 마커 / Javadoc leading * / Python triple-quote 제거. newline 은 보존.

    parse_docstring_structure 와 _collapse_doc_to_oneline 두 곳에서 공유.
    """
    s = raw.strip()
    for q in ('"""', "'''"):
        if s.startswith(q):
            s = s[len(q):]
        if s.endswith(q):
            s = s[: -len(q)]
    cleaned_lines = []
    for line in s.splitlines():
        ln = line  # leading whitespace 보존 (Google/NumPy block indentation 식별용)
        stripped = ln.strip()
        # 블록 주석 마커
        if stripped.startswith("/**"):
            ln = ln.replace("/**", "", 1)
        elif stripped.startswith("/*"):
            ln = ln.replace("/*", "", 1)
        if ln.rstrip().endswith("*/"):
            ln = ln.rstrip()[:-2]
        # Javadoc leading * — 문자 자체는 제거하되 indentation 한 칸 유지
        ln = re.sub(r"^(\s*)\*\s?", r"\1", ln)
        # 라인 주석
        for prefix in ("///", "//!", "//", "###", "##", "#"):
            stripped2 = ln.lstrip()
            if stripped2.startswith(prefix):
                # prefix 제거하되 그 자리 공백 유지
                idx = ln.find(prefix)
                ln = ln[:idx] + ln[idx + len(prefix):]
                break
        cleaned_lines.append(ln.rstrip())
    # 앞뒤 빈 줄 제거
    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()
    return "\n".join(cleaned_lines)


def _collapse_doc_to_oneline(raw_or_stripped: str) -> str:
    """multi-line text → 한 줄 + 200자 cap (footer 용)."""
    pieces = [ln.strip() for ln in raw_or_stripped.splitlines() if ln.strip()]
    return " ".join(pieces).strip()[:200]


def _normalize_doc_text(raw: str) -> str:
    """주석 마커 / Javadoc leading * / Python triple-quote 제거 + 공백 정리.

    상한 200자. 줄바꿈은 공백으로 (footer 한 줄로 들어감).
    backward-compat alias — 신규 코드는 _strip_doc_markers + _collapse_doc_to_oneline 사용.
    """
    return _collapse_doc_to_oneline(_strip_doc_markers(raw))


def walk_symbols(node, node_types: dict):
    """AST 를 순회하며 관심 노드 yield."""
    if node.type in node_types:
        yield (node, node_types[node.type])
    for ch in node.children:
        yield from walk_symbols(ch, node_types)


# ─ D — decorator 추출 ──────────────────────────────────────────────────────
# Python: decorated_definition 의 child decorator 노드들
# JS/TS: 함수/클래스 정의의 직전 sibling decorator (ECMAScript decorators stage 3)
# Java: modifiers 안의 marker_annotation / annotation
# Sonar 이슈 분석에서 `@require_role`, `@app.route` 같은 정적 의도 정보가 LLM 답변
# 의 보안·도메인 맥락을 직접 짚어줄 수 있다.
def extract_decorators(node, lang: str, source: bytes) -> list:
    """함수/메소드/클래스 노드에서 데코레이터 텍스트 리스트 추출.

    반환: ["@app.route('/login')", "@require_role('admin')", ...]
    각 항목은 한 줄로 정규화된 데코레이터 호출 표현.
    """
    out: list = []
    parent = node.parent
    if lang == "python":
        # Python tree-sitter 는 "decorated_definition" wrapper 안에 decorator 들과
        # 실제 def 가 같은 children 으로 위치. parent.type == decorated_definition 인 경우만.
        if parent is not None and parent.type == "decorated_definition":
            for ch in parent.children:
                if ch.type == "decorator":
                    txt = source[ch.start_byte:ch.end_byte].decode("utf-8", errors="replace")
                    out.append(_normalize_one_line(txt))
    elif lang in ("javascript", "typescript", "tsx"):
        # JS/TS 의 class member 에서 decorator 들은 같은 부모 (class_body) 안의
        # 직전 sibling 들이거나 method_definition 의 child 일 수 있음.
        # 단순 접근: 노드 자신의 children 에서 decorator 노드 수집 + 직전 sibling.
        for ch in node.children:
            if ch.type == "decorator":
                txt = source[ch.start_byte:ch.end_byte].decode("utf-8", errors="replace")
                out.append(_normalize_one_line(txt))
        cur = node.prev_sibling
        while cur is not None and cur.type == "decorator":
            txt = source[cur.start_byte:cur.end_byte].decode("utf-8", errors="replace")
            out.append(_normalize_one_line(txt))
            cur = cur.prev_sibling
    elif lang == "java":
        # Java 의 modifiers 안에 annotation 들 — 함수/클래스 노드의 첫 child 가
        # modifiers 인 경우가 일반적.
        for ch in node.children:
            if ch.type == "modifiers":
                for m in ch.children:
                    if m.type in ("marker_annotation", "annotation"):
                        txt = source[m.start_byte:m.end_byte].decode("utf-8", errors="replace")
                        out.append(_normalize_one_line(txt))
    # 짧은 cap — footer 비대 방지
    return out[:10]


def _normalize_one_line(s: str) -> str:
    """다중 라인 텍스트를 한 줄로, 연속 공백 정리, 200자 cap."""
    import re as _re
    out = _re.sub(r"\s+", " ", s.strip())
    return out[:200]


# ─ C — route endpoint 추출 ────────────────────────────────────────────────
# decorator 기반 우선 — 명령형 (`app.get('/path', handler)`) 은 별도 패스에서.
# 이 함수는 decorator 리스트에서 라우트 정보를 정규화해 반환.
_ROUTE_RE = re.compile(
    r"@(?:[\w.]+)\.(get|post|put|patch|delete|head|options|route)\s*\(\s*['\"]([^'\"]+)['\"]"
)


def parse_route_endpoint(decorators: list) -> str:
    """decorator 리스트에서 라우트 endpoint 한 줄로 추출.

    매칭:
      @app.get('/login')               → "GET /login"
      @app.route('/profile')           → "ANY /profile"
      @router.post('/api/users', ...)  → "POST /api/users"
      @blueprint.route('/x', methods=['GET'])  → "ANY /x" (methods 는 별도 파싱
        없이 일단 ANY 로)
    """
    for dec in decorators:
        m = _ROUTE_RE.search(dec)
        if m:
            method = m.group(1).upper()
            path = m.group(2)
            if method == "ROUTE":
                method = "ANY"
            return f"{method} {path}"
    return ""


# ─ Phase A F6 — imperative route 추출 ────────────────────────────────────
# Express/Koa/Fastify/Python `add_url_rule` 같은 명령형 라우트 등록을 AST 로
# 잡아내 handler 식별자에 endpoint 를 매핑. decorator 기반 등록은 이미
# extract_decorators + parse_route_endpoint 가 커버하므로 두 경로를 합쳐야
# 라우트 검출 누락이 없다.
#
# 잡는 패턴 (의사 코드):
#   <obj>.<METHOD>('/path', handlerIdent)        # Express/Koa
#   <obj>.<METHOD>('/path', mw1, mw2, handlerIdent)
#   app.add_url_rule('/path', view_func=handler) # Flask
# 익명 핸들러 (`(req, res) => {...}`) 는 chunk symbol 이 없어 매핑 불가 — skip.
_IMPERATIVE_HTTP_METHODS = {
    "get", "post", "put", "patch", "delete", "head", "options", "all", "use",
}


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def extract_imperative_routes(tree, source: bytes, lang: str) -> list:
    """AST 에서 imperative route 등록을 찾아 (handler_symbol, "METHOD /path") 반환.

    동일 handler 가 여러 번 등록되면 첫 매치 사용 (decorator 기반과 충돌 시도
    같은 정책 — extract_chunks_from_file 호출자가 endpoint 이미 채워졌으면 skip).
    """
    out: list = []

    def _string_literal(n) -> str:
        # 문자열 리터럴 노드의 따옴표 벗긴 내용. 다양한 파서 노드 타입 대응.
        if n is None:
            return ""
        txt = _node_text(n, source).strip()
        if len(txt) >= 2 and txt[0] in ("'", '"', "`") and txt[-1] == txt[0]:
            return txt[1:-1]
        return ""

    def _last_ident(n) -> str:
        # 인자 노드에서 식별자 텍스트 추출. arrow/anon function 은 빈 문자열.
        if n is None:
            return ""
        txt = _node_text(n, source).strip()
        if not txt or "=>" in txt or "function" in txt.split("(")[0]:
            return ""
        # 식별자 만 (영숫자 + _) 인 경우 그대로
        if txt.replace("_", "").isalnum():
            return txt
        # `module.handler` 같은 경우 마지막 토큰
        if "." in txt:
            tail = txt.rsplit(".", 1)[-1]
            if tail.replace("_", "").isalnum():
                return tail
        return ""

    def walk(n):
        # JS/TS/Flow — call_expression: <fn>(args)
        if lang in ("javascript", "typescript", "tsx") and n.type == "call_expression":
            fn = n.child_by_field_name("function")
            args = n.child_by_field_name("arguments")
            if fn is not None and args is not None:
                fn_txt = _node_text(fn, source)
                # member_expression 만 관심 (`app.get`, `router.post`, ...).
                # `app.use('/x', handler)` 도 포함 — 라우터 마운트.
                if "." in fn_txt:
                    method = fn_txt.rsplit(".", 1)[-1].lower()
                    if method in _IMPERATIVE_HTTP_METHODS:
                        # args 의 children 중 첫 string + 마지막 식별자
                        path = ""
                        last_ident = ""
                        for ch in args.children:
                            if ch.type in ("string", "template_string"):
                                if not path:
                                    path = _string_literal(ch)
                            elif ch.type == "identifier":
                                last_ident = _node_text(ch, source).strip()
                            elif ch.type == "member_expression":
                                ident = _last_ident(ch)
                                if ident:
                                    last_ident = ident
                        if path and last_ident:
                            mtag = method.upper()
                            if mtag in ("ALL", "USE"):
                                mtag = "ANY"
                            out.append((last_ident, f"{mtag} {path}"))
        # Python — call: app.add_url_rule('/x', view_func=handler) or
        #                app.route 를 명령형으로 사용한 경우.
        elif lang == "python" and n.type == "call":
            fn = n.child_by_field_name("function")
            args = n.child_by_field_name("arguments")
            if fn is not None and args is not None:
                fn_txt = _node_text(fn, source)
                if fn_txt.endswith(".add_url_rule"):
                    path = ""
                    handler = ""
                    method_hint = "ANY"
                    for ch in args.children:
                        if ch.type == "string" and not path:
                            path = _string_literal(ch)
                        elif ch.type == "keyword_argument":
                            # view_func=<ident> / methods=[...]
                            kn = ch.child_by_field_name("name")
                            kv = ch.child_by_field_name("value")
                            if kn is not None:
                                key = _node_text(kn, source).strip()
                                if key == "view_func" and kv is not None:
                                    handler = _last_ident(kv)
                                elif key == "methods" and kv is not None:
                                    txt = _node_text(kv, source)
                                    # ['GET', 'POST'] → 첫 메서드만 (간이)
                                    mt = re.search(r"['\"](GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)['\"]", txt, re.IGNORECASE)
                                    if mt:
                                        method_hint = mt.group(1).upper()
                        elif ch.type == "identifier" and not handler:
                            handler = _node_text(ch, source).strip()
                    if path and handler:
                        out.append((handler, f"{method_hint} {path}"))
        for ch in n.children:
            walk(ch)

    try:
        walk(tree.root_node)
    except Exception:
        return out
    return out


# ─ H — docstring 구조화 추출 ──────────────────────────────────────────────
# extract_leading_doc 의 결과 텍스트에서 JSDoc/Sphinx/Google 패턴을 분리.
# @param / @returns / @throws 정도만 우선. 비-구조 텍스트는 doc 필드로 유지.
# desc 부분은 다음 `@` 토큰 또는 줄 바꿈 전까지만 — 그렇지 않으면 다음
# @param/@returns 를 흡수해 잘못된 결과가 나옴.
_PARAM_RE = re.compile(
    r"@param\s+(?:\{([^}]+)\}\s+)?(\w+)\s*[:\-—]?\s*([^\n@]{0,80})",
    re.IGNORECASE,
)
_RETURNS_RE = re.compile(
    r"@returns?\s*(?:\{([^}]+)\})?\s*[:\-—]?\s*([^\n@]{0,80})",
    re.IGNORECASE,
)
_THROWS_RE = re.compile(
    r"@throws?\s+(?:\{([^}]+)\})?\s*([\w.]+)?\s*[:\-—]?\s*([^\n@]{0,80})",
    re.IGNORECASE,
)


# Phase A F7 — Google-style 블록 헤더. 콜론 종결 + 다음 줄에 indented entries.
_GOOGLE_BLOCK_HEADERS = {
    "args": "params", "arguments": "params", "parameters": "params",
    "returns": "returns", "return": "returns", "yields": "returns", "yield": "returns",
    "raises": "throws", "raise": "throws", "throws": "throws",
    "except": "throws", "exceptions": "throws",
}
# Google entry: `name (type): desc` 또는 `name: desc`
_GOOGLE_ENTRY_RE = re.compile(
    r"^\s+(\w+)\s*(?:\(([^)]+)\))?\s*:\s*(.*?)\s*$"
)
# NumPy entry: `name : type` 라인 + indented desc 라인. 헤더는 `Parameters\n----`.
_NUMPY_ENTRY_RE = re.compile(
    r"^\s*(\w+)\s*:\s*([^\n]+?)\s*$"
)


def _parse_google_blocks(raw_lines: list, out: dict) -> None:
    """Google-style block (Args:/Returns:/Raises:) 을 한 패스로 분해해 out 에 누적.

    헤더 라인: `<HEADER>:` 단독 (대소문자 무시)
    엔트리: 헤더 다음 줄부터 헤더 들여쓰기보다 깊은 indented 라인
    빈 줄 또는 새 헤더가 등장하면 블록 종료.
    """
    i = 0
    n = len(raw_lines)
    while i < n:
        line = raw_lines[i]
        stripped = line.strip()
        # 헤더 검출: `Args:` `Returns:` `Raises:` 등 단독
        if stripped.endswith(":") and " " not in stripped[:-1]:
            header_word = stripped[:-1].lower()
            target = _GOOGLE_BLOCK_HEADERS.get(header_word)
            if target is not None:
                header_indent = len(line) - len(line.lstrip())
                i += 1
                # 블록 본문 수집: header_indent 보다 깊고, 빈 줄 아니고, 새 헤더 아닌 라인.
                while i < n:
                    body_line = raw_lines[i]
                    body_stripped = body_line.strip()
                    if not body_stripped:
                        i += 1
                        # 다음 라인이 또 indented 면 같은 블록 (단순화 — 일단 종결)
                        break
                    body_indent = len(body_line) - len(body_line.lstrip())
                    if body_indent <= header_indent:
                        break  # 새 헤더 또는 다른 블록
                    # 새 헤더가 indented 영역에 등장하는 경우 (드물지만 안전)
                    if body_stripped.endswith(":") and " " not in body_stripped[:-1] \
                            and body_stripped[:-1].lower() in _GOOGLE_BLOCK_HEADERS:
                        break
                    if target == "params":
                        m = _GOOGLE_ENTRY_RE.match(body_line)
                        if m:
                            out["params"].append((
                                (m.group(2) or "").strip(),
                                (m.group(1) or "").strip(),
                                (m.group(3) or "").strip()[:80],
                            ))
                    elif target == "returns":
                        # `type: desc` 또는 단순 desc
                        if ":" in body_stripped and not body_stripped.startswith("`"):
                            t, _, d = body_stripped.partition(":")
                            if not out["returns"]:
                                out["returns"] = (t.strip(), d.strip()[:80])
                        elif not out["returns"]:
                            out["returns"] = ("", body_stripped[:80])
                    elif target == "throws":
                        m = _GOOGLE_ENTRY_RE.match(body_line)
                        if m:
                            out["throws"].append((
                                (m.group(2) or "").strip(),
                                (m.group(1) or "").strip(),
                                (m.group(3) or "").strip()[:80],
                            ))
                        elif ":" in body_stripped:
                            t, _, d = body_stripped.partition(":")
                            out["throws"].append((t.strip(), "", d.strip()[:80]))
                    i += 1
                continue
        i += 1


def _parse_numpy_blocks(raw_lines: list, out: dict) -> None:
    """NumPy-style block — 헤더 다음 줄이 `----` underline.

    Parameters
    ----------
    name : type
        description
    """
    i = 0
    n = len(raw_lines)
    while i < n - 1:
        header = raw_lines[i].strip().lower()
        underline = raw_lines[i + 1].strip()
        target = _GOOGLE_BLOCK_HEADERS.get(header)
        if target is not None and underline and set(underline) == {"-"}:
            i += 2
            # 블록 본문: 빈 줄 또는 다음 헤더+underline 패턴 만나기 전까지.
            while i < n:
                line = raw_lines[i]
                stripped = line.strip()
                if not stripped:
                    i += 1
                    # 빈 줄 다음에도 indented 데이터가 이어질 수 있으므로 한 번 더 보고 결정
                    if i < n - 1 and raw_lines[i].strip() and \
                            set(raw_lines[i + 1].strip() or "") == {"-"}:
                        break
                    continue
                # 다음 헤더 검출 (현재 줄이 헤더, 다음 줄이 underline)
                if i + 1 < n and set((raw_lines[i + 1].strip() or "")) == {"-"}:
                    break
                m = _NUMPY_ENTRY_RE.match(line)
                if m and target == "params":
                    name, type_ = m.group(1), m.group(2)
                    # 다음 indented 줄이 desc
                    desc = ""
                    if i + 1 < n:
                        nxt = raw_lines[i + 1]
                        if nxt.strip() and (len(nxt) - len(nxt.lstrip())) > (len(line) - len(line.lstrip())):
                            desc = nxt.strip()[:80]
                            i += 1
                    out["params"].append((type_.strip(), name.strip(), desc))
                elif target == "returns" and not out["returns"]:
                    if ":" in stripped:
                        t, _, d = stripped.partition(":")
                        out["returns"] = (t.strip(), d.strip()[:80])
                    else:
                        out["returns"] = (stripped[:40], "")
                elif target == "throws" and m:
                    name, type_ = m.group(1), m.group(2)
                    out["throws"].append((type_.strip(), name.strip(), ""))
                i += 1
            continue
        i += 1


def parse_docstring_structure(doc_raw: str) -> dict:
    """JSDoc/Sphinx/Google/NumPy 풍 docstring 에서 구조 정보 추출.

    반환: {"params": [(type, name, desc), ...], "returns": (type, desc), "throws": [...] }
    각 list 는 빈 list, returns 는 (None, "") 가능.

    Phase A F7: Google-style (`Args:`/`Returns:`/`Raises:`) + NumPy-style
    (`Parameters\n----`) 추가. 입력은 newline 보존된 raw 텍스트.
    """
    out = {"params": [], "returns": None, "throws": []}
    if not doc_raw:
        return out
    # 1) JSDoc/Sphinx 인라인 패턴 — newline 비의존이라 raw / oneline 모두 OK.
    for m in _PARAM_RE.finditer(doc_raw):
        out["params"].append((
            (m.group(1) or "").strip(),
            (m.group(2) or "").strip(),
            (m.group(3) or "").strip(),
        ))
    rm = _RETURNS_RE.search(doc_raw)
    if rm:
        out["returns"] = ((rm.group(1) or "").strip(), (rm.group(2) or "").strip())
    for m in _THROWS_RE.finditer(doc_raw):
        out["throws"].append((
            (m.group(1) or "").strip(),
            (m.group(2) or "").strip(),
            (m.group(3) or "").strip(),
        ))
    # 2) Google/NumPy block parser — newline 의존. raw 에 줄이 있을 때만 작동.
    if "\n" in doc_raw:
        raw_lines = doc_raw.splitlines()
        _parse_google_blocks(raw_lines, out)
        _parse_numpy_blocks(raw_lines, out)
    return out


# ─ F — import 그래프 추출 ────────────────────────────────────────────────
# 파일별로 import 한 symbol 이름 set 을 수집. pass 2 의 callers 인덱싱이
# "callee 이름이 caller 의 imports 에 있는가" 를 추가 검증해 동명 함수의
# false-positive 매칭 차단.
def collect_imports_for_file(tree, source: bytes, lang: str) -> set:
    """파일에서 import 한 symbol name set 반환. simple 한 형태 (모듈 경로 추적
    안 함, symbol 이름만). 같은 파일 내 호출 + builtin 호출은 import 없이도
    OK 이므로 pass 2 가 fallback 처리.
    """
    names: set = set()

    def walk(node):
        nt = node.type
        if lang == "python":
            if nt == "import_from_statement":
                # from <module> import <names>
                # children: 'from', dotted_name(module), 'import', names...
                for ch in node.children:
                    if ch.type == "dotted_name" or ch.type == "aliased_import":
                        # dotted_name 이 module 또는 이름. 두 번째 이후가 imported names.
                        # 단순: 모든 dotted_name / identifier 의 leaf 를 흡수.
                        for sub in walk_idents(ch):
                            names.add(sub)
                    elif ch.type == "identifier":
                        names.add(source[ch.start_byte:ch.end_byte].decode("utf-8", errors="replace"))
            elif nt == "import_statement":
                # import x, y, z [as alias]
                for ch in node.children:
                    if ch.type in ("dotted_name", "aliased_import"):
                        for sub in walk_idents(ch):
                            names.add(sub)
        elif lang in ("javascript", "typescript", "tsx"):
            if nt == "import_statement":
                # import { a, b } from '...' / import * as x from '...' / import a from '...'
                for ch in node.children:
                    for sub in walk_idents(ch):
                        names.add(sub)
            elif nt == "lexical_declaration":
                # const { x } = require('...')
                txt = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                if "require(" in txt:
                    for sub in walk_idents(node):
                        names.add(sub)
        for ch in node.children:
            walk(ch)

    def walk_idents(n):
        if n.type == "identifier" or n.type == "type_identifier":
            yield source[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
        for ch in n.children:
            yield from walk_idents(ch)

    walk(tree.root_node)
    # 흔한 builtin/keyword 노이즈 제거
    return {n for n in names if n and len(n) >= 2 and n not in {"from", "import", "as"}}


def is_test_location(rel_path: str) -> bool:
    """P1.5 H-1 / P4 확장 — 이 파일이 테스트 디렉토리/파일 규약에 해당하는지.

    P4 추가 패턴:
      - `*-test.js` / `*-spec.js` 처럼 **하이픈 형식** (nodegoat
         `test/security/profile-test.js` 등 e2e/cypress 관행)
      - `_spec.py` (RSpec 풍 Python BDD)
      - `.feature` (Cucumber/Gherkin)
      - `it/`, `integration/`, `unit/`, `functional/` 디렉토리
    """
    p = Path(rel_path)
    parts_lower = {x.lower() for x in p.parts}
    test_dirs = {
        "tests", "test", "__tests__", "spec", "specs",
        "e2e", "cypress", "integration", "unit", "functional", "it",
    }
    if parts_lower & test_dirs:
        return True
    fname = p.name.lower()
    test_suffixes = (
        "_test.py", "_spec.py",
        ".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx",
        ".test.js", ".spec.js", ".test.jsx", ".spec.jsx",
        "-test.js", "-spec.js", "-test.ts", "-spec.ts",
        "_test.go", "_spec.go",
        "test.java", "tests.java",
        ".feature",
    )
    if fname.startswith("test_") or fname.startswith("test-"):
        return True
    return fname.endswith(test_suffixes)


def filename_test_candidates(rel_path: str) -> list:
    """P1.5 H-2 — 테스트 파일명에서 대상 심볼명 후보 추출.

    예: `test/users.js` → ["users", "Users"]
        `user_test.py` → ["user", "User"]
        `LoginHandlerTest.java` → ["LoginHandler", "loginHandler"]
        `auth.spec.ts` → ["auth", "Auth"]
    heuristic 한계: 파일명 하나에 여러 함수가 있을 수 있어 모든 심볼이 이 후보
    들과 매칭되는 것은 아님. 역인덱스 단계에서 심볼 이름과 일치하는 것만 실제
    test_paths 로 연결되므로 false-positive 는 자연히 걸러진다.
    """
    p = Path(rel_path)
    base = p.stem  # extension 제거
    # test/spec 접미/접두 제거
    for suffix in (".test", ".spec", "_test", "-test", "_spec", "-spec",
                   "Test", "Tests"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    for prefix in ("test_", "test-", "Test"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    base = base.strip("._-")
    if not base or not base.replace("_", "").replace("-", "").isalnum():
        return []
    candidates = {base}
    # camelCase / PascalCase 변환
    if base[0].islower():
        candidates.add(base[0].upper() + base[1:])
    else:
        candidates.add(base[0].lower() + base[1:])
    return sorted(candidates)


def guess_test_for(rel_path: str, symbol: str):
    """기존 호환 — single string 반환. 신규 코드는 guess_test_for_candidates 사용."""
    cands = guess_test_for_candidates(rel_path, symbol)
    return cands[0] if cands else None


def guess_test_for_candidates(rel_path: str, symbol: str) -> list:
    """테스트 심볼이 가리키는 대상 심볼 후보 리스트.

    +T1: 단일 후보 ("Login") 만 반환하던 기존 로직이 nodegoat 의 OOP-style
    controller (`LoginHandler`) 와 매칭 못 해 test_paths 인덱스가 비어버리는
    문제 (관측: tests bucket fill 0%). 다중 후보를 반환하고 pass 2 가 prefix
    매칭까지 시도하면 `Login` → `LoginHandler` 같은 자연스러운 매칭이 살아남음.

    반환 후보 우선순위:
      1) symbol 자체가 test_X / testX 컨벤션이면 그 X 를 1순위
      2) 파일명 기반 후보 (`signup_spec.js` → `signup`, `Signup`)
      3) symbol body 에서 자주 호출되는 식별자 — 본 함수에선 미구현 (pass 2
         가 prefix 매칭으로 대신 커버)

    일반 코드면 [] (fast path).
    """
    if not is_test_location(rel_path):
        return []
    cands: list = []

    # 1) symbol 컨벤션 매칭
    if symbol.startswith("test_"):
        cand = symbol[5:]
        if cand:
            cands.append(cand)
            # camelCase 변형 — `test_login_handler` → `loginHandler` / `LoginHandler`
            if "_" in cand:
                parts = cand.split("_")
                camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
                pascal = "".join(p.capitalize() for p in parts)
                cands.append(camel)
                cands.append(pascal)
    elif symbol.startswith("test"):
        rest = symbol[4:]
        if rest and rest[0].isupper():
            cands.append(rest[0].lower() + rest[1:])
            cands.append(rest)  # PascalCase 그대로

    # 2) 파일명 기반 후보 — JS/TS e2e 의 익명 / cypress command 패턴 커버
    cands.extend(filename_test_candidates(rel_path))

    # dedup, 빈 문자열 제거, 안정 정렬 (입력 순)
    seen = set()
    out = []
    for c in cands:
        c = (c or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# +T1 — pass 2 test_paths 인덱싱 시 사용할 prefix 매칭 임계.
# 4자 미만이면 false-positive 가 너무 많음 (`get` 이 `getUser`, `getById` 등
# 모두 매칭). 4자 이상에서만 prefix 매칭 허용 — 정확 매칭은 길이 제한 없음.
_TEST_FOR_PREFIX_MIN_LEN = 4


def path_to_safe_filename(rel_path: str) -> str:
    """레포 상대경로 → 파일시스템 안전한 단일 파일명 (슬래시 → __)."""
    return rel_path.replace(os.sep, "__").replace("/", "__")


def extract_chunks_from_file(file_path: Path, repo_root: Path, commit_sha: str):
    """한 파일의 함수/클래스 청크 리스트 반환. 비지원/파싱실패는 [] 반환.

    F: collect_imports_for_file 결과를 _file_imports 키로 보존 (호출자가 활용).
    D/C/H: 각 청크에 decorators / endpoint / doc_params / doc_returns 박음.
    """
    ext = file_path.suffix.lower()
    cfg = LANG_CONFIG.get(ext)
    if cfg is None:
        return []

    try:
        source = file_path.read_bytes()
    except Exception:
        return []

    try:
        lang = get_language(cfg["lang"])
        parser = get_parser(cfg["lang"])
    except Exception:
        # 특정 언어 파서 로드 실패 시 빈 결과 (레포 전체는 계속 진행)
        return []

    try:
        tree = parser.parse(source)
    except Exception:
        return []

    rel_path = str(file_path.relative_to(repo_root))

    # F — 파일 단위 import 정보. extract_chunks_from_file 호출자가 chunks_by_path
    # 와 별도로 imports_by_path 를 유지하기 위해, 첫 청크의 _file_imports 에
    # set 을 prefix 로 보존한다. (한 파일은 동일 imports 를 공유하므로 set 1 회 계산)
    try:
        file_imports = collect_imports_for_file(tree, source, cfg["lang"])
    except Exception:
        file_imports = set()

    chunks = []
    for node, kind in walk_symbols(tree.root_node, cfg["node_types"]):
        symbol = get_symbol_name(node, cfg["name_field"])
        if not symbol:
            continue

        code = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        # 과도하게 큰 심볼 (예: 1000라인 God class) 은 상한 절단
        if len(code) > 30000:
            code = code[:30000] + "\n# ... [truncated]\n"

        test_for_cands = guess_test_for_candidates(rel_path, symbol)
        test_for = test_for_cands[0] if test_for_cands else None
        # P2 K-4 — leading docstring/comment 추출 (실패해도 빈 문자열로 안전).
        # Phase A F7: raw (newline 보존) 와 oneline (200자 cap) 두 형태로 추출.
        # raw 는 Google/NumPy block 파싱에, oneline 은 footer 노출에 사용.
        try:
            doc_raw = _extract_leading_doc_text(node, cfg["lang"], source)
        except Exception:
            doc_raw = ""
        doc = _collapse_doc_to_oneline(doc_raw) if doc_raw else ""
        # D — decorators
        try:
            decorators = extract_decorators(node, cfg["lang"], source)
        except Exception:
            decorators = []
        # C — endpoint (decorator 기반)
        try:
            endpoint = parse_route_endpoint(decorators)
        except Exception:
            endpoint = ""
        # H — docstring 구조화 (params / returns / throws). raw 텍스트 전달.
        try:
            doc_struct = parse_docstring_structure(doc_raw)
        except Exception:
            doc_struct = {"params": [], "returns": None, "throws": []}

        chunks.append({
            "path": rel_path,
            "symbol": symbol,
            "kind": kind,
            "lang": cfg["lang"],
            "lines": f"{node.start_point[0] + 1}-{node.end_point[0] + 1}",
            "code": code,
            "commit_sha": commit_sha,
            "callers": [],          # pass 2 에서 채움
            "callees": collect_callees(node, cfg["lang"]),
            "test_paths": [],       # pass 2 에서 채움 (대상 심볼 기준)
            "is_test": is_test_location(rel_path),
            "test_for": test_for,
            # +T1: pass 2 가 prefix 매칭에 사용. 직렬화는 안 함 (run-time only).
            "test_for_candidates": test_for_cands,
            "doc": doc,             # P2 K-4 — leading docstring/comment (200자 cap)
            # D / C / H — tree-sitter 강화 메타
            "decorators": decorators,        # D — ["@app.route('/x')", ...]
            "endpoint": endpoint,            # C — "GET /x" 또는 ""
            "doc_params": doc_struct["params"],    # H — list of (type, name, desc)
            "doc_returns": doc_struct["returns"],  # H — (type, desc) | None
            "doc_throws": doc_struct["throws"],    # H — list of (type, name, desc)
            # F — 파일 단위 imports (run-time only, JSONL 직렬화는 doc_processor 에서 무시)
            "_file_imports": list(file_imports),
        })

    # Phase A F6 + Phase D Fix B — imperative route 패스.
    # nodegoat 같은 일반적 Express 구조에서 라우트 등록 (`app/routes/index.js`) 과
    # 핸들러 정의 (`app/routes/session.js::SessionHandler.displayLoginPage`) 가
    # 분리된 파일에 있다. file-local 매칭만으로는 항상 0건이 되어 endpoint
    # 추출이 실패하는 게 사이클 3 의 결정적 누수였다. 이제 file-local 매칭은
    # 즉시 적용 (decorator 미사용 + 같은 파일 내 핸들러), 외부 파일 매칭용으로
    # 모든 imperative_pairs 를 첫 청크의 _imperative_pairs 키에 보존하고
    # scan_repo 가 pass 2 에서 cross-file 매핑을 수행한다.
    try:
        imperative_pairs = extract_imperative_routes(tree, source, cfg["lang"])
    except Exception:
        imperative_pairs = []
    if imperative_pairs:
        # 1) file-local 즉시 매칭 (기존 동작 보존)
        sym_to_idx: dict = {}
        for i, c in enumerate(chunks):
            if not c.get("endpoint"):
                sym_to_idx.setdefault(c.get("symbol", ""), i)
        for handler, endpoint in imperative_pairs:
            i = sym_to_idx.get(handler)
            if i is not None and not chunks[i].get("endpoint"):
                chunks[i]["endpoint"] = endpoint
        # 2) 전체 pairs 를 첫 청크에 첨부 (run-time only, JSONL 직렬화는 scan_repo 에서 제거).
        # 청크가 0 인 파일 (예: routes/index.js — 외부 핸들러만 등록)도 정보를
        # 보존하기 위해 첫 청크가 없으면 빈 stub chunk 를 만들지 않고 별도 sentinel.
        # 단순화: chunks 에 메타만 보존하는 sentinel chunk 1개 추가 (lines='0-0',
        # is_routing_only=True 표시) — pass 2 가 cross-file 매칭에 사용 후 JSONL
        # 직렬화 직전에 _is_routing_only=True chunk 는 제외.
        if chunks:
            chunks[0]["_imperative_pairs"] = list(imperative_pairs)
        else:
            chunks.append({
                "path": rel_path, "symbol": "", "kind": "routing_only",
                "lang": cfg["lang"], "lines": "0-0", "code": "",
                "commit_sha": commit_sha,
                "callers": [], "callees": [], "test_paths": [],
                "is_test": False, "test_for": None,
                "test_for_candidates": [],
                "doc": "",
                "decorators": [], "endpoint": "",
                "doc_params": [], "doc_returns": None, "doc_throws": [],
                "_file_imports": list(file_imports),
                "_imperative_pairs": list(imperative_pairs),
                "_is_routing_only": True,
            })

    return chunks


# ─ 레포 요약 (사람이 읽는 MD, P1 Stage 2 가 Sonar 기반 인간 리뷰에 유용) ──────
def safe_read_text(path: Path, max_bytes: int) -> str:
    try:
        return path.read_bytes()[:max_bytes].decode("utf-8", errors="ignore")
    except Exception:
        return ""


def build_tree(repo_root: Path, max_lines: int = 3000) -> str:
    lines = []
    count = 0
    for root, dirs, files in os.walk(repo_root):
        rel_root = Path(root).relative_to(repo_root)
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        depth = len(rel_root.parts)
        indent = "  " * depth
        if str(rel_root) == ".":
            lines.append(f"{repo_root.name}/")
        else:
            lines.append(f"{indent}{rel_root.name}/")
        count += 1
        if count >= max_lines:
            lines.append("[TRUNCATED] tree lines limit reached")
            break
        for f in sorted(files):
            if f == ".DS_Store":
                continue
            lines.append(f"{indent}  {f}")
            count += 1
            if count >= max_lines:
                lines.append("[TRUNCATED] tree lines limit reached")
                break
        if count >= max_lines:
            break
    return "\n".join(lines) + "\n"


def write_repo_summary(repo_root: Path, out_dir: Path, max_key_bytes: int = 30000):
    """사람이 읽는 _repo_summary.md 생성 (기존 context_<repo>.md 역할)."""
    parts = ["# Repository Summary", ""]
    parts.append("## Tree")
    parts.append("")
    parts.append("```text")
    parts.append(build_tree(repo_root))
    parts.append("```")
    parts.append("")

    parts.append("## Key Files")
    parts.append("")
    for k in KEY_FILES:
        p = repo_root / k
        if not p.exists():
            continue
        parts.append(f"### {k}")
        parts.append("")
        parts.append("```")
        parts.append(safe_read_text(p, max_key_bytes))
        parts.append("```")
        parts.append("")

    out = out_dir / "_repo_summary.md"
    out.write_text("\n".join(parts), encoding="utf-8")


# ─ 메인 ─────────────────────────────────────────────────────────────────────
def resolve_commit_sha(repo_root: Path, explicit: str) -> str:
    if explicit:
        return explicit
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return ""


# ─ pass 2: callers / test_paths 역인덱스 ──────────────────────────────────
# 대규모 유틸 심볼 (예: "log", "get") 은 callers/test_paths 배열이 수백 개로 부풀어
# footer 가 과대해진다. 아래 상한으로 잘라 metadata 혼잡을 막는다. 필요 시 환경변수로
# 재정의 가능.
MAX_CALLERS_PER_CHUNK = int(os.environ.get("REPO_CTX_MAX_CALLERS", "20"))
MAX_TEST_PATHS_PER_CHUNK = int(os.environ.get("REPO_CTX_MAX_TEST_PATHS", "5"))

# P3 — callers 역인덱스 노이즈 필터.
# Sonar 이슈 분석에서 callers 버킷이 10% 만 채워지는 원인 분석:
# `log`, `get`, `init` 같은 짧은 / 일반적 심볼이 다수 caller 청크의
# callees 에 출현 → 역인덱스가 부풀려져 진짜 의미있는 caller 청크의
# retrieval ranking 이 묻힌다. 짧은 심볼 + builtin/generic 화이트리스트
# 제외 후 인덱싱하면 caller 시그널이 정직하게 surfacing 된다.
_GENERIC_CALLEE_NAMES = {
    # Python builtins / common
    "len", "range", "list", "dict", "set", "tuple", "str", "int", "float",
    "bool", "type", "isinstance", "iter", "next", "callable", "open", "close",
    "read", "write", "send", "min", "max", "sum", "abs", "round", "all", "any",
    "print", "input", "format", "repr", "vars", "dir", "id", "hash", "hex",
    "bin", "oct", "ord", "chr", "bytes", "bytearray", "memoryview", "object",
    "getattr", "setattr", "hasattr", "delattr", "issubclass", "super",
    "enumerate", "zip", "map", "filter", "reduce", "sorted", "reversed",
    "log", "logger", "logging",
    # JS/TS common methods
    "console", "push", "pop", "shift", "unshift", "splice", "slice", "concat",
    "join", "split", "trim", "replace", "match", "search", "test", "exec",
    "indexOf", "lastIndexOf", "includes", "find", "findIndex", "every", "some",
    "forEach", "keys", "values", "entries", "assign", "create", "freeze",
    "stringify", "parse", "now", "Date", "Math", "JSON",
    "addEventListener", "removeEventListener", "dispatchEvent",
    # Common method names
    "init", "main", "run", "start", "stop", "reset", "update", "load", "save",
    "check", "validate", "parse", "format", "render", "build", "create",
    "get", "set", "has", "put", "add", "remove", "delete", "clear",
    # Sonar/regex 룰 메시지에 자주 나오는 식별자 (false-positive 매칭 차단)
    "parseInt", "parseFloat", "isNaN", "isFinite", "Number", "String", "Boolean",
}
_MIN_CALLEE_LEN = 4  # 4자 미만은 정보량 낮음 — 인덱스에서 제외


def _is_useful_callee_name(name: str) -> bool:
    if not name:
        return False
    if len(name) < _MIN_CALLEE_LEN:
        return False
    if name in _GENERIC_CALLEE_NAMES:
        return False
    return True


def build_reverse_indexes(chunks_by_path: dict) -> None:
    """in-place 로 각 청크의 `callers`, `test_paths` 를 채운다.

    chunks_by_path: { rel_path: [chunk, ...] } — extract_chunks_from_file 결과 모음.

    전략:
      - symbol_index: symbol_name → [(path, idx), ...]  (동명 심볼 여러 곳 정의 허용)
      - 각 caller 청크의 callees 를 순회해 target 청크마다 "caller_path#caller_symbol"
        문자열을 push. 동명 심볼에 대해 precision 없음 (recall 우선).
      - test_for 가 설정된 청크는 대상 심볼의 모든 후보에 "test_path#test_symbol" push.
    """
    symbol_index: dict = {}
    for path, chunks in chunks_by_path.items():
        for idx, ch in enumerate(chunks):
            symbol_index.setdefault(ch["symbol"], []).append((path, idx))

    # F — 파일 단위 imports 맵. 같은 파일 chunks 는 동일 imports 공유 (첫 청크에서 추출).
    imports_by_path: dict = {}
    for path, chunks in chunks_by_path.items():
        if chunks:
            imports_by_path[path] = set(chunks[0].get("_file_imports") or [])

    # callees → callers 역링크
    # P3 — generic / 짧은 callee 이름은 false-positive 가 다수라 인덱스에서
    # 제외. 진짜 의미있는 caller 시그널만 살아남도록.
    # F — caller 가 callee symbol 을 import 했는지 (또는 같은 파일인지) 추가 검증.
    # 동명 함수가 여러 파일에 있을 때 import 안 한 파일과의 false-positive 매칭 차단.
    for caller_path, caller_chunks in chunks_by_path.items():
        caller_imports = imports_by_path.get(caller_path, set())
        for caller in caller_chunks:
            caller_ref = f"{caller_path}#{caller['symbol']}"
            for callee_name in caller["callees"]:
                if not _is_useful_callee_name(callee_name):
                    continue
                candidates = symbol_index.get(callee_name, [])
                if not candidates:
                    continue
                # F — 후보 필터링.
                #   가능 case:
                #     1) 동일 파일 내 호출 → import 없이 OK (자기 모듈)
                #     2) 다른 파일 호출 → caller 가 callee_name 을 import 했어야 함
                #   imports 정보가 없으면 (수집 실패) 보수적으로 모두 통과 (이전 동작 유지).
                same_file = [(p, i) for (p, i) in candidates if p == caller_path]
                cross_file = [(p, i) for (p, i) in candidates if p != caller_path]
                accepted = list(same_file)
                if cross_file:
                    if caller_imports and callee_name in caller_imports:
                        accepted.extend(cross_file)
                    elif not caller_imports:
                        # imports 미수집 → 기존 동작 (모두 채택)
                        accepted.extend(cross_file)
                    # else: 명시적으로 import 안 했으면 cross-file 전부 차단
                for target_path, target_idx in accepted:
                    tgt = chunks_by_path[target_path][target_idx]
                    tgt["callers"].append(caller_ref)

    # test_for → test_paths 역링크
    # +T1: test_for_candidates 다중 후보 + prefix 매칭으로 nodegoat 같은 OOP
    # 컨벤션 (LoginHandler, ProfileHandler 등) 도 e2e 테스트와 연결. 단순
    # exact match 만으로는 `Login` 후보가 `LoginHandler` 청크와 안 매칭됨.
    for test_path, test_chunks in chunks_by_path.items():
        for test_ch in test_chunks:
            cands = test_ch.get("test_for_candidates") or []
            if not cands:
                # 하위 호환 — 기존 단일 test_for 도 후보로 흡수
                tf = test_ch.get("test_for")
                if tf:
                    cands = [tf]
            if not cands:
                continue
            test_ref = f"{test_path}#{test_ch['symbol']}"
            seen_targets = set()
            for cand in cands:
                # exact 매칭 (모든 길이)
                for target_path, target_idx in symbol_index.get(cand, []):
                    key = (target_path, target_idx)
                    if key in seen_targets:
                        continue
                    seen_targets.add(key)
                    chunks_by_path[target_path][target_idx]["test_paths"].append(test_ref)
                # prefix 매칭 — 후보가 4자 이상이고 indexed symbol 의 prefix 일 때만
                if len(cand) >= _TEST_FOR_PREFIX_MIN_LEN:
                    for indexed_sym, hits in symbol_index.items():
                        if indexed_sym == cand:
                            continue  # 위에서 이미 처리
                        # PascalCase prefix: cand="Login" → "LoginHandler" 매칭
                        # 단 cand 가 indexed_sym 의 정확한 prefix 여야 함 ("Log"
                        # 가 "LogEvent" 와 false-positive 매칭되는 것은 cand
                        # 길이 ≥ 4 로 어느 정도 차단)
                        if not indexed_sym.startswith(cand):
                            continue
                        # 추가 가드: prefix 다음 문자가 [A-Z_] 여야 단어 경계
                        # 의미. (login → loginHandler 의 `H` 가 대문자 OK)
                        next_ch = indexed_sym[len(cand):len(cand)+1]
                        if next_ch and not (next_ch.isupper() or next_ch == "_"):
                            continue
                        for target_path, target_idx in hits:
                            key = (target_path, target_idx)
                            if key in seen_targets:
                                continue
                            seen_targets.add(key)
                            chunks_by_path[target_path][target_idx]["test_paths"].append(test_ref)

    # dedup + 정렬 + 상한 적용
    for chunks in chunks_by_path.values():
        for ch in chunks:
            ch["callers"] = sorted(set(ch["callers"]))[:MAX_CALLERS_PER_CHUNK]
            ch["test_paths"] = sorted(set(ch["test_paths"]))[:MAX_TEST_PATHS_PER_CHUNK]


def scan_repo(repo_root: Path, out_dir: Path, commit_sha: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    # 기존 JSONL/summary 삭제 (재실행 시 잔재 방지)
    for old in out_dir.glob("*.jsonl"):
        try:
            old.unlink()
        except Exception:
            pass
    for old in out_dir.glob("_repo_summary.md"):
        try:
            old.unlink()
        except Exception:
            pass
    # Phase A — 사이드카 통계 파일도 갱신해야 stale 가 안 남음.
    sidecar_path = out_dir / "_kb_intelligence.json"
    if sidecar_path.exists():
        try:
            sidecar_path.unlink()
        except Exception:
            pass

    # pass 1 — 파일별 청크 수집 (메모리 보유, pass 2 역인덱스 구축을 위함).
    # 중대형 레포도 청크 수는 수천~수만 수준, JSON 한 줄 1~5KB → 10~50MB 로 메모리 안전.
    chunks_by_path: dict = {}
    skipped_trivial = 0  # K-2 통계
    skipped_dup = 0      # K-3 통계
    skipped_minified = 0  # K-1 통계 (파일 수준)
    # Phase A — 신규 통계: 사전학습 진단 리포트 4-stage 데이터 소스.
    parser_failed = 0          # 지원 확장자인데 청크 0개 (parse 실패 또는 빈 파일)
    parser_failed_files: list = []  # Phase D Fix C — 어떤 파일이 실패했는지 (cap 50)
    skipped_excluded_dirs = 0   # vendor/min 등 EXCLUDE_DIRS 경로
    skipped_unsupported_ext = 0 # LANG_CONFIG 에 없는 확장자
    files_seen_total = 0        # 모든 일반 파일 (디렉토리 제외)
    # Phase D Fix B — cross-file imperative route 매핑.
    # routing_only 파일들 (예: routes/index.js — 외부 핸들러만 등록) 의 pairs 를
    # 모은 후 pass 2 가 모든 chunk symbol 인덱스 대비 매칭.
    cross_file_pairs: list = []  # [(handler_name, "METHOD /path"), ...]
    seen_body_hashes: set = set()  # K-3 전역 중복 감지 — (symbol, body_hash) 키
    import hashlib
    for file_path in sorted(repo_root.rglob("*")):
        if not file_path.is_file():
            continue
        files_seen_total += 1
        if any(part in EXCLUDE_DIRS for part in file_path.parts):
            skipped_excluded_dirs += 1
            continue
        if file_path.suffix.lower() not in LANG_CONFIG:
            skipped_unsupported_ext += 1
            continue
        # K-1 파일명 기반 minified 제외
        if MINIFIED_FILE_PATTERNS.search(file_path.name):
            skipped_minified += 1
            continue

        rel_path_str = str(file_path.relative_to(repo_root))
        try:
            raw_chunks = extract_chunks_from_file(file_path, repo_root, commit_sha)
        except Exception as e:
            print(f"[skip:{rel_path_str}] {e}")
            parser_failed += 1
            if len(parser_failed_files) < 50:
                parser_failed_files.append(rel_path_str)
            continue
        if not raw_chunks:
            # 지원 확장자였는데 청크 0 → 파서 실패 또는 함수/클래스 정의 부재.
            parser_failed += 1
            if len(parser_failed_files) < 50:
                parser_failed_files.append(rel_path_str)
            continue

        # Phase D Fix B — routing_only 파일은 chunk 목록에서 제외하되 imperative
        # pairs 는 보존해 cross-file 매핑에 사용. 그 외 파일도 pairs 가 있으면
        # cross-file 후보로 흡수 (file-local 매칭은 이미 끝남, 다른 파일의
        # 핸들러도 매칭될 수 있게).
        kept = []
        for ch in raw_chunks:
            pairs = ch.get("_imperative_pairs") or []
            if pairs:
                cross_file_pairs.extend(pairs)
            if ch.get("_is_routing_only"):
                # sentinel — JSONL 로 직렬화하지 않음
                continue
            if is_trivial_chunk(ch["code"]):
                skipped_trivial += 1
                continue
            body_hash = hashlib.sha1(ch["code"].encode("utf-8", errors="replace")).hexdigest()
            key = (ch["symbol"], body_hash)
            if key in seen_body_hashes:
                skipped_dup += 1
                continue
            seen_body_hashes.add(key)
            kept.append(ch)
        if not kept:
            # 청크가 routing_only 만 있던 파일 — parser_failed 로 분류하지 않음
            # (의도적으로 chunk 가 없는 게 정상). 다만 routing pairs 는 이미 흡수됨.
            continue

        chunks_by_path[rel_path_str] = kept

    # Phase D Fix B — pass 1.5: cross-file imperative route 매핑.
    # 모든 청크 symbol 인덱스를 만들고 handler 이름과 일치하는 청크에 endpoint
    # 부여. 동명 handler 가 여러 파일에 있으면 모두 endpoint 받음 (recall 우선).
    if cross_file_pairs:
        sym_index: dict = {}  # symbol → [(path, idx), ...]
        for path, chunks in chunks_by_path.items():
            for i, ch in enumerate(chunks):
                sym = ch.get("symbol", "")
                if sym:
                    sym_index.setdefault(sym, []).append((path, i))
        applied = 0
        for handler, endpoint in cross_file_pairs:
            for path, i in sym_index.get(handler, []):
                if not chunks_by_path[path][i].get("endpoint"):
                    chunks_by_path[path][i]["endpoint"] = endpoint
                    applied += 1
        if applied:
            print(f"[repo_context_builder] cross-file imperative routes 매핑: {applied} 청크")

    # pass 2 — callers / test_paths 역인덱스 채움
    build_reverse_indexes(chunks_by_path)

    # 기록
    total_files = 0
    total_chunks = 0
    total_callers_links = 0
    total_test_links = 0
    # Phase A — 사전학습 진단 리포트용 인텔리전스 통계 누적.
    from collections import Counter as _Counter
    lang_chunks = _Counter()         # 언어별 청크 수 (Stage 1 시각화)
    lang_callers_links = _Counter()  # 언어별 callers 역링크 수 (Stage 3 비대칭 진단)
    kind_breakdown = _Counter()      # function/class/method/type 분포 (Stage 2)
    test_chunks_count = 0            # is_test 청크 수
    test_linked_count = 0            # test_paths 가 채워진 비-test 청크 수
    decorators_count = 0             # decorators 보유 청크
    endpoints_count = 0              # endpoint 보유 청크
    endpoints_examples = []          # PM 노출용 (top 5)
    type_chunks_count = 0            # type/enum/interface 청크
    docstring_count = 0              # doc 비지 않은 청크
    doc_struct_count = 0             # doc_params/returns/throws 중 하나라도 채워진 청크
    callees_present_count = 0        # callees 1개 이상 보유 청크 (Stage 2 호출관계 풍부도)
    sym_lengths = []                 # 함수 라인 수 분포 (보조 통계)
    # JSONL 직렬화 시 run-time only 필드 제거 (KB upload 에 의미 없음 + 용량 절약)
    _RUNTIME_ONLY_KEYS = (
        "_file_imports", "test_for_candidates",
        "_imperative_pairs", "_is_routing_only",  # Phase D Fix B
    )
    for rel_path, chunks in chunks_by_path.items():
        safe_name = path_to_safe_filename(rel_path)
        out_file = out_dir / f"{safe_name}.jsonl"
        with out_file.open("w", encoding="utf-8") as fh:
            for ch in chunks:
                total_callers_links += len(ch["callers"])
                total_test_links += len(ch["test_paths"])
                lang = ch.get("lang") or "?"
                lang_chunks[lang] += 1
                if ch.get("callers"):
                    lang_callers_links[lang] += len(ch["callers"])
                kind = ch.get("kind") or "?"
                kind_breakdown[kind] += 1
                if kind in ("type", "enum", "interface"):
                    type_chunks_count += 1
                if ch.get("is_test"):
                    test_chunks_count += 1
                elif ch.get("test_paths"):
                    test_linked_count += 1
                if ch.get("decorators"):
                    decorators_count += 1
                ep = (ch.get("endpoint") or "").strip()
                if ep:
                    endpoints_count += 1
                    if len(endpoints_examples) < 5:
                        endpoints_examples.append({
                            "endpoint": ep,
                            "path": ch.get("path", "?"),
                            "symbol": ch.get("symbol", "?"),
                        })
                if (ch.get("doc") or "").strip():
                    docstring_count += 1
                if (ch.get("doc_params") or ch.get("doc_returns") or ch.get("doc_throws")):
                    doc_struct_count += 1
                if ch.get("callees"):
                    callees_present_count += 1
                # 함수 라인 수 — "lines": "a-b" 형식
                lines_str = ch.get("lines") or ""
                if "-" in lines_str:
                    a, _, b = lines_str.partition("-")
                    try:
                        sym_lengths.append(max(1, int(b) - int(a) + 1))
                    except Exception:
                        pass
                # 직렬화 직전에 run-time only 필드 제거
                serializable = {k: v for k, v in ch.items() if k not in _RUNTIME_ONLY_KEYS}
                fh.write(json.dumps(serializable, ensure_ascii=False) + "\n")
        total_files += 1
        total_chunks += len(chunks)

    # 사람이 읽는 요약 MD 병행 생성
    write_repo_summary(repo_root, out_dir)

    # Phase A — 사전학습 인텔리전스 사이드카. 진단 리포트 (Phase C) 가
    # JSONL 재스캔 대신 이 파일을 우선 읽어 4-stage narrative 데이터 소스로 사용.
    avg_func_lines = (sum(sym_lengths) / len(sym_lengths)) if sym_lengths else 0.0
    # 비-test 청크 중 test_paths 매칭 비율 (테스트 연결률).
    non_test_chunks = total_chunks - test_chunks_count
    test_link_rate = (test_linked_count / non_test_chunks * 100.0) if non_test_chunks else 0.0
    # callees 보유 비율 — 호출 관계 그래프 풍부도 신호.
    callees_present_rate = (callees_present_count / total_chunks * 100.0) if total_chunks else 0.0
    # 언어별 callers 비대칭 — 청크는 있는데 callers 가 0 인 lang 식별.
    lang_callers_asymmetry = []
    for lang, n_chunks in lang_chunks.most_common():
        n_callers = lang_callers_links.get(lang, 0)
        # 청크 ≥ 5 인데 callers 0 인 lang 만 경고. 작은 샘플은 신뢰도 낮음.
        if n_chunks >= 5 and n_callers == 0:
            lang_callers_asymmetry.append({"lang": lang, "chunks": n_chunks, "callers_links": 0})

    intelligence = {
        "schema_version": 1,
        "commit_sha": commit_sha,
        "scope": {
            "files_seen_total": files_seen_total,
            "files_analyzed": total_files,
            "skipped_excluded_dirs": skipped_excluded_dirs,
            "skipped_unsupported_ext": skipped_unsupported_ext,
            "skipped_minified_files": skipped_minified,
            "parser_failed": parser_failed,
            # Phase D Fix C — 어떤 파일이 실패했는지 진단 가시화. cap 50.
            "parser_failed_files": parser_failed_files,
            "skipped_trivial_chunks": skipped_trivial,
            "skipped_duplicate_chunks": skipped_dup,
        },
        "depth": {
            "total_chunks": total_chunks,
            "lang_breakdown": dict(lang_chunks),
            "kind_breakdown": dict(kind_breakdown),
            "callees_present_count": callees_present_count,
            "callees_present_rate_pct": callees_present_rate,
            "callers_links_total": total_callers_links,
            "endpoints_count": endpoints_count,
            "endpoints_examples": endpoints_examples,
            "decorators_count": decorators_count,
            "type_chunks_count": type_chunks_count,
            "test_chunks_count": test_chunks_count,
            "test_links_total": total_test_links,
            "test_linked_non_test_count": test_linked_count,
            "test_link_rate_pct": test_link_rate,
            "docstring_count": docstring_count,
            "doc_struct_count": doc_struct_count,
            "avg_func_lines": avg_func_lines,
        },
        "quality": {
            "lang_callers_asymmetry": lang_callers_asymmetry,
            # parser 성공률 (전체 지원 확장자 파일 중 청크가 1개라도 추출된 비율)
            "parser_success_rate_pct": (
                total_files * 100.0 / (total_files + parser_failed)
            ) if (total_files + parser_failed) else 0.0,
        },
    }
    try:
        sidecar_path.write_text(
            json.dumps(intelligence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[repo_context_builder] KB intelligence sidecar → {sidecar_path}")
    except Exception as e:
        print(f"[repo_context_builder] sidecar write 실패: {e}")

    # 통계 딕셔너리 — HTML 리포트 생성에 사용.
    stats = {
        "total_files": total_files,
        "total_chunks": total_chunks,
        "total_callers_links": total_callers_links,
        "total_test_links": total_test_links,
        # K-1/K-2/K-3 통계 — 리포트에서 품질 필터 효과 가시화
        "skipped_minified_files": skipped_minified,
        "skipped_trivial_chunks": skipped_trivial,
        "skipped_duplicate_chunks": skipped_dup,
        # Phase A 신규
        "parser_failed": parser_failed,
        "files_seen_total": files_seen_total,
        "intelligence": intelligence,
    }

    print(
        f"[repo_context_builder] files={total_files} chunks={total_chunks} "
        f"callers_links={total_callers_links} test_links={total_test_links} "
        f"skipped(minified_files={skipped_minified}, trivial={skipped_trivial}, "
        f"dup={skipped_dup}, parser_failed={parser_failed}) "
        f"commit={commit_sha[:8] or 'n/a'} → {out_dir}"
    )
    return total_chunks, stats, chunks_by_path


# ─ HTML 리포트 생성 ────────────────────────────────────────────────────────
# Jenkins publishHTML 로 "Pre-training Report" 탭에 노출. zero_touch_qa 의
# test report 와 같은 UX 로 사전학습 결과를 사람이 바로 탐색할 수 있게 함.
# self-contained: inline CSS, 외부 JS/폰트 의존 없음 (Jenkins CSP 친화).
import html as _html


def _html_escape(s: str) -> str:
    return _html.escape(s or "", quote=True)


# ─ A1~A6: PM 친화 리포트용 데이터 추출 함수 ─────────────────────────────────
# 모두 LLM/네트워크 호출 없음 — 청크 JSONL + repo metadata 만으로 동작.

def extract_project_overview(repo_root: Path) -> dict:
    """README 첫 단락 + LICENSE + build tool + description.

    PM 이 "이 프로젝트가 뭐 하는 프로젝트인가" 를 첫 5초에 파악하는 입력.
    여러 메타 소스 중 가장 풍부한 것 우선 — README description > build.gradle > pom.xml > package.json.
    """
    out = {"summary": "", "license": "", "build_tool": "", "language_primary": "", "description": ""}

    for readme_name in ("README.md", "README.txt", "README.rst", "README"):
        p = repo_root / readme_name
        if p.is_file():
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            paras = [pa.strip() for pa in txt.split("\n\n") if pa.strip()]
            # 자연어 단어 수 기반 휴리스틱 — markdown link/image/URL/HTML tag 모두
            # strip 한 후 알파벳 단어 (≥3자) 5개 이상이면 진짜 설명 paragraph.
            # nested badge `[![label](url)](link)` 도 greedy 매칭으로 제거.
            for pa in paras:
                # markdown 헤더 (`#`), blockquote (`>`), whitespace 를 안정될 때까지 반복 제거.
                # 단순 lstrip("#>") 만으로는 "> ### ..." 같은 중첩 prefix 처리 불가.
                clean = pa
                for _ in range(8):
                    new = clean.strip().lstrip(">").lstrip("#").strip()
                    if new == clean:
                        break
                    clean = new
                stripped = _re.sub(r"!?\[.*?\]\([^)]*\)", "", clean)  # markdown link/image (nested OK)
                stripped = _re.sub(r"https?://\S+", "", stripped)
                stripped = _re.sub(r"<[^>]+>", "", stripped)
                words = [w for w in _re.findall(r"[A-Za-z가-힣]+", stripped) if len(w) >= 3]
                if len(words) < 5:
                    continue
                out["summary"] = clean[:250] + ("..." if len(clean) > 250 else "")
                break
            break

    lic_path = repo_root / "LICENSE"
    if lic_path.is_file():
        try:
            for ln in lic_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                ln = ln.strip()
                if ln:
                    out["license"] = ln[:80]
                    break
        except Exception:
            pass

    if (repo_root / "build.gradle").is_file() or (repo_root / "build.gradle.kts").is_file():
        out["build_tool"] = "gradle"
        for f in ("build.gradle", "build.gradle.kts"):
            p = repo_root / f
            if p.is_file():
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                    m = _re.search(r"description\s*=\s*['\"](.+?)['\"]", txt)
                    if m:
                        out["description"] = m.group(1)[:200]
                        break
                except Exception:
                    pass
    elif (repo_root / "pom.xml").is_file():
        out["build_tool"] = "maven"
        try:
            txt = (repo_root / "pom.xml").read_text(encoding="utf-8", errors="ignore")
            m = _re.search(r"<description>(.+?)</description>", txt, _re.DOTALL)
            if m:
                out["description"] = m.group(1).strip()[:200]
        except Exception:
            pass
    elif (repo_root / "package.json").is_file():
        out["build_tool"] = "npm"
        try:
            import json as _json
            data = _json.loads((repo_root / "package.json").read_text(encoding="utf-8", errors="ignore"))
            out["description"] = (data.get("description") or "")[:200]
        except Exception:
            pass
    elif (repo_root / "pyproject.toml").is_file() or (repo_root / "requirements.txt").is_file():
        out["build_tool"] = "pip"
    elif (repo_root / "go.mod").is_file():
        out["build_tool"] = "go"

    return out


# 핵심 의존성 분류 휴리스틱 — 이름 substring 매칭
_DEP_CATEGORIES = {
    "framework": ["spring", "django", "flask", "express", "fastapi", "gin", "actix", "rails", "laravel", "rocket"],
    "db":        ["mybatis", "jpa", "hibernate", "sqlalchemy", "mongoose", "prisma", "gorm", "diesel", "sequelize", "knex", "pg-", "mysql", "postgres", "mongodb", "redis"],
    "auth":      ["jwt", "oauth", "security", "passport", "cas-", "saml", "keycloak"],
    "test":      ["junit", "jest", "pytest", "mocha", "testify", "rspec", "phpunit", "minitest", "cucumber", "spock", "assertj", "mockito"],
    "util":      ["lodash", "jackson", "gson", "requests", "axios", "guava", "commons-", "okhttp", "retrofit"],
}


def _classify_dep(name: str) -> str:
    nm = name.lower()
    for cat, patterns in _DEP_CATEGORIES.items():
        for pat in patterns:
            if pat in nm:
                return cat
    return "other"


def extract_dependencies(repo_root: Path) -> dict:
    """build.gradle / pom.xml / package.json 의 의존성을 5 카테고리로 분류.

    PM 이 기술 스택을 한눈에 파악. 카테고리: framework / db / auth / test / util / other.
    """
    deps_raw: list = []  # [(name, version), ...]

    for fname in ("build.gradle", "build.gradle.kts"):
        p = repo_root / fname
        if p.is_file():
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
                for m in _re.finditer(
                    r"(?:implementation|api|compile|runtimeOnly|testImplementation|annotationProcessor)\s*[\(\s]['\"]([^:'\"]+):([^:'\"]+):?([^:'\"]*)['\"]",
                    txt,
                ):
                    group, art, ver = m.group(1), m.group(2), m.group(3)
                    deps_raw.append((art, ver or "—"))
            except Exception:
                pass

    pom = repo_root / "pom.xml"
    if pom.is_file():
        try:
            txt = pom.read_text(encoding="utf-8", errors="ignore")
            for m in _re.finditer(
                r"<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>(?:\s*<version>([^<]+)</version>)?",
                txt,
            ):
                _g, art, ver = m.group(1), m.group(2), m.group(3)
                deps_raw.append((art, ver or "—"))
        except Exception:
            pass

    pj = repo_root / "package.json"
    if pj.is_file():
        try:
            import json as _json
            data = _json.loads(pj.read_text(encoding="utf-8", errors="ignore"))
            for section in ("dependencies", "devDependencies"):
                for name, ver in (data.get(section) or {}).items():
                    deps_raw.append((name, ver))
        except Exception:
            pass

    pyproj = repo_root / "pyproject.toml"
    req = repo_root / "requirements.txt"
    if pyproj.is_file():
        try:
            txt = pyproj.read_text(encoding="utf-8", errors="ignore")
            for m in _re.finditer(r'^([a-zA-Z0-9_\-\.]+)\s*=\s*[\"\']([^\"\']+)[\"\']', txt, _re.MULTILINE):
                deps_raw.append((m.group(1), m.group(2)))
        except Exception:
            pass
    if req.is_file():
        try:
            for ln in req.read_text(encoding="utf-8", errors="ignore").splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                m = _re.match(r"([a-zA-Z0-9_\-\.]+)\s*([=<>!~].+)?", ln)
                if m:
                    deps_raw.append((m.group(1), (m.group(2) or "").strip() or "—"))
        except Exception:
            pass

    seen = set()
    deps_dedup = []
    for name, ver in deps_raw:
        if name not in seen:
            seen.add(name)
            deps_dedup.append((name, ver))

    out: dict = {"framework": [], "db": [], "auth": [], "test": [], "util": [], "other": [], "total": len(deps_dedup)}
    for name, ver in deps_dedup:
        out[_classify_dep(name)].append((name, ver))
    return out


# 도메인 엔티티 패턴
_ENTITY_NAME_SUFFIXES = ("entity", "model", "domain", "dto", "data", "record", "vo")
_ENTITY_PATH_HINTS = ("/entity/", "/model/", "/domain/", "/core/", "/data/")


def extract_domain_entities(chunks_by_path: dict, top_n: int = 10) -> list:
    """도메인 엔티티 후보 — class/type/interface/struct/record 청크 중 이름·경로 패턴 매칭.

    PM 이 시스템의 핵심 데이터 모델을 한눈에 파악.
    """
    candidates = []
    for rel_path, chunks in chunks_by_path.items():
        for ch in chunks:
            kind = ch.get("kind", "")
            if kind not in ("class", "type", "interface", "struct", "record"):
                continue
            symbol = ch.get("symbol", "")
            sym_lower = symbol.lower()
            path_lower = ("/" + rel_path.lower())

            name_match = any(sym_lower.endswith(suf) for suf in _ENTITY_NAME_SUFFIXES)
            path_match = any(hint in path_lower for hint in _ENTITY_PATH_HINTS)

            if not (name_match or path_match):
                continue
            # 안티패턴 — Controller / Service / Repository 는 제외
            if any(sym_lower.endswith(anti) for anti in ("controller", "service", "repository", "config", "handler", "test", "validator")):
                continue

            candidates.append({
                "name": symbol,
                "path": rel_path,
                "kind": kind,
                "callers": len(ch.get("callers") or []),
                "doc": (ch.get("doc") or "")[:120],
            })

    candidates.sort(key=lambda x: x["callers"], reverse=True)
    return candidates[:top_n]


def extract_class_inheritance(chunks_by_path: dict, repo_root: Path, top_n: int = 20) -> list:
    """클래스 상속/구현 관계 추출 (v1: Java only).

    Java class_declaration 의 superclass / superinterfaces 를 tree-sitter 로 재추출.
    Python/TS 는 다음 사이클.
    """
    out = []
    try:
        from tree_sitter_languages import get_language, get_parser
    except Exception:
        return out

    try:
        java_parser = get_parser("java")
    except Exception:
        return out

    for rel_path, chunks in chunks_by_path.items():
        if not rel_path.endswith(".java"):
            continue
        # 파일 단위 1회 파싱 (각 chunk 가 같은 파일이라 비효율 방지)
        file_path = repo_root / rel_path
        if not file_path.is_file():
            continue
        try:
            source = file_path.read_bytes()
            tree = java_parser.parse(source)
        except Exception:
            continue

        def _walk(node):
            if node.type == "class_declaration":
                name_node = node.child_by_field_name("name")
                child_name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace") if name_node else "?"
                parents = []
                kind = ""
                superclass = node.child_by_field_name("superclass")
                if superclass:
                    parents.append(source[superclass.start_byte:superclass.end_byte].decode("utf-8", errors="replace").lstrip("extends").strip())
                    kind = "extends"
                interfaces = node.child_by_field_name("interfaces")
                if interfaces:
                    for c in interfaces.children:
                        if c.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                            parents.append(source[c.start_byte:c.end_byte].decode("utf-8", errors="replace"))
                    if not kind:
                        kind = "implements"
                if parents:
                    out.append({
                        "child": child_name,
                        "parents": parents,
                        "kind": kind,
                        "child_path": rel_path,
                    })
            for c in node.children:
                _walk(c)

        _walk(tree.root_node)

    out.sort(key=lambda x: len(x["parents"]), reverse=True)
    return out[:top_n]


def compute_test_coverage_map(chunks_by_path: dict, top_n: int = 10) -> dict:
    """테스트 커버리지 매핑 — 어떤 production 함수에 테스트가 매핑됐는가.

    test_for 역인덱스 활용. uncovered_public_top10 = 테스트 없는 + caller 많은 함수.
    PM 액션 직결: "테스트 추가가 시급한 영역".
    """
    total_prod = 0
    covered = 0
    uncovered_candidates = []

    for rel_path, chunks in chunks_by_path.items():
        for ch in chunks:
            if ch.get("is_test"):
                continue
            kind = ch.get("kind", "")
            if kind not in ("function", "method"):
                continue
            total_prod += 1
            test_paths = ch.get("test_paths") or []
            if test_paths:
                covered += 1
                continue
            symbol = ch.get("symbol", "")
            # public/exported 식별 (휴리스틱)
            code = ch.get("code", "")
            is_public = (
                code.lstrip().startswith("public ")  # Java
                or code.lstrip().startswith("export ")  # JS/TS
                or (not symbol.startswith("_"))  # Python convention
            )
            uncovered_candidates.append({
                "symbol": symbol,
                "path": rel_path,
                "callers": len(ch.get("callers") or []),
                "is_public": is_public,
            })

    # public 우선 + caller 많은 순
    uncovered_candidates.sort(key=lambda x: (x["is_public"], x["callers"]), reverse=True)
    coverage_pct = (covered / total_prod * 100.0) if total_prod else 0.0

    return {
        "total_production_methods": total_prod,
        "covered_methods": covered,
        "coverage_pct": coverage_pct,
        "uncovered_public_top10": uncovered_candidates[:top_n],
    }


def compute_overall_verdict(stats: dict, coverage: dict, kb_intel: dict) -> tuple:
    """종합 신뢰도 verdict — rule-based 0~100 scoring.

    Returns: (emoji, label, score, summary, reason_lines)
      - summary: 한 줄 종합 평가 (TL;DR 카드용)
      - reason_lines: 점수 산출 근거 list (§7 펼침)
    """
    score = 0
    reasons = []

    chunks = stats.get("total_chunks", 0)
    if chunks > 50:
        score += 30
        reasons.append(f"✓ 청크 {chunks}개 — 의미있는 학습 (>50)")
    elif chunks > 0:
        score += 10
        reasons.append(f"⚠ 청크 {chunks}개 — 적은 양 (≤50)")
    else:
        reasons.append("✗ 청크 0개 — 학습 실패")

    orphan = stats.get("orphan_ratio", 1.0)
    if orphan < 0.7:
        score += 20
        reasons.append(f"✓ 고아 비율 {orphan*100:.0f}% — 역인덱스 작동 (<70%)")
    else:
        reasons.append(f"⚠ 고아 비율 {orphan*100:.0f}% — 역인덱스 매칭 적음 (≥70%)")

    endpoints = stats.get("endpoints_count", 0)
    if endpoints > 0:
        score += 15
        reasons.append(f"✓ HTTP API {endpoints}개 식별 — 진입점 추적 가능")
    else:
        reasons.append("⚠ HTTP API 0개 — Web 프로젝트면 추출 누수, 라이브러리면 정상")

    cov = coverage.get("coverage_pct", 0.0)
    if cov > 30:
        score += 15
        reasons.append(f"✓ 테스트 커버 {cov:.0f}% — 검증 가능 (>30%)")
    else:
        reasons.append(f"⚠ 테스트 커버 {cov:.0f}% — 검증 부족 (≤30%)")

    parser_failed = stats.get("parser_failed", 0)
    if parser_failed == 0:
        score += 10
        reasons.append("✓ AST 파싱 100% 성공")
    else:
        reasons.append(f"⚠ AST 파싱 실패 {parser_failed} 파일")

    callers_links = stats.get("total_callers_links", 0)
    if callers_links > chunks:  # 평균 1+ caller
        score += 10
        reasons.append(f"✓ caller 링크 {callers_links}개 — 호출 그래프 풍부")
    else:
        reasons.append(f"⚠ caller 링크 {callers_links}개 — 호출 매핑 부족")

    if score >= 80:
        return ("🟢", "high", score,
                "이 KB 로 04 분석이 정확한 답변을 낼 가능성이 높음", reasons)
    elif score >= 50:
        return ("🟡", "medium", score,
                "부분 정확도 — 04 답변에 RAG 보조 필요", reasons)
    else:
        return ("🔴", "low", score,
                "재학습/데이터 보강 필요 — 04 답변 신뢰도 낮음", reasons)


def suggest_pm_actions(stats: dict, coverage: dict, verdict: tuple) -> list:
    """PM 액션 제안 (rule-based, 최대 3개)."""
    actions = []

    if coverage.get("coverage_pct", 0) < 30:
        actions.append({
            "title": "🧪 테스트 우선 추가",
            "why": f"테스트 커버 {coverage.get('coverage_pct', 0):.0f}% — public 함수 다수 미검증",
            "where": "§5 의 'uncovered_public_top10' 표를 백로그에 옮기기",
        })

    if stats.get("endpoints_count", 0) > 0 and stats.get("decorators_count", 0) == 0:
        actions.append({
            "title": "🔐 API 룰북 정의 권장",
            "why": "HTTP endpoint 는 발견됐으나 decorator (인증/검증) 추출 0건",
            "where": "§4 의 HTTP 진입점 표 — 각 endpoint 에 인증/입력검증 정책 매핑",
        })

    if stats.get("orphan_ratio", 0) > 0.8:
        actions.append({
            "title": "🔍 역인덱스 분석",
            "why": f"고아 심볼 {stats.get('orphan_ratio', 0)*100:.0f}% — caller 매칭 누수 의심",
            "where": "동명 심볼 / import resolution 부재 가능. 파이프라인 02 의 pass 2 로직 점검",
        })

    if stats.get("parser_failed", 0) > 5:
        actions.append({
            "title": "🛠 AST 추출 실패 파일 점검",
            "why": f"{stats.get('parser_failed', 0)} 파일 파싱 실패 — KB 누락 위험",
            "where": "§9 디버깅의 parser_failed_files 목록 확인",
        })

    if not actions:
        actions.append({
            "title": "✅ 큰 액션 불필요",
            "why": f"학습 신뢰도 {verdict[1]} ({verdict[2]}점) — KB 활용 가능",
            "where": "04 분석 결과를 그대로 사용. 결과 부족 시 RAG retrieval 임계치 조정 검토",
        })

    return actions[:3]


def load_chunks_from_jsonl_dir(jsonl_dir: Path) -> tuple:
    """E4 검증용 — 기존 JSONL 디렉토리에서 chunks_by_path / stats 재구성.

    KB 재학습 없이 리포트만 다시 그릴 때 사용 (--report-only 모드).
    """
    import json as _json
    chunks_by_path: dict = {}
    total_chunks = 0
    total_callers = 0
    total_tests = 0
    for jsonl_path in sorted(Path(jsonl_dir).glob("*.jsonl")):
        try:
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                ch = _json.loads(line)
                rel_path = ch.get("path", "")
                if not rel_path:
                    continue
                chunks_by_path.setdefault(rel_path, []).append(ch)
                total_chunks += 1
                total_callers += len(ch.get("callers") or [])
                total_tests += len(ch.get("test_paths") or [])
        except Exception as e:
            print(f"[load_chunks] {jsonl_path.name} skip: {e}")
            continue
    stats = {
        "total_files": len(chunks_by_path),
        "total_chunks": total_chunks,
        "total_callers_links": total_callers,
        "total_test_links": total_tests,
    }
    return stats, chunks_by_path


def write_html_report(
    out_path: Path,
    repo_root: Path,
    commit_sha: str,
    stats: dict,
    chunks_by_path: dict,
    kb_dir: "Path | None" = None,
) -> None:
    """v3 — PM 친화 사전학습 리포트 (8 섹션 + sticky nav + 9 디버깅 접힘).

    구조:
      §1 🎯 한눈에 (TL;DR 카드 + 종합 verdict)
      §2 📖 이 프로젝트는 무엇인가 (overview + deps)
      §3 🏗 코드 구조 (tree + entities)
      §4 🔗 코드 연관관계 (endpoints + caller/callees hub + inheritance)
      §5 🧪 테스트 커버리지 (uncovered_public_top10)
      §6 📚 AI 학습 진단 (4-stage from diagnostic_report_builder)
      §7 🤖 AI 신뢰도 + 액션 제안
      §9 🔧 디버깅 정보 (접힘)
    """
    import datetime
    from collections import Counter

    # ─ 1. 청크 집계 ─────────────────────────────────────────────────────────
    lang_count: Counter = Counter()
    kind_count: Counter = Counter()
    caller_hub: list = []
    callees_counter: Counter = Counter()
    endpoints_rows: list = []
    decorators_count = 0
    docstring_count = 0
    orphan_chunks = 0
    is_test_chunks = 0
    total_chunks_local = 0
    for rel_path, chunks in chunks_by_path.items():
        for ch in chunks:
            total_chunks_local += 1
            lang_count[ch["lang"]] += 1
            kind_count[ch["kind"]] += 1
            cls = ch.get("callers") or []
            if not cls:
                orphan_chunks += 1
            if ch.get("is_test"):
                is_test_chunks += 1
            caller_hub.append((len(cls), rel_path, ch["symbol"], ch["lang"]))
            for callee in ch.get("callees") or []:
                callees_counter[callee] += 1
            ep = (ch.get("endpoint") or "").strip()
            if ep:
                endpoints_rows.append((ep, ch["symbol"], rel_path))
            if ch.get("decorators"):
                decorators_count += 1
            if (ch.get("doc") or "").strip():
                docstring_count += 1

    caller_hub.sort(reverse=True)
    caller_hub_top = [x for x in caller_hub if x[0] > 0][:10]
    callees_top = callees_counter.most_common(10)
    endpoints_rows.sort()
    orphan_pct = (orphan_chunks * 100.0 / total_chunks_local) if total_chunks_local else 0.0

    # ─ 2. 신규 추출 (A1~A5) ──────────────────────────────────────────────────
    overview = extract_project_overview(repo_root)
    deps = extract_dependencies(repo_root)
    entities = extract_domain_entities(chunks_by_path, top_n=10)
    inheritance = extract_class_inheritance(chunks_by_path, repo_root, top_n=10)
    coverage = compute_test_coverage_map(chunks_by_path, top_n=10)

    # ─ 3. verdict 산출용 stats 풍부화 ────────────────────────────────────────
    stats_for_verdict = dict(stats)
    stats_for_verdict["total_chunks"] = total_chunks_local
    stats_for_verdict["orphan_ratio"] = orphan_pct / 100.0
    stats_for_verdict["endpoints_count"] = len(endpoints_rows)
    stats_for_verdict["decorators_count"] = decorators_count
    stats_for_verdict["docstring_count"] = docstring_count
    parser_failed = stats.get("parser_failed", 0) or 0
    stats_for_verdict["parser_failed"] = parser_failed
    callers_links = stats.get("total_callers_links", 0)
    stats_for_verdict["total_callers_links"] = callers_links

    verdict = compute_overall_verdict(stats_for_verdict, coverage, {})
    actions = suggest_pm_actions(stats_for_verdict, coverage, verdict)

    # ─ 4. KB intelligence narrative (B1) — 가능 시 import ────────────────────
    kb_intel_html = ""
    if kb_dir:
        try:
            from diagnostic_report_builder import render_kb_intelligence_section
            kb_intel_html = render_kb_intelligence_section(kb_dir)
        except Exception as e:
            kb_intel_html = f"<p class='note warn'>KB intelligence 섹션 렌더 실패: {_html_escape(str(e))}</p>"

    # ─ 5. HTML helpers ───────────────────────────────────────────────────────
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    repo_display = _html_escape(str(repo_root))
    commit_display = _html_escape((commit_sha or "n/a")[:12])
    primary_lang = lang_count.most_common(1)[0][0] if lang_count else ""

    css = """
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       margin: 0; color: #24292e; background: #fafbfc; }
.container { max-width: 1200px; margin: 0 auto; padding: 24px; }
nav.toc { position: sticky; top: 0; background: #fff; border-bottom: 1px solid #e1e4e8;
          padding: 10px 24px; font-size: 13px; z-index: 100;
          box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
nav.toc a { color: #0366d6; text-decoration: none; margin-right: 14px; }
nav.toc a:hover { text-decoration: underline; }
h1 { border-bottom: 2px solid #0366d6; padding-bottom: 6px; }
h2 { margin-top: 36px; border-bottom: 1px solid #e1e4e8; padding-bottom: 6px;
     scroll-margin-top: 60px; }
h3 { margin-top: 20px; color: #0366d6; }
.meta { color: #586069; font-size: 13px; margin-bottom: 24px; }
.tldr-summary { background: #fff; border-left: 4px solid #0366d6;
                padding: 12px 16px; margin: 16px 0 24px;
                font-size: 14px; line-height: 1.6; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
         gap: 12px; margin-bottom: 24px; }
.card { padding: 14px 16px; background: #fff; border: 1px solid #e1e4e8;
        border-radius: 8px; transition: transform 0.1s; }
.card:hover { transform: translateY(-1px); box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
.card a { text-decoration: none; color: inherit; display: block; }
.card .label { color: #586069; font-size: 11px; text-transform: uppercase;
                letter-spacing: 0.5px; font-weight: 600; }
.card .value { font-size: 26px; font-weight: 700; color: #0366d6; margin-top: 6px; }
.card .sub { color: #586069; font-size: 12px; margin-top: 4px; }
.verdict-card { background: #fff; border: 2px solid; border-radius: 8px; padding: 16px;
                margin: 16px 0; }
.verdict-card.high { border-color: #2da44e; }
.verdict-card.medium { border-color: #d4a72c; }
.verdict-card.low { border-color: #cf222e; }
.verdict-emoji { font-size: 28px; margin-right: 12px; vertical-align: middle; }
.verdict-label { font-size: 22px; font-weight: 700; vertical-align: middle; }
.verdict-score { color: #586069; font-size: 13px; margin-left: 8px; }
.action-card { background: #fff; border-left: 4px solid #0969da;
               padding: 12px 16px; margin: 10px 0; border-radius: 4px; }
.action-card .title { font-weight: 600; font-size: 15px; color: #0969da; }
.action-card .why { color: #24292e; font-size: 13px; margin: 6px 0 4px; }
.action-card .where { color: #586069; font-size: 12px; font-style: italic; }
table { border-collapse: collapse; margin: 12px 0; font-size: 13px; }
th, td { border: 1px solid #e1e4e8; padding: 6px 12px; text-align: left; }
th { background: #f6f8fa; font-weight: 600; }
code { font-family: 'SF Mono', Monaco, Consolas, monospace; font-size: 12px;
       background: #f6f8fa; padding: 2px 4px; border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px;
      padding: 12px; overflow-x: auto; font-size: 12px; line-height: 1.4; max-height: 320px; }
.bar { display: inline-block; height: 10px; background: #0366d6;
       border-radius: 2px; vertical-align: middle; }
.warn { color: #b08800; }
.note { color: #586069; font-size: 12px; margin: 6px 0 16px; }
details { background: #fff; border: 1px solid #e1e4e8; border-radius: 6px;
          padding: 12px; margin: 12px 0; }
details summary { cursor: pointer; font-weight: 600; color: #586069; }
.dep-pill { display: inline-block; background: #ddf4ff; color: #0969da;
            padding: 2px 8px; border-radius: 12px; font-size: 12px; margin: 2px; }
.tree { font-family: 'SF Mono', Monaco, Consolas, monospace; font-size: 12px;
        line-height: 1.4; white-space: pre; background: #f6f8fa;
        padding: 12px; border-radius: 6px; max-height: 400px; overflow: auto; }
.endpoint-method { display: inline-block; padding: 2px 8px; border-radius: 3px;
                   font-weight: 700; font-size: 11px; color: #fff; }
.endpoint-method.GET { background: #2da44e; }
.endpoint-method.POST { background: #0969da; }
.endpoint-method.PUT { background: #bf8700; }
.endpoint-method.DELETE { background: #cf222e; }
.endpoint-method.PATCH { background: #8250df; }
</style>
"""

    def table_from_dict(d: dict, header_a: str, header_b: str) -> str:
        if not d:
            return "<p class='note'>(데이터 없음)</p>"
        total = sum(d.values()) or 1
        rows = []
        for k, v in sorted(d.items(), key=lambda x: -x[1]):
            pct = v * 100.0 / total
            bar_w = int(pct * 3)
            rows.append(
                f"<tr><td>{_html_escape(str(k))}</td><td>{v}</td>"
                f"<td><span class='bar' style='width:{bar_w}px'></span> {pct:.1f}%</td></tr>"
            )
        return (
            f"<table><tr><th>{_html_escape(header_a)}</th><th>{_html_escape(header_b)}</th>"
            f"<th>비율</th></tr>" + "".join(rows) + "</table>"
        )

    # ─ §1 TL;DR ──────────────────────────────────────────────────────────────
    coverage_pct = coverage.get("coverage_pct", 0.0)
    summary_line = overview.get("description") or overview.get("summary") or ""

    s1 = [
        "<h2 id='sec-1'>🎯 §1 한눈에 — 5초 만에 파악</h2>",
        (f"<div class='tldr-summary'>📦 <strong>{_html_escape(repo_root.name)}</strong> "
         f"— {_html_escape(summary_line) if summary_line else '(설명 없음)'}</div>"),
        "<div class='cards'>",
        (f"<div class='card'><a href='#sec-3'><div class='label'>파일</div>"
         f"<div class='value'>{stats.get('total_files', 0)}</div>"
         f"<div class='sub'>{_html_escape(primary_lang)}</div></a></div>"),
        (f"<div class='card'><a href='#sec-3'><div class='label'>함수·클래스</div>"
         f"<div class='value'>{total_chunks_local}</div>"
         f"<div class='sub'>tree-sitter AST 청크</div></a></div>"),
        (f"<div class='card'><a href='#sec-4'><div class='label'>HTTP API</div>"
         f"<div class='value'>{len(endpoints_rows)}</div>"
         f"<div class='sub'>endpoint 식별</div></a></div>"),
        (f"<div class='card'><a href='#sec-5'><div class='label'>테스트 커버</div>"
         f"<div class='value'>{coverage_pct:.0f}%</div>"
         f"<div class='sub'>{coverage.get('covered_methods',0)}/{coverage.get('total_production_methods',0)} 매핑</div></a></div>"),
        (f"<div class='card'><a href='#sec-7'><div class='label'>AI 신뢰도</div>"
         f"<div class='value'>{verdict[0]} {verdict[2]}</div>"
         f"<div class='sub'>{_html_escape(verdict[1])} ({verdict[2]}/100)</div></a></div>"),
        "</div>",
    ]

    # ─ §2 프로젝트 ───────────────────────────────────────────────────────────
    s2 = ["<h2 id='sec-2'>📖 §2 이 프로젝트는 무엇인가</h2>"]
    if overview.get("summary"):
        s2.append(f"<p>{_html_escape(overview['summary'])}</p>")
    meta_bits = []
    if overview.get("license"):
        meta_bits.append(f"라이선스 <code>{_html_escape(overview['license'])}</code>")
    if overview.get("build_tool"):
        meta_bits.append(f"빌드 <code>{_html_escape(overview['build_tool'])}</code>")
    if primary_lang:
        meta_bits.append(f"주 언어 <code>{_html_escape(primary_lang)}</code>")
    if meta_bits:
        s2.append("<p class='note'>" + " · ".join(meta_bits) + "</p>")

    s2.append("<h3>핵심 의존성</h3>")
    if deps["total"] == 0:
        s2.append("<p class='note'>(의존성 추출 결과 없음 — build.gradle/pom.xml/package.json 미발견)</p>")
    else:
        s2.append(f"<p class='note'>총 {deps['total']}개 의존성 — 카테고리별 분류:</p>")
        cat_labels = {"framework": "🏛 Framework", "db": "🗄 Database", "auth": "🔐 Auth/Security",
                       "test": "🧪 Test", "util": "🔧 Util", "other": "📦 Other"}
        for cat, label in cat_labels.items():
            items = deps[cat]
            if not items:
                continue
            pills = " ".join(f"<span class='dep-pill'>{_html_escape(n)}</span>" for n, _v in items[:15])
            s2.append(f"<p><strong>{label}</strong> ({len(items)}): {pills}</p>")

    # ─ §3 코드 구조 ──────────────────────────────────────────────────────────
    s3 = ["<h2 id='sec-3'>🏗 §3 코드 구조</h2>", "<h3>디렉토리 트리 (top 200 lines)</h3>"]
    try:
        tree_text = build_tree(repo_root, max_lines=200)
    except Exception as e:
        tree_text = f"(트리 생성 실패: {e})"
    s3.append(f"<div class='tree'>{_html_escape(tree_text)}</div>")

    s3.append("<h3>도메인 엔티티 후보 (호출 빈도순 top 10)</h3>")
    s3.append("<p class='note'>이름 패턴 (`*Entity / *Model / *Domain / *Data`) 또는 경로 (`entity / model / core / domain`) 기반 자동 인식.</p>")
    if entities:
        ent_rows = "".join(
            f"<tr><td><code>{_html_escape(e['name'])}</code></td>"
            f"<td>{_html_escape(e['kind'])}</td><td>{e['callers']}</td>"
            f"<td><code>{_html_escape(e['path'])}</code></td>"
            f"<td>{_html_escape(e['doc'])}</td></tr>"
            for e in entities
        )
        s3.append(f"<table><tr><th>이름</th><th>kind</th><th>callers</th><th>경로</th><th>설명</th></tr>{ent_rows}</table>")
    else:
        s3.append("<p class='note'>(도메인 엔티티 패턴에 매칭되는 청크 없음)</p>")

    # ─ §4 코드 연관관계 ──────────────────────────────────────────────────────
    s4 = ["<h2 id='sec-4'>🔗 §4 코드 연관관계</h2>", "<h3>HTTP 진입점 맵</h3>"]
    if endpoints_rows:
        ep_rows_html = []
        for ep, handler, fp in endpoints_rows[:30]:
            method, _, path_part = ep.partition(" ")
            ep_rows_html.append(
                f"<tr><td><span class='endpoint-method {_html_escape(method)}'>{_html_escape(method)}</span></td>"
                f"<td><code>{_html_escape(path_part)}</code></td>"
                f"<td><code>{_html_escape(handler)}</code></td>"
                f"<td><code>{_html_escape(fp)}</code></td></tr>"
            )
        s4.append(f"<table><tr><th>method</th><th>path</th><th>handler</th><th>파일</th></tr>{''.join(ep_rows_html)}</table>")
        if len(endpoints_rows) > 30:
            s4.append(f"<p class='note'>...전체 {len(endpoints_rows)}개 중 상위 30개 표시.</p>")
    else:
        s4.append("<p class='note'>(HTTP endpoint 추출 결과 없음 — 라이브러리 프로젝트면 정상)</p>")

    s4.append("<h3>호출 hub (다른 곳에서 가장 많이 호출되는 함수 top 10)</h3>")
    if caller_hub_top:
        hub_rows = "".join(
            f"<tr><td>{n}</td><td><code>{_html_escape(p)}::{_html_escape(sym)}</code></td><td>{_html_escape(lg)}</td></tr>"
            for n, p, sym, lg in caller_hub_top
        )
        s4.append(f"<table><tr><th>callers</th><th>심볼</th><th>언어</th></tr>{hub_rows}</table>")
    else:
        s4.append("<p class='note'>(caller 매칭 0건)</p>")

    s4.append("<h3>의존도 hub (가장 많이 호출하는 함수 top 10 — '의존성' 측면)</h3>")
    if callees_top:
        callees_rows = "".join(
            f"<tr><td><code>{_html_escape(name)}</code></td><td>{cnt}</td></tr>"
            for name, cnt in callees_top
        )
        s4.append(f"<table><tr><th>호출되는 심볼</th><th>호출 빈도</th></tr>{callees_rows}</table>")
        s4.append("<p class='note'>이 목록 상위는 외부 라이브러리 함수 또는 레포 핵심 유틸. 변경 시 영향 범위 큼.</p>")
    else:
        s4.append("<p class='note'>(callees 추출 결과 없음)</p>")

    if inheritance:
        s4.append("<h3>클래스 상속/구현 (Java)</h3>")
        inh_rows = "".join(
            f"<tr><td><code>{_html_escape(x['child'])}</code></td>"
            f"<td>{_html_escape(x['kind'])}</td>"
            f"<td>{_html_escape(', '.join(x['parents'][:3]))}</td>"
            f"<td><code>{_html_escape(x['child_path'])}</code></td></tr>"
            for x in inheritance
        )
        s4.append(f"<table><tr><th>자식</th><th>관계</th><th>부모</th><th>파일</th></tr>{inh_rows}</table>")

    # ─ §5 테스트 커버리지 ────────────────────────────────────────────────────
    cov_emoji = "🟢" if coverage_pct >= 70 else ("🟡" if coverage_pct >= 30 else "🔴")
    s5 = [
        "<h2 id='sec-5'>🧪 §5 테스트 커버리지</h2>",
        (f"<div class='cards'><div class='card'><div class='label'>커버 비율</div>"
         f"<div class='value'>{cov_emoji} {coverage_pct:.0f}%</div>"
         f"<div class='sub'>{coverage.get('covered_methods',0)} / {coverage.get('total_production_methods',0)} production methods</div></div></div>"),
        "<h3>⚠ 테스트 없는 public 함수 — 우선 추가 권장 top 10</h3>",
        "<p class='note'>caller 가 많은 public/exported 함수 중 매핑된 테스트가 없는 것. 변경 위험이 큰 곳부터.</p>",
    ]
    uncov = coverage.get("uncovered_public_top10") or []
    if uncov:
        uc_rows = "".join(
            f"<tr><td>{'🔴 PUB' if u['is_public'] else 'pri'}</td>"
            f"<td>{u['callers']}</td>"
            f"<td><code>{_html_escape(u['symbol'])}</code></td>"
            f"<td><code>{_html_escape(u['path'])}</code></td></tr>"
            for u in uncov
        )
        s5.append(f"<table><tr><th>가시성</th><th>callers</th><th>심볼</th><th>경로</th></tr>{uc_rows}</table>")
    else:
        s5.append("<p class='note'>(미매핑 public 함수 없음 — 좋은 신호)</p>")

    # ─ §6 4-stage AI 학습 진단 ──────────────────────────────────────────────
    s6 = ["<h2 id='sec-6'>📚 §6 AI 학습 진단 (4-stage)</h2>"]
    if kb_intel_html:
        s6.append(kb_intel_html)
    else:
        s6.append("<p class='note'>(kb_dir 미전달 또는 kb_intelligence 데이터 없음 — 04 빌드 후 활용 가능)</p>")

    # ─ §7 verdict + 액션 ────────────────────────────────────────────────────
    # verdict 5-tuple: (emoji, label, score, label_text, reasons)
    verdict_label_text = verdict[3] if len(verdict) >= 5 else ""
    verdict_reasons = verdict[4] if len(verdict) >= 5 else (verdict[3] if isinstance(verdict[3], list) else [])
    s7 = [
        "<h2 id='sec-7'>🤖 §7 AI 신뢰도 + 다음 액션</h2>",
        (f"<div class='verdict-card {verdict[1]}'>"
         f"<span class='verdict-emoji'>{verdict[0]}</span>"
         f"<span class='verdict-label'>학습 신뢰도 {verdict[1]}</span>"
         f"<span class='verdict-score'>({verdict[2]}/100)</span>"
         f"<p style='margin:8px 0 0;font-size:14px;'>{_html_escape(verdict_label_text)}</p>"
         "<ul style='margin-top:12px;font-size:13px;line-height:1.6;'>"
         + "".join(f"<li>{_html_escape(r)}</li>" for r in verdict_reasons)
         + "</ul></div>"),
        "<h3>PM 액션 제안</h3>",
    ]
    for a in actions:
        s7.append(
            f"<div class='action-card'>"
            f"<div class='title'>{_html_escape(a['title'])}</div>"
            f"<div class='why'>{_html_escape(a['why'])}</div>"
            f"<div class='where'>📍 {_html_escape(a['where'])}</div>"
            "</div>"
        )

    # ─ §9 디버깅 (접힘) ─────────────────────────────────────────────────────
    parser_failed_files = stats.get("parser_failed_files") or []
    debug_inner = [
        "<h3>언어 분포</h3>",
        table_from_dict(dict(lang_count), "언어", "청크 수"),
        "<h3>Kind 분포</h3>",
        table_from_dict(dict(kind_count), "kind", "청크 수"),
        f"<h3>고아 심볼 비율</h3>"
        f"<p>callers=0 청크: <strong>{orphan_chunks}</strong> / {total_chunks_local} ({orphan_pct:.1f}%)</p>"
        "<p class='note'>public entry point (main, 라우트 핸들러) 는 caller 외부라 0 정상.</p>",
        f"<h3>Parser 실패 파일 ({parser_failed} 건)</h3>",
    ]
    if parser_failed_files:
        debug_inner.append("<ul style='font-size:12px;'>" + "".join(
            f"<li><code>{_html_escape(f)}</code></li>" for f in parser_failed_files[:50]
        ) + "</ul>")
    else:
        debug_inner.append("<p class='note'>(없음)</p>")

    s9 = [
        "<h2 id='sec-9'>🔧 §9 디버깅 정보 (개발자용)</h2>",
        "<details><summary>펼쳐 보기 — 청크 분포 / 고아 비율 / parser 실패 / 메타 통계</summary>",
        "".join(debug_inner),
        "<h3>메타 통계</h3>",
        f"<p>decorator 보유 청크: {decorators_count} · doc 보유 청크: {docstring_count} · "
        f"caller links: {callers_links} · test links: {stats.get('total_test_links', 0)}</p>",
        "</details>",
    ]

    # ─ Sticky Nav ───────────────────────────────────────────────────────────
    nav = (
        "<nav class='toc'>"
        "<a href='#sec-1'>§1 한눈에</a>"
        "<a href='#sec-2'>§2 프로젝트</a>"
        "<a href='#sec-3'>§3 구조</a>"
        "<a href='#sec-4'>§4 연관관계</a>"
        "<a href='#sec-5'>§5 테스트</a>"
        "<a href='#sec-6'>§6 학습진단</a>"
        "<a href='#sec-7'>§7 신뢰도+액션</a>"
        "<a href='#sec-9'>§9 디버깅</a>"
        "</nav>"
    )

    body_parts = [
        nav,
        "<div class='container'>",
        f"<h1>📊 사전학습 리포트 v3 — {_html_escape(repo_root.name)}</h1>",
        f"<div class='meta'>repo: <code>{repo_display}</code> · commit: <code>{commit_display}</code> · generated: {now}</div>",
        *s1, *s2, *s3, *s4, *s5, *s6, *s7, *s9,
        "</div>",
    ]

    html_doc = (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
        f"<title>사전학습 리포트 v3 — {_html_escape(repo_root.name)} ({commit_display})</title>"
        f"{css}</head><body>{''.join(body_parts)}</body></html>"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Tree-sitter based code chunker")
    ap.add_argument("--repo_root", required=True, help="레포 루트 경로")
    ap.add_argument("--out", required=True, help="JSONL 출력 디렉터리 (파일당 1 JSONL)")
    ap.add_argument("--commit-sha", default="", help="커밋 SHA (미지정 시 git rev-parse HEAD)")
    ap.add_argument("--report-html", default="",
                    help="HTML 리포트 출력 경로 (비어있으면 생성 skip). Jenkins publishHTML 대상.")
    ap.add_argument("--report-only", action="store_true",
                    help="청크 추출 skip 하고 기존 JSONL 디렉토리 (--out) 에서 리포트만 재생성. "
                         "v3 리포트 디자인 검증 / KB 재학습 없이 리포트만 갱신할 때 사용.")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    out_dir = Path(args.out).resolve()

    if not repo_root.is_dir():
        raise SystemExit(f"[repo_context_builder] repo_root 없음: {repo_root}")

    commit_sha = resolve_commit_sha(repo_root, args.commit_sha)

    if args.report_only:
        # 기존 JSONL 디렉토리에서 chunks_by_path / stats 재구성. scan_repo 호출 X.
        if not out_dir.is_dir():
            raise SystemExit(f"[repo_context_builder] --report-only — out_dir 없음: {out_dir}")
        stats, chunks_by_path = load_chunks_from_jsonl_dir(out_dir)
        print(f"[repo_context_builder] --report-only: 기존 JSONL 에서 {stats['total_files']} 파일 / "
              f"{stats['total_chunks']} 청크 로드")
    else:
        _total, stats, chunks_by_path = scan_repo(repo_root, out_dir, commit_sha)

    if args.report_html:
        write_html_report(
            out_path=Path(args.report_html).resolve(),
            repo_root=repo_root,
            commit_sha=commit_sha,
            stats=stats,
            chunks_by_path=chunks_by_path,
            kb_dir=out_dir,  # § 6 4-stage 학습진단 + parser_failed_files 사이드카 source
        )
        print(f"[repo_context_builder] HTML 리포트 생성: {args.report_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
