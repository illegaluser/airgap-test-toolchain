# DOM Grounding 인벤토리 스키마 (Phase 1 T1.1 산출물)

> Planner LLM 호출 직전 `srs_text` 앞에 prepend 되는 DOM 인벤토리의 직렬화 형식.

## 단일 요소 스키마

```python
@dataclass
class InventoryElement:
    role: str              # ARIA role — "button", "textbox", "link", "combobox", ...
    name: str              # 접근 가능한 이름 (label, aria-label, text content)
    text: str              # 가시 텍스트 (name 과 다를 수 있음, 클리핑 100자)
    selector_hint: str     # getByRole / getByText / getByTestId / CSS 우선순위
    visible: bool          # 사용자에게 노출 중인가
    enabled: bool          # 인터랙션 가능한가
    position: tuple[int, int] | None  # (x, y) 뷰포트 좌상단 기준 — pruner 의 viewport 외 컷용
```

`position` 은 추출 시점의 viewport 기준. 본 스키마는 직렬화 단계에서 좌표를 노출하지 않는다 (LLM 토큰 절약). pruner 내부에서만 사용.

## 인벤토리 (전체) 스키마

```python
@dataclass
class Inventory:
    target_url: str
    elements: list[InventoryElement]
    truncated: bool        # 토큰 예산 가드가 잘랐는지
    fetched_at: str        # ISO8601
    error: str | None      # 추출 실패 시 사유 (graceful degradation)
```

## LLM 친화 직렬화 (마커 블록)

`dify_client.generate_scenario()` 가 `srs_text` 앞에 prepend 하는 형식 (PLAN 의 §"아키텍처" 와 일치).

```text
=== DOM INVENTORY (target_url=https://example.com/login) ===
- {role=textbox, name="이메일", selector_hint=getByRole('textbox', {name: '이메일'})}
- {role=textbox, name="비밀번호", selector_hint=getByRole('textbox', {name: '비밀번호'})}
- {role=button, name="로그인", selector_hint=getByRole('button', {name: '로그인'})}
- {role=link, name="비밀번호 찾기", selector_hint=getByRole('link', {name: '비밀번호 찾기'})}
- {role=heading, level=1, text="로그인"}
=== END INVENTORY ===

위 인벤토리는 target_url 의 실제 DOM 에서 추출된 요소 목록이다.
- 셀렉터는 가능하면 위 인벤토리의 selector_hint 를 그대로 사용한다.
- 인벤토리에 없는 요소가 필요하면 출력에 `(요소 미발견: <설명>)` 마커를 남긴다.
- 우선순위: getByRole(role, {name}) > getByText > getByTestId > CSS

(SRS 본문 이어서)
```

### 직렬화 규칙

1. 인터랙티브 role 만 표기 — `button`, `link`, `textbox`, `combobox`, `checkbox`, `radio`, `tab`, `menuitem`, `option`, `searchbox`. 추가 컨텍스트로 `heading` / `landmark` 만 보조 노출.
2. `name` 이 비어 있으면 `text` 를 fallback. 양쪽 다 비면 해당 요소는 인벤토리에서 제외.
3. `text` 는 100자 초과 시 `…` 로 클리핑. 인벤토리 라인은 한 줄.
4. `visible=false` 또는 `enabled=false` 요소는 pruner 가 제외 (특수 케이스만 표기).
5. 같은 (role, name) 중복은 최초 N개만 (기본 N=10).

### 토큰 추정

- 한 줄 평균 약 30~80 토큰 (`tiktoken` cl100k_base 기준).
- 50개 요소 ≈ 2000~4000 토큰. T1.4 의 한도 1500 토큰 = 약 30개 (보수 출발).

## 골든 스펙 (T1.1 검증용)

다음 5종 페이지에서 인벤토리를 추출 후 사람이 검토해 골든 셋 확정.

| 페이지 ID | 카탈로그 | 기대 요소 수 (대략) | 검증 포인트 |
| --- | --- | --- | --- |
| P0-FX-01 | `click.html` | 1 button | 단일 인터랙션 |
| P0-FX-02 | `fill.html` | 2 textbox + 1 button | 폼 입력 케이스 |
| P0-FX-03 | `select.html` | 1 combobox + N option | 드롭다운 case |
| P0-FX-04 | `verify_conditions.html` | 다양 | 다중 verify 대상 |
| P0-FX-05 | `full_dsl.html` | 14대 액션 모두 | 인벤토리가 14대 시나리오 충당 가능한지 |

골든 셋은 T1.7 평가 하니스의 ground truth 로 재사용.

## 변경 이력

| 날짜 | 변경 |
| --- | --- |
| 2026-04-28 | 초기 작성 (Phase 1 T1.1) |
