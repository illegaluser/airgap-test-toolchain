# PLAN — Step 8 click→close PASS, resolver miss 진단/보정

(원래 Step 8 click→close 만 다뤘으나, 동일 시나리오 후속 재생에서 발견된
Step 27 류의 resolver 0건 silent skip 도 함께 다룸)

## 추가 — resolver miss 진단 로그

`_try_initial_target` 가 `resolver.resolve()` 결과 None 일 때 silent 하게 다음
치유 단계로 빠지던 것을 가시화. `_log_resolver_miss` 가 동일 name 으로
role={button, link, tab, menuitem} 별 count + text count 를 진단 로그로 남김.
이 로그 덕분에 Step 27 의 진짜 원인이 "role=button 0건 / role=tab 1건 / text
2건" 임이 드러남 (codegen 라벨링 부정확 또는 페이지 컨텍스트 불일치).

## 추가 — role 오라벨링 보정 (resolver)

`_resolve_role` 에 fallback 추가 — `role=X, name=Y` 가 0건이면 등가 클릭-가능
role (`link`, `button`, `menuitem`) 들에서 정확히 1개만 매치할 때 채택.

**`tab` 은 fallback 에서 제외**. 사유: tab 활성화는 button/link click 과
의미가 다름. footer link 의도였던 step 27 이 tab 1건 매치로 잘못 채택되어
PASS 처리되는 false-PASS 사례 확인 (실제 의도된 navigation 미발생).

## 추가 — DIFY_API_KEY 자동 조회 (폐쇄망 다중 PC 배포 대응)

### 문제

`recording-ui/recording_service/replay_proxy.py` 가 컨테이너 Dify DB 에서 fresh token 을
자동 조회하는 함수 (`_fetch_dify_token_from_container`) 를 보유하고 있었으나,
이 동작이 **recording UI 경유 호출에만** 연결돼 있었다. 결과:

- Recording UI → 자동 token 주입 ✓
- 직접 CLI (`python -m zero_touch_qa --mode execute …`), regression test 재생,
  자동화 스크립트 → env 빈값 → 401 ✗
- 컨테이너 재 provision 으로 토큰이 재발급되면, 사용자가 export 한 옛 토큰도
  무효 → 모든 호출 경로에서 401

폐쇄망 다중 PC 배포 시 PC 마다 사람이 토큰 export 작업을 해야 하고, provision
때마다 갱신 작업이 또 필요해 운영 부담이 큼.

### 해결

token 자동 조회를 **가장 낮은 레이어 (Config)** 로 내림. 모든 호출 경로가
공통 혜택.

- 신규 모듈 `shared/zero_touch_qa/dify_token.py` — `fetch_token_from_container()`
  단독 함수. 외부 zero_touch_qa 의존 없음 (Config / DifyClient /
  recording_service 어디서나 import 가능).
- `Config.from_env()` — `DIFY_API_KEY` env 가 빈값이면 컨테이너 DB 조회 시도.
  성공 시 INFO 로그 + 사용. 실패 시 WARNING 후 빈값 유지 (graceful degrade,
  기존 401 행동 호환).
- `recording-ui/recording_service/replay_proxy.py` 의 중복 함수 제거, 새 모듈 import 로
  대체 (단일 진실 소스 — 향후 schema 변경 시 한 곳만 수정).

### 검증

```text
$ python -m zero_touch_qa --mode execute …  # env DIFY_API_KEY 미설정
[Config] DIFY_API_KEY 자동 조회 — 컨테이너 DB 에서 토큰 획득
… Dify 치유 요청 중 …
status_code: 200  (이전: 401)
```

### 폐쇄망 / 멀티 PC 적합성

- docker exec 1회 (~수십~수백 ms), 컨테이너 미기동 시 graceful None.
- 컨테이너 이름/앱 이름은 고정 표준값 (`dscore.ttc.playwright`,
  `ZeroTouch QA Brain`) — 모든 PC 에서 동일.
- env 명시 set 한 사용자엔 영향 없음 (env 우선).
- 네트워크 egress 없음 — docker CLI + 컨테이너 내부 psql.

### 검토 후 보류

- **navigation race 폴링 재시도**: 직전 step 의 새 페이지로의 navigation 직후
  resolver 0건이 발생한다는 가설로 800ms→5s polling retry 를 시도. 검증
  결과 5s 이후에도 resolver 0건 유지 — race 가 아니라 Playwright `get_by_role`
  의 `<a role="button">` 매칭이 실 환경(인증 사용자)에서 0건이고 LocalHealer
  가 별도 셀렉터로 잡는 별개 메커니즘. 5s 지연 누적 비용 대비 효과 없음 →
  롤백.
- **LocalHealer 다중매치 핸들링 (2)**: text 2건+ ambiguous 케이스를 가시성/
  href 기준으로 우선순위 정렬. 본 시나리오에선 LocalHealer 가 모든
  ambiguous 케이스를 살리므로 실증 회귀 케이스 없이는 변경 불가. 사례
  축적 후 재검토.
- **Step navigation 효과 검증 (3)**: Step 26 같은 logo 클릭이 의도와
  다른 page 로 이동하는 false-PASS 가설. 실측 결과 false-PASS 아님 — 가설
  reject.

## 배경 (재현)

녹화 시나리오 `recordings/20ee24cc8d7a/scenario.json` 재생 중 Step 8 에서 크래시.

- Step 7: `page1` (네이버/Any-ID 팝업) 에서 '닫기' 클릭
- Step 8: `page1` 에서 '등록 안함' 클릭 → **팝업 자체가 닫힘**
- Step 9~: `page` (부모) 로 컨텍스트 복귀

실제 로그:

```text
[Step 8] click: 클릭
[Click] href=None text='등록 안함'
[Step 8] 기본 타겟 실패: Page.screenshot: Target page, context or browser has been closed
[Step 8] Dify LLM 치유 요청 중 (timeout=60s)...
playwright._impl._errors.TargetClosedError: Page.content: Target page, context or browser has been closed
```

흐름 분석:

1. `_perform_action` 의 click 자체는 성공 (`[Click]` 로그 정상 발사).
2. 직후 `_screenshot(page, ...)` 호출 — 이미 닫힌 page 라 raise.
3. `_try_initial_target` 의 generic except 가 잡아 "기본 타겟 실패" 로그 + 치유 진입.
4. `_try_dify_healer` 가 `page.content()` 호출 → `TargetClosedError` 가 unhandled
   로 worker 까지 전파 → 시나리오 전체 abort.

핵심 오류: **의도된 페이지 종료가 click 실패로 오분류됨 + healer 가 closed page
를 방어하지 않음.**

## 수정 (C: 두 문제 모두)

`shared/zero_touch_qa/executor.py`:

1. 모듈 헬퍼 `_page_closed(page)` 추가 — `is_closed()` 자체가 raise 해도
   closed 로 안전 판정.

2. `_try_initial_target`:
   - 성공 path — `_perform_action` 직후 `action == "click"` 이고 page 가 closed
     이면 스크린샷 시도 없이 PASS 반환.
   - except path — `_perform_action` 또는 `_screenshot` 가 raise 했더라도
     `action == "click" + _page_closed(page)` 면 "의도된 팝업 닫기" 로 간주하고
     PASS 반환. fail 로그가 오해를 낳지 않도록 info 레벨로 명시 로깅.

3. `_try_dify_healer`:
   - 진입 시 `_page_closed(page)` 면 즉시 `None` (skip). `page.content()` /
     `page.screenshot()` 가 unhandled raise 하지 않도록 방어.

## 적용 범위 / 비-범위

- **범위**: click action + page closed 의 PASS 처리, Dify healer 의 closed page
  방어.
- **비-범위**:
  - fill/press/select 등 비-click action 은 page closed 시 여전히 FAIL — 의도가
    "값 입력" 인데 page 가 사라졌다면 진짜 실패로 봐야 함.
  - local healer / fallback / alternatives 는 추가 가드 안 넣음. closed page
    에서 locator.resolve / perform_action 호출 시 raise 가 except 에서 잡혀
    None 으로 떨어지므로 크래시는 안 남. (Dify 만 raise 가 worker 까지 새서
    명시 가드 필요했음.)

## 대안 / 트레이드오프

- **대안 A — click→close 인정 안 함, healer 방어만**: scenario 작성자가
  명시적으로 "close" action 을 써야 함. 그러나 녹화 변환기가 click 만 발사하므로
  녹화 기반 시나리오가 항상 깨짐. 기각.
- **대안 B — 다음 step 의 page alias 가 다를 때만 PASS**: 더 정확하지만
  `_try_initial_target` 시점에는 다음 step 정보가 없음. 시그니처 확장 비용 vs
  false-PASS 위험 (click 으로 page 가 닫혔는데 다음 step 도 같은 page 를
  가리키는 모순 시나리오) 비교 시, 후자는 녹화 산출물에서 사실상 발생 불가.
  현 구현 채택.
- **대안 C — close 를 "PASS" 가 아닌 "HEALED/AUX_SKIP" 로 마킹**: 통계
  의미는 명확해지나 현재 status 분류 체계 변경 비용이 있음. 보류.

## 검증 (Step 8)

- `python3 -c "import ast; ast.parse(...)"` — syntax OK.
- 회귀 검증: 동일 시나리오 (`recordings/20ee24cc8d7a/`) 를
  `--mode execute --slow-mo 1000` 로 재생 → Step 8 PASS, Step 9 이후 부모 page
  로 정상 진행되는지 확인 필요 (사용자 환경에서 수동 검증).
