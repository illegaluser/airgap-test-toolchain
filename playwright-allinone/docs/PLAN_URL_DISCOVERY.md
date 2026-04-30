# Plan: Recording UI 에 "Discover URLs" 추가

---

# Part 1. — 무엇을 왜 만드는가

## 어떤 문제가 있나

회귀(리그레션) 테스트는 "사이트가 어제 멀쩡했던 페이지가 오늘도 멀쩡한지" 자동으로 다시 확인하는 작업이다. 그러려면 먼저 **사이트 안에 어떤 페이지들이 있는지** 알아야 한다.

지금은 이 목록을 사람이 손으로 만든다. 메뉴를 하나하나 눌러보면서 "이 URL", "저 URL" 적어놓는 식이다. 사이트가 클수록 빠뜨리기 쉽고, 새 메뉴가 추가돼도 모른다. portal.koreaconnect.kr 같은 로그인이 필요한 사이트는 더 번거롭다.

## 무엇을 만드나

Recording UI(브라우저로 `http://localhost:18092` 접속해서 쓰는 화면)에 **"Discover URLs"(URL 자동 수집)** 버튼을 새로 만든다.

사용자가 하는 일은 두 가지뿐이다.
1. **시작 주소** 하나 입력 (예: `https://portal.koreaconnect.kr/user/ma/main`)
2. **로그인 프로파일** 선택 (이미 만들어둔 것 — 최근에 마무리된 기능)

버튼을 누르면 시스템이 알아서:
- 시작 주소를 열고
- 그 페이지 안의 `<a href="...">` 링크를 모으고
- 각 링크를 따라가서 또 링크를 모으고
- **같은 사이트 안의 페이지만**, 정해진 한도(기본 200페이지)와 깊이 안에서 훑은 뒤
- URL 목록을 표 형태로 화면에 보여주고 CSV 파일로 내려준다.

각 줄에는 URL, 페이지 제목, 응답 코드(정상/에러), 몇 단계 깊이의 페이지인지가 함께 기록된다.

## 어떻게 작동하나 (한 단락)

내부적으로는 사람이 브라우저를 켜서 페이지 안의 링크를 따라가는 것과 같은 일을 자동으로 한다. 로그인이 필요한 사이트도 미리 저장해둔 **세션(쿠키 묶음)** 을 자동으로 끼워 넣어 들어간다 — 이건 최근에 추가된 기능이라 여기 그대로 빌려 쓴다.

- **URL 수집(Discover)** 단계는 빠른 응답을 위해 항상 *화면을 띄우지 않고(headless)* 돈다.
- **선택 URL 검증(Tour Script)** 단계는 기본적으로 *브라우저 창을 띄워* 사용자가 진행을 직접 확인할 수 있게 한다. 백그라운드 실행이 필요하면 UI 의 "헤드리스(백그라운드) 실행" 체크박스를 켠다 (또는 다운받은 스크립트에 `TOUR_HEADLESS=1` env 설정).

1차 구현은 정적 anchor 링크 중심이다. SPA 라우터 버튼, hover 로만 열리는 메뉴, API 응답 안의 URL 까지 완전 수집하는 것은 후속 범위로 둔다.

## 이번에 만드는 범위와, 다음에 별도로 다룰 일

이번에 만드는 것:

1. **URL 목록을 자동으로 수집해서 보여주고 파일로 저장**.
2. **선택한 URL 들이 *정상 화면을 노출하는지* 자동으로 검증하는 pytest 기반 Python Playwright 스크립트** (`tour_selected.py`) 생성. 다운받아 `pytest` 또는 `python tour_selected.py` 로 실행하면 URL 별로 PASS/FAIL 이 결정된다.

같은 파일이 두 경로 모두에서 동작 (Recording UI 의 *Play Script from File* 호환):

- `pytest tour_selected.py -v` — pytest CLI 직접
- `python tour_selected.py` — Recording UI 가 호스트 venv 에서 subprocess 로 실행하는 형태

검증 항목 (한 URL 의 PASS 조건, §2.3.2 상세):

1. navigation 자체 성공
2. HTTP status `< 400`
3. 최종 URL host 가 seed host 와 같은 도메인 계열 (로그인 페이지로 빠지지 않음)
4. `<title>` 비어있지 않음 + body 텍스트 길이 ≥ 50자

검증 강도 옵션(loose/strict) 노출, 콘솔/네트워크 에러 기반 정밀 검증, baseline 비교 등은 후속.

## 안전장치

운영 중인 실제 사이트를 자동으로 훑게 되므로 다음 가드를 기본으로 둔다.
- **최대 페이지 수**: 200 (사용자가 화면에서 늘리거나 줄일 수 있음)
- **최대 깊이**: 3 (시작 주소에서 3번 안에 도달하는 페이지까지)
- **요청 간격**: 0.5초 (사이트에 부담 주지 않기)
- **같은 도메인만**: 외부 링크는 따라가지 않음
- **건너뛸 패턴**: `/logout`, `mailto:`, 파일 다운로드 링크(`.pdf`, `.zip` 등)
- **로그인 만료 감지**: 시작 직전에 세션이 살아 있는지 자동 확인, 죽었으면 즉시 중단
- **크롤 도중 세션 만료 감지**: 최근 N개 응답이 모두 로그인/SSO host 로 리다이렉트되면 의미 없는 데이터 누적을 막기 위해 자동 abort
- **사용자 취소 가능**: 진행 중 화면의 [취소] 버튼으로 언제든 정지 (운영 사이트에 잘못 걸렸을 때 즉시 멈출 수 있어야 한다)

## 사용자가 보게 될 화면 흐름

1. Recording UI 접속 → 기존 "새 녹화 시작" 폼 아래에 "🔍 Discover URLs" 섹션이 새로 보인다
2. 시작 URL + (선택) 로그인 프로파일 + 한도 입력 → "Discover URLs" 클릭
3. 진행 중 표시(작업 ID, 발견 페이지 수 카운트), 옆의 **취소** 버튼으로 중단 가능
4. 완료되면 표가 뜬다: 선택 · 응답코드 · 깊이 · 제목 · URL (Part 2 컬럼 순서와 동일)
5. **CSV 다운로드** 버튼, URL별 체크박스, 그리고 **선택 URL Tour Script 생성** 버튼이 보인다
6. 사용자가 일부/전체 URL을 선택하면, 선택한 URL을 순서대로 방문하는 Python Playwright 스크립트를 내려받을 수 있다

## 성공 기준 (이게 되면 끝)

- 외부 의존 없는 내장 fixture(`http://localhost:18081/fixtures/full_dsl.html`)에 대해 인증 없이 동작
- 로그인 프로파일 지정 시 portal.koreaconnect.kr 같은 SSO 사이트에서도 로그인 필요한 내부 페이지가 목록에 나옴
- 결과 CSV가 `~/.dscore.ttc.playwright-agent/discoveries/<작업ID>/urls.csv` 에 저장됨 (`DISCOVERY_HOST_ROOT` 로 override 가능)
- 선택한 URL 목록으로 `~/.dscore.ttc.playwright-agent/discoveries/<작업ID>/tour_selected.py` 생성 및 다운로드 가능
- 운영 사이트에 부담을 주지 않는 속도와 한도로 마무리됨

---

# Part 2. — 구현 컴포넌트와 태스크

## 전제와 재사용 자산

- 언어/런타임: Python 3.11+, Playwright sync API (서버는 FastAPI sync 라우트 + thread worker 패턴 사용 중)
- 인증 세션: [auth_profiles.py](playwright-allinone/zero_touch_qa/auth_profiles.py) 의 storageState + Fingerprint 그대로 재사용
  - `browser.new_context(storage_state=str(storage_path), **fingerprint.to_browser_context_kwargs())` 호출 규약은 [auth_profiles.py:1029-1032](playwright-allinone/zero_touch_qa/auth_profiles.py#L1029-L1032), [auth_profiles.py:1096-1098](playwright-allinone/zero_touch_qa/auth_profiles.py#L1096-L1098) 와 동일
- 기존 비동기 패턴: [server.py:1337](playwright-allinone/recording_service/server.py#L1337) 의 `threading.Thread(target=_seed_worker, ..., daemon=True).start()` 그대로 차용
- 결과 저장 루트: **recordings 용 `storage.host_root()` 를 재사용하지 않는다.** `storage.host_root()` 의 기본값은 `~/.dscore.ttc.playwright-agent/recordings` 이므로 그대로 쓰면 `recordings/discoveries/...` 로 잘못 저장된다. discover 전용 헬퍼는 기존 `recording_service/storage.py` 안에 `host_root()` 바로 옆에 두어 두 루트 헬퍼가 한 모듈에 모이도록 한다:
  ```python
  # recording_service/storage.py
  def discoveries_root() -> Path:
      raw = os.environ.get("DISCOVERY_HOST_ROOT")
      if raw:
          return Path(raw).expanduser()
      return Path(os.environ.get("DSCORE_AGENT_DIR", "~/.dscore.ttc.playwright-agent")).expanduser() / "discoveries"
  ```
- auth_profile 검증/extras 로딩 로직: [server.py:132-193](playwright-allinone/recording_service/server.py#L132-L193) `_resolve_auth_profile_extras()` — discover 워커도 같은 검증이 필요하므로 **공통화 단계가 태스크 0**

## 손대지 않을 것 (외과적 변경 원칙)

- `zero_touch_qa/__main__.py` 의 단일 `--target-url` 흐름
- 기존 `/recording/start`, `/recording/stop/{sid}` 엔드포인트의 동작
- `crawl4ai` 의존성 — 사용도 안 하고 제거도 안 함
- Jenkins Pipeline / Docker 빌드 / 호스트 agent 스크립트
- LLM/Dify/Ollama 관련 코드 일체

## 신규/수정 파일 일람

| 파일 | 신규/수정 | 목적 |
|---|---|---|
| `playwright-allinone/zero_touch_qa/url_discovery.py` | 신규 | BFS 크롤러 본체 + URL 정규화 함수 (서버 측 tour-script 검증과 공유) |
| `playwright-allinone/recording_service/storage.py` | 수정 | `discoveries_root()` 헬퍼 추가 |
| `playwright-allinone/recording_service/server.py` | 수정 | discover 엔드포인트(start/get/cancel/csv/json/tour-script) + worker + auth_profile 로딩 함수 추출 + tour script 생성 |
| `playwright-allinone/recording_service/web/index.html` | 수정 | discover 폼 섹션 추가 |
| `playwright-allinone/recording_service/web/app.js` | 수정 | discover fetch + 폴링 + 결과 표 + 체크박스 선택 + CSV 링크 + tour script 다운로드 |
| `playwright-allinone/test/test_url_discovery.py` | 신규 | 정적 fixture 기반 단위 테스트 |
| `playwright-allinone/test/test_discover_api_e2e.py` | 신규 | `/discover` job/CSV/tour script/auth error mapping API 통합 테스트 |

## 태스크

### 태스크 0 — auth_profile 로딩 로직 함수 추출 (server.py)

`_resolve_auth_profile_extras()` ([server.py:132-193](playwright-allinone/recording_service/server.py#L132-L193)) 는 codegen 용 `--load-storage` extra args 를 만들기 위한 함수다. discover 워커는 codegen extra args 가 필요 없고, **storage_state 파일 경로 + fingerprint 객체** 만 있으면 된다.

- 새 헬퍼 추가 (server.py 모듈 내, `_resolve_auth_profile_extras` 위에 배치):
  ```python
  def _load_profile_for_browser(name: str | None) -> tuple[Path | None, "FingerprintProfile" | None, bool]:
      """auth_profile 이름 → (storage_path, fingerprint, machine_mismatch).
      이름이 None 이면 (None, None, False).
      """
  ```
- 에러 규약은 기존 `/recording/start` 와 맞춘다.
  - profile 없음: `HTTPException(404, {"reason": "profile_not_found", ...})`
  - profile verify 실패/만료: `HTTPException(409, {"reason": "profile_expired", ...})`
  - CHIPS 등 런타임 문제: `HTTPException(503, {"reason": "chips_not_supported", ...})`
  - 기타 auth profile 오류: `HTTPException(400, {"reason": "...", "message": ...})`
- 머신 불일치는 차단하지 않는다. 기존 recording start 처럼 경고 신호로만 다룬다. discover 시작 응답에는 `X-Auth-Machine-Mismatch: 1` 헤더와 `machine_mismatch: true` 필드를 넣을 수 있다.
- **machine_mismatch 결정 책임은 `_load_profile_for_browser()` 한 곳**. `_resolve_auth_profile_extras()` 는 헬퍼가 반환한 값을 그대로 전달만 하고, 자체적으로 `current_machine_id()` 를 다시 비교하지 않는다(중복 계산 금지).
- `_resolve_auth_profile_extras()` 는 위 헬퍼를 호출해 storage_path/fingerprint 를 받고 codegen extra args 를 만들도록 리팩터. 동작 변경 금지(테스트 통과 유지).
- 검증: 기존 `test/test_auth_profiles.py` + `_resolve_auth_profile_extras` 가 사용되는 호출부(`/recording/start` L388-463) 동작 동일.

### 태스크 1 — 크롤러 모듈 신규 (`zero_touch_qa/url_discovery.py`)

```python
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse, urldefrag, urlunparse, parse_qsl, urlencode

@dataclass
class DiscoveredUrl:
    url: str
    status: int | None
    title: str | None
    depth: int
    found_at: str  # ISO8601

@dataclass
class DiscoverConfig:
    seed_url: str
    storage_state_path: Path | None
    fingerprint_kwargs: dict        # Fingerprint.to_browser_context_kwargs() 결과 — JSON-safe 만 허용
    max_pages: int = 200
    max_depth: int = 3
    request_interval_sec: float = 0.5
    nav_timeout_ms: int = 15000
    wait_until: str = "domcontentloaded"
    settle_timeout_ms: int = 2000
    exclude_patterns: tuple[str, ...] = (
        "/logout", "/signout", "mailto:", "tel:", "javascript:",
    )
    exclude_extensions: tuple[str, ...] = (
        ".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif",
        ".svg", ".ico", ".css", ".js", ".woff", ".woff2",
    )
    trash_query_params: tuple[str, ...] = (
        "utm_source", "utm_medium", "utm_campaign", "utm_term",
        "utm_content", "_t", "timestamp", "_",
    )
    # 크롤 도중 세션 만료 휴리스틱: 최근 N개 응답 final_url host 가 모두
    # seed host 와 다르면 abort. 0 이면 비활성.
    auth_drift_window: int = 5

def normalize_url(raw: str, *, trash_query_params: tuple[str, ...]) -> str:
    """visited-set 키 + tour-script 검증에 공유되는 정규화 함수.

    - scheme/host 소문자화, fragment 제거
    - trash_query_params 제거 후 남은 쿼리는 키 정렬
    - 기본 포트(80/443) 정규화
    - 트레일링 슬래시: path 가 빈 경우만 "/" 로 보정. 그 외 `/foo` vs `/foo/` 는 별개 페이지로 취급
      (서버 라우팅에 따라 실제로 다른 응답일 수 있음)
    - http vs https 는 별개 호스트로 취급 (보안 경계)
    """

def discover_urls(
    cfg: DiscoverConfig,
    *,
    on_progress=None,
    cancel_event: "threading.Event | None" = None,
) -> tuple[list[DiscoveredUrl], str | None]:
    """반환: (결과, abort_reason). abort_reason 은 정상 종료/사용자 취소 시 None,
    세션 만료 휴리스틱 발동 시 "auth_drift"."""
    ...
```

핵심 규칙:

- BFS 큐 + visited set. visited 키는 위 `normalize_url()` 결과를 사용. 같은 정규화 함수를 tour-script 입력 검증에서도 재사용 (URL 매칭 정합성 확보).
- 동일 호스트 판정: `urlparse(seed).netloc == urlparse(candidate).netloc` (대소문자 무시, port 포함). 서브도메인은 **다른 호스트**로 취급(1차 단순화). `http` ↔ `https` 도 다른 호스트로 본다.
- 링크 추출은 `domcontentloaded` 로 페이지를 먼저 확보한 뒤 짧은 안정화 대기는 best-effort 로만 시도한다. `networkidle` 은 포털/SPA 에서 영원히 안 올 수 있어 timeout 을 무시한다.

```python
response = page.goto(url, wait_until=cfg.wait_until, timeout=cfg.nav_timeout_ms)
try:
    page.wait_for_load_state("networkidle", timeout=cfg.settle_timeout_ms)
except PlaywrightTimeoutError:
    pass
hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
```

`href` 는 브라우저가 절대 URL로 풀어준 결과라 base 처리 불필요.

- 응답 status: `response.status` 저장. 응답이 None 이거나 예외면 `status=None`, title 은 `page.title()` 시도 후 실패 시 `None`.
- **per-URL 격리**: BFS 루프의 각 URL 처리 블록은 `try/except Exception` 으로 감싼다. URL 단위 실패(타임아웃/dialog/빈 응답)는 `status=None` 으로 기록하고 다음 URL 로 진행한다. 브라우저/컨텍스트 자체가 닫히는 등의 상위 예외만 BFS 를 종료시킨다.
- 깊이 가드: 큐에서 꺼낼 때 `depth > max_depth` 이면 skip. seed = depth 0.
- `on_progress(count, last_url)` 콜백 — server worker 가 진행 카운트 업데이트에 사용.
- `cancel_event` 가 set 되면 **현재 URL 처리 직후** BFS 를 정상 종료하고 그때까지 모은 결과를 반환한다(부분 결과 보존).
- request_interval_sec 만큼 `time.sleep` 사이마다 (운영 사이트 부담 가드). seed 첫 페이지 *직전*에는 슬립을 걸지 않는다(불필요한 시작 지연 방지).
- **세션 만료 휴리스틱**: 최근 `auth_drift_window` 개 응답의 final_url host 가 모두 seed host 와 다르고 같은 외부 host 로 수렴하면, 로그인 페이지 무한 리다이렉트로 판단하고 BFS 를 abort 한다(부분 결과는 보존). 이때 `discover_urls` 는 정상 반환하고 호출자가 `meta.json` 의 `aborted_reason="auth_drift"` 로 표시.

브라우저 컨텍스트:
```python
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx_kwargs = dict(cfg.fingerprint_kwargs)
    if cfg.storage_state_path:
        ctx_kwargs["storage_state"] = str(cfg.storage_state_path)
    context = browser.new_context(**ctx_kwargs)
    page = context.new_page()
    page.set_default_navigation_timeout(cfg.nav_timeout_ms)
    try:
        ...  # BFS
    finally:
        context.close()
        browser.close()
```

### 태스크 2 — FastAPI 엔드포인트 추가 (`recording_service/server.py`)

기존 `/recording/start` 정의 근처(L388 부근) 또는 파일 말미 `/recording/stop/{sid}` 다음에 배치.

#### 2.1 데이터 모델

```python
class DiscoverReq(BaseModel):
    seed_url: HttpUrl
    auth_profile: str | None = None
    max_pages: int = Field(200, ge=1, le=2000)
    max_depth: int = Field(3, ge=0, le=10)

class TourScriptReq(BaseModel):
    # NOTE: 일부러 list[str]. Pydantic v2 HttpUrl 은 입력을 정규화(끝슬래시/host 소문자/기본포트 제거)
    # 해서 urls.json 의 원본 문자열과 정확 매칭이 깨진다. 검증은 server 단에서
    # `url_discovery.normalize_url()` 로 양쪽 모두 정규화한 set 비교로 수행한다.
    urls: list[str] = Field(..., min_length=1, max_length=500)
    auth_profile: str | None = None
    # UI 기본값은 False — 사용자가 브라우저 창을 띄워 진행을 직접 보는 워크플로우.
    # CI/배경 실행 시 UI 체크박스 또는 직접 호출에서 True 로.
    # 다운받은 스크립트는 TOUR_HEADLESS env 로 즉시 override 가능.
    headless: bool = False
    preflight_verify: bool = True
    wait_until: Literal["domcontentloaded", "load", "networkidle"] = "domcontentloaded"
    nav_timeout_ms: int = Field(15000, ge=1000, le=120000)

class DiscoverJob(BaseModel):
    job_id: str
    state: Literal["running", "cancelling", "done", "failed", "cancelled"]
    seed_url: str
    auth_profile: str | None
    machine_mismatch: bool = False
    started_at: str
    finished_at: str | None = None
    count: int = 0
    last_url: str | None = None
    result_dir: str | None = None
    error: str | None = None
    aborted_reason: str | None = None  # "auth_drift" 등 워커가 자동 중단한 사유
```

#### 2.2 모듈 전역 상태

```python
_discover_jobs: dict[str, DiscoverJob] = {}
_discover_cancel_events: dict[str, threading.Event] = {}
_discover_lock = threading.Lock()

# 1차 정책: 동시 실행 상한 2. 초과 시 POST /discover 가 429.
# 운영 사이트 부하 + 로컬 Chromium 프로세스 수 가드.
DISCOVER_MAX_CONCURRENT = 2
```

**메모리 정책 (1차)**: `_discover_jobs` 는 서버 프로세스 생애 동안만 보존하며 TTL/캡을 두지 않는다 (재시작 시 비워짐). 결과 디스크 파일(`discoveries_root()/<job_id>/`)은 영구 보존되며 디스크 회수는 후속 범위. 운영 환경에서 jobs dict 가 부담이 될 정도로 호출되지 않는다는 가정이며, 그 가정이 깨지면 LRU 또는 시간 기반 GC 를 후속에서 추가한다.

#### 2.3 엔드포인트

- `POST /discover` → 202
  ```python
  @app.post("/discover", status_code=202)
  def discover(req: DiscoverReq, response: Response):
      with _discover_lock:
          running = sum(1 for j in _discover_jobs.values() if j.state in ("running", "cancelling"))
      if running >= DISCOVER_MAX_CONCURRENT:
          raise HTTPException(429, {"reason": "too_many_running_discover_jobs",
                                    "limit": DISCOVER_MAX_CONCURRENT})
      job_id = uuid.uuid4().hex[:12]
      storage_path, fp, machine_mismatch = _load_profile_for_browser(req.auth_profile)
      job = DiscoverJob(job_id=job_id, state="running",
                        seed_url=str(req.seed_url),
                        auth_profile=req.auth_profile,
                        machine_mismatch=machine_mismatch,
                        started_at=_now_iso())
      cancel_event = threading.Event()
      with _discover_lock:
          _discover_jobs[job_id] = job
          _discover_cancel_events[job_id] = cancel_event
      threading.Thread(
          target=_discover_worker,
          args=(job_id, storage_path, fp, req.max_pages, req.max_depth, cancel_event),
          daemon=True,
      ).start()
      if machine_mismatch:
          response.headers["X-Auth-Machine-Mismatch"] = "1"
      return {"job_id": job_id, "state": "running", "machine_mismatch": machine_mismatch}
  ```

- `GET /discover/{job_id}` — DiscoverJob 직렬화 반환. 없으면 404.
- `POST /discover/{job_id}/cancel` — cooperative cancel. job 이 `running` 이면 cancel_event 를 set 하고 state 를 `cancelling` 으로 전이. 워커가 다음 URL 처리 직후 부분 결과를 디스크에 쓰고 state 를 `cancelled` 로 마무리. 이미 `done/failed/cancelled` 면 409. job 미발견은 404.
- `GET /discover/{job_id}/csv` — `result_dir/urls.csv` `FileResponse` 반환. state 가 `done` 또는 `cancelled` 일 때만 허용 (그 외 409). `cancelled` 도 부분 결과 파일이 있으면 다운로드 가능.
- `GET /discover/{job_id}/json` — `urls.json` 파일 응답. 동일 규칙.
- `POST /discover/{job_id}/tour-script` — 선택 URL 목록을 받아 `tour_selected.py` 생성 후 `FileResponse` 로 반환. state 가 `done` 또는 `cancelled` 일 때만 허용. URL 검증은 아래 2.3.1 참조.

`DiscoverReq.seed_url` 은 Pydantic v2/v1 호환성을 고려해 worker 에 넘길 때 반드시 `str(req.seed_url)` 로 문자열화한다.

#### 2.3.1 Tour script 생성

`POST /discover/{job_id}/tour-script` 는 discovery 결과 중 사용자가 선택한 URL만 순회하는 Python Playwright sync script 를 만든다.

규칙:

- 생성되는 tour script 는 **pytest 기반 회귀 검증 스크립트**다. 각 URL 이 `parametrize` 의 한 case 가 되어, 정상 화면 노출 여부를 4가지 항목으로 자동 판정한다 (검증 항목은 §2.3.2).
- 같은 파일이 두 가지 실행 경로 모두에서 동일하게 동작:
  1. `pytest tour_selected.py -v` — pytest CLI 로 직접 실행 (개발 반복용).
  2. `python tour_selected.py` — Recording UI 의 *Play Script from File* → ▶ Codegen 녹화코드 실행 흐름. 파일 끝의 `if __name__ == "__main__": sys.exit(pytest.main([__file__, ...]))` 가 라이브러리로 pytest 를 호출.
- 요청 URL은 해당 job 의 `urls.json` 에 존재하는 URL만 허용한다. 임의 외부 URL 주입을 막기 위해 미발견 URL은 422 로 거부한다.
- **URL 매칭은 정규화 후 set 비교**로 수행한다. `req.urls` 와 `urls.json` 양쪽 모두 `url_discovery.normalize_url()` 로 정규화한 뒤, 정규화된 입력 set 이 정규화된 발견 set 의 부분집합이 아니면 422. 응답 본문 / 생성 script 의 `URLS = [...]` 에는 클라이언트가 보낸 원본 형태가 아니라 `urls.json` 에 저장된 *원본 URL* 을 사용한다(매칭 후 룩업). 가독성을 위해 `URLS` 는 한 줄당 하나로 정렬해 박는다.
- `auth_profile` 이 지정되면 `_load_profile_for_browser()` 로 다시 verify 하고, script 에는 쿠키 내용을 직접 박지 않는다. storageState 파일 경로, fingerprint kwargs, preflight verify spec 만 참조한다.
- **`CONTEXT_KWARGS` 직렬화 안전성**: `Fingerprint.to_browser_context_kwargs()` 결과를 그대로 `repr()` 하지 않는다. 생성기는 (a) 결과를 `json.dumps()` 로 직렬화 가능한지 먼저 검증하고, (b) script 안에서는 `CONTEXT_KWARGS_JSON = "..."` 로 박은 뒤 `json.loads()` 로 복원하는 방식을 사용한다. JSON 비호환 값(non-primitive 객체)이 끼어 있으면 500 + `reason=fingerprint_not_serializable` 로 거부한다.
- **storageState 경로**: 사용자 홈 노출을 줄이기 위해 가능하면 `~` prefix 로 줄여 기록하고 script 가 `Path(STORAGE_STATE).expanduser()` 로 풀어 쓰도록 한다. 다른 머신에서 실행 시 즉시 fail 함을 헤더 코멘트로 1줄 명시(공유 시 auth_profile 없이 재생성 권장).
- 시작 시 `Path(STORAGE_STATE).expanduser().is_file()` 검사. 파일 없으면 한국어 안내와 함께 `pytest.exit(returncode=2)` — 어떤 URL 도 시도하지 않는다.
- 시작 시 preflight verify (auth_profile 의 verify_url) 도 한 번 수행. 실패하면 모든 URL 테스트를 `pytest.skip` (사유 포함). 생성 시점 verify 와 실행 시점 사이에 세션이 만료될 수 있기 때문.
- 생성 파일은 `discoveries_root() / job_id / "tour_selected.py"` 에 저장한다.
- 실행 결과는 같은 디렉토리 기준 `tour_results.jsonl` (URL 별 status/title/body_len/ok/error/screenshot 경로) 과 `tour_screenshots/` 에 저장한다.
- **스크린샷 정책**: 기본은 *모든 URL* 저장 (`full_page=True`). headed 모드 검토 시 부분 렌더 페이지 확인용. 디스크 절약이 필요하면 `TOUR_SCREENSHOTS_FAILED_ONLY=1` 로 실패만 저장.
- 스크린샷 직전 `networkidle` best-effort settle (1.5s) 로 부분 렌더 노출을 줄인다.
- 이 스크립트는 단순 visitor 가 아니라 **자동 회귀 판정** 스크립트다. exit code 0 = 모두 통과, 1 = 하나 이상 실패, 2 = STORAGE_STATE 누락, 5 = no tests collected.

#### 2.3.2 검증 항목 (한 URL 의 PASS 조건)

각 URL 은 다음 4가지를 *모두* 통과해야 PASS. 하나라도 어긋나면 해당 URL 케이스가 fail 처리되고 (a) `tour_results.jsonl` 에 `ok: false + error` 기록, (b) `tour_screenshots/` 에 PNG 저장, (c) pytest exit code 1.

1. **navigation 자체 성공**: `page.goto()` 가 예외 없이 반환, response 가 None 아님.
2. **HTTP status `< 400`**: 4xx/5xx 즉시 fail.
3. **세션 유지 (host 일치)**: 최종 URL 의 hostname 이 seed URL 의 hostname 과 같거나 같은 도메인 계열 (`endswith("." + seed)`). SSO 로그인 페이지로 빠지면 즉시 검출.
4. **기본 렌더링**: `<title>` 비어있지 않음 + body inner_text 길이 ≥ `MIN_BODY_TEXT_LEN` (기본 50). 빈 화면/스피너만 도는 페이지 차단.

검증 강도는 1차 standard 고정. 후속에서 UI 의 라디오 옵션(loose/standard/strict)으로 노출 가능.

#### 2.3.3 Preflight verify (auth_profile 지정 시)

- auth_profile 이 없으면 preflight 는 생략 (`tour_results.jsonl` 에 `phase=preflight, skipped` 한 줄만 남김).
- auth_profile 이 있으면 카탈로그의 `verify.service_url` 과 `verify.service_text` 를 script 에 상수로 기록.
- session-scope fixture 가 시작 시 한 번 `VERIFY_SERVICE_URL` 로 이동.
- `VERIFY_SERVICE_TEXT` 가 있으면 body text 에 포함 여부 확인.
- `VERIFY_SERVICE_TEXT` 가 비어 있으면 응답 status `< 400` 이고 최종 host 가 검증 URL host 와 같거나 같은 도메인 계열인지 확인.
- 실패 시 모든 URL 테스트를 `pytest.skip` (사유 포함). `tour_results.jsonl` 에 `phase=preflight, ok=false, error=...` 한 줄 기록.
- STORAGE_STATE 파일 자체가 없으면 preflight 이전에 한국어 안내와 함께 `pytest.exit(returncode=2)` — 어떤 URL 도 시도하지 않는다.

#### 2.3.4 생성 script 골격 (pytest 형식)

```python
"""Auto-generated tour script — pytest 기반 회귀 검증.

실행:
    pytest tour_selected.py -v
    TOUR_SCREENSHOTS_FAILED_ONLY=1 pytest tour_selected.py   # 실패만 PNG (기본: 전체)
    TOUR_HEADLESS=0 python tour_selected.py                  # 브라우저 창 띄움
    TOUR_HEADLESS=1 python tour_selected.py                  # 헤드리스 강제 (CI 등)
    python tour_selected.py                                  # Recording UI 'Play Script from File' 호환
"""
import json, os, re
from pathlib import Path
from urllib.parse import urlparse
import pytest
from playwright.sync_api import sync_playwright

URLS = [
    "https://example/a",
    "https://example/b",
]
SEED_HOST = "example"
STORAGE_STATE = "~/.dscore.../auth-profiles/X.storage.json"  # 또는 None
CONTEXT_KWARGS_JSON = "{...}"
HEADLESS = False                                     # UI 기본 OFF — 브라우저 창 띄움
PREFLIGHT_VERIFY = True
VERIFY_SERVICE_URL = "..."
VERIFY_SERVICE_TEXT = ""
MIN_BODY_TEXT_LEN = 50
SETTLE_TIMEOUT_MS = 1500       # screenshot 직전 networkidle best-effort settle
SCREENSHOTS_FAILED_ONLY = os.environ.get("TOUR_SCREENSHOTS_FAILED_ONLY", "0") == "1"
# 환경변수로 즉시 override (스크립트 재생성 없이):
_he = os.environ.get("TOUR_HEADLESS")
if _he is not None:
    HEADLESS = _he not in ("0", "false", "False", "no", "")
# (그 외 OUT_DIR/RESULTS/SCREENSHOT_DIR/WAIT_UNTIL/NAV_TIMEOUT_MS)

@pytest.fixture(scope="session")
def _storage_state_path():
    """파일 없으면 친절한 한국어 메시지 + pytest.exit(2)."""

@pytest.fixture(scope="session")
def browser_context(_storage_state_path):
    """sync_playwright + storage_state 적용된 1개 컨텍스트. 테스트마다 fresh page."""

@pytest.fixture(scope="session")
def preflight(browser_context):
    """verify_url 로 한 번 이동 + 실패 시 모든 테스트 skip."""

@pytest.mark.parametrize("url", URLS, ids=lambda u: u)
def test_url_renders_normally(url, browser_context, preflight):
    """검증 항목 1~4. fail 시 자동으로 PNG 저장 (또는 SCREENSHOTS_ALL 시 전체)."""

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "-p", "no:cacheprovider"]))
```

#### 2.4 워커

```python
def _discover_worker(job_id: str, storage_path: Path | None, fp,
                     max_pages: int, max_depth: int,
                     cancel_event: threading.Event):
    job = _discover_jobs[job_id]
    out_dir = discoveries_root() / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    aborted_reason: str | None = None
    try:
        cfg = DiscoverConfig(
            seed_url=job.seed_url,
            storage_state_path=storage_path,
            fingerprint_kwargs=fp.to_browser_context_kwargs() if fp else {},
            max_pages=max_pages,
            max_depth=max_depth,
        )
        def _progress(count, last_url):
            with _discover_lock:
                job.count = count
                job.last_url = last_url
        # discover_urls 내부 휴리스틱이 abort 시 reason 을 cfg 또는 별도 채널로
        # 알려주도록 구현 (예: dataclass 에 _aborted_reason 필드, 또는 결과 튜플).
        # 1차 구현 단순화를 위해 module-level 'last_abort_reason' 변수 또는
        # discover_urls 의 반환을 (results, abort_reason) 튜플로 바꿔도 무방.
        results, aborted_reason = discover_urls(cfg, on_progress=_progress,
                                                cancel_event=cancel_event)
        meta = {"seed_url": job.seed_url, "auth_profile": job.auth_profile,
                "machine_mismatch": job.machine_mismatch,
                "started_at": job.started_at, "finished_at": _now_iso(),
                "count": len(results),
                "aborted_reason": aborted_reason,
                "cancelled_by_user": cancel_event.is_set()}
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        (out_dir / "urls.json").write_text(json.dumps([asdict(r) for r in results],
                                                      ensure_ascii=False, indent=2))
        # utf-8-sig: Excel 호환을 위한 BOM 포함. 한국어 사용자 다수.
        with (out_dir / "urls.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["url","status","title","depth","found_at"])
            w.writeheader()
            for r in results:
                w.writerow(asdict(r))
        with _discover_lock:
            if cancel_event.is_set():
                job.state = "cancelled"
            else:
                job.state = "done"
            job.aborted_reason = aborted_reason
            job.finished_at = meta["finished_at"]
            job.count = len(results)
            job.result_dir = str(out_dir)
    except Exception as e:
        with _discover_lock:
            job.state = "failed"
            job.error = repr(e)
            job.finished_at = _now_iso()
    finally:
        with _discover_lock:
            _discover_cancel_events.pop(job_id, None)
```

`discover_urls()` 의 반환은 `(list[DiscoveredUrl], abort_reason: str | None)` 튜플로 정의한다(태스크 1 시그니처 보강). `abort_reason` 은 정상 종료 시 None, 세션 만료 휴리스틱 발동 시 `"auth_drift"`. 사용자 취소는 `cancel_event.is_set()` 으로 별도 식별하며 `aborted_reason` 과는 직교한다.

`_now_iso()` 가 server.py 에 이미 있으면 재사용, 없으면 `datetime.now(timezone.utc).isoformat()` 인라인.

### 태스크 3 — UI 추가

#### 3.1 `web/index.html`

기존 "새 녹화 시작" `<form id="start-form">` 블록(L34-74) 다음에 추가:

```html
<details id="discover-section" style="margin-top:1.5em;">
  <summary>🔍 Discover URLs (사이트 내 URL 자동 수집)</summary>
  <form id="discover-form">
    <label>시작 URL <input type="url" name="seed_url" required placeholder="https://..."></label>
    <label>로그인 프로파일
      <select name="auth_profile" id="discover-auth-profile">
        <option value="">(없음)</option>
      </select>
    </label>
    <label>최대 페이지 수 <input type="number" name="max_pages" value="200" min="1" max="2000"></label>
    <label>최대 깊이 <input type="number" name="max_depth" value="3" min="0" max="10"></label>
    <button type="submit">Discover URLs</button>
    <button type="button" id="btn-discover-cancel" hidden>취소</button>
  </form>
  <div id="discover-status"></div>
  <div id="discover-actions" hidden>
    <a id="discover-csv-link" href="#" download>CSV 다운로드</a>
    <button type="button" id="btn-discover-select-all">전체 선택</button>
    <button type="button" id="btn-discover-select-none">선택 해제</button>
    <button type="button" id="btn-discover-tour-script" disabled>선택 URL Tour Script 생성</button>
    <label class="discover-headless-toggle">
      <input type="checkbox" id="discover-headless"> 헤드리스(백그라운드) 실행
    </label>
    <span id="discover-selected-count">0개 선택</span>
  </div>
  <div id="discover-result"></div>
</details>
```

실제 현재 UI 의 auth profile selector 는 `#auth-profile-select` 이다. discover 섹션은 별도 selector `#discover-auth-profile` 을 가지되, 기본값은 현재 recording selector 선택값과 동기화한다. 사용자가 discover 섹션에서 따로 바꾸면 그 값을 우선한다.

#### 3.2 `web/app.js`

- 현재 auth profile 목록 로딩 함수는 `loadAuthProfiles()` 이고 `#auth-profile-select` 를 채운다. 같은 `profiles` 배열로 `#discover-auth-profile` 도 함께 채운다.
- `#auth-profile-select` 변경 시 `#discover-auth-profile` 이 아직 사용자 수정되지 않았다면 같은 값으로 맞춘다.
- 신규 함수:
  ```js
  async function startDiscover(form) {
    const data = Object.fromEntries(new FormData(form));
    data.max_pages = Number(data.max_pages);
    data.max_depth = Number(data.max_depth);
    if (!data.auth_profile) delete data.auth_profile;
    const r = await fetch('/discover', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    });
    const j = await r.json();
    if (!r.ok) { showDiscoverError(j); return; }
    if (j.machine_mismatch) showMachineMismatchWarningForDiscover();
    pollDiscover(j.job_id);
  }

  async function pollDiscover(jobId) {
    const statusEl = document.getElementById('discover-status');
    const resultEl = document.getElementById('discover-result');
    while (true) {
      const r = await fetch(`/discover/${jobId}`);
      const j = await r.json();
      statusEl.textContent = `[${j.state}] ${j.count}건 · 최근: ${j.last_url || '-'}`;
      if (j.state === 'done' || j.state === 'cancelled') {
        const list = await (await fetch(`/discover/${jobId}/json`)).json();
        document.getElementById('discover-csv-link').href = `/discover/${jobId}/csv`;
        document.getElementById('discover-actions').hidden = false;
        document.getElementById('btn-discover-cancel').hidden = true;
        if (j.state === 'cancelled') statusEl.textContent += ' (취소됨)';
        if (j.aborted_reason === 'auth_drift') statusEl.textContent += ' · 세션 만료 감지로 자동 중단';
        renderDiscoverTable(resultEl, jobId, list);
        return;
      }
      if (j.state === 'failed') {
        statusEl.textContent = `실패: ${j.error}`;
        document.getElementById('btn-discover-cancel').hidden = true;
        return;
      }
      await new Promise(res => setTimeout(res, 2000));
    }
  }

  function renderDiscoverTable(rootEl, jobId, list) {
    // 표 헤더: 선택 · status · depth · title · URL (Part 1 / Part 2 동일 순서)
    // 각 행 checkbox: data-url=row.url
    // 전체 선택 / 선택 해제 / 선택 개수 갱신
    //
    // ⚠ XSS 방지: row.title / row.url 은 임의 사이트의 임의 문자열이다.
    //   DOM 삽입은 반드시 textContent 사용. innerHTML / insertAdjacentHTML 금지.
    //   URL 컬럼의 클릭 가능한 링크는 element.textContent 로 표시 텍스트를 박고
    //   href 는 setAttribute('href', row.url) 로 설정하되, javascript:/data: 스킴은
    //   클라이언트에서 한 번 더 거른다(서버에서 이미 exclude_patterns 로 거르지만 방어적으로).
  }

  async function cancelDiscover(jobId) {
    const r = await fetch(`/discover/${jobId}/cancel`, { method: 'POST' });
    if (!r.ok) showDiscoverError(await r.json().catch(() => ({})));
    // pollDiscover 가 다음 폴링에서 state=cancelled 를 받아 표를 그린다.
  }

  async function generateTourScript(jobId) {
    const urls = [...document.querySelectorAll('.discover-url-check:checked')]
      .map((el) => el.dataset.url);
    if (!urls.length) return;
    const authProfile = document.getElementById('discover-auth-profile').value || null;
    const r = await fetch(`/discover/${jobId}/tour-script`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        urls,
        auth_profile: authProfile,
        headless: true,
        include_screenshots: true,
        preflight_verify: true,
      }),
    });
    if (!r.ok) { showDiscoverError(await r.json().catch(() => ({}))); return; }
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'tour_selected.py';
    a.click();
    URL.revokeObjectURL(a.href);
  }
  ```
- `#discover-form` submit 이벤트 바인딩은 기존 `#start-form` 바인딩 패턴(L322-343 부근) 그대로 모방.
- 행별 "이 URL로 녹화 시작" 버튼은 만들지 않는다. discovery 결과는 개별 녹화 시작보다 체크박스 선택 + tour script 생성 흐름으로 연결한다.
- 현재 `#start-form` 은 파일 앞쪽 기본 handler 와 뒤쪽 auth-profile capture handler 가 함께 존재한다. discover 구현은 `#start-form` submit handler 를 더 건드리지 말고, discover 전용 form/listener 로 격리한다.

### 태스크 4 — 단위 테스트 신규 (`test/test_url_discovery.py`)

- `pytest` + `playwright` (이미 의존성에 있음).
- fixture: `tmp_path` 에 5개 HTML 파일 생성 (index → a, b, c → d, b → e), `socketserver.TCPServer(("127.0.0.1", 0), handler)` 를 백그라운드 thread 로 띄움. 기존 `test_auth_profile_api_e2e.py` / `e2e_p1_auth_profiles.py` 의 local HTTP fixture 패턴을 차용한다.
- 테스트 케이스:
  1. `discover_urls(seed=index)` → 5개 URL, depth 0~2 정확
  2. `max_pages=2` → 정확히 2개에서 정지
  3. `exclude_patterns` 에 `/b` 포함 시 b 와 그 자손 제외
  4. mailto:/.pdf 자동 제외
  5. fragment 와 query 정규화 — `?utm_source=x` 와 `?` 가 같은 URL 로 dedup
  6. **`normalize_url()` 단위 회귀 케이스** (visited-set 정합성 + tour-script 매칭에 직결):
     - 쿼리 키 순서 무관: `?a=1&b=2` ≡ `?b=2&a=1`
     - host 대소문자 무관: `HTTP://Example.com` ≡ `http://example.com`
     - 기본 포트 제거: `http://x:80/p` ≡ `http://x/p`, `https://x:443/p` ≡ `https://x/p`
     - 트레일링 슬래시: `/foo` ≢ `/foo/` (별개로 보존)
     - http vs https: 별개 호스트
     - fragment 제거: `/p#a` ≡ `/p`
  7. **per-URL 격리**: 한 URL 이 의도적으로 timeout 나도 BFS 가 계속되어 나머지 URL 이 수집되는지
  8. **cancel_event**: 큐가 채워진 상태에서 cancel_event.set() → 다음 URL 처리 직후 정상 반환, 부분 결과 길이가 max_pages 미만
  9. **세션 만료 휴리스틱**: 모든 응답을 외부 host 로 redirect 하는 fixture 로 `auth_drift_window` 만큼 이어지면 abort_reason="auth_drift" 반환
- storageState/fingerprint 경로는 None 으로 인증 없이 동작 검증 (실제 SSO 사이트 테스트는 수동).

### 태스크 5 — API 통합 테스트 신규 (`test/test_discover_api_e2e.py`)

기존 `test_auth_profile_api_e2e.py` 의 별도 uvicorn daemon fixture 패턴을 재사용한다. 포트는 기존 18093/18094 와 충돌하지 않는 별도 포트를 사용한다.

검증 케이스:
1. `POST /discover` → 202, job_id 반환.
2. `GET /discover/{job_id}` polling → `done`, count 증가.
3. `GET /discover/{job_id}/json` → JSON 배열 반환.
4. `GET /discover/{job_id}/csv` → CSV header + URL rows 반환.
5. `POST /discover/{job_id}/tour-script` 에 발견 URL 2개를 보내면 `tour_selected.py` 가 반환되고 파일 내용에 선택 URL과 preflight verify 로직이 포함됨.
6. `POST /discover/{job_id}/tour-script` 에 미발견 URL을 보내면 422 로 거부.
7. **정규화 매칭**: 클라이언트가 `urls.json` 의 URL 을 끝슬래시 추가/host 대문자 등으로 미세 변형해서 보내도 같은 URL 로 인식되어 422 가 아니라 200 응답.
8. **동시 실행 상한**: `DISCOVER_MAX_CONCURRENT` 만큼 미완료 job 이 있을 때 추가 `POST /discover` → 429 + `reason=too_many_running_discover_jobs`.
9. **취소**: `POST /discover/{job_id}/cancel` → 200, 잠시 후 polling 결과 state=`cancelled`, 부분 결과 CSV/JSON 다운로드 가능.
10. **이미 종료된 job 의 cancel**: 409.
11. `DISCOVERY_HOST_ROOT=<tmp>` 지정 시 `<tmp>/<job_id>/urls.csv`, `<tmp>/<job_id>/tour_selected.py` 에 저장되고, `RECORDING_HOST_ROOT` 아래에는 생성되지 않음.
12. 존재하지 않는 auth_profile 지정 → 404 + `reason=profile_not_found`.
13. 만료 auth_profile 은 unit/mock 수준에서 `_load_profile_for_browser()` 또는 discover route 를 monkeypatch 해 409 + `reason=profile_expired` 보장.

UI e2e 는 선택 사항이다. 다만 최소한 `app.js` 정적 smoke 또는 수동 검증에서 `#discover-form` submit, polling 완료, checkbox 선택/해제, 선택 URL 개수 갱신, tour script 다운로드를 확인한다.

### 태스크 6 — 수동 통합 검증 체크리스트

1. `./build.sh --redeploy` (또는 컨테이너 재기동 없이 `recording_service/server.py` 만 재시작 가능하면 그쪽이 빠름).
2. Recording UI 접속, "🔍 Discover URLs" 섹션 보임.
3. seed_url=`http://localhost:18081/fixtures/full_dsl.html`, auth_profile=비움, max_pages=20 → 진행 카운트 업데이트, 완료 후 체크박스 포함 표 표시, CSV 다운로드 버튼 작동.
4. seed_url=`https://portal.koreaconnect.kr/user/ma/main`, auth_profile=기존 프로파일 → 로그인 필요한 내부 페이지가 목록에 포함됨, status 200.
5. 만료된 auth_profile 지정 → 즉시 409 에러(`reason=profile_expired`), UI 에 재시드 유도 메시지 표시.
6. 없는 auth_profile 지정 → 즉시 404 에러(`reason=profile_not_found`), UI 에 새 시드 안내 표시.
7. 다른 머신에서 만든 auth_profile 지정 → 작업은 시작되고 `machine_mismatch` 경고가 표시됨.
8. **진행 중 [취소] 버튼** → 곧 state=cancelled 로 종료, 부분 결과 CSV 가 다운로드되고 표가 그려짐.
9. **동시 실행 상한** — discover job 2개 진행 중에 3번째 요청 → UI 가 "동시 실행 한도 초과" 안내 (HTTP 429 + reason).
10. URL 2~3개 선택 → 헤드리스 체크박스 OFF (기본) → "선택 URL Tour Script 생성" → `tour_selected.py` 다운로드.
11. 다운로드한 `tour_selected.py` 를 `python tour_selected.py` 로 실행 → **브라우저 창이 뜨면서** 선택 URL 을 순회, 통과/실패가 pytest 로그에 표시되고 실패한 URL 의 PNG 가 `tour_screenshots/` 에 저장됨.
12. 같은 스크립트를 `TOUR_HEADLESS=1 python tour_selected.py` 로 다시 실행 → 브라우저 창 뜨지 않고 동일한 결과를 jsonl 에 남김. 헤드리스 체크박스 ON 으로 재생성한 스크립트도 같은 결과여야 함.
13. 만료된 storageState 로 `tour_selected.py` 실행 → URL tour 를 시작하지 않고 한국어 안내 + exit code 2.
14. STORAGE_STATE 파일 자체가 없는 환경에 가져가 실행 → 즉시 한국어 안내 + exit code 2.
15. Recording UI → "Play Script from File" 으로 `tour_selected.py` 업로드 → ▶ Codegen 녹화코드 실행 → pytest 가 subprocess 로 돌고 PASS/FAIL 이 결과 카드의 stdout 에 출력됨. rc=1 일 때 `result-state` 가 `error` 가 아닌 정상 흐름으로 끝나는지 확인.
16. 결과 파일 확인:
   ```
   ls -la ~/.dscore.ttc.playwright-agent/discoveries/<job_id>/
   # meta.json, urls.csv, urls.json, tour_selected.py, tour_results.jsonl, tour_screenshots/
   ```

## 위험과 대응

| 위험 | 대응 |
|---|---|
| 운영 사이트 부하 | max_pages=200, max_depth=3, request_interval=0.5s 기본. 사용자 조정 가능. |
| storageState 만료 | 시작 시 `_load_profile_for_browser()` 가 즉시 409 거부. |
| 무한 SPA(쿼리 변종 폭증) | trash_query_params 제거 + sorted query dedup + max_pages 강제 종료. |
| networkidle 대기 타임아웃 | 기본 navigation 은 `domcontentloaded`, `networkidle` 은 짧은 best-effort settle 로만 사용. 실패 시 링크 추출은 계속 시도. |
| SPA/버튼 라우팅 누락 | 1차 범위는 anchor 링크 수집으로 명시. hover 메뉴/SPA route discovery 는 후속 작업으로 분리. |
| tour script 의 검증 항목 정의 모호 | 1차는 검증 4종 (nav 성공 / status<400 / seed host 유지 / title+body 길이) 고정. 강도 옵션(loose/strict) 은 후속에서 UI 노출. |
| tour script 실행 시점 세션 만료 | script 시작 시 preflight verify 를 수행. 실패하면 URL tour 를 시작하지 않고 exit code 2 로 종료. |
| tour script 의 storageState 경로 노출 | 쿠키 본문은 script 에 쓰지 않고 storageState 파일 경로와 verify spec 만 참조. 같은 머신/사용자 환경 실행 전제이며 공유 시 auth_profile 없이 재생성 권장. |
| robots.txt 미준수 | 1차 미구현. 후속에서 옵션화 가능 — 명시적으로 plan 에서 제외. |
| 동시 discover job 과다 | 전역 동시 실행 상한 `DISCOVER_MAX_CONCURRENT=2`. 초과 시 `POST /discover` → 429 + `reason=too_many_running_discover_jobs`. |
| 잘못된 seed 로 운영 사이트 폭격 | `POST /discover/{job_id}/cancel` 즉시 가능. 워커는 cancel_event 를 매 URL 처리 후 확인하고 부분 결과 보존 + state=`cancelled` 로 종료. |
| 크롤 도중 세션 만료 | 최근 N개 응답이 모두 외부 host 로 수렴하면 `auth_drift` 로 자동 abort, 부분 결과 보존, UI 에 사유 표시. |
| 발견 URL 과 tour-script 입력 매칭 정합성 | `Pydantic HttpUrl` 정규화로 인한 불일치를 피하기 위해 양쪽 모두 `url_discovery.normalize_url()` 결과로 set 비교. 회귀 테스트 케이스로 보호. |
| `_discover_jobs` dict 메모리 누적 | 1차는 TTL/캡 없음 (재시작 시 비워짐). 결과 디스크 파일은 영구. 누적이 부담될 정도 호출 가정 부재. 후속에서 LRU/시간 GC. |
| `Fingerprint.to_browser_context_kwargs()` 가 JSON 비호환 객체 포함 | tour-script 생성 시 `json.dumps()` 사전 검증 후 거부(500 + reason). script 는 `CONTEXT_KWARGS_JSON` → `json.loads()` 로 안전 복원. |

## 후속 작업으로 분리되는 것 (이번 plan 밖)

- **검증 강도 UI 옵션** (loose / standard / strict). 1차는 standard 고정.
- **콘솔/네트워크 에러 기반 정밀 검증**: tour script 가 페이지의 console error / failed XHR 도 캡처해 추가 fail 사유로 사용.
- **baseline 비교**: 같은 URL 의 이전 회귀 결과와 비교해 신규/사라진 항목 표시.
- Jenkins Pipeline 통합 (예: `Discover-And-Verify` job).
- robots.txt 준수 옵션.
- 서브도메인 포함 옵션.
- SPA route discovery, hover 메뉴 열기, 버튼 클릭 기반 탐색.
- 차분 모드 (이전 결과와 비교해 신규/사라진 URL 표시).
