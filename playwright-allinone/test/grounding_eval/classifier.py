"""Phase 1 T1.7 — selector classifier.

Compares the golden step selector against the LLM output selector and
labels each pair as one of three classes: exact / partial / fail.

Class definitions (docs/PLAN_GROUNDING_RECORDING_AGENT.md §"DoD §classification"):

| class   | definition |
| ---     | --- |
| exact   | role+name match (getByRole-to-getByRole or semantically equivalent) / identical CSS tokens |
| partial | points to the same element but the selector format has lower priority (CSS-id ↔ role, etc.) |
| fail    | different element or empty / meaningless selector |

Steps with mock_target=true or action ∈ {wait, navigate} are excluded
from selector evaluation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# selector evaluation policy by action
SKIP_ACTIONS = frozenset({"wait", "navigate"})

# regex: getByRole('role', { name: '<name>' })
_ROLE_NAME_RE = re.compile(
    r"getByRole\(\s*['\"]([\w-]+)['\"]\s*(?:,\s*\{\s*name:\s*['\"]([^'\"]*)['\"]\s*\})?\s*\)",
    re.IGNORECASE,
)

# regex: getByText('<text>') / getByLabel / getByPlaceholder
_BY_TEXT_RE = re.compile(
    r"getBy(Text|Label|Placeholder|TestId)\(\s*['\"]([^'\"]*)['\"]\s*\)",
    re.IGNORECASE,
)

# CSS id (#foo) / class (.bar) / attribute selectors
_CSS_ID_RE = re.compile(r"#([\w-]+)")
_CSS_CLASS_RE = re.compile(r"\.([\w-]+)")
_CSS_ATTR_RE = re.compile(r"\[([\w-]+)(?:\s*=\s*['\"]?([^'\"\]]*)['\"]?)?\]")


@dataclass
class ParsedSelector:
    """Semantic parse result of a selector string."""
    raw: str
    kind: str  # "role" | "text" | "label" | "placeholder" | "testid" | "css" | "empty"
    role: Optional[str] = None
    name: Optional[str] = None
    text: Optional[str] = None       # getByText / getByLabel
    css_ids: tuple[str, ...] = ()
    css_classes: tuple[str, ...] = ()
    css_attrs: tuple[tuple[str, str], ...] = ()


def parse_selector(sel: str) -> ParsedSelector:
    """Parse a selector string into semantic parts.

    Empty string / None → kind=empty.
    No regex match → kind=css (best-effort, keep tokens as-is).
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
    """Compare golden and observed selectors and return 'exact' / 'partial' / 'fail'.

    Rules:
    - both empty → exact (e.g. navigate where selector has no meaning)
    - only one side empty → fail
    - identical raw after normalization → exact
    - both role form: role+name both match → exact, only role matches →
      partial, role also differs → fail
    - one role / other css or text: same element if name/text substring-matches
      one of the css ids or vice versa → partial
    - both css: id sets identical (or first id same) → exact, partial overlap
      → partial, no overlap → fail
    - both text/label/placeholder/testid: text equal → exact, partial substring
      → partial
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
            # role match, name only partial (substring) → partial
            if g.name and o.name and (
                _normalize_text(g.name) in _normalize_text(o.name)
                or _normalize_text(o.name) in _normalize_text(g.name)
            ):
                return "partial"
            return "partial"  # same role with name mismatch is still partial
        return "fail"

    # text/label/placeholder/testid pairs
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

    # mixed: role ↔ css / text ↔ css etc.
    # If one side's role.name substring-matches any token of the other side's
    # css ids/classes, return partial.
    g_keywords = _selector_keywords(g)
    o_keywords = _selector_keywords(o)
    if g_keywords and o_keywords:
        norm_g = {_normalize_text(k) for k in g_keywords if k}
        norm_o = {_normalize_text(k) for k in o_keywords if k}
        # exact-token overlap → partial (style mismatch, so don't promote to exact)
        if norm_g & norm_o:
            return "partial"
        # substring match
        for a in norm_g:
            for b in norm_o:
                if a and b and (a in b or b in a):
                    return "partial"
    return "fail"


def _selector_keywords(p: ParsedSelector) -> list[str]:
    """Flatten role.name / text / css ids / css classes into comparison tokens."""
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


# ── per-page scoring ────────────────────────────────────────────────────────


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
    """Compare golden vs observed for one page."""
    step_evals: list[StepEval] = []
    obs_by_step = {int(s.get("step", i + 1)): s for i, s in enumerate(observed_steps)}

    for g in golden_steps:
        step_no = int(g.get("step", 0))
        action = g.get("action", "")
        golden_target = str(g.get("target", "") or "")
        mock_target = bool(g.get("mock_target"))

        o = obs_by_step.get(step_no, {})
        observed_target = str(o.get("target", "") or "")

        # skip selector evaluation for mock_target or wait/navigate
        if mock_target or action in SKIP_ACTIONS:
            cls = "skipped"
            note = "selector eval skipped (mock_target or wait/navigate)"
        elif not o:
            cls = "fail"
            note = "observed step missing"
        elif o.get("action") != action:
            cls = "fail"
            note = f"action mismatch: golden={action} observed={o.get('action')}"
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
