# Plan: Recording UI 에 "Discover URLs" 추가

---

# Part 1. 비개발자용 — 무엇을 왜 만드는가

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
- 그 페이지 안의 모든 링크를 모으고
- 각 링크를 따라가서 또 링크를 모으고
- **같은 사이트 안의 페이지만**, 정해진 한도(기본 200페이지) 안에서 끝까지 훑은 뒤
- URL 목록을 표 형태로 화면에 보여주고 CSV 파일로 내려준다.

각 줄에는 URL, 페이지 제목, 응답 코드(정상/에러), 몇 단계 깊이의 페이지인지가 함께 기록된다.

## 어떻게 작동하나 (한 단락)

내부적으로는 사람이 브라우저를 켜서 링크를 클릭하는 것과 같은 일을 자동으로 한다. 단, 화면을 띄우지 않고(headless) 빠르게 한다. 로그인이 필요한 사이트도 미리 저장해둔 **세션(쿠키 묶음)** 을 자동으로 끼워 넣어 들어간다 — 이건 최근에 추가된 기능이라 여기 그대로 빌려 쓴다.

## 이번에 만드는 범위와, 다음에 별도로 다룰 일

이번에 만드는 것: **URL 목록을 자동으로 수집해서 보여주고 파일로 저장하는 것까지**.

이 다음 단계인 "수집한 URL 각각을 자동으로 검증해 회귀 결과를 내는 일"은 일부러 분리한다. 이유는 두 가지다.
- "자동 검증"의 정의가 아직 모호하다 — 페이지가 열리기만 하면 OK인가? 특정 텍스트가 보여야 하나? 클릭 시나리오까지 돌려야 하나? 이건 따로 합의가 필요하다.
- 한 번에 두 가지 새 기능을 섞으면 검증과 디버깅이 어려워진다.

그래서 이번 1차는 **"수집"** 까지, 그다음 별도 작업으로 **"수집된 목록을 받아서 자동 검증"** 을 한다. 1차 결과 파일(CSV)이 그대로 다음 작업의 입력이 되니 낭비는 없다.

## 안전장치

운영 중인 실제 사이트를 자동으로 훑게 되므로 다음 가드를 기본으로 둔다.
- **최대 페이지 수**: 200 (사용자가 화면에서 늘리거나 줄일 수 있음)
- **최대 깊이**: 3 (시작 주소에서 3번 안에 도달하는 페이지까지)
- **요청 간격**: 0.5초 (사이트에 부담 주지 않기)
- **같은 도메인만**: 외부 링크는 따라가지 않음
- **건너뛸 패턴**: `/logout`, `mailto:`, 파일 다운로드 링크(`.pdf`, `.zip` 등)
- **로그인 만료 감지**: 시작 직전에 세션이 살아 있는지 자동 확인, 죽었으면 즉시 중단

## 사용자가 보게 될 화면 흐름

1. Recording UI 접속 → 기존 "새 녹화 시작" 폼 아래에 "🔍 Discover URLs" 섹션이 새로 보인다
2. 시작 URL + (선택) 로그인 프로파일 + 한도 입력 → "Discover URLs" 클릭
3. 진행 중 표시(작업 ID, 발견 페이지 수 카운트)
4. 완료되면 표가 뜬다: URL · 응답코드 · 제목 · 깊이
5. **CSV 다운로드** 버튼, 그리고 행마다 **"이 URL로 녹화 시작"** 버튼 (기존 녹화 흐름으로 바로 연결)

## 성공 기준 (이게 되면 끝)

- 외부 의존 없는 내장 fixture(`http://localhost:18081/fixtures/full_dsl.html`)에 대해 인증 없이 동작
- 로그인 프로파일 지정 시 portal.koreaconnect.kr 같은 SSO 사이트에서도 로그인 필요한 내부 페이지가 목록에 나옴
- 결과 CSV가 `~/.dscore.ttc.playwright-agent/discoveries/<작업ID>/urls.csv` 에 저장됨
- 운영 사이트에 부담을 주지 않는 속도와 한도로 마무리됨

---

# Part 2. 개발자용 — 구현 컴포넌트와 태스크

## 전제와 재사용 자산

- 언어/런타임: Python 3.11+, Playwright sync API (서버는 FastAPI sync 라우트 + thread worker 패턴 사용 중)
- 인증 세션: [auth_profiles.py](playwright-allinone/zero_touch_qa/auth_profiles.py) 의 storageState + Fingerprint 그대로 재사용
  - `browser.new_context(storage_state=str(storage_path), **fingerprint.to_browser_context_kwargs())` 호출 규약은 [auth_profiles.py:1029-1032](playwright-allinone/zero_touch_qa/auth_profiles.py#L1029-L1032), [auth_profiles.py:1096-1098](playwright-allinone/zero_touch_qa/auth_profiles.py#L1096-L1098) 와 동일
- 기존 비동기 패턴: [server.py:1337](playwright-allinone/recording_service/server.py#L1337) 의 `threading.Thread(target=_seed_worker, ..., daemon=True).start()` 그대로 차용
- 결과 저장 루트: [server.py:251](playwright-allinone/recording_service/server.py#L251) `host_root()` (`~/.dscore.ttc.playwright-agent/`)
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
| `playwright-allinone/zero_touch_qa/url_discovery.py` | 신규 | BFS 크롤러 본체 |
| `playwright-allinone/recording_service/server.py` | 수정 | discover 엔드포인트 3종 + worker + auth_profile 로딩 함수 추출 |
| `playwright-allinone/recording_service/web/index.html` | 수정 | discover 폼 섹션 추가 |
| `playwright-allinone/recording_service/web/app.js` | 수정 | discover fetch + 폴링 + 결과 표 + CSV 링크 + "이 URL로 녹화 시작" 버튼 |
| `playwright-allinone/test/test_url_discovery.py` | 신규 | 정적 fixture 기반 단위 테스트 |

## 태스크

### 태스크 0 — auth_profile 로딩 로직 함수 추출 (server.py)

`_resolve_auth_profile_extras()` ([server.py:132-193](playwright-allinone/recording_service/server.py#L132-L193)) 는 codegen 용 `--load-storage` extra args 를 만들기 위한 함수다. discover 워커는 codegen extra args 가 필요 없고, **storage_state 파일 경로 + fingerprint 객체** 만 있으면 된다.

- 새 헬퍼 추가 (server.py 모듈 내, `_resolve_auth_profile_extras` 위에 배치):
  ```python
  def _load_profile_for_browser(name: str | None) -> tuple[Path | None, "Fingerprint" | None, str | None]:
      """auth_profile 이름 → (storage_path, fingerprint, machine_mismatch_msg).
      이름이 None 이면 (None, None, None). 만료/없음이면 HTTPException(422).
      """
  ```
- `_resolve_auth_profile_extras()` 는 위 헬퍼를 호출해 storage_path 를 받고 codegen extra args 를 만들도록 리팩터. 동작 변경 금지(테스트 통과 유지).
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
    fingerprint_kwargs: dict        # Fingerprint.to_browser_context_kwargs() 결과
    max_pages: int = 200
    max_depth: int = 3
    request_interval_sec: float = 0.5
    nav_timeout_ms: int = 15000
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

def discover_urls(cfg: DiscoverConfig, *, on_progress=None) -> list[DiscoveredUrl]:
    ...
```

핵심 규칙:
- BFS 큐 + visited set. URL 정규화 키: scheme+netloc+path+sorted(filtered_query). fragment 제거.
- 동일 호스트 판정: `urlparse(seed).netloc == urlparse(candidate).netloc` (대소문자 무시, port 포함). 서브도메인은 **다른 호스트**로 취급(1차 단순화).
- 링크 추출: `page.goto(url, wait_until="networkidle", timeout=cfg.nav_timeout_ms)` → `page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")`. `href` 는 브라우저가 절대 URL로 풀어준 결과라 base 처리 불필요.
- 응답 status: `response = page.goto(...)`; `response.status` 저장. 실패(타임아웃 등)는 `status=None`, title 은 `page.title()` 시도 후 실패 시 `None`.
- 깊이 가드: 큐에서 꺼낼 때 `depth > max_depth` 이면 skip. seed = depth 0.
- `on_progress(count, last_url)` 콜백 — server worker 가 진행 카운트 업데이트에 사용.
- request_interval_sec 만큼 `time.sleep` 사이마다 (운영 사이트 부담 가드).

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

class DiscoverJob(BaseModel):
    job_id: str
    state: Literal["running", "done", "failed"]
    seed_url: str
    auth_profile: str | None
    started_at: str
    finished_at: str | None = None
    count: int = 0
    last_url: str | None = None
    result_dir: str | None = None
    error: str | None = None
```

#### 2.2 모듈 전역 상태

```python
_discover_jobs: dict[str, DiscoverJob] = {}
_discover_lock = threading.Lock()
```

#### 2.3 엔드포인트

- `POST /discover` → 202
  ```python
  @app.post("/discover", status_code=202)
  def discover(req: DiscoverReq):
      job_id = uuid.uuid4().hex[:12]
      storage_path, fp, mismatch = _load_profile_for_browser(req.auth_profile)
      if mismatch:
          raise HTTPException(422, mismatch)
      job = DiscoverJob(job_id=job_id, state="running",
                        seed_url=str(req.seed_url),
                        auth_profile=req.auth_profile,
                        started_at=_now_iso())
      with _discover_lock:
          _discover_jobs[job_id] = job
      threading.Thread(
          target=_discover_worker,
          args=(job_id, storage_path, fp, req.max_pages, req.max_depth),
          daemon=True,
      ).start()
      return {"job_id": job_id, "state": "running"}
  ```

- `GET /discover/{job_id}` — DiscoverJob 직렬화 반환. 없으면 404.
- `GET /discover/{job_id}/csv` — `result_dir/urls.csv` `FileResponse` 반환. state != "done" 이면 409.
- `GET /discover/{job_id}/json` — `urls.json` 파일 응답.

#### 2.4 워커

```python
def _discover_worker(job_id: str, storage_path: Path | None, fp, max_pages: int, max_depth: int):
    job = _discover_jobs[job_id]
    out_dir = host_root() / "discoveries" / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
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
        results = discover_urls(cfg, on_progress=_progress)
        # 저장
        meta = {"seed_url": job.seed_url, "auth_profile": job.auth_profile,
                "started_at": job.started_at, "finished_at": _now_iso(),
                "count": len(results)}
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        (out_dir / "urls.json").write_text(json.dumps([asdict(r) for r in results],
                                                      ensure_ascii=False, indent=2))
        with (out_dir / "urls.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["url","status","title","depth","found_at"])
            w.writeheader()
            for r in results:
                w.writerow(asdict(r))
        with _discover_lock:
            job.state = "done"
            job.finished_at = meta["finished_at"]
            job.count = len(results)
            job.result_dir = str(out_dir)
    except Exception as e:
        with _discover_lock:
            job.state = "failed"
            job.error = repr(e)
            job.finished_at = _now_iso()
```

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
  </form>
  <div id="discover-status"></div>
  <div id="discover-result"></div>
</details>
```

#### 3.2 `web/app.js`

- `populateAuthProfiles()` 같은 기존 함수가 `#auth-profile` 을 채운다면, `#discover-auth-profile` 도 함께 채우도록 selector 확장.
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
    pollDiscover(j.job_id);
  }

  async function pollDiscover(jobId) {
    const statusEl = document.getElementById('discover-status');
    const resultEl = document.getElementById('discover-result');
    while (true) {
      const r = await fetch(`/discover/${jobId}`);
      const j = await r.json();
      statusEl.textContent = `[${j.state}] ${j.count}건 · 최근: ${j.last_url || '-'}`;
      if (j.state === 'done') {
        const list = await (await fetch(`/discover/${jobId}/json`)).json();
        renderDiscoverTable(resultEl, jobId, list);
        return;
      }
      if (j.state === 'failed') {
        statusEl.textContent = `실패: ${j.error}`;
        return;
      }
      await new Promise(res => setTimeout(res, 2000));
    }
  }

  function renderDiscoverTable(rootEl, jobId, list) {
    // 표 헤더: URL · status · title · depth · 액션
    // 액션 버튼: "이 URL로 녹화 시작" → fetch('/recording/start', {target_url: row.url, auth_profile: 현재 선택})
    // CSV 다운로드 링크: /discover/{jobId}/csv
  }
  ```
- `#discover-form` submit 이벤트 바인딩은 기존 `#start-form` 바인딩 패턴(L322-343 부근) 그대로 모방.
- "이 URL로 녹화 시작" 버튼은 기존 `startRecording()` (L117-137) 호출 재사용.

### 태스크 4 — 단위 테스트 신규 (`test/test_url_discovery.py`)

- `pytest` + `playwright` (이미 의존성에 있음).
- fixture: `tmp_path` 에 5개 HTML 파일 생성 (index → a, b, c → d, b → e), `python -m http.server` 를 백그라운드로 띄움 (다른 테스트 파일에 동일 패턴이 있으면 차용).
- 테스트 케이스:
  1. `discover_urls(seed=index)` → 5개 URL, depth 0~2 정확
  2. `max_pages=2` → 정확히 2개에서 정지
  3. `exclude_patterns` 에 `/b` 포함 시 b 와 그 자손 제외
  4. mailto:/.pdf 자동 제외
  5. fragment 와 query 정규화 — `?utm_source=x` 와 `?` 가 같은 URL 로 dedup
- storageState/fingerprint 경로는 None 으로 인증 없이 동작 검증 (실제 SSO 사이트 테스트는 수동).

### 태스크 5 — 수동 통합 검증 체크리스트

1. `./build.sh --redeploy` (또는 컨테이너 재기동 없이 `recording_service/server.py` 만 재시작 가능하면 그쪽이 빠름).
2. Recording UI 접속, "🔍 Discover URLs" 섹션 보임.
3. seed_url=`http://localhost:18081/fixtures/full_dsl.html`, auth_profile=비움, max_pages=20 → 진행 카운트 업데이트, 완료 후 표 표시, CSV 다운로드 버튼 작동.
4. seed_url=`https://portal.koreaconnect.kr/user/ma/main`, auth_profile=기존 프로파일 → 로그인 필요한 내부 페이지가 목록에 포함됨, status 200.
5. 만료된 auth_profile 지정 → 즉시 422 에러, UI 에 메시지 표시.
6. 결과 파일 확인:
   ```
   ls -la ~/.dscore.ttc.playwright-agent/discoveries/<job_id>/
   # meta.json, urls.csv, urls.json
   ```
7. 표의 "이 URL로 녹화 시작" 버튼 → 기존 `/recording/start` 흐름 정상 시작.

## 위험과 대응

| 위험 | 대응 |
|---|---|
| 운영 사이트 부하 | max_pages=200, max_depth=3, request_interval=0.5s 기본. 사용자 조정 가능. |
| storageState 만료 | 시작 시 `_load_profile_for_browser()` 가 즉시 422 거부. |
| 무한 SPA(쿼리 변종 폭증) | trash_query_params 제거 + sorted query dedup + max_pages 강제 종료. |
| networkidle 대기 타임아웃 | nav_timeout_ms=15000 고정, 실패 시 status=None 으로 기록 후 다음 URL 진행 (전체 중단 안 함). |
| robots.txt 미준수 | 1차 미구현. 후속에서 옵션화 가능 — 명시적으로 plan 에서 제외. |
| 동시 discover job 충돌 | job_id 별 격리, 결과 디렉토리 분리. 동시 실행 제한 안 둠(필요 시 후속). |

## 후속 작업으로 분리되는 것 (이번 plan 밖)

- 발견된 URL 각각에 대한 자동 visit·스모크 검증(스크린샷·콘솔/네트워크 에러·HTTP 상태 카탈로그). 1차 결과 CSV 가 그대로 입력이 됨.
- Jenkins Pipeline 통합 (예: `Discover-And-Verify` job).
- robots.txt 준수 옵션.
- 서브도메인 포함 옵션.
- 차분 모드 (이전 결과와 비교해 신규/사라진 URL 표시).
