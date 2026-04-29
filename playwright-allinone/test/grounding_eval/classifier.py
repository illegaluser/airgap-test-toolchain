"""Phase 1 T1.7 — selector 분류기.

골든 스텝의 셀렉터와 LLM 출력의 셀렉터를 비교해 정확/부분/실패 3분류로 라벨링한다.

분류 정의 (docs/PLAN_GROUNDING_RECORDING_AGENT.md §"DoD §분류 정의"):

| 분류 | 정의 |
| --- | --- |
| exact   | role+name 매칭 (getByRole 끼리 또는 의미적 동등) / CSS 동일 토큰 |
| partial | 같은 요소를 가리키지만 셀렉터 우선순위가 낮은 형식 (CSS-id ↔ role 등) |
| fail    | 다른 요소 또는 빈 selector / 의미 없음 |

mock_target=true 또는 action ∈ {wait, navigate} 인 step 은 셀렉터 평가에서 제외.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# action 별 selector 평가 정책
SKIP_ACTIONS = frozenset({"wait", "navigate"})

# 정규식: getByRole('role', { name: '<name>' })
_ROLE_NAME_RE = re.compile(
    r"getByRole\(\s*['\"]([\w-]+)['\"]\s*(?:,\s*\{\s*name:\s*['\"]([^'\"]*)['\"]\s*\})?\s*\)",
    re.IGNORECASE,
)

# 정규식: getByText('<text>') / getByLabel / getByPlaceholder
_BY_TEXT_RE = re.compile(
    r"getBy(Text|Label|Placeholder|TestId)\(\s*['\"]([^'\"]*)['\"]\s*\)",
    re.IGNORECASE,
)

# CSS id (#foo) / class (.bar) / attr 셀렉터
_CSS_ID_RE = re.compile(r"#([\w-]+)")
_CSS_CLASS_RE = re.compile(r"\.([\w-]+)")
_CSS_ATTR_RE = re.compile(r"\[([\w-]+)(?:\s*=\s*['\"]?([^'\"\]]*)['\"]?)?\]")


@dataclass
class ParsedSelector:
    """셀렉터 문자열의 의미 파싱 결과."""
    raw: str
    kind: str  # "role" | "text" | "label" | "placeholder" | "testid" | "css" | "empty"
    role: Optional[str] = None
    name: Optional[str] = None
    text: Optional[str] = None       # getByText / getByLabel
    css_ids: tuple[str, ...] = ()
    css_classes: tuple[str, ...] = ()
    css_attrs: tuple[tuple[str, str], ...] = ()


def parse_selector(sel: str) -> ParsedSelector:
    """셀렉터 문자열을 의미 단위로 파싱.

    빈 문자열 / None → kind=empty.
    매칭 실패 → kind=css (best-effort, 토큰 그대로 유지).
    """
    raw = (sel or "").strip()
    if not raw:
        return ParsedSelector(raw="", kind="empty")

    m = _ROLE_NAME_RE.search(raw)
    if m:
        return ParsedSelector(
            raw=raw, kind="role", role=m.group(1).lower(),
            name=(m.group(2) or "").strip() or None,
        )
    m = _BY_TEXT_RE.search(raw)
    if m:
        verb = m.group(1).lower()  # text / label / placeholder / testid
        kind_map = {"text": "text", "label": "label", "placeholder": "placeholder", "testid": "testid"}
        return ParsedSelector(raw=raw, kind=kind_map.get(verb, "text"), text=m.group(2))

    ids = tuple(_CSS_ID_RE.findall(raw))
    classes = tuple(_CSS_CLASS_RE.findall(raw))
    attrs = tuple((k.lower(), v or "") for k, v in _CSS_ATTR_RE.findall(raw))
    return ParsedSelector(
        raw=raw, kind="css",
        css_ids=ids, css_classes=classes, css_attrs=attrs,
    )


def _normalize_text(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def classify_selector(golden: str, observed: str) -> str:
    """golden 과 observed 셀렉터를 비교해 'exact' / 'partial' / 'fail' 반환.

    규칙:
    - 둘 다 empty → exact (navigate 같이 selector 가 의미 없는 경우)
    - 한쪽만 empty → fail
    - 정규화 후 raw 동일 → exact
    - 둘 다 role 형식: role+name 모두 일치 → exact, role 만 일치 → partial,
      role 도 다름 → fail
    - 한쪽 role / 다른 쪽 css 또는 text: 같은 요소를 의미 — name/text 가
      css ids 의 어느 한 토큰과 substring 매칭이거나 그 반대 → partial
    - 둘 다 css: id 집합 또는 첫 id 가 같으면 exact, 부분 교집합 → partial,
      교집합 0 → fail
    - 둘 다 text/label/placeholder/testid 형식: text 일치 → exact, 부분일치 → partial
    """
    g = parse_selector(golden)
    o = parse_selector(observed)

    if g.kind == "empty" and o.kind == "empty":
        return "exact"
    if g.kind == "empty" or o.kind == "empty":
        return "fail"

    if g.raw == o.raw:
        return "exact"

    # role vs role
    if g.kind == "role" and o.kind == "role":
        if g.role == o.role and _normalize_text(g.name) == _normalize_text(o.name):
            return "exact"
        if g.role == o.role:
            # role 일치, name 만 부분일치 (substring) → partial
            if g.name and o.name and (
                _normalize_text(g.name) in _normalize_text(o.name)
                or _normalize_text(o.name) in _normalize_text(g.name)
            ):
                return "partial"
            return "partial"  # 같은 role 이라도 name 불일치는 부분
        return "fail"

    # text/label/placeholder/testid 끼리
    text_kinds = {"text", "label", "placeholder", "testid"}
    if g.kind in text_kinds and o.kind in text_kinds:
        if g.kind == o.kind and _normalize_text(g.text) == _normalize_text(o.text):
            return "exact"
        if _normalize_text(g.text) and _normalize_text(o.text) and (
            _normalize_text(g.text) in _normalize_text(o.text)
            or _normalize_text(o.text) in _normalize_text(g.text)
        ):
            return "partial"
        return "fail"

    # css vs css
    if g.kind == "css" and o.kind == "css":
        gids = set(g.css_ids)
        oids = set(o.css_ids)
        if gids and gids == oids:
            return "exact"
        if gids & oids:
            return "partial"
        gcls = set(g.css_classes)
        ocls = set(o.css_classes)
        if gcls and gcls == ocls and not gids and not oids:
            return "exact"
        if gcls & ocls:
            return "partial"
        return "fail"

    # 혼합: role ↔ css / text ↔ css 등
    # 한쪽 role 의 name 이 다른 쪽 css ids/classes 의 어느 토큰과 substring 일치하면 partial.
    g_keywords = _selector_keywords(g)
    o_keywords = _selector_keywords(o)
    if g_keywords and o_keywords:
        norm_g = {_normalize_text(k) for k in g_keywords if k}
        norm_o = {_normalize_text(k) for k in o_keywords if k}
        # 완전일치 토큰 있음 → partial (스타일 불일치라 exact 는 안 줌)
        if norm_g & norm_o:
            return "partial"
        # substring 매칭
        for a in norm_g:
            for b in norm_o:
                if a and b and (a in b or b in a):
                    return "partial"
    return "fail"


def _selector_keywords(p: ParsedSelector) -> list[str]:
    """role.name / text / css ids / css classes 를 비교 토큰으로 평탄화."""
    out: list[str] = []
    if p.role:
        out.append(p.role)
    if p.name:
        out.append(p.name)
    if p.text:
        out.append(p.text)
    out.extend(p.css_ids)
    out.extend(p.css_classes)
    for k, v in p.css_attrs:
        if v:
            out.append(v)
    return [t for t in out if t]


# ── 페이지 단위 채점 ──────────────────────────────────────────────────────────


@dataclass
class StepEval:
    step: int
    action: str
    selector_class: str  # exact | partial | fail | skipped
    golden_target: str
    observed_target: str
    note: str = ""


@dataclass
class PageEval:
    catalog_id: str
    target_url: str
    steps: list[StepEval]
    healer_calls: int = 0
    planner_elapsed_ms: float = 0.0
    grounding_inventory_tokens: Optional[int] = None
    grounding_truncated: Optional[bool] = None
    grounding_used: Optional[bool] = None

    def selector_accuracy(self) -> float:
        scored = [s for s in self.steps if s.selector_class != "skipped"]
        if not scored:
            return 0.0
        exact = sum(1 for s in scored if s.selector_class == "exact")
        return exact / len(scored)

    def partial_rate(self) -> float:
        scored = [s for s in self.steps if s.selector_class != "skipped"]
        if not scored:
            return 0.0
        return sum(1 for s in scored if s.selector_class == "partial") / len(scored)


def evaluate_page(
    *,
    catalog_id: str,
    target_url: str,
    golden_steps: list[dict],
    observed_steps: list[dict],
    healer_calls: int = 0,
    planner_elapsed_ms: float = 0.0,
    grounding_inventory_tokens: Optional[int] = None,
    grounding_truncated: Optional[bool] = None,
    grounding_used: Optional[bool] = None,
) -> PageEval:
    """한 페이지의 골든 vs 관측 비교."""
    step_evals: list[StepEval] = []
    obs_by_step = {int(s.get("step", i + 1)): s for i, s in enumerate(observed_steps)}

    for g in golden_steps:
        step_no = int(g.get("step", 0))
        action = g.get("action", "")
        golden_target = str(g.get("target", "") or "")
        mock_target = bool(g.get("mock_target"))

        o = obs_by_step.get(step_no, {})
        observed_target = str(o.get("target", "") or "")

        # mock_target 또는 wait/navigate 는 selector 평가 제외
        if mock_target or action in SKIP_ACTIONS:
            cls = "skipped"
            note = "selector 평가 제외 (mock_target 또는 wait/navigate)"
        elif not o:
            cls = "fail"
            note = "관측 step 누락"
        elif o.get("action") != action:
            cls = "fail"
            note = f"action 불일치: golden={action} observed={o.get('action')}"
        else:
            cls = classify_selector(golden_target, observed_target)
            note = ""

        step_evals.append(StepEval(
            step=step_no, action=action, selector_class=cls,
            golden_target=golden_target, observed_target=observed_target,
            note=note,
        ))

    return PageEval(
        catalog_id=catalog_id,
        target_url=target_url,
        steps=step_evals,
        healer_calls=healer_calls,
        planner_elapsed_ms=planner_elapsed_ms,
        grounding_inventory_tokens=grounding_inventory_tokens,
        grounding_truncated=grounding_truncated,
        grounding_used=grounding_used,
    )
