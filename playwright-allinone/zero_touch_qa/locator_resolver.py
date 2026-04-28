import re
import logging

from playwright.sync_api import Page, Locator

log = logging.getLogger(__name__)

# DSL target prefix 상수 — _resolve_* 와 _raw_* 양쪽에서 공용.
_ROLE_PREFIX = "role="


class LocatorResolver:
    """
    Dify가 생성한 target을 Playwright Locator로 변환하는 7단계 시맨틱 탐색 엔진.

    탐색 순서:
      1. role + name   (접근성 역할 기반, 가장 안정적)
      2. text          (화면 표시 텍스트)
      3. label         (입력 폼 라벨)
      4. placeholder   (입력 필드 힌트)
      5. testid        (data-testid 속성)
      6. CSS / XPath   (구조적 폴백)
      7. 존재 검증     (count > 0 확인 후 반환, 실패 시 None)
    """

    def __init__(self, page: Page):
        self.page = page
        # 같은 시나리오 내에서 한 번 healed 된 selector 매핑.
        # 예: step 2 의 fill 이 'name=query' → 'placeholder=검색' 로 복구되면
        # step 3 의 press 가 같은 'name=query' 를 만났을 때 곧바로
        # 'placeholder=검색' 부터 시도해 동일 element 에 작용하게 한다.
        self.healed_aliases: dict[str, str] = {}

    @staticmethod
    def _safe_count(loc: Locator) -> int:
        """요소 수를 반환하되, 잘못된 선택자 시 0 을 반환한다."""
        try:
            return loc.count()
        except Exception:
            return 0

    def record_alias(self, original, healed) -> None:
        """원본 target 이 healed target 으로 복구된 사실을 기록한다.

        같은 시나리오 안에서 후속 스텝이 같은 ``original`` 을 만나면
        곧바로 ``healed`` 를 첫 시도로 사용해 일관성을 유지한다.
        ``original`` 이 비어 있으면 무시한다.
        """
        if not original or not healed:
            return
        key = str(original).strip()
        val = str(healed).strip()
        if not key or not val or key == val:
            return
        if self.healed_aliases.get(key) != val:
            log.info("[Resolver] alias 등록: %s → %s", key, val)
            self.healed_aliases[key] = val

    def resolve(self, target) -> Locator | None:
        """DSL target 을 Playwright Locator 로 변환한다.

        7단계 시맨틱 탐색 순서: role→text→label→placeholder→testid→CSS/XPath→존재 검증.

        Args:
            target: DSL 스텝의 target 값. 문자열(``"role=button, name=로그인"``),
                    dict(``{"role": "button", "name": "확인"}``), 또는 None.

        Returns:
            매칭된 ``Locator`` 객체(항상 ``.first``). 요소 미발견 시 ``None``.

        T-A (P0.4) 확장: target 후미 modifier (`, nth=N` / `, has_text=...`) 처리.
        AST 변환기가 codegen 의 ``.nth(N)`` / ``.first`` / ``.filter(has_text=...)``
        를 보존하도록 출력하는 14-DSL 옵션을 receiver-side 에서 해석.
        """
        if not target:
            return None

        # 직전 healed alias 가 있으면 그쪽을 우선 사용
        if isinstance(target, str):
            aliased = self.healed_aliases.get(target.strip())
            if aliased:
                log.debug("[Resolver] alias 사용: %s → %s", target, aliased)
                target = aliased

        # Dict 타겟 (Dify가 JSON 객체로 보낸 경우)
        if isinstance(target, dict):
            return self._resolve_dict(target)

        target_str = str(target).strip()

        # T-A (P0.4) — 후미 modifier (nth, has_text) 추출.
        # 본체 selector 와 modifier 를 분리.
        base_str, modifiers = _split_modifiers(target_str)

        if not modifiers:
            # 기존 경로 — .first 가 즉시 적용된 단일 element locator 반환.
            loc = self._resolve_role(base_str)
            if loc is None:
                loc = self._resolve_semantic_prefix(base_str)
            if loc is None:
                loc = self._resolve_css_xpath(base_str)
            return loc

        # T-A modifier 경로 — raw multi-element locator 에 nth/filter 적용.
        # `.first` 위에 `.nth(N)` 거는 것은 Playwright 의미상 N≥1 일 때 빈 결과.
        raw = self._resolve_raw(base_str)
        if raw is None:
            return None
        return _apply_modifiers(raw, modifiers)

    def _resolve_dict(self, target: dict) -> Locator | None:
        """dict 형태의 target 을 키(role/label/text/placeholder/testid) 우선순위로 해석한다.

        각 키에 대해 ``count() > 0`` 존재 검증을 수행하여
        요소가 없을 때 30초 타임아웃을 방지한다.
        """
        if target.get("role"):
            loc = self.page.get_by_role(
                target["role"], name=target.get("name", "")
            )
            return loc.first if self._safe_count(loc) > 0 else None
        if target.get("label"):
            loc = self.page.get_by_label(target["label"])
            return loc.first if self._safe_count(loc) > 0 else None
        if target.get("text"):
            loc = self.page.get_by_text(target["text"])
            return loc.first if self._safe_count(loc) > 0 else None
        if target.get("placeholder"):
            loc = self.page.get_by_placeholder(target["placeholder"])
            return loc.first if self._safe_count(loc) > 0 else None
        if target.get("testid"):
            loc = self.page.get_by_test_id(target["testid"])
            return loc.first if self._safe_count(loc) > 0 else None
        # 폴백: selector 키 또는 문자열 변환
        fallback = target.get("selector", str(target))
        return self._resolve_css_xpath(str(fallback).strip())

    # name 한정자 없이 쓰면 페이지 전체에서 첫 매치(보통 헤더/로고 등) 가 잡혀
    # 의도와 다른 element 에 액션이 가는 false-positive PASS 를 만든다.
    # 이런 광범위 role 은 거부하고 fallback_targets 로 강제 진입시킨다.
    _AMBIGUOUS_ROLES_WITHOUT_NAME = {
        "link", "button", "textbox", "checkbox", "radio",
        "searchbox", "combobox", "menuitem", "tab", "option",
    }

    def _resolve_role(self, target_str: str) -> Locator | None:
        """``role=`` 접두사가 있는 target 을 get_by_role 로 해석한다.

        ``count() > 0`` 존재 검증을 수행하여 요소가 없을 때
        30초 타임아웃 없이 즉시 None 을 반환한다.

        ``name=`` 한정자 없는 광범위 role (link, button 등) 은 거부한다 —
        '첫 번째 검색 결과 링크' 의도가 페이지 헤더/로고에 잘못 매치되는
        false-positive PASS 를 막기 위함. fallback_targets 가 있으면 그쪽 사용.
        """
        if not target_str.startswith(_ROLE_PREFIX):
            return None
        m = re.match(r"role=(.+?),\s*name=(.+)", target_str)
        if m:
            loc = self.page.get_by_role(
                m.group(1).strip(), name=m.group(2).strip()
            )
            return loc.first if self._safe_count(loc) > 0 else None
        # role만 있고 name이 없는 경우
        role_only = target_str.replace(_ROLE_PREFIX, "", 1).strip()
        # "role=link, text=X" 같은 복합 셀렉터 → role 부분만 추출
        if "," in role_only:
            role_only = role_only.split(",", 1)[0].strip()
        if not role_only:
            return None
        if role_only.lower() in self._AMBIGUOUS_ROLES_WITHOUT_NAME:
            log.warning(
                "[Resolver] role=%r 에 name= 없음 → 광범위 매치 위험으로 거부. "
                "fallback_targets 또는 휴리스틱으로 처리",
                role_only,
            )
            return None
        loc = self.page.get_by_role(role_only)
        return loc.first if self._safe_count(loc) > 0 else None

    def _resolve_semantic_prefix(self, target_str: str) -> Locator | None:
        """text=/label=/placeholder=/testid= 접두사를 매칭하여 해당 메서드를 호출한다.

        ``count() > 0`` 존재 검증을 수행하여 요소가 없을 때
        30초 타임아웃 없이 즉시 None 을 반환한다.
        """
        prefix_map = {
            "text=": self.page.get_by_text,
            "label=": self.page.get_by_label,
            "placeholder=": self.page.get_by_placeholder,
            "testid=": self.page.get_by_test_id,
        }
        for prefix, method in prefix_map.items():
            if target_str.startswith(prefix):
                value = target_str.replace(prefix, "", 1).strip()
                loc = method(value)
                return loc.first if self._safe_count(loc) > 0 else None
        return None

    def _resolve_css_xpath(self, target_str: str) -> Locator | None:
        """CSS 선택자 또는 XPath 로 요소를 탐색하고, count > 0 이면 반환한다."""
        try:
            loc = self.page.locator(target_str)
            if self._safe_count(loc) > 0:
                return loc.first
        except Exception:
            log.debug("CSS/XPath 탐색 실패: %s", target_str)
        return None

    def _resolve_raw(self, base_str: str) -> Locator | None:
        """T-A modifier 경로용. ``.first`` 미적용 multi-element Locator 반환.

        ``_resolve_role`` / ``_resolve_semantic_prefix`` / ``_resolve_css_xpath``
        와 같은 dispatch 순서를 따르되, 단일 element 로 reduce 하지 않는다.
        modifier (`nth=N` / `has_text=...`) 가 있을 때만 호출.
        """
        loc = self._raw_role(base_str)
        if loc is None:
            loc = self._raw_semantic_prefix(base_str)
        if loc is None:
            loc = self._raw_css_xpath(base_str)
        return loc

    def _raw_role(self, base_str: str) -> Locator | None:
        """``role=...`` 패턴의 raw multi-element locator. modifier 가 명시됐으므로
        ambiguous role 거부는 적용 안 한다."""
        if not base_str.startswith(_ROLE_PREFIX):
            return None
        m = re.match(r"role=(.+?),\s*name=(.+)", base_str)
        if m:
            loc = self.page.get_by_role(
                m.group(1).strip(), name=m.group(2).strip()
            )
            return loc if self._safe_count(loc) > 0 else None
        role_only = base_str.replace(_ROLE_PREFIX, "", 1).strip()
        if "," in role_only:
            role_only = role_only.split(",", 1)[0].strip()
        if not role_only:
            return None
        loc = self.page.get_by_role(role_only)
        return loc if self._safe_count(loc) > 0 else None

    def _raw_semantic_prefix(self, base_str: str) -> Locator | None:
        """``text=`` / ``label=`` / ``placeholder=`` / ``testid=`` 의 raw locator."""
        prefix_map = {
            "text=": self.page.get_by_text,
            "label=": self.page.get_by_label,
            "placeholder=": self.page.get_by_placeholder,
            "testid=": self.page.get_by_test_id,
        }
        for prefix, method in prefix_map.items():
            if base_str.startswith(prefix):
                value = base_str.replace(prefix, "", 1).strip()
                loc = method(value)
                return loc if self._safe_count(loc) > 0 else None
        return None

    def _raw_css_xpath(self, base_str: str) -> Locator | None:
        """CSS/XPath 의 raw locator (``.first`` 미적용)."""
        try:
            loc = self.page.locator(base_str)
            if self._safe_count(loc) > 0:
                return loc
        except Exception:
            log.debug("[Resolver] raw CSS/XPath 탐색 실패: %s", base_str)
        return None


# ─────────────────────────────────────────────────────────────────────────
# Modifier 처리 (T-A / P0.4)
# ─────────────────────────────────────────────────────────────────────────

# nth/has_text 후미 modifier 파싱 — base selector 안의 콤마 (예 'role=link, name=뉴스')
# 와 구분. modifier 키 prefix 로만 매칭한다.
_MODIFIER_KEYS = ("nth", "has_text")


def _split_modifiers(target_str: str) -> tuple[str, list[tuple[str, str]]]:
    """target 문자열의 끝 부분에서 ``, nth=N`` / ``, has_text=T`` 를 분리.

    ``role=link, name=뉴스, nth=1, has_text=메인`` → base=``role=link, name=뉴스``,
    modifiers=[(``nth``, ``1``), (``has_text``, ``메인``)].

    base 문자열 안의 ``, name=...`` 은 modifier 가 아니므로 보존된다.
    """
    parts = target_str.split(", ")
    modifiers: list[tuple[str, str]] = []
    while parts:
        last = parts[-1]
        if "=" not in last:
            break
        key, _, value = last.partition("=")
        key = key.strip()
        if key not in _MODIFIER_KEYS:
            break
        modifiers.append((key, value.strip()))
        parts.pop()
    modifiers.reverse()
    return ", ".join(parts), modifiers


def _apply_modifiers(
    loc: Locator, modifiers: list[tuple[str, str]],
) -> Locator | None:
    """nth(N) / filter(has_text=T) 를 순서대로 적용. 잘못된 인자는 None 반환."""
    for key, value in modifiers:
        try:
            if key == "nth":
                idx = int(value)
                # nth(-1) 은 last() 와 동등 — Playwright 가 음수 지원
                loc = loc.nth(idx)
            elif key == "has_text":
                loc = loc.filter(has_text=value)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[Resolver] modifier 적용 실패 (%s=%s): %s",
                key, value, e,
            )
            return None
    return loc
