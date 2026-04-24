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
import subprocess
from pathlib import Path

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
        },
        "name_field": "name",
    },
    ".tsx": {
        "lang": "tsx",
        "node_types": {
            "function_declaration": "function",
            "method_definition": "method",
            "class_declaration": "class",
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
def collect_callees(node, lang: str) -> list:
    """node 서브트리에서 호출된 symbol 이름들을 수집 (중복 제거·정렬)."""
    callees = set()

    def walk(n):
        if lang == "python":
            if n.type == "call":
                fn = n.child_by_field_name("function")
                if fn is not None:
                    txt = fn.text.decode("utf-8", errors="replace")
                    # attribute 형태 (obj.method) 에서 메서드명만 추출
                    if "." in txt:
                        txt = txt.rsplit(".", 1)[-1]
                    if txt.isidentifier():
                        callees.add(txt)
        elif lang in ("java",):
            if n.type == "method_invocation":
                name = n.child_by_field_name("name")
                if name is not None:
                    callees.add(name.text.decode("utf-8", errors="replace"))
        elif lang in ("javascript", "typescript", "tsx"):
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                if fn is not None:
                    txt = fn.text.decode("utf-8", errors="replace")
                    if "." in txt:
                        txt = txt.rsplit(".", 1)[-1]
                    if txt.replace("_", "").isalnum():
                        callees.add(txt)
        for ch in n.children:
            walk(ch)

    walk(node)
    return sorted(callees)


def get_symbol_name(node, name_field: str):
    """child_by_field_name(name_field) 우선, 실패 시 첫 identifier 자식."""
    n = node.child_by_field_name(name_field)
    if n is not None:
        return n.text.decode("utf-8", errors="replace")
    for ch in node.children:
        if ch.type == "identifier":
            return ch.text.decode("utf-8", errors="replace")
    return None


def walk_symbols(node, node_types: dict):
    """AST 를 순회하며 관심 노드 yield."""
    if node.type in node_types:
        yield (node, node_types[node.type])
    for ch in node.children:
        yield from walk_symbols(ch, node_types)


def is_test_location(rel_path: str) -> bool:
    """P1.5 H-1 — 이 파일이 테스트 디렉토리/파일 규약에 해당하는지."""
    p = Path(rel_path)
    parts_lower = {x.lower() for x in p.parts}
    if parts_lower & {"tests", "test", "__tests__", "spec", "specs", "e2e", "cypress"}:
        return True
    fname = p.name.lower()
    return (
        fname.startswith("test_")
        or fname.endswith("_test.py")
        or fname.endswith(".test.ts")
        or fname.endswith(".spec.ts")
        or fname.endswith(".test.tsx")
        or fname.endswith(".spec.tsx")
        or fname.endswith(".test.js")
        or fname.endswith(".spec.js")
        or fname.endswith("test.java")  # SomethingTest.java
        or fname.endswith("tests.java")
    )


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
    """테스트 심볼이면 타깃 심볼명 추정.

    P1.5 확장: 테스트 위치라고 판단되면 symbol 이름이 test_X/testX 컨벤션이
    아니어도 파일명에서 대상을 추정해 돌려준다. 이로써 mocha/jest 의
    익명 `describe("Foo", fn)` 스타일에서 tree-sitter 가 symbol 을 function /
    anonymous 로 뽑아도 test_for 가 채워진다. 여러 후보 중 첫 번째.

    일반 코드면 None (fast path).
    """
    if not is_test_location(rel_path):
        return None

    # 1) symbol 자체가 test_X / testX 컨벤션인 경우 정확 매칭
    if symbol.startswith("test_"):
        return symbol[5:]
    if symbol.startswith("test"):
        rest = symbol[4:]
        if rest and rest[0].isupper():
            return rest[0].lower() + rest[1:]

    # 2) 파일명 기반 후보 — symbol 이 컨벤션을 따르지 않는 JS/TS 테스트에 유효
    candidates = filename_test_candidates(rel_path)
    if candidates:
        return candidates[0]
    return None


def path_to_safe_filename(rel_path: str) -> str:
    """레포 상대경로 → 파일시스템 안전한 단일 파일명 (슬래시 → __)."""
    return rel_path.replace(os.sep, "__").replace("/", "__")


def extract_chunks_from_file(file_path: Path, repo_root: Path, commit_sha: str):
    """한 파일의 함수/클래스 청크 리스트 반환. 비지원/파싱실패는 [] 반환."""
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
    chunks = []
    for node, kind in walk_symbols(tree.root_node, cfg["node_types"]):
        symbol = get_symbol_name(node, cfg["name_field"])
        if not symbol:
            continue

        code = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        # 과도하게 큰 심볼 (예: 1000라인 God class) 은 상한 절단
        if len(code) > 30000:
            code = code[:30000] + "\n# ... [truncated]\n"

        test_for = guess_test_for(rel_path, symbol)
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
            # P1.5 H-1 — is_test 는 위치 기반으로 결정. test_for (대상 심볼명)
            # 가 None 이어도 테스트 파일 안의 청크는 is_test=true 가 되어야
            # build_kb_query 의 "test_for: {fn}" 쿼리 외에도 전역 "is_test: true"
            # 쿼리로 접근 가능하다.
            "is_test": is_test_location(rel_path),
            "test_for": test_for,
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

    # callees → callers 역링크
    for caller_path, caller_chunks in chunks_by_path.items():
        for caller in caller_chunks:
            caller_ref = f"{caller_path}#{caller['symbol']}"
            for callee_name in caller["callees"]:
                for target_path, target_idx in symbol_index.get(callee_name, []):
                    tgt = chunks_by_path[target_path][target_idx]
                    tgt["callers"].append(caller_ref)

    # test_for → test_paths 역링크
    for test_path, test_chunks in chunks_by_path.items():
        for test_ch in test_chunks:
            target_sym = test_ch.get("test_for")
            if not target_sym:
                continue
            test_ref = f"{test_path}#{test_ch['symbol']}"
            for target_path, target_idx in symbol_index.get(target_sym, []):
                tgt = chunks_by_path[target_path][target_idx]
                tgt["test_paths"].append(test_ref)

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

    # pass 1 — 파일별 청크 수집 (메모리 보유, pass 2 역인덱스 구축을 위함).
    # 중대형 레포도 청크 수는 수천~수만 수준, JSON 한 줄 1~5KB → 10~50MB 로 메모리 안전.
    chunks_by_path: dict = {}
    skipped_trivial = 0  # K-2 통계
    skipped_dup = 0      # K-3 통계
    skipped_minified = 0  # K-1 통계 (파일 수준)
    seen_body_hashes: set = set()  # K-3 전역 중복 감지 — (symbol, body_hash) 키
    import hashlib
    for file_path in sorted(repo_root.rglob("*")):
        if not file_path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in file_path.parts):
            continue
        if file_path.suffix.lower() not in LANG_CONFIG:
            continue
        # K-1 파일명 기반 minified 제외
        if MINIFIED_FILE_PATTERNS.search(file_path.name):
            skipped_minified += 1
            continue

        try:
            raw_chunks = extract_chunks_from_file(file_path, repo_root, commit_sha)
        except Exception as e:
            print(f"[skip:{file_path.relative_to(repo_root)}] {e}")
            continue
        if not raw_chunks:
            continue

        # K-2 trivial skip + K-3 dedup
        kept = []
        for ch in raw_chunks:
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
            continue

        rel_path = str(file_path.relative_to(repo_root))
        chunks_by_path[rel_path] = kept

    # pass 2 — callers / test_paths 역인덱스 채움
    build_reverse_indexes(chunks_by_path)

    # 기록
    total_files = 0
    total_chunks = 0
    total_callers_links = 0
    total_test_links = 0
    for rel_path, chunks in chunks_by_path.items():
        safe_name = path_to_safe_filename(rel_path)
        out_file = out_dir / f"{safe_name}.jsonl"
        with out_file.open("w", encoding="utf-8") as fh:
            for ch in chunks:
                total_callers_links += len(ch["callers"])
                total_test_links += len(ch["test_paths"])
                fh.write(json.dumps(ch, ensure_ascii=False) + "\n")
        total_files += 1
        total_chunks += len(chunks)

    # 사람이 읽는 요약 MD 병행 생성
    write_repo_summary(repo_root, out_dir)

    # 통계 딕셔너리 — 리포트 생성용. Jenkinsfile 이 별도 리포트 경로로 참조.
    stats = {
        "total_files": total_files,
        "total_chunks": total_chunks,
        "total_callers_links": total_callers_links,
        "total_test_links": total_test_links,
        # K-1/K-2/K-3 통계 — 리포트에서 품질 필터 효과 가시화
        "skipped_minified_files": skipped_minified,
        "skipped_trivial_chunks": skipped_trivial,
        "skipped_duplicate_chunks": skipped_dup,
    }

    print(
        f"[repo_context_builder] files={total_files} chunks={total_chunks} "
        f"callers_links={total_callers_links} test_links={total_test_links} "
        f"skipped(minified_files={skipped_minified}, trivial={skipped_trivial}, "
        f"dup={skipped_dup}) commit={commit_sha[:8] or 'n/a'} → {out_dir}"
    )
    return total_chunks, stats, chunks_by_path


# ─ HTML 리포트 생성 ────────────────────────────────────────────────────────
# Jenkins publishHTML 로 "Pre-training Report" 탭에 노출. zero_touch_qa 의
# test report 와 같은 UX 로 사전학습 결과를 사람이 바로 탐색할 수 있게 함.
# self-contained: inline CSS, 외부 JS/폰트 의존 없음 (Jenkins CSP 친화).
import html as _html


def _html_escape(s: str) -> str:
    return _html.escape(s or "", quote=True)


def write_html_report(
    out_path: Path,
    repo_root: Path,
    commit_sha: str,
    stats: dict,
    chunks_by_path: dict,
) -> None:
    """Pre-training 결과 HTML 리포트 1 장 생성.

    섹션:
      1. 헤더 — repo / commit / 실행 시각
      2. 통계 카드 — files / chunks / callers_links / test_links / is_test 청크 수
      3. 언어 분포 표
      4. kind 분포 표
      5. 상위 caller hub 심볼 (레포 공용 유틸 식별)
      6. 고아 심볼 비율
      7. 언어별 청크 샘플 (top-3 × 언어)
    """
    import datetime

    # 집계
    lang_count: dict = {}
    kind_count: dict = {}
    caller_hub: list = []
    orphan_chunks = 0
    is_test_chunks = 0
    total_chunks = 0
    for rel_path, chunks in chunks_by_path.items():
        for ch in chunks:
            total_chunks += 1
            lang_count[ch["lang"]] = lang_count.get(ch["lang"], 0) + 1
            kind_count[ch["kind"]] = kind_count.get(ch["kind"], 0) + 1
            if not ch["callers"]:
                orphan_chunks += 1
            if ch.get("is_test"):
                is_test_chunks += 1
            caller_hub.append((len(ch["callers"]), rel_path, ch["symbol"], ch["lang"]))

    caller_hub.sort(reverse=True)
    caller_hub_top = [x for x in caller_hub if x[0] > 0][:10]

    # 언어별 샘플 top-3 (첫 N 개. 정렬 없음 — 파일 순서)
    lang_samples: dict = {}
    for rel_path, chunks in chunks_by_path.items():
        for ch in chunks:
            lang_samples.setdefault(ch["lang"], []).append((rel_path, ch))
    for lg in lang_samples:
        lang_samples[lg] = lang_samples[lg][:3]

    # HTML 조립
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    repo_display = _html_escape(str(repo_root))
    commit_display = _html_escape((commit_sha or "n/a")[:12])

    css = """
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       margin: 24px; color: #24292e; max-width: 1100px; }
h1 { border-bottom: 2px solid #0366d6; padding-bottom: 6px; }
h2 { margin-top: 32px; border-bottom: 1px solid #e1e4e8; padding-bottom: 4px; }
.meta { color: #586069; font-size: 13px; margin-bottom: 24px; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }
.card { flex: 1; min-width: 150px; padding: 12px 16px; background: #f6f8fa;
        border: 1px solid #e1e4e8; border-radius: 6px; }
.card .label { color: #586069; font-size: 12px; text-transform: uppercase;
                letter-spacing: 0.5px; }
.card .value { font-size: 24px; font-weight: 600; color: #0366d6; margin-top: 4px; }
table { border-collapse: collapse; margin: 12px 0; font-size: 13px; }
th, td { border: 1px solid #e1e4e8; padding: 6px 12px; text-align: left; }
th { background: #f6f8fa; font-weight: 600; }
code { font-family: 'SF Mono', Monaco, Consolas, monospace; font-size: 12px;
       background: #f6f8fa; padding: 2px 4px; border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px;
      padding: 12px; overflow-x: auto; font-size: 12px; line-height: 1.4; }
.bar { display: inline-block; height: 10px; background: #0366d6;
       border-radius: 2px; vertical-align: middle; }
.warn { color: #b08800; }
.note { color: #586069; font-size: 12px; margin: 6px 0 16px; }
</style>
"""

    def table_from_dict(d: dict, header_a: str, header_b: str) -> str:
        if not d:
            return "<p class='note'>(데이터 없음)</p>"
        total = sum(d.values()) or 1
        rows = []
        for k, v in sorted(d.items(), key=lambda x: -x[1]):
            pct = v * 100.0 / total
            bar_w = int(pct * 3)  # 300px max
            rows.append(
                f"<tr><td>{_html_escape(str(k))}</td><td>{v}</td>"
                f"<td><span class='bar' style='width:{bar_w}px'></span> "
                f"{pct:.1f}%</td></tr>"
            )
        return (
            f"<table><tr><th>{_html_escape(header_a)}</th><th>{_html_escape(header_b)}</th>"
            f"<th>비율</th></tr>"
            + "".join(rows) + "</table>"
        )

    # caller hub
    if caller_hub_top:
        hub_rows = "".join(
            f"<tr><td>{n}</td><td><code>{_html_escape(p)}::{_html_escape(sym)}</code></td>"
            f"<td>{_html_escape(lg)}</td></tr>"
            for n, p, sym, lg in caller_hub_top
        )
        hub_table = (
            "<table><tr><th>callers</th><th>심볼</th><th>언어</th></tr>"
            + hub_rows + "</table>"
        )
    else:
        hub_table = "<p class='note'>(caller 가 하나도 없는 레포 — 단일 파일 실험/샘플 가능성)</p>"

    # 언어별 샘플
    sample_html_parts = []
    for lg in sorted(lang_samples.keys()):
        sample_html_parts.append(f"<h3>{_html_escape(lg)}</h3>")
        for rel_path, ch in lang_samples[lg]:
            header = f"<code>{_html_escape(rel_path)}::{_html_escape(ch['symbol'])}</code>"
            sub = []
            if ch["callers"]:
                sub.append(f"callers={len(ch['callers'])}")
            if ch["test_paths"]:
                sub.append(f"tests={len(ch['test_paths'])}")
            if ch["callees"]:
                sub.append(f"callees={len(ch['callees'])}")
            sub_str = " · ".join(sub) or "no links"
            code_preview = ch["code"][:1200]
            if len(ch["code"]) > 1200:
                code_preview += "\n# ... [truncated]"
            sample_html_parts.append(
                f"<p>{header} <span class='note'>({sub_str}, lines {ch['lines']})</span></p>"
                f"<pre>{_html_escape(code_preview)}</pre>"
            )

    orphan_pct = (orphan_chunks * 100.0 / total_chunks) if total_chunks else 0.0

    body_parts = [
        f"<h1>Pre-training Report</h1>",
        f"<div class='meta'>"
        f"repo: <code>{repo_display}</code> · commit: <code>{commit_display}</code> · "
        f"generated: {now}"
        f"</div>",
        "<div class='cards'>",
        f"<div class='card'><div class='label'>files</div>"
        f"<div class='value'>{stats['total_files']}</div></div>",
        f"<div class='card'><div class='label'>chunks</div>"
        f"<div class='value'>{stats['total_chunks']}</div></div>",
        f"<div class='card'><div class='label'>callers links</div>"
        f"<div class='value'>{stats['total_callers_links']}</div></div>",
        f"<div class='card'><div class='label'>test links</div>"
        f"<div class='value'>{stats['total_test_links']}</div></div>",
        f"<div class='card'><div class='label'>is_test chunks</div>"
        f"<div class='value'>{is_test_chunks}</div></div>",
        f"<div class='card'><div class='label'>orphan ratio</div>"
        f"<div class='value'>{orphan_pct:.1f}%</div></div>",
        "</div>",
        "<h2>언어 분포</h2>",
        table_from_dict(lang_count, "언어", "청크 수"),
        "<h2>Kind 분포</h2>",
        table_from_dict(kind_count, "kind", "청크 수"),
        "<h2>상위 caller hub (레포 공용 유틸 식별)</h2>",
        "<p class='note'>callers 배열이 긴 심볼일수록 레포에서 널리 쓰이는 유틸일 가능성이 높다. "
        "이 목록이 비어 있으면 pass 2 역인덱스 매칭이 거의 발생하지 않은 상태로, "
        "import resolution 부재 (동명 심볼만으로 매칭) 의 한계와 연관될 수 있다.</p>",
        hub_table,
        "<h2>고아 심볼 비율</h2>",
        f"<p>callers 역링크가 0 인 청크: <strong>{orphan_chunks}</strong> / "
        f"{total_chunks} ({orphan_pct:.1f}%).</p>",
        "<p class='note'>public entry point (main, 라우트 핸들러, 이벤트 리스너 등) 는 "
        "당연히 caller 가 외부이므로 0 이 정상. 이 비율이 80% 를 크게 넘으면 "
        "파싱/역인덱싱 결함을 의심할 여지.</p>",
        "<h2>언어별 청크 샘플</h2>",
        "<p class='note'>각 언어의 첫 3 청크 미리보기. 본문 1200자까지만 표시.</p>",
        *sample_html_parts,
    ]

    html_doc = (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
        f"<title>Pre-training Report — {commit_display}</title>{css}</head>"
        f"<body>{''.join(body_parts)}</body></html>"
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
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    out_dir = Path(args.out).resolve()

    if not repo_root.is_dir():
        raise SystemExit(f"[repo_context_builder] repo_root 없음: {repo_root}")

    commit_sha = resolve_commit_sha(repo_root, args.commit_sha)
    _total, stats, chunks_by_path = scan_repo(repo_root, out_dir, commit_sha)

    if args.report_html:
        write_html_report(
            out_path=Path(args.report_html).resolve(),
            repo_root=repo_root,
            commit_sha=commit_sha,
            stats=stats,
            chunks_by_path=chunks_by_path,
        )
        print(f"[repo_context_builder] HTML 리포트 생성: {args.report_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
