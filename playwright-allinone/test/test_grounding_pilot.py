"""Phase 1 T1.8 — fixture 카탈로그 파일럿 smoke (결정론적).

각 P0-FX-* 골든 시나리오의 role 기반 셀렉터가 실제 grounding 추출 결과
(인벤토리) 에 존재하는지 검증한다. 한 건이라도 빠지면 LLM 이 prompt
가이드를 받아도 그 step 의 셀렉터를 정확히 생성할 수 없다 — 즉 grounding
upper-bound 가 1.0 미만임을 의미.

본 테스트는 LLM 호출 없이 결정론적이며 실제 효과 측정 (T1.7 페어 실행) 의
선결 조건이다. 한 fixture 가 실패하면 fixture 자체나 골든을 조정해야 한다.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from grounding_eval.classifier import parse_selector

from zero_touch_qa.grounding import fetch_inventory, serialize_block
from zero_touch_qa.grounding.budget import (
    DEFAULT_TOKEN_BUDGET,
    estimate_tokens,
    fit_to_budget,
)
from zero_touch_qa.grounding.pruner import prune


FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_DIR = Path(__file__).parent / "grounding_eval" / "golden"


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _file_url(name: str) -> str:
    return "file://" + str(FIXTURES_DIR / name)


def _load_fx_golden(catalog_id: str) -> dict:
    path = GOLDEN_DIR / f"{catalog_id}.scenario.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_filename_from_url(url: str) -> str:
    # file:///app/test/fixtures/click.html → click.html
    return url.rsplit("/", 1)[-1]


@pytest.mark.parametrize("catalog_id", [
    "P0-FX-01", "P0-FX-02", "P0-FX-03", "P0-FX-04", "P0-FX-05",
])
def test_fixture_inventory_within_budget(catalog_id: str):
    """추출 → prune → budget fit 까지 한 페이지 인벤토리가 1500 토큰 이하."""
    spec = _load_fx_golden(catalog_id)
    fixture_name = _fixture_filename_from_url(spec["target_url"])
    url = _file_url(fixture_name)

    inv = fetch_inventory(url)
    if inv.error:
        pytest.skip(f"인벤토리 추출 실패 (browser 없을 수 있음): {inv.error}")

    prune(inv)
    fit_to_budget(inv, budget=DEFAULT_TOKEN_BUDGET)
    block = serialize_block(inv)
    assert block, "직렬화 블록이 비어 있음"
    tokens = estimate_tokens(block)
    assert tokens <= DEFAULT_TOKEN_BUDGET, f"한도 초과: {tokens} > {DEFAULT_TOKEN_BUDGET}"


def _golden_role_step_missing_from_inventory(
    step: dict, inv_keys: set[tuple[str, str]],
) -> bool:
    """골든 step 의 role 셀렉터가 인벤토리에 없는지 확인."""
    if step.get("mock_target"):
        return False
    ps = parse_selector(str(step.get("target") or ""))
    if ps.kind != "role":
        return False
    role = ps.role.lower() if ps.role else ""
    name_norm = _normalize(ps.name or "")
    if not name_norm:
        # name 없는 role 셀렉터 (예: getByRole('heading')) 는 role 만 매칭
        return not any(r == role for r, _n in inv_keys)
    return (role, name_norm) not in inv_keys


@pytest.mark.parametrize("catalog_id", [
    "P0-FX-01", "P0-FX-02", "P0-FX-03", "P0-FX-05",
])
def test_fixture_role_selectors_present_in_inventory(catalog_id: str):
    """골든의 getByRole 셀렉터들이 인벤토리에 모두 존재해야 한다.

    P0-FX-04 는 verify-only 페이지라 role 기반 selector 가 없어 제외.
    """
    spec = _load_fx_golden(catalog_id)
    fixture_name = _fixture_filename_from_url(spec["target_url"])
    url = _file_url(fixture_name)

    inv = fetch_inventory(url)
    if inv.error:
        pytest.skip(f"인벤토리 추출 실패: {inv.error}")

    inv_keys = {(e.role.lower(), _normalize(e.name)) for e in inv.elements}
    missing = [
        (s.get("step"), s.get("target"))
        for s in spec["steps"]
        if _golden_role_step_missing_from_inventory(s, inv_keys)
    ]

    assert not missing, (
        f"{catalog_id}: 골든의 role 셀렉터 {len(missing)} 개가 인벤토리에 없음 — "
        f"grounding upper-bound 가 100% 미만이라 fixture/골든 조정 필요. "
        f"missing={missing}"
    )


def test_inventory_block_prepends_in_dify_client_for_each_fixture(monkeypatch):
    """5종 fixture 각각에 대해 _prepend_dom_inventory 가 마커 블록을 srs 앞에 붙인다."""
    from zero_touch_qa.config import Config
    from zero_touch_qa.dify_client import DifyClient

    cfg = Config.from_env()
    client = DifyClient(cfg)

    failures: list[str] = []
    for cid in ["P0-FX-01", "P0-FX-02", "P0-FX-03", "P0-FX-04", "P0-FX-05"]:
        spec = _load_fx_golden(cid)
        fixture_name = _fixture_filename_from_url(spec["target_url"])
        url = _file_url(fixture_name)
        merged, meta = client._prepend_dom_inventory(
            srs_text="ORIGINAL", target_url=url,
        )
        if not meta.get("used"):
            failures.append(f"{cid}: meta={meta}")
            continue
        if merged.find("=== DOM INVENTORY") > merged.find("ORIGINAL"):
            failures.append(f"{cid}: 마커가 ORIGINAL 뒤에 옴")
        if (meta.get("grounding_inventory_tokens") or 0) <= 0:
            failures.append(f"{cid}: tokens=0")

    if failures:
        pytest.fail("fixture 파일럿 실패: " + "; ".join(failures))


def test_grounding_eval_token_budget_env_override(monkeypatch):
    """env GROUNDING_TOKEN_BUDGET 으로 한도 override 동작."""
    from zero_touch_qa.config import Config
    from zero_touch_qa.dify_client import DifyClient

    cfg = Config.from_env()
    client = DifyClient(cfg)

    # 한도를 빡빡하게 → truncate 발생해야 함. GUIDE_FOOTER 만 ~131 토큰, 1 요소
    # 추가로 ~40 토큰. 한도 300 이면 2~3 요소만 살아남고 truncated=True 가 된다.
    monkeypatch.setenv("GROUNDING_TOKEN_BUDGET", "300")
    url = _file_url("full_dsl.html")
    _, meta = client._prepend_dom_inventory(srs_text="x", target_url=url)
    if not meta.get("used"):
        pytest.skip(f"인벤토리 추출 실패: {meta.get('error')}")
    assert meta.get("grounding_truncated") is True
    assert meta.get("grounding_inventory_tokens", 0) <= 300
