"""Discover 결과를 사이트 계층 트리로 재구성 + self-contained HTML 렌더.

두 가지 트리를 같은 노드 스키마로 산출:

1. **크롤 토폴로지** (`build_crawl_tree`) — BFS 가 기록한 ``parent_url`` 필드로
   부모-자식 구성. seed 가 root, parent 없는 (sitemap 등) 출처는 root 의 ``orphans``.
   사이트의 *실제 링크 그래프* 를 그대로 반영.
2. **URL 경로 기반** (`build_path_tree`) — `/a/b/c` 의 path segment 로 트리
   구성. 사이트의 *URL IA* 그대로 반영. SPA / 쿼리 식별자에는 약함.

두 트리를 한 HTML 안에 탭으로 묶어 외부 공유용으로 export
(`render_self_contained_tree_html`).

노드 스키마 (양쪽 빌더 공통):

```python
{
    "url": str,
    "title": str | None,
    "depth": int,
    "source": str,
    "status": int | None,
    "children": [<node>, ...],
}
```
"""

from __future__ import annotations

from html import escape as html_escape
from typing import Optional
from urllib.parse import urlparse

# 외부 record 는 dataclass / dict 둘 다 들어올 수 있어 .get() 으로 통일 접근.
Record = dict


def _node_from_record(rec: Record) -> dict:
    """record(dict) 에서 트리 노드 dict 생성. children 은 빈 리스트."""
    return {
        "url": rec.get("url", ""),
        "title": rec.get("title"),
        "depth": rec.get("depth", 0),
        "source": rec.get("source", ""),
        "status": rec.get("status"),
        "children": [],
    }


def build_crawl_tree(records: list[Record], seed_url: str) -> dict:
    """``parent_url`` 기반 크롤 토폴로지 트리.

    seed (parent 없는 첫 record) 를 root 로, 나머지는 parent_url 로 연결.
    parent_url 이 None 이지만 seed 가 아닌 record 는 ``orphans`` 그룹으로 분리
    (대표적으로 sitemap 시드).

    Args:
        records: ``urls.json`` 의 record 리스트.
        seed_url: ``meta.json`` 의 seed_url. records 내 매칭 못 찾을 때 fallback root.

    Returns:
        ``{"root": <seed-node>, "orphans": [<node>, ...]}`` 형태의 dict.
    """
    by_url: dict[str, dict] = {}
    for r in records:
        url = r.get("url")
        if not url:
            continue
        # 같은 URL 이 두 번 들어오면 첫 record 우선 (BFS 가 첫 발견에서 visited 처리).
        if url not in by_url:
            by_url[url] = _node_from_record(r)

    root: Optional[dict] = None
    orphans: list[dict] = []
    for r in records:
        url = r.get("url")
        if not url or url not in by_url:
            continue
        node = by_url[url]
        parent = r.get("parent_url")
        if parent is None:
            # seed 거나 sitemap 등의 root-인접 출처. seed 가 정확히 일치하면 root.
            if (root is None) and (url == seed_url or r.get("source") == "seed"):
                root = node
            else:
                orphans.append(node)
            continue
        parent_node = by_url.get(parent)
        if parent_node is None:
            # parent 가 results 에 없음 (예: 차단/제외된 URL) — orphan 처리.
            orphans.append(node)
        else:
            parent_node["children"].append(node)

    if root is None:
        # records 에 seed 가 없는 비정상 케이스 — 합성 root.
        root = {
            "url": seed_url, "title": None, "depth": 0,
            "source": "seed", "status": None, "children": [],
        }
    return {"root": root, "orphans": orphans}


def build_path_tree(records: list[Record], seed_url: str) -> dict:
    """URL path segment 기반 트리.

    seed host 와 같은 host 는 path segment (``/a/b/c`` → ``a > b > c``) 로 nest,
    다른 host 는 root 의 ``external`` 그룹.

    Args:
        records: ``urls.json`` 의 record 리스트.
        seed_url: 기준 host 결정용.

    Returns:
        ``{"root": <root-node>, "external": [<node>, ...]}``.
    """
    seed_parsed = urlparse(seed_url)
    seed_host = seed_parsed.hostname or ""

    root: dict = {
        "url": f"{seed_parsed.scheme}://{seed_host}/" if seed_host else seed_url,
        "title": None,
        "depth": 0,
        "source": "seed",
        "status": None,
        "children": [],
    }
    external: list[dict] = []

    # path segment 별 노드를 캐시 — 같은 prefix 가 여러 URL 사이에 공유될 때.
    # key: (host, "/seg1/seg2") → node.
    path_index: dict[tuple[str, str], dict] = {(seed_host, ""): root}

    for r in records:
        url = r.get("url")
        if not url:
            continue
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host != seed_host:
            external.append(_node_from_record(r))
            continue

        # 같은 host — path segment 로 nest.
        path = parsed.path or "/"
        segments = [s for s in path.split("/") if s]
        # 최종 노드. 중간 segment 는 합성 노드 (URL 이 직접 나타나지 않음).
        cur = root
        cur_path = ""
        for seg in segments:
            cur_path = f"{cur_path}/{seg}"
            key = (host, cur_path)
            child = path_index.get(key)
            if child is None:
                child = {
                    # 중간 노드는 가상 — URL 은 공통 prefix.
                    "url": f"{parsed.scheme}://{host}{cur_path}",
                    "title": None,
                    "depth": cur["depth"] + 1,
                    "source": "(path)",
                    "status": None,
                    "children": [],
                }
                path_index[key] = child
                cur["children"].append(child)
            cur = child
        # 실제 record 의 메타를 leaf 노드에 덮어씀 (status / title / source).
        cur["title"] = r.get("title")
        cur["status"] = r.get("status")
        cur["source"] = r.get("source", cur["source"])
        cur["depth"] = r.get("depth", cur["depth"])

    return {"root": root, "external": external}


# ── HTML 렌더 ─────────────────────────────────────────────────────────────


def render_self_contained_tree_html(
    crawl: dict, path: dict, meta: Optional[dict] = None,
) -> str:
    """두 트리를 한 HTML 에 탭으로 묶어 self-contained 본문 반환.

    외부 자산 의존성 0 — 받는 사람이 더블클릭만 하면 끝.
    """
    meta = meta or {}
    seed = html_escape(str(meta.get("seed_url", "")))
    job = html_escape(str(meta.get("job_id", "")))
    crawl_html = _render_tree_section(crawl, mode="crawl")
    path_html = _render_tree_section(path, mode="path")

    return _PAGE_TEMPLATE.format(
        seed=seed, job=job, crawl=crawl_html, path=path_html,
    )


def _render_tree_section(tree: dict, mode: str) -> str:
    root = tree.get("root")
    if not root:
        return '<p class="muted">— 트리 없음 —</p>'
    extras_key = "orphans" if mode == "crawl" else "external"
    extras = tree.get(extras_key, [])
    extras_label = "Orphans (sitemap 등)" if mode == "crawl" else "외부 host"

    body = '<ul class="tree">' + _render_node(root) + '</ul>'
    if extras:
        body += (
            f'<h3 class="extras-title">{html_escape(extras_label)} ({len(extras)})</h3>'
            '<ul class="tree">'
            + "".join(_render_node(n) for n in extras)
            + '</ul>'
        )
    return body


def _render_node(node: dict) -> str:
    title = node.get("title")
    title_text = f" — {html_escape(str(title))}" if title else ""
    status = node.get("status")
    status_badge = (
        f' <span class="badge st-{_status_class(status)}">{status}</span>'
        if status is not None else ""
    )
    src = node.get("source") or ""
    src_badge = (
        f' <span class="badge src">{html_escape(src)}</span>'
        if src and src != "(path)" else ""
    )
    safe_url = html_escape(node.get("url", ""))

    children = node.get("children") or []
    if children:
        child_html = (
            '<ul class="tree">'
            + "".join(_render_node(c) for c in children)
            + '</ul>'
        )
        # depth 가 깊으면 기본 접힘 — 큰 사이트 페이지 폭증 방지.
        is_open = (node.get("depth", 0) <= 1)
        open_attr = " open" if is_open else ""
        return (
            f'<li><details{open_attr}>'
            f'<summary><a href="{safe_url}" target="_blank">{safe_url}</a>'
            f'{title_text}{status_badge}{src_badge} '
            f'<span class="count">({_count_descendants(node)})</span>'
            f'</summary>{child_html}</details></li>'
        )
    return (
        f'<li><a href="{safe_url}" target="_blank">{safe_url}</a>'
        f'{title_text}{status_badge}{src_badge}</li>'
    )


def _count_descendants(node: dict) -> int:
    children = node.get("children") or []
    n = len(children)
    for c in children:
        n += _count_descendants(c)
    return n


def _status_class(status) -> str:
    try:
        s = int(status)
    except (TypeError, ValueError):
        return "unknown"
    if 200 <= s < 300:
        return "ok"
    if 300 <= s < 400:
        return "warn"
    return "fail"


_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8"/>
  <title>Site Tree — {job}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #fafafa; color: #1d1d1f; margin: 0; padding: 24px; }}
    h1 {{ margin: 0 0 6px; }}
    .meta {{ font-family: ui-monospace, Menlo, Consolas, monospace; color: #6e6e73;
             font-size: 0.9em; margin-bottom: 16px; }}
    .tabs {{ display: flex; gap: 8px; margin: 12px 0; }}
    .tab {{ padding: 6px 14px; border: 1px solid #d2d2d7; border-radius: 6px;
            cursor: pointer; background: #fff; font-size: 0.9em; }}
    .tab.active {{ background: #0071e3; color: #fff; border-color: #0071e3; }}
    .tree {{ list-style: none; padding-left: 18px; margin: 0; }}
    .tree li {{ padding: 2px 0; line-height: 1.5; }}
    .tree details > summary {{ cursor: pointer; list-style: none; }}
    .tree details > summary::-webkit-details-marker {{ display: none; }}
    .tree details > summary::before {{
      content: "▶︎"; display: inline-block; width: 1em; margin-right: 4px;
      color: #6e6e73; transition: transform 0.15s ease;
    }}
    .tree details[open] > summary::before {{ transform: rotate(90deg); }}
    .tree a {{ color: #0071e3; text-decoration: none; }}
    .tree a:hover {{ text-decoration: underline; }}
    .badge {{ display: inline-block; padding: 1px 6px; border-radius: 8px;
              font-size: 0.78em; margin-left: 4px; }}
    .badge.src {{ background: #ececf0; color: #6e6e73; }}
    .badge.st-ok {{ background: #d6f3df; color: #0a7c2f; }}
    .badge.st-warn {{ background: #fde4c2; color: #b25b00; }}
    .badge.st-fail {{ background: #fbd0d0; color: #c70000; }}
    .badge.st-unknown {{ background: #ececf0; color: #6e6e73; }}
    .count {{ color: #6e6e73; font-size: 0.85em; }}
    .extras-title {{ margin-top: 14px; font-size: 1em; color: #6e6e73; }}
    .pane {{ display: none; background: #fff; border: 1px solid #e5e5e7;
             border-radius: 8px; padding: 16px 20px; }}
    .pane.active {{ display: block; }}
    .muted {{ color: #6e6e73; }}
  </style>
</head>
<body>
  <h1>Site Hierarchy</h1>
  <div class="meta">seed: {seed} · job: {job}</div>
  <div class="tabs">
    <button class="tab active" data-pane="crawl">크롤 토폴로지</button>
    <button class="tab" data-pane="path">URL 경로</button>
  </div>
  <div id="pane-crawl" class="pane active">{crawl}</div>
  <div id="pane-path" class="pane">{path}</div>
  <script>
    document.querySelectorAll('.tab').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('pane-' + btn.dataset.pane).classList.add('active');
      }});
    }});
  </script>
</body>
</html>
"""
