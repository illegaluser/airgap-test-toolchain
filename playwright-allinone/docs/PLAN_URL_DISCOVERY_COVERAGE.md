# Plan: Discover URLs 커버리지 보강 (sitemap / request capture / SPA selectors / query strip / subdomains)

본 문서는 [PLAN_URL_DISCOVERY.md](PLAN_URL_DISCOVERY.md) 의 후속이다. 1차 구현은 `<a href>` 기반 BFS 로 운영 사이트의 *시드용* URL 목록을 만드는 데 충분했지만, 다음 5가지 누락 패턴이 분석에서 식별됐다. 본 plan 은 이를 **하나의 묶음 변경**으로 처리한다.

---

# Part 1. — 무엇을 왜 만드는가

## 풀려는 5가지 누락

지금 구현([url_discovery.py:196-198](../zero_touch_qa/url_discovery.py#L196-L198))은 같은 호스트의 `<a href>` 만 BFS 로 따라간다. 다음은 잡히지 않는다.

| # | 누락 | 영향 |
|---|---|---|
| 1 | `sitemap.xml` 미참조 | 운영자가 공식 발행한 정답 목록을 무시. 가장 큰 ROI 손실. |
| 2 | SPA 의 `fetch/XHR` 응답이나 동적 navigation | API 기반 라우팅 사이트에서 detail URL 누락. |
| 3 | `<a href>` 외 SPA 신호 (`role="link"`, `[onclick]`, `<button data-href>`) | React/Vue 사이트의 메뉴/카드 링크 통째 누락. |
| 4 | 쿼리 변종 (`?page=1..N`, `?sort=..&filter=..`) | visited-set 폭증 → `max_pages` 가 의미 없는 변종으로 채워짐. |
| 5 | 다른 서브도메인 (`portal.x.kr` ↔ `admin.x.kr`) | 같은 운영 단위인데 단절. |

## 무엇을 만드나

`url_discovery.py` 의 BFS 루프와 `DiscoverConfig` 옵션, Recording UI 의 "Discover URLs" 폼, server 의 `DiscoverReq` 페이로드에 위 5가지를 옵션으로 추가한다. **외과적 변경 원칙**: 기존 anchor BFS 의 동작은 옵션 OFF 시 비트 단위로 동일해야 한다.

다섯 옵션:

1. **`use_sitemap`** (기본 **ON**) — `/sitemap.xml`, `/robots.txt` 의 `Sitemap:` 디렉티브를 BFS 시작 직전에 수집해 큐에 시드.
2. **`capture_requests`** (기본 **ON**) — page navigation 중 같은 호스트로 나가는 `document/xhr/fetch` GET 요청 URL 을 캡처해 큐에 시드.
3. **`spa_selectors`** (기본 **OFF**) — `[role="link"][data-href]`, `button[data-href]`, `[data-link-to]`, `[data-route]` 등 SPA 신호 셀렉터로 추가 URL 추출.
4. **`ignore_query`** (기본 **OFF**) — `normalize_url()` 시 모든 쿼리 문자열을 제거해 페이지네이션/필터 변종을 한 페이지로 통합.
5. **`include_subdomains`** (기본 **OFF**) — 같은 호스트 판정을 "정확 일치" 에서 "seed hostname 또는 그 서브도메인" 으로 완화.

각 발견 URL 은 새 필드 `source` 로 어디서 왔는지 추적된다 (`anchor` / `sitemap` / `request` / `spa_selector` / `seed`).

## 왜 일부는 ON, 일부는 OFF 가 기본인가

- **sitemap / request capture**: 깨지기 어렵고 false-positive 가 거의 없음. 사이트가 sitemap 을 안 두면 조용히 skip. request 캡처는 navigation 중 자연스럽게 발생하는 것만 보므로 노이즈 적음. 기본 ON.
- **SPA 셀렉터**: 마크업 관습이 사이트마다 달라 false-positive 가 큼. 데이터 속성을 navigation 으로 오해 가능. 옵트인.
- **`ignore_query`**: 게시판 사이트엔 필수, 검색결과 사이트엔 정보 손실. 사이트 특성 따라 사용자가 켠다.
- **`include_subdomains`**: 보안 경계가 흐려지는 변경. 명시적 옵트인.

## 사용자가 보게 될 변화

1. Recording UI 의 "🔍 Discover URLs" 폼에 **고급 옵션** `<details>` 추가. 기본 접힘. 펼치면 5개 토글이 보인다.
2. 결과 표에 새 컬럼 **출처** (anchor / sitemap / request / spa_selector / seed). 같은 URL 이 두 경로로 잡혀도 먼저 본 출처 1개만 기록된다.
3. CSV 에도 같은 컬럼이 추가된다 (`source`).
4. `meta.json` 에 사용된 옵션값 그대로 박힌다 (재현성).

## 성공 기준

- 기존 단위 테스트 ([test/test_url_discovery.py](../test/test_url_discovery.py)) 의 모든 케이스가 변경 없이 통과 — 기본 동작 비파괴.
- `use_sitemap=True` 일 때 fixture 가 발행한 `/sitemap.xml` 의 URL 이 결과에 포함되고 `source="sitemap"` 으로 표시.
- `capture_requests=True` 일 때 같은 호스트로 나가는 GET 문서/XHR URL 이 결과에 포함되고 `source="request"` 로 표시.
- `spa_selectors=True` 일 때 `<button data-href="/x">` 등 SPA 셀렉터 fixture 가 결과에 포함되고 `source="spa_selector"` 로 표시.
- `ignore_query=True` 일 때 `?page=1..50` fixture 가 1개로 dedup.
- `include_subdomains=True` 일 때 fixture `a.local` seed 에서 `b.a.local` 링크가 결과에 포함되고 OFF 일 땐 빠짐.
- API 통합 테스트로 5개 옵션이 worker 까지 전달되어 결과에 반영됨을 확인.
- Recording UI 의 고급 옵션 토글이 페이로드에 정확히 실림.

## 이 plan 의 범위 밖

- `<a href="javascript:..." onclick="...">` 의 onclick 본문 정적 분석 — 복잡도 대비 ROI 낮음.
- iframe / Shadow DOM 내부 링크 — 별도 plan.
- Hover 만으로 열리는 메뉴 자동 탐색 — 별도 plan (사용자 시뮬레이션 필요).
- 클릭 기반 SPA route discovery (실제로 버튼을 눌러 라우팅 변화 관찰) — 별도 plan.
- `tldextract` 도입한 정확한 registrable-domain 비교 — 1차는 hostname suffix 매칭으로 단순화.
- `sitemap.xml.gz` 압축 / 외부 도메인 sitemap 인덱스 — 1차는 plain XML + 같은 호스트만.
- robots.txt 의 `Disallow:` 준수 — 1차는 `Sitemap:` 디렉티브만 읽고 `Disallow:` 는 무시 (PLAN_URL_DISCOVERY 의 위험 표와 동일 정책).

---

# Part 2. — 구현 컴포넌트와 태스크

## 전제와 재사용 자산

- 기존 BFS / `normalize_url` / `_same_host` 는 [url_discovery.py](../zero_touch_qa/url_discovery.py) 에 위치.
- server 측 옵션 패스스루는 [server.py:1447-1451](../recording_service/server.py#L1447-L1451) `DiscoverReq` 와 [server.py:1486-1572](../recording_service/server.py#L1486-L1572) `_discover_worker` 두 곳.
- tour-script 생성기 ([server.py:2023-2092](../recording_service/server.py#L2023-L2092)) 는 본 변경에 영향 받지 않는다 (정규화 키만 일치하면 됨). 단, **`ignore_query=True` 로 만든 결과에서 tour-script 를 생성할 때 일관성**은 §2.5 에서 다룬다.
- UI 는 [index.html:181-227](../recording_service/web/index.html#L181-L227) 의 discover 섹션과 [app.js:1396-...](../recording_service/web/app.js#L1396) 의 discover 모듈에 한정.

## 손대지 않을 것

- `<a href>` BFS 의 깊이/큐/visited-set 메인 루프 자체. 새 출처는 큐 초기 시드(sitemap) 또는 페이지 처리 직후 **추가 후보 수집**(request capture / spa selectors) 으로만 합류한다.
- `_resolve_auth_profile_extras` 와 `/recording/start` 흐름.
- tour-script 생성 로직과 `TourScriptReq`. 단 `ignore_query=True` 결과의 매칭 호환성은 §2.5 에 한정.
- 기존 테스트 케이스의 입력 fixture HTML.

## 신규/수정 파일 일람

| 파일 | 신규/수정 | 목적 |
|---|---|---|
| `playwright-allinone/shared/zero_touch_qa/url_discovery.py` | 수정 | `DiscoverConfig` 5개 옵션 + `source` 필드 + sitemap fetch 헬퍼 + request capture hook + SPA 셀렉터 + 쿼리 무시 정규화 분기 + 서브도메인 매칭 분기 |
| `playwright-allinone/recording-ui/recording_service/server.py` | 수정 | `DiscoverReq` 5개 옵션 + worker 패스스루 + CSV `source` 컬럼 + meta.json 옵션 기록. `TourScriptReq` 매칭에 `ignore_query` 파생 정규화 동일 적용 |
| `playwright-allinone/recording-ui/recording_service/web/index.html` | 수정 | discover 폼에 고급 옵션 `<details>` 추가 (체크박스 5개) |
| `playwright-allinone/recording-ui/recording_service/web/app.js` | 수정 | 폼 → 페이로드, 표에 `source` 컬럼 추가 |
| `playwright-allinone/test/test_url_discovery.py` | 수정 | 5개 옵션의 단위 회귀 케이스 추가 (fixture HTML/sitemap/handler 확장) |
| `playwright-allinone/test/test_discover_api_e2e.py` | 수정 | 5개 옵션이 페이로드 → worker → 결과까지 전달되는지 1~2개 통합 케이스 추가 |

## 태스크

### 태스크 0 — 데이터 모델 보강 (`url_discovery.py`)

#### 0.1 `DiscoveredUrl` 에 `source` 추가

```python
from typing import Literal

UrlSource = Literal["seed", "anchor", "sitemap", "request", "spa_selector"]

@dataclass
class DiscoveredUrl:
    url: str
    status: Optional[int]
    title: Optional[str]
    depth: int
    found_at: str
    source: UrlSource = "anchor"   # ← 추가. 기본값으로 기존 케이스 호환.
```

기존 단위 테스트는 `source` 를 검사하지 않으므로 영향 없음. CSV/JSON serializer 는 `asdict()` 로 자동 반영.

#### 0.2 `DiscoverConfig` 에 옵션 5개 추가

```python
@dataclass
class DiscoverConfig:
    # ... 기존 필드 그대로 ...
    use_sitemap: bool = True
    capture_requests: bool = True
    spa_selectors: bool = False
    ignore_query: bool = False
    include_subdomains: bool = False

    # SPA 셀렉터 ON 시 사용할 querySelector 목록. 사용자 노출 X (1차 고정).
    spa_selector_list: tuple[str, ...] = (
        '[role="link"][data-href]',
        'button[data-href]',
        '[data-link-to]',
        '[data-route]',
    )

    # capture_requests 수집 대상 resource type. 1차 고정.
    capture_resource_types: tuple[str, ...] = ("document", "xhr", "fetch")

    # sitemap fetch 시 최대 가져올 URL 수. <urlset> 가 너무 큰 사이트 가드.
    sitemap_max_urls: int = 1000
    # <sitemapindex> 의 child sitemap 따라가기 한도. 1=index 1단계만.
    sitemap_max_index_followed: int = 50
    # sitemap fetch timeout
    sitemap_timeout_sec: float = 5.0
```

검증 기준: 새 필드 모두 기본값을 가지므로 `DiscoverConfig(seed_url=..., storage_state_path=..., fingerprint_kwargs=...)` 호출은 깨지지 않는다.

#### 0.3 `normalize_url()` 분기

`ignore_query=True` 가 호출 시점에 키 1개로 들어와야 한다. 기존 시그니처를 *확장* 하되 default 호환을 지킨다:

```python
def normalize_url(
    raw: str,
    *,
    trash_query_params: tuple[str, ...] = (),
    strip_all_query: bool = False,   # ← 추가
) -> str:
    ...
    if strip_all_query:
        query = ""
    else:
        # 기존 trash 제거 + 정렬 로직
        ...
```

BFS 루프에서 visited 키 만들 때, tour-script 매칭에서 입력/저장 비교할 때 모두 이 인자를 일관되게 넘긴다.

회귀 보호: 기존 `test_normalize_equiv` / `test_normalize_distinct` 는 `strip_all_query` 미지정으로 호출되므로 비파괴.

#### 0.4 `_same_host()` 분기

```python
def _host_matches(seed_host: str, candidate_host: str, *,
                  include_subdomains: bool) -> bool:
    s = seed_host.lower()
    c = candidate_host.lower()
    if c == s:
        return True
    if include_subdomains and c.endswith("." + s):
        return True
    return False
```

기존 `_same_host(seed_netloc, candidate_netloc)` 는 netloc 비교 (port 포함). 본 변경은 hostname 비교로 의미가 바뀌는데, `include_subdomains=False` 인 기본 경로에서 결과가 달라지면 안 된다. 따라서 BFS 루프에서:

```python
seed_host = (seed_parsed.hostname or "").lower()
seed_port = seed_parsed.port

# 기존: _same_host(seed_netloc, parsed.netloc) — netloc(host:port) 정확 일치
# 신규: hostname 매칭 + (subdomains OFF 시) port 도 정확 일치
candidate_host = (parsed.hostname or "").lower()
if not _host_matches(seed_host, candidate_host, include_subdomains=cfg.include_subdomains):
    continue
if not cfg.include_subdomains:
    if (parsed.port or _DEFAULT_PORTS.get(parsed.scheme.lower())) != \
       (seed_port or _DEFAULT_PORTS.get(seed_scheme)):
        continue
```

`include_subdomains=True` 일 땐 port 일치 강제를 풀지 않는다 (cross-port 는 별도 호스트로 본다). 보안 경계 일관성.

검증: 기존 외부 링크/127.0.0.1 vs localhost 케이스가 그대로 유지되는지 `test_bfs_discovers_all_pages` / `test_auth_drift_aborts` 가 보장.

---

### 태스크 1 — sitemap.xml 시드 (`url_discovery.py`)

#### 1.1 헬퍼 신규

```python
def _fetch_sitemap_seeds(
    page,
    seed_url: str,
    *,
    max_urls: int,
    max_index_followed: int,
    timeout_sec: float,
) -> list[str]:
    """seed origin 의 robots.txt + sitemap.xml 을 best-effort 로 읽고 URL 리스트 반환.

    실패는 조용히 빈 리스트. 외부 도메인 sitemap 은 무시.
    """
```

구현 노트:

- HTTP fetch 는 `page.context.request` 를 사용한다 (이미 storageState/fingerprint 가 적용된 `APIRequestContext`). 별도 `requests`/`httpx` 의존성 추가 안 함.
- 단계:
  1. `<seed_origin>/robots.txt` GET, 200 이면 line 별로 `Sitemap:` 디렉티브 추출.
  2. 후보가 비어있으면 `<seed_origin>/sitemap.xml` 한 개를 기본 후보로.
  3. 각 후보를 GET → XML 파싱.
     - 표준 라이브러리 `xml.etree.ElementTree` 사용. namespace 무시 (`tag.split('}')[-1]`).
     - 루트가 `<sitemapindex>` 면 `<sitemap><loc>` 들을 모아 `max_index_followed` 만큼 따라간다 (1단계만, 재귀 X).
     - 루트가 `<urlset>` 이면 `<url><loc>` 만 추출.
  4. 같은 origin 의 URL 만 반환. 누적 한도는 `max_urls`.
- gzip (`.xml.gz`) 은 1차 미지원. content-type 확인해 skip.

#### 1.2 BFS 큐 시드

`discover_urls()` 의 큐 초기화 직전에:

```python
if cfg.use_sitemap:
    sitemap_urls = _fetch_sitemap_seeds(
        page, cfg.seed_url,
        max_urls=cfg.sitemap_max_urls,
        max_index_followed=cfg.sitemap_max_index_followed,
        timeout_sec=cfg.sitemap_timeout_sec,
    )
    for u in sitemap_urls:
        key = normalize_url(u, trash_query_params=cfg.trash_query_params,
                            strip_all_query=cfg.ignore_query)
        if key in visited:
            continue
        # 같은 호스트 검증은 _host_matches + (port 정책) 으로 동일하게.
        # depth=1 로 큐에 push: seed 가 0 이라는 의미를 유지하되 sitemap 은 "찾아낸 후보".
        visited.add(key)
        queue.append((u, 1, "sitemap"))   # source 정보를 큐 튜플에 같이 싣는다.
```

큐 튜플 시그니처가 `(url, depth)` → `(url, depth, source)` 로 확장된다. seed 자신은 `"seed"`, anchor 추출은 `"anchor"`, request capture 는 `"request"`, spa 는 `"spa_selector"`.

`DiscoveredUrl(...)` 생성 시 큐에서 꺼낸 `source` 를 그대로 박는다.

#### 1.3 sitemap fetch 가 큐 채우기 전에 필요한 page 객체

원래 `page = context.new_page()` 직후 BFS 루프 진입이지만, sitemap fetch 는 `page.context.request` 만 쓰면 되므로 페이지 navigation 전에도 호출 가능하다. 순서: browser → context → page 생성 → sitemap fetch (use_sitemap=True 일 때만) → BFS 루프.

#### 1.4 한계 1줄 코멘트

코드에는 1줄짜리만 남긴다 (CLAUDE.md 의 *코멘트 규칙*).

```python
# robots.txt 의 Disallow 는 1차 미준수. PLAN_URL_DISCOVERY_COVERAGE.md 참조.
```

---

### 태스크 2 — request 캡처 (`url_discovery.py`)

#### 2.1 페이지마다 핸들러 부착

BFS 루프 안에서 `page.goto()` 호출 직전에 페이지 단위 캡처 리스트를 준비하고 핸들러를 등록, 직후 분리한다:

```python
captured_request_urls: list[str] = []

def _on_request(req):
    if not cfg.capture_requests:
        return
    if req.resource_type not in cfg.capture_resource_types:
        return
    if req.method != "GET":
        return
    captured_request_urls.append(req.url)

if cfg.capture_requests:
    page.on("request", _on_request)
try:
    response = page.goto(url, ...)
    ...
finally:
    if cfg.capture_requests:
        page.remove_listener("request", _on_request)
```

루프 매번 등록/해제하는 이유: 페이지 단위로 어떤 URL 이 어디서 발견됐는지 깔끔히 분리. 컨텍스트 레벨에 한 번만 붙이면 thread-safety 와 출처 추적이 흐려진다.

#### 2.2 캡처된 URL 을 큐에 합류

`page.goto()` 직후, anchor href 추출과 같은 위치에서:

```python
candidate_pool: list[tuple[str, str]] = [(h, "anchor") for h in hrefs]
if cfg.capture_requests:
    candidate_pool.extend((u, "request") for u in captured_request_urls)
if cfg.spa_selectors:
    spa_hrefs = _extract_spa_hrefs(page, cfg.spa_selector_list)
    candidate_pool.extend((u, "spa_selector") for u in spa_hrefs)
```

기존 anchor 처리 루프를 `candidate_pool` 순회로 치환하고, 통과한 후보는 `queue.append((href, depth + 1, source))` 형태로 push.

중복 가드는 기존 `visited` set 그대로. 같은 URL 이 anchor 와 request 양쪽에서 잡히면 먼저 본 쪽의 source 만 기록되고 나중 건은 visited 로 컷된다 — 이는 의도된 동작이며 §1 의 "같은 URL 이 두 경로로 잡혀도 먼저 본 출처 1개만" 을 만족시킨다.

#### 2.3 노이즈 가드

- request capture 결과는 path 기반 `exclude_extensions` (.png, .css 등) 을 통과해야 함. 이미 anchor 처리에서 그렇게 한다 — 같은 코드 경로를 공유.
- redirect 응답을 따라간 URL 은 `request` 이벤트가 두 번 발생할 수 있다 (원본 + final). 둘 다 잡되 visited dedup 으로 자연스럽게 정리. `exclude_patterns` 의 `/logout`, `/signout` 도 자동 적용.
- 같은 호스트 매칭은 anchor 와 동일하게 `_host_matches` + port 정책을 통과해야 한다.
- API XHR 응답이 페이지가 아닌 JSON 인 경우도 큐에 들어가지만, BFS 가 그 URL 을 `page.goto()` 한 결과 status<400 이면 결과에 남고 그렇지 않으면 status 만 기록되고 자손은 못 추출한다 (HTML 이 아니므로). 기능적으로는 "URL 이 존재한다"는 사실이 결과에 남는 게 우리가 원하는 동작.

---

### 태스크 3 — SPA 셀렉터 (`url_discovery.py`)

#### 3.1 헬퍼 신규

```python
def _extract_spa_hrefs(page, selectors: tuple[str, ...]) -> list[str]:
    """data-href / data-link-to / data-route 같은 SPA 신호 셀렉터로 URL 후보 추출.

    매칭한 element 의 텍스트 속성에서 다음 우선순위로 URL 을 뽑는다:
    1. element.getAttribute('data-href')
    2. element.getAttribute('data-link-to')
    3. element.getAttribute('data-route')
    4. element.dataset.href / dataset.linkTo / dataset.route 의 fallback

    상대 URL 은 page.url 기준으로 absolute 화 한다.
    """
```

구현은 `page.eval_on_selector_all(...)` 한 번으로:

```python
script = """
(els) => els.map(e => {
  const v = e.getAttribute('data-href') || e.getAttribute('data-link-to')
            || e.getAttribute('data-route');
  if (!v) return null;
  try { return new URL(v, location.href).href; } catch (_) { return null; }
}).filter(Boolean)
"""
combined_selector = ", ".join(selectors)
hrefs = page.eval_on_selector_all(combined_selector, script)
```

`PlaywrightError` 는 anchor 와 동일하게 빈 리스트로 swallow.

#### 3.2 한계 코멘트 (1줄)

```python
# onclick 본문 정적 분석은 미지원. data-* 속성 기반만.
```

---

### 태스크 4 — 옵션 패스스루 (`server.py`)

#### 4.1 `DiscoverReq` 확장

```python
class DiscoverReq(BaseModel):
    seed_url: str = Field(..., description="크롤 시작 URL")
    auth_profile: Optional[str] = None
    max_pages: int = Field(200, ge=1, le=2000)
    max_depth: int = Field(3, ge=0, le=10)
    use_sitemap: bool = True
    capture_requests: bool = True
    spa_selectors: bool = False
    ignore_query: bool = False
    include_subdomains: bool = False
```

기존 클라이언트가 새 필드 없이 보내도 기본값으로 동작 — 후방 호환.

#### 4.2 worker 시그니처 확장

`_discover_worker(...)` 에 5개 인자 추가, `discover_start` 에서 그대로 전달, `DiscoverConfig` 에 그대로 셋. 의도적으로 dict 한 덩이로 묶지 않는다 — 함수 시그니처가 명시적으로 무엇이 흐르는지 보여주는 게 디버깅에 낫다.

#### 4.3 meta.json 에 옵션 기록

```python
meta = {
    ...
    "options": {
        "use_sitemap": cfg.use_sitemap,
        "capture_requests": cfg.capture_requests,
        "spa_selectors": cfg.spa_selectors,
        "ignore_query": cfg.ignore_query,
        "include_subdomains": cfg.include_subdomains,
    },
}
```

#### 4.4 CSV 컬럼 추가

```python
fieldnames=["url", "status", "title", "depth", "source", "found_at"]
```

`source` 를 `depth` 와 `found_at` 사이에 둔다 — 행을 읽을 때 "어디서 어떻게 발견됐는지" 가 한눈에.

#### 4.5 `TourScriptReq` 매칭 호환

tour-script 생성기는 `urls.json` 의 URL 과 입력 URL 을 `normalize_url(..., trash_query_params=trash)` 로 맞춰 비교한다 ([server.py:2037-2054](../recording_service/server.py#L2037-L2054)).

`ignore_query=True` 로 만든 discovery 결과 URL 은 본래 쿼리가 없는 형태일 수 있는데, 사용자가 UI 에서 그걸 그대로 보고 체크해서 보내면 매칭은 깨지지 않는다. 다만 안전을 위해 **server 가 meta.json 의 options 에서 `ignore_query` 를 읽어** 양쪽 정규화에 같은 `strip_all_query` 를 적용한다:

```python
opts = meta.get("options", {})
strip = bool(opts.get("ignore_query", False))
norm_to_original.setdefault(
    normalize_url(u, trash_query_params=trash, strip_all_query=strip), u
)
# req.urls 측도 같은 strip 적용
```

이게 PLAN_URL_DISCOVERY 의 *§위험: 발견 URL 과 tour-script 입력 매칭 정합성* 가드를 유지한다. 회귀 보호: 기존 `test_discover_api_e2e.py` 의 매칭 케이스가 `ignore_query` 없이도 통과해야 한다 (default False).

---

### 태스크 5 — UI (`index.html` + `app.js`)

#### 5.1 폼에 고급 옵션 추가

`<form id="discover-form">` 안 `<div class="form-actions">` 직전에:

```html
<details class="discover-advanced">
  <summary>고급 옵션</summary>
  <label class="optional">
    <input type="checkbox" name="use_sitemap" checked>
    sitemap.xml / robots.txt 의 Sitemap 사용 (기본 ON)
  </label>
  <label class="optional">
    <input type="checkbox" name="capture_requests" checked>
    페이지가 부르는 같은 호스트의 요청 URL 도 수집 (기본 ON)
  </label>
  <label class="optional">
    <input type="checkbox" name="spa_selectors">
    SPA 신호 셀렉터(data-href, role=link 등) 추가 수집
  </label>
  <label class="optional">
    <input type="checkbox" name="ignore_query">
    URL 의 쿼리 문자열 무시 (페이지네이션/필터 변종 통합)
  </label>
  <label class="optional">
    <input type="checkbox" name="include_subdomains">
    같은 루트 도메인의 서브도메인 포함
  </label>
</details>
```

UX 결정: 5개 모두 한 곳에 모은 `<details>` 1개. 기본 접힘.

#### 5.2 페이로드 구성

`startDiscover(form)` 흐름에서:

```js
const data = Object.fromEntries(new FormData(form));
data.max_pages = Number(data.max_pages);
data.max_depth = Number(data.max_depth);
// 체크박스 5개 — FormData 는 unchecked 면 키 자체가 없다.
data.use_sitemap = form.elements.use_sitemap.checked;
data.capture_requests = form.elements.capture_requests.checked;
data.spa_selectors = form.elements.spa_selectors.checked;
data.ignore_query = form.elements.ignore_query.checked;
data.include_subdomains = form.elements.include_subdomains.checked;
if (!data.auth_profile) delete data.auth_profile;
```

#### 5.3 결과 표 컬럼 추가

`_renderDiscoverTable()` 의 헤더와 row 에 `source` 컬럼을 `depth` 다음에 삽입. `<th>출처</th>`, `<td>${source}</td>` (textContent 로). 기존 XSS 가드 그대로 유지 — `innerHTML` 금지 ([app.js:1452](../recording_service/web/app.js#L1452) 부근의 정책 유지).

---

### 태스크 6 — 단위 테스트 (`test/test_url_discovery.py`)

각 옵션마다 fixture 1개, 회귀 케이스 1~2개. 기존 케이스는 그대로 둔다.

#### 6.1 `use_sitemap`

`tmp_path` 에 `index.html` (빈 페이지) + `sitemap.xml` (`<urlset>` 안에 `<url><loc>http://127.0.0.1:{port}/a.html</loc>` 등 3개) + `a.html`/`b.html`/`c.html`. seed=`/index.html`.

- `use_sitemap=False` → index 1건만 (anchor 없음).
- `use_sitemap=True` → index + 3건. 모두 `source=="sitemap"` 또는 `seed`.

추가: `<sitemapindex>` → child sitemap 1개 follow 케이스 1개.

추가: `/robots.txt` 에 `Sitemap: ...` 디렉티브 → 해당 sitemap 이 fetch 되는지 1개.

#### 6.2 `capture_requests`

`index.html` 안에 `<script>fetch('/api/x')</script>` 와 `<a href='/y'>` 두 개. `/api/x` 는 200 JSON, `/y` 는 200 HTML.

- `capture_requests=False` → index + y. (api/x 는 anchor 가 아니라 빠짐)
- `capture_requests=True` → index + y + api/x. `api/x` 의 `source=="request"`.

#### 6.3 `spa_selectors`

`index.html` 안에 `<button data-href="/x">x</button>`, `<div role="link" data-href="/y">y</div>`. `/x`, `/y` 는 200 HTML.

- `spa_selectors=False` → index 만 (anchor 없음).
- `spa_selectors=True` → index + x + y. `source=="spa_selector"`.

#### 6.4 `ignore_query`

`index.html` 안에 `<a href='/list?page=1'>`, `<a href='/list?page=2'>`, ..., `<a href='/list?page=10'>`.

- `ignore_query=False` → index + 10건 (각 page 가 별개).
- `ignore_query=True` → index + 1건 (`/list` 로 dedup).

#### 6.5 `include_subdomains`

이건 단일 호스트 `127.0.0.1` 로 시뮬레이트가 어렵다. 두 개 포트로 mock 호스트 두 개를 띄워 fixture 세팅이 복잡해진다. **단순화: hostname 매칭 함수 `_host_matches` 를 직접 단위 테스트** + BFS 통합 케이스는 monkeypatch 로 `urlparse` 결과를 흉내내는 fixture 한 개.

```python
def test_host_matches():
    assert _host_matches("a.example", "a.example", include_subdomains=False)
    assert not _host_matches("a.example", "b.a.example", include_subdomains=False)
    assert _host_matches("a.example", "b.a.example", include_subdomains=True)
    assert not _host_matches("a.example", "ba.example", include_subdomains=True)
    assert not _host_matches("a.example", "evil.com", include_subdomains=True)
```

BFS 통합은 manual 검증 (§7.x) 으로 보강 — 자동화 비용 대비 ROI.

#### 6.6 비파괴 가드

기존 `test_bfs_discovers_all_pages` / `test_dedup_via_normalize` / `test_per_url_isolation` / `test_cancel_event_partial_results` / `test_auth_drift_aborts` 가 변경 없이 통과해야 한다. 이게 *기본값 선택의 정합성* 보장.

특히 **`capture_requests=True` 가 기본 ON 이므로** `test_bfs_discovers_all_pages` 의 5페이지 사이트가 `<a>` 외에 fetch 를 안 부른다는 사실이 회귀 보호. fixture HTML 에 외부 리소스가 없는지 확인 (현재는 plain HTML — OK).

---

### 태스크 7 — API 통합 테스트 (`test/test_discover_api_e2e.py`)

- 옵션 5개를 페이로드에 실어 보내고 `meta.json` 의 `options` 에 그대로 기록되는지 1개 테스트.
- `urls.csv` 에 `source` 컬럼이 있는지 1개 테스트.
- `use_sitemap=True` + `<urlset>` fixture 로 결과에 `source="sitemap"` 이 포함되는지 1개 통합 테스트 (가장 ROI 큰 옵션이라 e2e 까지).
- `ignore_query=True` 로 만든 discovery 의 tour-script 매칭이 깨지지 않는지 1개 테스트 (§4.5 정합성).

---

### 태스크 8 — 수동 검증 체크리스트

1. `./build.sh --redeploy` 또는 server.py 만 재시작.
2. UI 진입, "🔍 Discover URLs" → 고급 옵션 펼침, 5개 토글 보임.
3. seed=`http://localhost:18081/fixtures/full_dsl.html`, 옵션 default → 결과 표에 `출처` 컬럼이 보이고 모두 `seed`/`anchor` (해당 fixture 는 sitemap/SPA 가 없으므로).
4. seed=`https://portal.koreaconnect.kr/user/ma/main`, auth_profile=기존, 기본 옵션:
   - 결과에 `source="sitemap"` 이 1개 이상 나오는지 (사이트가 sitemap 을 발행한다면).
   - `source="request"` 가 0개 이상.
5. `spa_selectors=ON` 으로 같은 seed 재시도 → 추가 URL 이 잡히는지.
6. `ignore_query=ON` → 게시판 변종이 한 줄로 묶이는지.
7. `include_subdomains=ON` 으로 root domain 시드 → 다른 서브도메인 URL 이 표에 나오는지.
8. 각 옵션 OFF 로 한번씩 → 영향 없음 / 누락 분명히 확인 (옵션 효과 검증).
9. CSV 다운로드 → `source` 컬럼 확인.
10. `meta.json` 의 `options` 가 UI 에서 보낸 값과 일치.
11. tour-script 생성 → 선택 URL 이 `ignore_query` 모드에서도 정상 매칭되어 422 안 남.
12. 다운로드 받은 `tour_selected.py` 가 기존과 같이 동작.

---

## 위험과 대응

| 위험 | 대응 |
|---|---|
| sitemap.xml 거대 (수만 URL) | `sitemap_max_urls=1000`, `sitemap_max_index_followed=50` 하드 캡. 이후 BFS 가 `max_pages` 로 추가 컷. |
| sitemap fetch 가 네트워크 타임아웃 | `sitemap_timeout_sec=5.0`, 실패는 조용히 빈 리스트 — discover 자체는 진행. |
| robots.txt Disallow 무시 | 1차 정책 그대로 (PLAN_URL_DISCOVERY 와 동일). 명시적으로 plan 에 박아둠. |
| request capture 노이즈 (광고/트래커) | `_host_matches` + `exclude_extensions` 가드로 같은 호스트 GET 만. 서드파티 도메인은 자동 컷. |
| request capture 의 API JSON URL 이 `page.goto()` 에서 200 JSON 으로 응답 | 결과에 status=200, title=None 으로 남는다. anchor 추출은 빈 리스트. 동작상 무해. 사용자에게 source="request" 로 보여 구분 가능. |
| SPA 셀렉터의 false-positive (`data-route` 가 진짜 URL 이 아닌 사이트) | 옵트인 (기본 OFF). UI 토글 라벨에 "신호 셀렉터" 명시. |
| `ignore_query=True` 로 정보 손실 | UI 토글 라벨에 "변종 통합" 명시. tour-script 매칭은 §4.5 로 대칭 보장. |
| `include_subdomains` 의 hostname suffix 매칭 정확성 (`a.example` 가 `evil-a.example` 잡지 않음) | `endswith("." + seed_host)` 의 점 포함이 가드. 단위 테스트 `test_host_matches` 로 보호. |
| `include_subdomains=True` 시 cross-port 도 같이 풀리는 오해 | 명시적으로 port 일치 강제 유지. 코드 주석 1줄 + 테스트 1개. |
| 페이지마다 request 핸들러 등록/해제 race | sync API 라 단일 thread. `page.on/remove_listener` 는 동기. 큐 합류는 navigation 완료 후 1회. race 없음. |
| sitemap 시드와 BFS depth 정합성 | sitemap URL 은 depth=1 로 큐. seed=0 의 의미를 보존하면서, sitemap 만 먼저 받아 BFS depth 한도가 sitemap 자손까지 효과 있게 함. 기본 `max_depth=3` 이면 sitemap+2단계까지 자동 확장. |
| 큐 튜플 시그니처 변경 (`(url, depth)` → `(url, depth, source)`) 의 외부 영향 | 큐는 module-private. 외부 노출 없음. 단위 테스트는 큐 직접 검사 안 함. |

## 후속 작업 (이번 plan 밖)

- `<a href="javascript:..." onclick="...">` 의 onclick 정적 분석.
- iframe / Shadow DOM 침투.
- Hover 메뉴 자동 열기 / 클릭 기반 SPA route 발견.
- `tldextract` 도입 (정확한 registrable-domain 매칭).
- `sitemap.xml.gz` 압축 해제.
- robots.txt 의 `Disallow` 준수 옵션.
- 발견 URL 의 *복수 출처* 기록 (현재는 첫 출처만).
- `source` 별 통계 카드 (UI 에 "anchor 12, sitemap 30, request 5" 같은 요약).
