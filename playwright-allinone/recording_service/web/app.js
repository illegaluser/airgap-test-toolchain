// DSCORE Recording Service Web UI (TR.4)
// vanilla JS — 프레임워크 없음. 단일 worker 데몬 가정.

const $ = (sel) => document.querySelector(sel);
const $all = (sel) => document.querySelectorAll(sel);

// ── 전역 상태 (단일 활성 세션 가정) ──────────────────────────────────────────
let _state = {
  activeSid: null,
  startedAt: null,
  pollTimer: null,
  elapsedTimer: null,
};

// ── API helpers ──────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (r.status === 204) return null;
  const text = await r.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = { raw: text }; }
  if (!r.ok) {
    const detail = (data && data.detail) || `HTTP ${r.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function loadHealth() {
  try {
    const h = await api("/healthz");
    const badge = $("#health-badge");
    if (h.codegen_available) {
      badge.textContent = `✓ healthy · v${h.version}`;
      badge.className = "ok";
    } else {
      badge.textContent = `⚠ codegen 미설치 · v${h.version}`;
      badge.className = "warn";
    }
  } catch (e) {
    $("#health-badge").textContent = "✗ unreachable";
    $("#health-badge").className = "err";
  }
}

// P3 — 세션 필터. localStorage 에 마지막 값 보존.
const _SESSION_FILTER_KEY = "rec.sessionFilter";
const _SESSION_STATE_KEY = "rec.sessionStateFilter";

let _sessionsCache = [];

function _renderSessionRows() {
  const q = ($("#session-filter")?.value || "").trim().toLowerCase();
  const stateF = $("#session-state-filter")?.value || "";
  const filtered = _sessionsCache.filter((s) => {
    if (stateF && s.state !== stateF) return false;
    if (q) {
      const hay = `${s.id} ${s.target_url || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const tbody = $("#session-tbody");
  if (!filtered.length) {
    const all = _sessionsCache.length;
    tbody.innerHTML = `<tr class="muted"><td colspan="8">— ${all > 0 ? "필터 일치 0건 (" + all + "건 중)" : "세션 없음"} —</td></tr>`;
    _updateSessionBulkUi();
    return;
  }
  tbody.innerHTML = filtered.map((s) => `
    <tr>
      <td><input type="checkbox" class="session-row-check" data-sid="${s.id}"></td>
      <td><code>${s.id}</code></td>
      <td><span class="state-pill state-${s.state}">${s.state}</span></td>
      <td class="ellipsis" title="${escapeHtml(s.target_url || "")}">${escapeHtml(s.target_url || "")}</td>
      <td class="muted">${escapeHtml(s.auth_profile || "—")}</td>
      <td>${s.action_count || 0}</td>
      <td class="muted">${formatIso(s.created_at_iso)}</td>
      <td class="row-actions">
        <button data-act="open" data-sid="${s.id}">열기</button>
        <button data-act="del" data-sid="${s.id}" class="danger">삭제</button>
      </td>
    </tr>
  `).join("");
  _updateSessionBulkUi();
}

function _selectedSessionIds() {
  return [...document.querySelectorAll(".session-row-check:checked")]
    .map((el) => el.dataset.sid);
}

function _updateSessionBulkUi() {
  const n = _selectedSessionIds().length;
  const cnt = $("#session-selected-count");
  if (cnt) cnt.textContent = `${n}개 선택`;
  const btn = $("#btn-session-delete-selected");
  if (btn) btn.disabled = (n === 0);
  // header check 상태: 전부 체크면 ON, 일부면 indeterminate, 아니면 OFF
  const head = $("#session-th-check");
  const all = document.querySelectorAll(".session-row-check");
  if (head) {
    if (all.length === 0) {
      head.checked = false; head.indeterminate = false;
    } else if (n === all.length) {
      head.checked = true; head.indeterminate = false;
    } else if (n === 0) {
      head.checked = false; head.indeterminate = false;
    } else {
      head.checked = false; head.indeterminate = true;
    }
  }
}

async function loadSessions() {
  try {
    _sessionsCache = await api("/recording/sessions");
  } catch (e) {
    console.warn("세션 목록 조회 실패:", e);
    return;
  }
  _renderSessionRows();
}

document.addEventListener("DOMContentLoaded", () => {
  const f = $("#session-filter");
  const sf = $("#session-state-filter");
  if (f) {
    f.value = localStorage.getItem(_SESSION_FILTER_KEY) || "";
    f.addEventListener("input", () => {
      localStorage.setItem(_SESSION_FILTER_KEY, f.value);
      _renderSessionRows();
    });
  }
  if (sf) {
    sf.value = localStorage.getItem(_SESSION_STATE_KEY) || "";
    sf.addEventListener("change", () => {
      localStorage.setItem(_SESSION_STATE_KEY, sf.value);
      _renderSessionRows();
    });
  }
});

async function startRecording(target_url, planning_doc_ref, auth_profile) {
  const body = { target_url };
  if (planning_doc_ref) body.planning_doc_ref = planning_doc_ref;
  if (auth_profile) body.auth_profile = auth_profile;
  // raw fetch 로 응답 헤더 (X-Auth-Machine-Mismatch) 확인 가능하게.
  const r = await fetch("/recording/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(data.detail?.reason || data.detail?.message || `HTTP ${r.status}`);
    err.status = r.status;
    err.detail = data.detail || {};
    throw err;
  }
  // 머신 불일치 헤더는 normal 200/201 에서만 의미 있음.
  data._machineMismatch = r.headers.get("X-Auth-Machine-Mismatch") === "1";
  return data;
}

async function stopRecording(sid) {
  return api(`/recording/stop/${sid}`, { method: "POST" });
}

async function deleteSession(sid) {
  return api(`/recording/sessions/${sid}`, { method: "DELETE" });
}

async function getSession(sid) {
  return api(`/recording/sessions/${sid}`);
}

async function getSessionScenario(sid) {
  return api(`/recording/sessions/${sid}/scenario`);
}

async function getSessionOriginal(sid) {
  // text 본문 — fetch 직접 사용 (api() 는 JSON 파싱 시도).
  const r = await fetch(`/recording/sessions/${sid}/original`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.text();
}

async function addAssertion(sid, payload) {
  return api(`/recording/sessions/${sid}/assertion`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// R-Plus — 백엔드는 /experimental/* 로 분리. UI 는 메인 결과 화면에 함께 노출.
// 만료/머신 등 에러 status/detail 을 보존해야 _runPlay 가 409 → 만료 모달 분기를
// 칠 수 있어, api() 가 아니라 raw fetch 로 호출하고 err.status/err.detail 을 박는다.
async function _playRawFetch(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = data.detail || {};
    const msg = typeof detail === "string"
      ? detail
      : (detail.reason || detail.message || `HTTP ${r.status}`);
    const err = new Error(msg);
    err.status = r.status;
    err.detail = (typeof detail === "object" && detail !== null) ? detail : { message: msg };
    throw err;
  }
  return data;
}

// R-Plus Play 의 옵션 패널(auth_profile select + headed checkbox) 값을 body 로 변환.
// auth-profile select 의 sentinel "__default__" 는 body 에서 키를 빼서 서버가
// metadata 의 프로파일을 그대로 쓰게 한다. 빈 문자열은 "인증 없이" 의미.
function _rplusPlayBody() {
  const sel = document.getElementById("rplus-auth-profile");
  const headedEl = document.getElementById("rplus-headed");
  const slowmoEnabled = document.getElementById("rplus-slowmo-enabled");
  const slowmoMs = document.getElementById("rplus-slowmo-ms");
  const body = {};
  if (sel) {
    const v = sel.value;
    if (v !== "__default__") body.auth_profile = v;
  }
  if (headedEl) body.headed = headedEl.checked;
  // 슬로모 — 체크박스가 켜져 있을 때만 값을 실음. 빈/0 이면 키 자체를 빼서
  // 서버가 "꺼짐"으로 판단하게 한다.
  if (slowmoEnabled && slowmoEnabled.checked) {
    const n = Number((slowmoMs && slowmoMs.value) || 0);
    if (Number.isFinite(n) && n > 0) body.slow_mo_ms = n;
  }
  return body;
}

// 슬로모 체크박스 ↔ 숫자 입력 활성화 토글.
(function _wireSlowmoToggle() {
  const cb = document.getElementById("rplus-slowmo-enabled");
  const num = document.getElementById("rplus-slowmo-ms");
  if (!cb || !num) return;
  const sync = () => { num.disabled = !cb.checked; };
  cb.addEventListener("change", sync);
  sync();
})();

async function playCodegen(sid) {
  return _playRawFetch(`/experimental/sessions/${sid}/play-codegen`, _rplusPlayBody());
}

async function playLLM(sid) {
  return _playRawFetch(`/experimental/sessions/${sid}/play-llm`, _rplusPlayBody());
}

async function enrichSession(sid) {
  return api(`/experimental/sessions/${sid}/enrich`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

async function compareSession(sid, doc_dsl, threshold) {
  return api(`/experimental/sessions/${sid}/compare`, {
    method: "POST",
    body: JSON.stringify({ doc_dsl, threshold: Number(threshold) }),
  });
}

// ── 활성 세션 패널 ───────────────────────────────────────────────────────────
function showActivePanel(session) {
  _state.activeSid = session.id;
  _state.startedAt = Date.now();

  $("#active-session").hidden = false;
  $("#active-id").textContent = session.id;
  $("#active-url").textContent = session.target_url;
  setStatePill("#active-state", session.state);

  // 폴링 + 경과 시간
  if (_state.pollTimer) clearInterval(_state.pollTimer);
  _state.pollTimer = setInterval(() => pollActive(session.id), 2000);
  if (_state.elapsedTimer) clearInterval(_state.elapsedTimer);
  _state.elapsedTimer = setInterval(updateElapsed, 1000);
  updateElapsed();
}

function hideActivePanel() {
  $("#active-session").hidden = true;
  _state.activeSid = null;
  _state.startedAt = null;
  if (_state.pollTimer) { clearInterval(_state.pollTimer); _state.pollTimer = null; }
  if (_state.elapsedTimer) { clearInterval(_state.elapsedTimer); _state.elapsedTimer = null; }
}

async function pollActive(sid) {
  try {
    const s = await getSession(sid);
    setStatePill("#active-state", s.state);
    if (["done", "error"].includes(s.state)) {
      hideActivePanel();
      await openSession(sid);
      await loadSessions();
    }
  } catch (e) {
    // 세션이 사라짐 (삭제됨)
    console.warn("폴링 실패:", e);
    hideActivePanel();
  }
}

function updateElapsed() {
  if (_state.startedAt == null) return;
  const sec = Math.round((Date.now() - _state.startedAt) / 1000);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  $("#active-elapsed").textContent = m > 0 ? `${m}분 ${s}초` : `${s}초`;
}

// ── 결과 패널 ────────────────────────────────────────────────────────────────
async function openSession(sid) {
  const s = await getSession(sid);
  $("#result-section").hidden = false;
  $("#result-id").textContent = s.id;
  setStatePill("#result-state", s.state);
  $("#result-step-count").textContent = s.action_count || 0;
  $("#result-path").textContent = `~/.dscore.ttc.playwright-agent/recordings/${s.id}/scenario.json`;

  // Scenario JSON 카드 — state=done 일 때만 노출 + 다운로드 링크 갱신 (TR.4+.2).
  const scenarioCard = $("#scenario-card");
  // scenario 길이 capture — Generate Doc / Play with LLM 버튼 활성 결정에 사용.
  let _scenarioStepCount = 0;
  if (s.state === "done") {
    scenarioCard.hidden = false;
    $("#dl-scenario").href = `/recording/sessions/${sid}/scenario?download=1`;
    try {
      const scenario = await getSessionScenario(sid);
      _scenarioStepCount = Array.isArray(scenario) ? scenario.length : 0;
      $("#result-json").textContent = JSON.stringify(scenario, null, 2);
    } catch (err) {
      $("#result-json").textContent = `(scenario.json 로드 실패: ${err.message})`;
    }
    _refreshPreviewToggle("result-json");
    // 산출물이 있는 카드는 토글 펼침 (R6).
    const det = $("#scenario-details");
    if (det) det.open = true;
  } else {
    scenarioCard.hidden = true;
  }

  // Original .py 카드 — original.py 가 존재할 때 노출 (TR.4+.1).
  // recording 도중 stop 직전 / done / error 모두에서 codegen 산출물이 있으면 표시.
  const originalCard = $("#original-card");
  $("#dl-original").href = `/recording/sessions/${sid}/original?download=1`;
  try {
    const original = await getSessionOriginal(sid);
    originalCard.hidden = false;
    $("#result-original").textContent = original || "(empty)";
    _refreshPreviewToggle("result-original");
    const det = $("#original-details");
    if (det) det.open = true;
  } catch (err) {
    originalCard.hidden = true;
  }

  // P1 — Run-log (실행 결과) 카드. run_log.jsonl 이 있을 때만.
  await _renderRunLog(sid);
  // 항목 4 — Regression .py 별도 카드 + 비교 분석 카드.
  await _renderRegression(sid);
  await _renderHealedScenario(sid);
  await _renderPlayLlmLog(sid);
  await _renderDiff(sid);

  // Assertion 추가 영역은 state=done 일 때만 노출.
  $("#assertion-section").hidden = s.state !== "done";
  $("#assertion-form").dataset.sid = sid;

  // Play & more 섹션은 항상 노출 (토글). 세션이 done 인 경우만 옵션/버튼을 활성화.
  if (s.state === "done") {
    _setRplusActive(sid);
    // 빈 시나리오면 LLM 재생 / Generate Doc 비활성. 사유는 tooltip 으로.
    _setLlmDocAvailability(_scenarioStepCount);
  } else {
    _setRplusInactive();
  }

  // 결과 확인 영역 토글 — 세션이 열렸으니 자동 펼침. 사용자가 직접 접은 의도가
  // 없을 때 기본적으로 보여주는 게 자연스럽다.
  const resultToggle = document.getElementById("result-area-toggle");
  if (resultToggle) resultToggle.open = true;
}

// scenario.json 이 비면 (예: tour 가 아닌 일반 임포트가 변환 실패한 경우)
// LLM-기반 재생/문서화 버튼만 별도로 비활성. 'codegen 원본 실행' 은 그대로 가능.
function _setLlmDocAvailability(scenarioStepCount) {
  const empty = !scenarioStepCount;
  const btnLlm = document.getElementById("btn-play-llm");
  const btnEnrich = document.getElementById("btn-enrich");
  const btnCompare = document.getElementById("btn-compare-open");
  const emptyMsg = (
    "scenario.json 이 비어 있어 사용 불가 — 시나리오로 변환되지 않은 임포트 스크립트 " +
    "(codegen 산출물도 tour 패턴도 아닌 경우). 테스트코드 원본 실행만 가능합니다."
  );
  [btnLlm, btnEnrich, btnCompare].forEach((b) => {
    if (!b) return;
    if (empty) {
      b.dataset.origTitle = b.dataset.origTitle || (b.title || "");
      b.disabled = true;
      b.title = emptyMsg;
    } else if (b.dataset.origTitle !== undefined) {
      // 활성 복원 — _setRplusActive 가 disabled=false 처리하므로 title 만 복원.
      b.title = b.dataset.origTitle;
    }
  });
}

// 세션 종속 영역(옵션·Play·Doc) 활성화 + sid 표시.
function _setRplusActive(sid) {
  const rplus = $("#rplus-section");
  rplus.dataset.sid = sid;
  $("#rplus-sid").textContent = sid;
  const hint = $("#rplus-empty-hint");
  if (hint) hint.hidden = true;
  const area = $("#rplus-session-area");
  if (area) area.setAttribute("aria-disabled", "false");
  $("#rplus-output").hidden = true;
  $("#rplus-output").textContent = "—";
  // 세션 종속 컨트롤들을 모두 활성.
  _rplusToggleSessionControls(true);
  // 토글이 닫혀 있으면 펼쳐서 사용자가 즉시 옵션을 볼 수 있게.
  const dlg = $("#rplus-toggle");
  if (dlg && !dlg.open) dlg.open = true;
}

// 세션 종속 영역 비활성화 — 업로드 진입은 그대로 사용 가능.
function _setRplusInactive() {
  const rplus = $("#rplus-section");
  if (rplus) delete rplus.dataset.sid;
  $("#rplus-sid").textContent = "—";
  const hint = $("#rplus-empty-hint");
  if (hint) hint.hidden = false;
  const area = $("#rplus-session-area");
  if (area) area.setAttribute("aria-disabled", "true");
  _rplusToggleSessionControls(false);
  // 결과 확인 토글도 함께 접음 — 세션이 없으면 보여줄 결과도 없음.
  const resultToggle = document.getElementById("result-area-toggle");
  if (resultToggle) resultToggle.open = false;
}

// 세션 종속 컨트롤 disabled 토글 — 업로드 버튼/파일 input 은 제외.
function _rplusToggleSessionControls(enable) {
  const ids = [
    "rplus-auth-profile", "rplus-headed",
    "rplus-slowmo-enabled", "rplus-slowmo-ms",
    "btn-play-codegen", "btn-play-llm",
    "btn-enrich", "btn-compare-open",
  ];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = !enable;
  });
  // dropdown toggle 버튼들도 비활성.
  document.querySelectorAll("#rplus-section .dropdown-toggle").forEach((b) => {
    b.disabled = !enable;
  });
  // 슬로모 입력은 enable + 체크박스 켜졌을 때만 활성 — 재동기화.
  if (enable) {
    const cb = document.getElementById("rplus-slowmo-enabled");
    const num = document.getElementById("rplus-slowmo-ms");
    if (cb && num) num.disabled = !cb.checked;
  }
}

// ── 폼 핸들러 ────────────────────────────────────────────────────────────────
$("#start-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const target_url = fd.get("target_url").trim();
  const planning = (fd.get("planning_doc_ref") || "").trim();
  if (!target_url) return;
  $("#btn-start").disabled = true;
  try {
    const data = await startRecording(target_url, planning || null);
    showActivePanel(data);
    $("#result-section").hidden = true;
    $("#scenario-card").hidden = true;
    $("#original-card").hidden = true;
    $("#play-llm-log-card").hidden = true;
    $("#assertion-section").hidden = true;
    // Play & more 카드 자체는 항상 노출 (토글) — 세션 종속 영역만 비활성.
    _setRplusInactive();
    await loadSessions();
  } catch (err) {
    alert("Start 실패: " + err.message);
  } finally {
    $("#btn-start").disabled = false;
  }
});

$("#btn-stop").addEventListener("click", async () => {
  if (!_state.activeSid) return;
  $("#btn-stop").disabled = true;
  try {
    const data = await stopRecording(_state.activeSid);
    hideActivePanel();
    if (data.id) await openSession(data.id);
    await loadSessions();
  } catch (err) {
    alert("Stop 실패: " + err.message);
  } finally {
    $("#btn-stop").disabled = false;
  }
});

// action 변경 시 value 입력 보조 — scroll 은 into_view 자동 채움, hover 는 비움.
$("#assertion-form select[name='action']").addEventListener("change", (e) => {
  const action = e.target.value;
  const valueInput = $("#assertion-form input[name='value']");
  if (action === "scroll") {
    valueInput.value = valueInput.value || "into_view";
  } else if (action === "hover" && (valueInput.value === "into_view" || valueInput.value === "")) {
    valueInput.value = "";
  }
});

$("#assertion-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const sid = e.target.dataset.sid;
  if (!sid) { alert("세션 미선택."); return; }
  const fd = new FormData(e.target);
  const payload = {
    action: fd.get("action"),
    target: fd.get("target").trim(),
    value: (fd.get("value") || "").trim(),
    description: (fd.get("description") || "").trim(),
  };
  const cond = (fd.get("condition") || "").trim();
  if (cond) payload.condition = cond;
  // position 미입력 (빈 문자열) ⇒ 백엔드 기본 = 끝에 append.
  // 정수면 1-base step 번호로 전달.
  const posRaw = (fd.get("position") || "").trim();
  if (posRaw) {
    const pos = parseInt(posRaw, 10);
    if (Number.isNaN(pos) || pos < 1) {
      alert("position 은 1 이상의 정수여야 합니다.");
      return;
    }
    payload.position = pos;
  }

  try {
    const data = await addAssertion(sid, payload);
    e.target.reset();
    await openSession(sid);
    alert(`Step ${data.step_added} 추가됨 (총 ${data.step_count} 스텝)`);
  } catch (err) {
    alert("Step 추가 실패: " + err.message);
  }
});

// R-Plus 핸들러 — RPLUS_ENABLED 미설정 시 백엔드가 404 던지므로 알림으로 안내.
function _rplusOutputBox() {
  const box = $("#rplus-output");
  box.hidden = false;
  return box;
}

function _currentRplusSid() {
  return $("#rplus-section").dataset.sid;
}

function _annotateLine(a) {
  if (!a) return "";
  if (a.skipped) return `\nannotate: skipped — ${a.skipped}`;
  if (a.injected === 0) {
    return `\nannotate: examined ${a.examined_clicks} clicks → 0 hover 주입 (원본 그대로 사용)`;
  }
  const triggers = (a.triggers || []).map((t, i) => `  ${i + 1}. ${t}`).join("\n");
  return (
    `\nannotate: examined ${a.examined_clicks} clicks → ${a.injected} hover 주입\n` +
    triggers
  );
}

async function _runPlay(label, btnSel, fn, kind /* "llm" | "codegen" */) {
  const sid = _currentRplusSid();
  if (!sid) return;
  const btn = $(btnSel);
  btn.disabled = true;
  // headless 모드면 브라우저 창이 안 뜨므로 안내 문구를 분기 — 사용자가
  // "왜 창이 안 뜨지" 하고 헷갈리지 않게. 진행 상황은 아래 진행 로그가 유일한
  // 신호가 된다 (1초 주기 폴링).
  const headedEl = document.getElementById("rplus-headed");
  const headed = headedEl ? headedEl.checked : true;
  _rplusOutputBox().textContent = headed
    ? `⏳ ${label} 진행 중... (호스트 화면에 브라우저 창이 뜹니다 — 끝까지 닫지 마세요)`
    : `⏳ ${label} 진행 중... (헤드리스 모드 — 화면에 창이 뜨지 않습니다. 아래 ‘실시간 진행 로그’ 가 진행 상황의 유일한 신호입니다)`;

  // P2 — 실시간 로그 진행 박스 초기화 + 1s 폴링.
  const progress = $("#play-progress");
  const details = $("#play-progress-details");
  progress.textContent = "";
  details.hidden = false;
  details.open = true;
  let offset = 0;
  let stopped = false;
  const pollKind = kind || "llm";
  const tailTimer = setInterval(async () => {
    if (stopped) return;
    try {
      const t = await api(
        `/recording/sessions/${sid}/play-log/tail?kind=${pollKind}&from=${offset}`
      );
      if (t.content) {
        progress.textContent += t.content;
        progress.scrollTop = progress.scrollHeight;
      }
      offset = t.offset;
    } catch (_) {
      // 폴링 실패는 무시 — subprocess 끝날 때까지 재시도.
    }
  }, 1000);

  try {
    const data = await fn(sid);
    const status = data.returncode === 0 ? `✓ ${label} 완료` : `✗ ${label} 실패`;
    _rplusOutputBox().textContent =
      `${status}\n\n` +
      `returncode: ${data.returncode}\n` +
      `elapsed: ${data.elapsed_ms.toFixed(0)}ms` +
      _annotateLine(data.annotate) +
      (data.stdout_tail ? `\n\n--- stdout (tail) ---\n${data.stdout_tail}` : "") +
      (data.stderr_tail ? `\n\n--- stderr (tail) ---\n${data.stderr_tail}` : "");
    // 실행 끝 — Run-log 새로고침 + 방금 실행한 모드 탭으로 자동 전환.
    await _renderRunLog(sid, { mode: kind || "llm" });
    // Play with LLM 실행 후 새로 생성/갱신되는 결과 카드들을 함께 다시 그린다.
    // openSession 에서만 채우면 사용자가 세션을 다시 클릭해야 보이는 문제가 있었음.
    await _renderRegression(sid);
    await _renderHealedScenario(sid);
    await _renderPlayLlmLog(sid);
    await _renderDiff(sid);
    // 결과 영역 토글이 닫혀 있으면 자동 펼침 — 새로 생긴 카드를 사용자가 즉시 볼 수 있게.
    const resultToggle = document.getElementById("result-area-toggle");
    if (resultToggle) resultToggle.open = true;
    // 진행 박스는 자동 collapse — 사용자가 펼쳐 보고 싶으면 수동으로.
    details.open = false;
  } catch (err) {
    // post-review fix — auth-profile 만료/미존재 (rplus router 의 409) 는
    // 일반 실패 메시지가 아니라 만료 모달로 분기. UI 의 [재시드] 버튼이
    // 같은 카탈로그 prefill 흐름을 재사용.
    if (err.status === 409 && err.detail?.reason === "profile_expired") {
      const profName = err.detail.profile_name || _authState.selected || "—";
      const reason = err.detail.fail_reason || err.detail.reason || "verify failed";
      _rplusOutputBox().textContent =
        `⚠ ${label} 중단 — 인증 세션 '${profName}' 만료 (${reason}). 재시드 후 다시 실행하세요.`;
      // 만료된 프로파일 이름을 관리 상태에도 반영 — 재시드 다이얼로그가
      // detail fetch / prefill 을 정상 수행하도록.
      if (profName && profName !== "—") {
        _authState.selected = profName;
      }
      _showExpiredDialog(profName, reason);
    } else {
      _rplusOutputBox().textContent = `✗ ${label} 실패: ` + err.message;
    }
  } finally {
    stopped = true;
    clearInterval(tailTimer);
    btn.disabled = false;
  }
}

$("#btn-play-codegen").addEventListener("click", () =>
  _runPlay("테스트코드 실행", "#btn-play-codegen", playCodegen, "codegen"),
);

$("#btn-play-llm").addEventListener("click", () =>
  _runPlay("LLM 적용 코드 실행", "#btn-play-llm", playLLM, "llm"),
);

$("#btn-enrich").addEventListener("click", async () => {
  const sid = _currentRplusSid();
  if (!sid) return;
  $("#btn-enrich").disabled = true;
  _rplusOutputBox().textContent = "⏳ Ollama 역추정 진행 중... (수십 초~수 분 소요 가능)";
  try {
    const data = await enrichSession(sid);
    _rplusOutputBox().textContent =
      `✓ Generate Doc 완료 (model=${data.model}, ${data.elapsed_ms.toFixed(0)}ms)\n` +
      `저장: ${data.saved_to}\n\n` +
      "─".repeat(40) + "\n\n" +
      data.markdown;
  } catch (err) {
    _rplusOutputBox().textContent = "✗ 역추정 실패: " + err.message;
  } finally {
    $("#btn-enrich").disabled = false;
  }
});

$("#btn-compare-open").addEventListener("click", () => {
  const dlg = $("#compare-dialog");
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
});

$("#compare-form").addEventListener("submit", async (e) => {
  if (e.submitter && e.submitter.value === "cancel") return;
  e.preventDefault();
  const sid = _currentRplusSid();
  if (!sid) return;
  const fd = new FormData(e.target);
  let docDsl;
  try {
    docDsl = JSON.parse(fd.get("doc_dsl"));
    if (!Array.isArray(docDsl)) throw new Error("doc-DSL 은 JSON 배열이어야 합니다.");
  } catch (err) {
    alert("JSON 파싱 실패: " + err.message);
    return;
  }
  const threshold = fd.get("threshold") || 0.7;

  $("#btn-compare-submit").disabled = true;
  try {
    const data = await compareSession(sid, docDsl, threshold);
    const c = data.counts;
    _rplusOutputBox().textContent =
      `✓ Compare 완료\n\n` +
      `정확: ${c.exact} · 값차이: ${c.value_diff} · 누락: ${c.missing} · ` +
      `추가: ${c.extra} · 녹화 외 의도: ${c.intent_only}\n` +
      `리포트 HTML: ${data.report_html_url}\n`;
    window.open(data.report_html_url, "_blank");
    $("#compare-dialog").close();
  } catch (err) {
    alert("Compare 실패: " + err.message);
  } finally {
    $("#btn-compare-submit").disabled = false;
  }
});

// 세션 테이블의 row 액션 (열기 / 삭제)
$("#session-tbody").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const sid = btn.dataset.sid;
  if (btn.dataset.act === "open") {
    await openSession(sid);
  } else if (btn.dataset.act === "del") {
    if (!confirm(`세션 ${sid} 를 삭제할까요? (호스트 디렉토리도 함께 제거)`)) return;
    try {
      await deleteSession(sid);
      $("#result-section").hidden = true;
      $("#assertion-section").hidden = true;
      await loadSessions();
    } catch (err) {
      alert("삭제 실패: " + err.message);
    }
  }
});

// 세션 행 체크박스 — change 이벤트 위임
$("#session-tbody").addEventListener("change", (e) => {
  if (e.target.classList.contains("session-row-check")) {
    _updateSessionBulkUi();
  }
});

// 일괄 선택 / 해제 / 삭제
document.addEventListener("DOMContentLoaded", () => {
  const all = $("#btn-session-select-all");
  const none = $("#btn-session-select-none");
  const head = $("#session-th-check");
  const del = $("#btn-session-delete-selected");

  function _setAll(checked) {
    document.querySelectorAll(".session-row-check").forEach((c) => {
      c.checked = checked;
    });
    _updateSessionBulkUi();
  }
  if (all) all.addEventListener("click", () => _setAll(true));
  if (none) none.addEventListener("click", () => _setAll(false));
  if (head) head.addEventListener("change", () => _setAll(head.checked));

  if (del) del.addEventListener("click", async () => {
    const sids = _selectedSessionIds();
    if (!sids.length) return;
    if (!confirm(`선택한 ${sids.length}개 세션을 삭제할까요?\n(호스트 디렉토리도 함께 제거)`)) return;
    del.disabled = true;
    const failures = [];
    for (const sid of sids) {
      try {
        await deleteSession(sid);
      } catch (err) {
        failures.push(`${sid}: ${err.message || err}`);
      }
    }
    $("#result-section").hidden = true;
    $("#assertion-section").hidden = true;
    await loadSessions();
    if (failures.length) {
      alert(`${sids.length - failures.length}/${sids.length} 삭제 완료. 실패:\n` + failures.join("\n"));
    }
  });
});

// ── util ─────────────────────────────────────────────────────────────────────
function setStatePill(sel, state) {
  const el = $(sel);
  el.textContent = state;
  el.className = `state-pill state-${state}`;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function formatIso(iso) {
  if (!iso) return "—";
  // ISO8601 → HH:MM:SS 만 표시 (오늘) 또는 MM-DD HH:MM
  try {
    const d = new Date(iso);
    const today = new Date();
    if (d.toDateString() === today.toDateString()) {
      return d.toLocaleTimeString();
    }
    return d.toLocaleString();
  } catch (_) { return iso; }
}

// ── P1 (항목 5) — Run-log 시각화 + P4 (항목 8) step JSON 복사 ──────────────
// run-log 카드 상태: 어떤 모드 탭이 현재 선택돼있는지, 두 모드의 데이터 가용성.
const _runLogState = { sid: null, selectedMode: "llm", available: { llm: false, codegen: false } };

function _setRunLogTabUI(mode) {
  document.querySelectorAll("#run-log-mode-tabs .run-log-mode-tab").forEach((btn) => {
    const isActive = btn.dataset.mode === mode;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", isActive ? "true" : "false");
    // 데이터 없는 탭은 disabled (단, 선택된 탭은 항상 표시)
    const has = _runLogState.available[btn.dataset.mode] === true;
    btn.disabled = !has && !isActive;
  });
}

/**
 * Run Log 카드 렌더. mode 미지정 시 가장 최근 호출의 selectedMode (없으면 'auto').
 */
async function _renderRunLog(sid, opts = {}) {
  const card = $("#run-log-card");
  const container = $("#run-log-container");
  _runLogState.sid = sid;
  const requestedMode = opts.mode || _runLogState.selectedMode || "auto";

  // 두 모드의 가용성 동시 체크 — 카드 노출 / 탭 활성 기준.
  const [llmRes, cgRes] = await Promise.all([
    api(`/recording/sessions/${sid}/run-log?mode=llm`).then((r) => r).catch(() => null),
    api(`/recording/sessions/${sid}/run-log?mode=codegen`).then((r) => r).catch(() => null),
  ]);
  _runLogState.available.llm = !!llmRes;
  _runLogState.available.codegen = !!cgRes;

  const det = $("#run-log-details");
  if (!llmRes && !cgRes) {
    card.hidden = true;
    if (det) det.open = false;
    return;
  }
  card.hidden = false;
  // 산출물이 있을 때 카드 토글 펼침 (R6).
  if (det) det.open = true;
  // self-contained 리포트 다운로드 링크 — endpoint 가 둘 중 하나라도 있으면 200.
  const dlReport = $("#dl-report");
  if (dlReport) dlReport.href = `/recording/sessions/${sid}/report`;

  // 실제 표시할 모드 결정 — 요청한 mode 가 가용하면 그것, 아니면 가용한 첫 번째.
  let resolved = requestedMode;
  if (resolved === "auto" || !_runLogState.available[resolved]) {
    resolved = _runLogState.available.llm ? "llm" : "codegen";
  }
  _runLogState.selectedMode = resolved;
  _setRunLogTabUI(resolved);

  const payload = resolved === "llm" ? llmRes : cgRes;
  const records = (payload && Array.isArray(payload.records)) ? payload.records : [];
  if (records.length === 0) {
    container.innerHTML = '<p class="muted">— 빈 run-log —</p>';
    return;
  }

  let firstFailIdx = -1;
  const rows = records.map((rec, i) => {
    const status = (rec.status || "").toUpperCase();
    const heal = rec.heal_stage || "none";
    if (firstFailIdx < 0 && status === "FAIL") firstFailIdx = i;
    const shotCell = rec.screenshot
      ? `<button class="shot-link" data-shot="${escapeHtml(rec.screenshot)}"
                  data-shot-sid="${escapeHtml(sid)}"
                  data-shot-mode="${escapeHtml(resolved)}"
                  data-shot-step="${escapeHtml(String(rec.step))}"
                  title="스크린샷 확대">📷</button>`
      : "—";
    const recJson = JSON.stringify(rec).replace(/'/g, "&#39;");
    const copyCell = `<button class="copy-step-btn" data-step-json='${recJson}' title="이 step JSON 복사">📋</button>`;
    return `
      <tr class="run-log-row run-log-${status.toLowerCase()}" data-step="${escapeHtml(String(rec.step))}">
        <td class="step-no">${escapeHtml(String(rec.step ?? "—"))}</td>
        <td class="step-action"><code>${escapeHtml(rec.action || "—")}</code></td>
        <td class="step-target" title="${escapeHtml(rec.target || "")}">
          <code>${escapeHtml((rec.target || "").slice(0, 60))}${(rec.target || "").length > 60 ? "…" : ""}</code>
        </td>
        <td><span class="state-pill status-${status.toLowerCase()}">${escapeHtml(status || "—")}</span></td>
        <td><span class="heal-pill heal-${escapeHtml(heal)}">${escapeHtml(heal)}</span></td>
        <td>${shotCell}</td>
        <td>${copyCell}</td>
      </tr>`;
  }).join("");
  container.innerHTML = `
    <table class="run-log-table">
      <thead>
        <tr><th>step</th><th>action</th><th>target</th><th>status</th><th>heal_stage</th><th>📷</th><th></th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
  // FAIL step 자동 스크롤.
  if (firstFailIdx >= 0) {
    const failRow = container.querySelector(`tr[data-step="${records[firstFailIdx].step}"]`);
    if (failRow) failRow.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

// 모드 탭 클릭 → 현재 sid 로 다시 렌더.
document.addEventListener("DOMContentLoaded", () => {
  const tabs = $("#run-log-mode-tabs");
  if (!tabs) return;
  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".run-log-mode-tab");
    if (!btn || btn.disabled) return;
    const mode = btn.dataset.mode;
    if (!mode || !_runLogState.sid) return;
    _renderRunLog(_runLogState.sid, { mode });
  });
});

// ── 항목 4 — codegen 원본 ↔ LLM healed regression 비교 ────────────────────
function _renderUnifiedDiff(text) {
  if (!text) return '<span class="muted">(차이 없음)</span>';
  return text.split("\n").map((line) => {
    const safe = escapeHtml(line);
    if (line.startsWith("+++") || line.startsWith("---")) return `<span class="diff-meta">${safe}</span>`;
    if (line.startsWith("@@")) return `<span class="diff-hunk">${safe}</span>`;
    if (line.startsWith("+")) return `<span class="diff-add">${safe}</span>`;
    if (line.startsWith("-")) return `<span class="diff-del">${safe}</span>`;
    return `<span class="diff-ctx">${safe}</span>`;
  }).join("\n");
}

// 매우 작은 markdown → HTML 렌더러 (분석 결과용. 외부 라이브러리 없이).
function _renderMarkdown(md) {
  const lines = md.split("\n");
  const out = [];
  let inCode = false;
  let codeBuf = [];
  for (const raw of lines) {
    if (raw.startsWith("```")) {
      if (inCode) {
        out.push(`<pre class="md-code">${escapeHtml(codeBuf.join("\n"))}</pre>`);
        codeBuf = [];
        inCode = false;
      } else {
        inCode = true;
      }
      continue;
    }
    if (inCode) { codeBuf.push(raw); continue; }
    let line = escapeHtml(raw);
    // inline code
    line = line.replace(/`([^`]+)`/g, '<code>$1</code>');
    // bold
    line = line.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    if (/^### /.test(raw)) out.push(`<h4>${line.replace(/^### /, "")}</h4>`);
    else if (/^## /.test(raw)) out.push(`<h3>${line.replace(/^## /, "")}</h3>`);
    else if (/^# /.test(raw)) out.push(`<h2>${line.replace(/^# /, "")}</h2>`);
    else if (/^\s*[-*] /.test(raw)) out.push(`<li>${line.replace(/^\s*[-*] /, "")}</li>`);
    else if (raw.trim() === "") out.push("<br>");
    else out.push(`<p>${line}</p>`);
  }
  // <li> 들을 <ul> 로 감싸기 (간단 fix-up)
  return out.join("\n").replace(/((?:<li>[^<]*<\/li>\n?)+)/g, "<ul>$1</ul>");
}

async function _renderDiff(sid) {
  const card = $("#diff-card");
  let data;
  try {
    data = await api(`/experimental/sessions/${sid}/diff-codegen-vs-llm`);
  } catch (err) {
    card.hidden = true;
    return;
  }
  if (!data.right_exists) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  $("#diff-output").innerHTML = _renderUnifiedDiff(data.unified_diff);
  // 분석 영역 초기화 (다른 세션 진입 시 이전 결과 흔적 제거)
  $("#diff-analysis-output").innerHTML =
    "— <strong>🔎 LLM 분석</strong> 버튼을 누르면 분석을 시작합니다 —";
}

// LLM 분석 버튼 핸들러
$("#btn-analyze-diff").addEventListener("click", async () => {
  const sid = _currentRplusSid();
  if (!sid) return;
  const out = $("#diff-analysis-output");
  const btn = $("#btn-analyze-diff");
  btn.disabled = true;
  out.innerHTML =
    '<span class="muted">⏳ Ollama 호출 중... 모델 추론에 30~60s 소요됩니다.</span>';
  try {
    const data = await api(
      `/experimental/sessions/${sid}/diff-analysis`, { method: "POST" },
    );
    const meta = `<p class="muted">model: <code>${escapeHtml(data.model)}</code> · ` +
                 `elapsed: ${data.elapsed_ms.toFixed(0)}ms</p>`;
    out.innerHTML = meta + '<div class="md-render">' +
                    _renderMarkdown(data.markdown) + '</div>';
  } catch (err) {
    out.innerHTML =
      '<span class="err">✗ 분석 실패:</span> ' + escapeHtml(err.message);
  } finally {
    btn.disabled = false;
  }
});

// Regression Test Script (.py) — Original Script 카드 안의 sub-block 으로 흡수 (R6).
// 파일 존재 시 sub-block 노출 + 카드 details 자동 펼침.
async function _renderRegression(sid) {
  const block = $("#regression-block");
  let body;
  try {
    const r = await fetch(`/recording/sessions/${sid}/regression`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    body = await r.text();
  } catch (err) {
    block.hidden = true;
    return;
  }
  block.hidden = false;
  $("#dl-regression").href = `/recording/sessions/${sid}/regression?download=1`;
  $("#result-regression").textContent = body || "(empty)";
  _refreshPreviewToggle("result-regression");
  // 산출물이 새로 생겼으니 부모 details 를 펼쳐서 사용자가 즉시 볼 수 있게.
  const det = $("#original-details");
  if (det) det.open = true;
}

// Healed Scenario — Scenario JSON 카드 안의 sub-block 으로 흡수 (R6).
// 원본과 다를 때만 sub-block 노출.
async function _renderHealedScenario(sid) {
  const block = $("#scenario-healed-block");
  let healedText;
  try {
    const r = await fetch(`/recording/sessions/${sid}/scenario_healed`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    healedText = await r.text();
  } catch (_) {
    block.hidden = true;
    return;
  }
  // 원본 scenario.json 과 동일하면 굳이 표시할 가치가 없음 — 셀프힐링이 아무것도 바꾸지 않은 경우.
  let same = false;
  try {
    const origText = await (await fetch(`/recording/sessions/${sid}/scenario`)).text();
    const a = JSON.stringify(JSON.parse(origText));
    const b = JSON.stringify(JSON.parse(healedText));
    same = a === b;
  } catch (_) { /* 비교 실패 시 그냥 노출 */ }
  if (same) {
    block.hidden = true;
    return;
  }
  block.hidden = false;
  $("#dl-scenario-healed").href = `/recording/sessions/${sid}/scenario_healed?download=1`;
  let pretty = healedText;
  try { pretty = JSON.stringify(JSON.parse(healedText), null, 2); } catch (_) { /* 그대로 */ }
  $("#result-json-healed").textContent = pretty;
  _refreshPreviewToggle("result-json-healed");
  const det = $("#scenario-details");
  if (det) det.open = true;
}

// LLM 실행 로그 카드 — play-llm.log 가 존재할 때만 노출 + 토글 자동 펼침.
async function _renderPlayLlmLog(sid) {
  const card = $("#play-llm-log-card");
  const det = $("#play-llm-log-details");
  let body;
  try {
    const r = await fetch(`/recording/sessions/${sid}/play_llm_log`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    body = await r.text();
  } catch (_) {
    card.hidden = true;
    if (det) det.open = false;
    return;
  }
  card.hidden = false;
  $("#dl-play-llm-log").href = `/recording/sessions/${sid}/play_llm_log?download=1`;
  $("#result-play-llm-log").textContent = body || "(empty)";
  _refreshPreviewToggle("result-play-llm-log");
  if (det) det.open = true;
}

// 스크린샷 모달 — 클릭 위임.
// 모달 열기 직전 스크롤 위치를 저장해 두고 닫힐 때 복원한다. 네이티브
// <dialog>.showModal() 가 일부 브라우저에서 스크롤을 reset 하거나, 모달 닫힌 뒤
// 포커스가 trigger 가 아닌 곳으로 빠져 페이지가 최상단으로 점프하는 현상을
// 방지 — 스크린샷을 확인한 그 자리(스텝 행) 로 자연스럽게 돌아오게 한다.
let _shotPrevScrollY = null;
let _shotOpenerBtn = null;
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".shot-link");
  if (!btn) return;
  const sid = btn.dataset.shotSid;
  const name = btn.dataset.shot;
  const step = btn.dataset.shotStep;
  const mode = btn.dataset.shotMode || "llm";
  if (!sid || !name) return;
  const q = mode === "codegen" ? "?mode=codegen" : "";
  $("#shot-img").src = `/recording/sessions/${sid}/screenshot/${encodeURIComponent(name)}${q}`;
  $("#shot-caption").textContent = `Step ${step} — ${name}`;
  _shotPrevScrollY = window.scrollY;
  _shotOpenerBtn = btn;
  const dlg = $("#shot-dialog");
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
});

// 모달 닫힘 — 저장한 스크롤 위치 복원 + opener 버튼에 포커스 (스크롤 점프 방지).
(function _wireShotDialogRestore() {
  const dlg = document.getElementById("shot-dialog");
  if (!dlg) return;
  dlg.addEventListener("close", () => {
    if (_shotPrevScrollY != null) {
      window.scrollTo({ top: _shotPrevScrollY, behavior: "instant" });
    }
    if (_shotOpenerBtn && document.contains(_shotOpenerBtn)) {
      // preventScroll 로 포커스 복원이 추가 스크롤을 일으키지 않게 한다.
      _shotOpenerBtn.focus({ preventScroll: true });
    }
    _shotPrevScrollY = null;
    _shotOpenerBtn = null;
  });
})();

// P4 — Step 단위 JSON 복사. data-step-json 에서 직접 읽음.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".copy-step-btn");
  if (!btn) return;
  const text = btn.dataset.stepJson || "";
  try {
    await _copyToClipboard(text);
    btn.textContent = "✓";
    setTimeout(() => { btn.textContent = "📋"; }, 800);
  } catch (err) {
    alert("Step 복사 실패: " + err.message);
  }
});

// ── R-Plus dropdown (항목 3 — hover-open + click-stick) ───────────────────
function _closeAllDropdowns(except) {
  document.querySelectorAll(".dropdown-group.expanded").forEach((g) => {
    if (g !== except) {
      g.classList.remove("expanded");
      const toggle = g.querySelector(".dropdown-toggle");
      if (toggle) toggle.setAttribute("aria-expanded", "false");
    }
  });
}
document.addEventListener("click", (e) => {
  const toggle = e.target.closest(".dropdown-toggle");
  if (toggle) {
    const group = toggle.closest(".dropdown-group");
    const willExpand = !group.classList.contains("expanded");
    _closeAllDropdowns(group);
    group.classList.toggle("expanded", willExpand);
    toggle.setAttribute("aria-expanded", String(willExpand));
    return;
  }
  // 그룹 외부 클릭 → 모든 dropdown 닫기.
  if (!e.target.closest(".dropdown-group")) {
    _closeAllDropdowns(null);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") _closeAllDropdowns(null);
});

// ── 클립보드 복사 (항목 2) ─────────────────────────────────────────────────
async function _copyToClipboard(text) {
  if (!navigator.clipboard) {
    throw new Error("브라우저가 clipboard API 미지원 — HTTPS/localhost 필요");
  }
  await navigator.clipboard.writeText(text);
}

document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".copy-btn");
  if (!btn) return;
  const targetId = btn.dataset.copyTarget;
  if (!targetId) return;
  const src = document.getElementById(targetId);
  if (!src) return;
  try {
    await _copyToClipboard(src.textContent || "");
    const toast = document.querySelector(`.copy-toast[data-toast-for='${targetId}']`);
    if (toast) {
      toast.hidden = false;
      toast.classList.add("show");
      setTimeout(() => {
        toast.classList.remove("show");
        toast.hidden = true;
      }, 800);
    }
  } catch (err) {
    alert("복사 실패: " + err.message);
  }
});

// 미리보기 토글 — pre.collapsible 의 max-height 제약을 걸고/풀어서 전체 표시 여부 제어.
// textContent 는 항상 전체이므로 복사/다운로드는 토글 상태와 무관하게 정상 동작.
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".preview-toggle");
  if (!btn) return;
  const targetId = btn.dataset.toggleTarget;
  if (!targetId) return;
  const pre = document.getElementById(targetId);
  if (!pre) return;
  const willExpand = !pre.classList.contains("expanded");
  pre.classList.toggle("expanded", willExpand);
  btn.textContent = willExpand ? "▴ 접기" : "▾ 전체 펼치기";
});

// 길이가 임계 이상일 때만 토글 버튼 노출. 짧으면 어차피 전체 보이므로 UI 군더더기.
// pre 의 실제 scrollHeight > clientHeight (즉, max-height 에 의해 잘렸음) 일 때 노출.
function _refreshPreviewToggle(targetId) {
  const pre = document.getElementById(targetId);
  if (!pre) return;
  const btn = document.querySelector(`.preview-toggle[data-toggle-target='${targetId}']`);
  if (!btn) return;
  // 펼친 상태였으면 측정 위해 잠깐 접고 비교 — 그러나 일반적으로 텍스트 갱신 시점에는
  // 닫힘 상태이므로 그대로 측정.
  pre.classList.remove("expanded");
  btn.textContent = "▾ 전체 펼치기";
  const clipped = pre.scrollHeight > pre.clientHeight + 1;
  btn.hidden = !clipped;
}

// ── 항목 (import-script) — 사용자 .py 업로드 + 결과 화면 자동 진입 ──────
$("#btn-import-script").addEventListener("click", () => {
  $("#import-file-input").click();
});

$("#import-file-input").addEventListener("change", async (e) => {
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  // 상단 폼의 로그인 프로파일 셀렉터 값을 그대로 사용 — 사용자가 이미 골라뒀다면
  // 그게 자연스러운 default. 빈 값이면 키를 빼서 서버에서 None 처리.
  const ap = ($("#recording-auth-profile") || {}).value || "";
  if (ap) fd.append("auth_profile", ap);
  const btn = $("#btn-import-script");
  btn.disabled = true;
  btn.textContent = "⏳ 업로드 중...";
  try {
    const r = await fetch("/recording/import-script", {
      method: "POST", body: fd,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      // detail 은 문자열 또는 구조화된 객체({reason, message, fail_reason, ...}).
      // 그대로 더하면 '[object Object]' 가 표시되므로 메시지를 추출.
      const d = data && data.detail;
      let msg;
      if (typeof d === "string") msg = d;
      else if (d && typeof d === "object") {
        msg = d.fail_reason || d.message || d.reason || JSON.stringify(d);
      } else {
        msg = `HTTP ${r.status}`;
      }
      alert("업로드 실패: " + msg);
      return;
    }
    await loadSessions();
    await openSession(data.id);
    // 결과 화면으로 스크롤
    $("#result-section").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    alert("업로드 실패: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "📁 Play Script from File";
    e.target.value = "";  // 같은 파일 재업로드 가능하도록 reset
  }
});

// ── 뒤로가기 버튼 — 항상 노출 (Jenkins 의 Safe HTML 정책이 <a target="_blank">
// 의 target 을 스트립해 새 탭 진입이 불가능 — 동일 탭 진입 사용자의 복귀 경로). ──
(function _initBackButton() {
  const btn = document.getElementById("back-btn");
  if (!btn) return;
  btn.addEventListener("click", () => {
    // 시도 1: referrer (가장 결정적). 다른 origin 에서 왔다면 100% 동작.
    if (document.referrer) {
      window.location.href = document.referrer;
      return;
    }
    // 시도 2: history.back. 이 페이지 진입 전에 history 가 있으면 이동.
    if (window.history.length > 1) {
      window.history.back();
      return;
    }
    // 시도 3: 시작점 — 안내 후 Jenkins 기본 URL 로.
    alert("이전 페이지가 없습니다 — Jenkins 메인으로 이동합니다.");
    window.location.href = "http://localhost:18080/";
  });
})();

// ─────────────────────────────────────────────────────────────────────────
// Auth Profile UI (P5.2 ~ P5.9)
// ─────────────────────────────────────────────────────────────────────────
// 설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §3 + §4
//
// 흐름:
//   1) 페이지 로드 → GET /auth/profiles → 드롭다운 채움 (P5.2)
//   2) 드롭다운 선택 → 라벨 갱신 + verify 버튼 활성화
//   3) ↻ verify → POST /auth/profiles/{name}/verify (P5.3)
//   4) + 새 세션 시드 → 시드 입력 모달 → POST /auth/profiles/seed (P5.4)
//   5) 시드 진행 모달 → 1초 폴링 → ready/error (P5.5)
//   6) Start Recording 시 409 (만료) → 만료 모달 → 재시드 prefill (P5.6)
//   7) 응답 헤더 X-Auth-Machine-Mismatch=1 → 머신 불일치 모달 (P5.7)
//   8) sessionStorage 경고 (P5.9) — list 응답의 session_storage_warning 표시

async function fetchAuthProfiles() {
  return api("/auth/profiles");
}

async function fetchAuthProfileDetail(name) {
  // P2.1 — 만료 모달의 [재시드] 가 verify_service_url / verify_service_text 까지
  // prefill 할 수 있도록 단일 프로파일 detail 조회.
  return api(`/auth/profiles/${encodeURIComponent(name)}`);
}

async function verifyAuthProfile(name) {
  return api(`/auth/profiles/${encodeURIComponent(name)}/verify`, { method: "POST" });
}

async function deleteAuthProfile(name) {
  return api(`/auth/profiles/${encodeURIComponent(name)}`, { method: "DELETE" });
}

async function seedAuthProfileStart(payload) {
  return api("/auth/profiles/seed", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

async function seedAuthProfilePoll(seedSid) {
  return api(`/auth/profiles/seed/${encodeURIComponent(seedSid)}`);
}

const _authState = {
  profiles: [],
  selected: "",
  lastVerify: null,        // {ok, service_ms, ...}
  pollTimer: null,
  reseedPrefill: null,     // 재시드 시 입력 폼 prefill 용
};

function _formatRelative(iso) {
  if (!iso) return "—";
  try {
    const dt = new Date(iso);
    const sec = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 1000));
    if (sec < 60) return `${sec}초 전`;
    if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}시간 전`;
    return `${Math.floor(sec / 86400)}일 전`;
  } catch {
    return iso;
  }
}

async function loadAuthProfiles() {
  let profiles;
  try {
    profiles = await fetchAuthProfiles();
  } catch (e) {
    console.warn("auth profiles 조회 실패:", e);
    return;
  }
  _authState.profiles = profiles;
  const optionsHtml = `<option value="">(없음 — 비로그인 녹화)</option>` +
    profiles.map((p) => {
      const warn = p.session_storage_warning ? " ⚠sessionStorage" : "";
      return `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)} — ${escapeHtml(p.service_domain)}${warn}</option>`;
    }).join("");

  const sel = $("#auth-profile-select");
  const current = _authState.selected;
  sel.innerHTML = optionsHtml;
  if (current && profiles.some((p) => p.name === current)) {
    sel.value = current;
    _renderAuthStatus();
  } else {
    _authState.selected = "";
    _renderAuthStatus();
  }

  // New Recording 의 단순 dropdown 도 같은 옵션으로 채운다 (선택값 보존).
  const recSel = $("#recording-auth-profile");
  if (recSel) {
    const prevRec = recSel.value;
    recSel.innerHTML = optionsHtml;
    if (prevRec && profiles.some((p) => p.name === prevRec)) {
      recSel.value = prevRec;
    }
  }

  // R-Plus Play 영역의 셀렉터 — 추가 sentinel "(세션 기본값 사용)" 을 맨 위에.
  // 기본값은 항상 sentinel — 사용자가 직접 다른 값으로 바꿀 때만 override 된다.
  const rplusSel = $("#rplus-auth-profile");
  if (rplusSel) {
    const prev = rplusSel.value || "__default__";
    rplusSel.innerHTML =
      `<option value="__default__">(세션 기본값 사용)</option>` +
      `<option value="">(인증 없이 실행)</option>` +
      profiles.map((p) => {
        const warn = p.session_storage_warning ? " ⚠sessionStorage" : "";
        return `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)} — ${escapeHtml(p.service_domain)}${warn}</option>`;
      }).join("");
    if (prev === "" || prev === "__default__" || profiles.some((p) => p.name === prev)) {
      rplusSel.value = prev;
    } else {
      rplusSel.value = "__default__";
    }
  }
}

function _selectedProfile() {
  return _authState.profiles.find((p) => p.name === _authState.selected) || null;
}

function _renderAuthStatus() {
  const p = _selectedProfile();
  const status = $("#auth-status");
  const verifyBtn = $("#btn-auth-verify");
  const deleteBtn = $("#btn-auth-delete");
  if (!p) {
    status.textContent = "— 프로파일을 선택하거나 새로 시드하세요 —";
    status.className = "auth-status muted";
    verifyBtn.disabled = true;
    if (deleteBtn) deleteBtn.disabled = true;
    return;
  }
  verifyBtn.disabled = false;
  if (deleteBtn) deleteBtn.disabled = false;
  let txt = `프로파일 "${p.name}" — ${p.service_domain}`;
  if (p.last_verified_at) {
    txt += ` · ${_formatRelative(p.last_verified_at)} 검증`;
  } else {
    txt += " · 미검증";
  }
  // P5.9 — sessionStorage 의심 서비스 경고.
  if (p.session_storage_warning) {
    txt += " · ⚠ sessionStorage 의존 가능 (재시드 빈도↑)";
  }
  if (_authState.lastVerify && _authState.lastVerify.profile === p.name) {
    if (_authState.lastVerify.ok) {
      txt = "✓ verify OK · " + txt;
      status.className = "auth-status ok";
    } else {
      txt = "✗ verify FAIL · " + txt;
      status.className = "auth-status err";
    }
  } else {
    status.className = "auth-status muted";
  }
  status.textContent = txt;
}

// 드롭다운 선택 변경.
$("#auth-profile-select").addEventListener("change", (e) => {
  _authState.selected = e.target.value;
  _authState.lastVerify = null;
  _renderAuthStatus();
});

// 🗑 삭제 — 카탈로그 + storageState 파일 제거.
$("#btn-auth-delete").addEventListener("click", async () => {
  const p = _selectedProfile();
  if (!p) return;
  const ok = window.confirm(
    `프로파일 "${p.name}" (${p.service_domain}) 을 삭제하시겠습니까?\n` +
    `카탈로그 항목과 저장된 storageState 파일이 함께 제거됩니다.`,
  );
  if (!ok) return;
  const btn = $("#btn-auth-delete");
  btn.disabled = true;
  $("#auth-status").textContent = `"${p.name}" 삭제 중...`;
  $("#auth-status").className = "auth-status muted";
  try {
    await deleteAuthProfile(p.name);
    _authState.selected = "";
    _authState.lastVerify = null;
    await loadAuthProfiles();
    $("#auth-status").textContent = `✓ "${p.name}" 삭제됨`;
    $("#auth-status").className = "auth-status ok";
  } catch (err) {
    $("#auth-status").textContent = `✗ 삭제 실패: ${err.message || err}`;
    $("#auth-status").className = "auth-status err";
    btn.disabled = false;
  }
});

// ↻ verify (P5.3).
$("#btn-auth-verify").addEventListener("click", async () => {
  const p = _selectedProfile();
  if (!p) return;
  const btn = $("#btn-auth-verify");
  btn.disabled = true;
  $("#auth-status").textContent = "verify 진행 중...";
  $("#auth-status").className = "auth-status muted";
  try {
    const result = await verifyAuthProfile(p.name);
    _authState.lastVerify = { profile: p.name, ...result };
    // 카탈로그 last_verified_at 갱신 — 다시 fetch.
    await loadAuthProfiles();
  } catch (err) {
    _authState.lastVerify = { profile: p.name, ok: false, fail_reason: err.message };
    _renderAuthStatus();
  } finally {
    btn.disabled = false;
  }
});

// ── 시드 입력 모달 (P5.4) ────────────────────────────────────────────────
function _openSeedDialog(prefill) {
  const dlg = $("#auth-seed-dialog");
  const form = $("#auth-seed-form");
  form.reset();
  if (prefill) {
    if (prefill.name) form.elements["name"].value = prefill.name;
    if (prefill.seed_url) form.elements["seed_url"].value = prefill.seed_url;
    if (prefill.verify_service_url) form.elements["verify_service_url"].value = prefill.verify_service_url;
    if (prefill.verify_service_text) form.elements["verify_service_text"].value = prefill.verify_service_text;
    if (prefill.ttl_hint_hours) form.elements["ttl_hint_hours"].value = prefill.ttl_hint_hours;
    if (prefill.naver_probe !== undefined) form.elements["naver_probe"].checked = !!prefill.naver_probe;
  }
  dlg.showModal();
}

$("#btn-auth-seed").addEventListener("click", () => _openSeedDialog());

// 취소 버튼은 type="button" — form 의 required 필드가 비어있어도 dialog 를 즉시 닫는다.
// (type="submit" 은 HTML5 validation 이 submit 자체를 차단해 우리 핸들러 + form
// method="dialog" close 까지 모두 건너뛴다.)
$("#btn-auth-seed-cancel-input").addEventListener("click", () => {
  $("#auth-seed-dialog").close();
});

$("#auth-seed-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  const payload = {
    name: fd.get("name").trim(),
    seed_url: fd.get("seed_url").trim(),
    verify_service_url: fd.get("verify_service_url").trim(),
    verify_service_text: fd.get("verify_service_text").trim(),
    naver_probe: fd.get("naver_probe") === "on",
    ttl_hint_hours: parseInt(fd.get("ttl_hint_hours") || "12", 10),
  };
  // 입력 모달 닫고 진행 모달 오픈 + 시드 시작.
  $("#auth-seed-dialog").close();
  _authState.reseedPrefill = payload;  // 만료 → 재시드 시 동일 입력 prefill.
  _startSeedFlow(payload);
});

// ── 시드 진행 폴링 (P5.5) ────────────────────────────────────────────────
async function _startSeedFlow(payload) {
  const dlg = $("#auth-seed-progress");
  const status = $("#auth-seed-progress-status");
  const elapsed = $("#auth-seed-progress-elapsed");
  const hint = $("#auth-seed-progress-hint");
  const cancelBtn = $("#btn-auth-seed-cancel");
  const skipBtn = $("#btn-auth-seed-skip");
  const doneBtn = $("#btn-auth-seed-done");
  const inputDlg = $("#auth-seed-dialog");
  if (inputDlg && inputDlg.open) inputDlg.close();
  status.textContent = "⏳ 로그인 창 대기 중 — 로그인 완료 화면 확인 후 열린 브라우저 창을 닫으세요";
  elapsed.textContent = `경과 0초 / 한도 ${payload.timeout_sec || 600}초`;
  hint.textContent = "창이 닫히면 세션을 저장하고 검증 대상 페이지를 잠시 보여준 뒤 완료됩니다.";
  cancelBtn.hidden = false;
  skipBtn.hidden = true;
  doneBtn.hidden = true;
  cancelBtn.textContent = "취소 (창은 직접 닫으세요)";
  cancelBtn.dataset.action = "cancel";
  skipBtn.dataset.profile = "";
  doneBtn.dataset.profile = "";
  dlg.showModal();

  let resp;
  try {
    resp = await seedAuthProfileStart(payload);
  } catch (err) {
    status.textContent = `✗ 시작 실패: ${err.message}`;
    return;
  }
  const seedSid = resp.seed_sid;
  if (_authState.pollTimer) clearInterval(_authState.pollTimer);
  _authState.pollTimer = setInterval(async () => {
    let poll;
    try {
      poll = await seedAuthProfilePoll(seedSid);
    } catch (err) {
      console.warn("seed poll 실패:", err);
      return;
    }
    elapsed.textContent =
      `경과 ${Math.floor(poll.elapsed_sec)}초 / 한도 ${poll.timeout_sec}초`;
    if (poll.message) status.textContent = poll.message;
    if (poll.phase === "verifying") {
      hint.textContent = "검증 브라우저가 대상 페이지를 천천히 표시한 뒤 자동 종료됩니다.";
    }
    if (poll.state === "ready") {
      clearInterval(_authState.pollTimer);
      status.textContent = poll.message || `✓ 시드 완료 — 프로파일 "${poll.profile_name}"`;
      hint.textContent = "이번 녹화에 사용할지 선택하세요. 사용하지 않아도 프로파일은 목록에 저장됩니다.";
      await loadAuthProfiles();
      cancelBtn.hidden = true;
      skipBtn.hidden = false;
      doneBtn.hidden = false;
      skipBtn.dataset.profile = poll.profile_name || "";
      doneBtn.dataset.profile = poll.profile_name || "";
    } else if (poll.state === "error") {
      clearInterval(_authState.pollTimer);
      const kind = poll.error_kind ? `[${poll.error_kind}] ` : "";
      status.textContent = poll.message || `✗ 실패 — ${kind}${poll.error}`;
      hint.textContent = "입력값을 확인한 뒤 다시 시드하세요.";
      cancelBtn.textContent = "다시 입력";
      cancelBtn.dataset.action = "retry";
    }
  }, 1000);
}

$("#btn-auth-seed-cancel").addEventListener("click", () => {
  if (_authState.pollTimer) clearInterval(_authState.pollTimer);
  $("#auth-seed-progress").close();
  if ($("#btn-auth-seed-cancel").dataset.action === "retry") {
    _openSeedDialog(_authState.reseedPrefill);
  }
});

$("#btn-auth-seed-skip").addEventListener("click", () => {
  _authState.selected = "";
  $("#auth-profile-select").value = "";
  _renderAuthStatus();
  $("#auth-seed-progress").close();
});

$("#btn-auth-seed-done").addEventListener("click", () => {
  const profile = $("#btn-auth-seed-done").dataset.profile || "";
  if (profile) {
    _authState.selected = profile;
    $("#auth-profile-select").value = profile;
    // 새로 시드한 프로파일을 New Recording 에서도 즉시 사용할 수 있게 미리 선택.
    const recSel = $("#recording-auth-profile");
    if (recSel && [...recSel.options].some((o) => o.value === profile)) {
      recSel.value = profile;
    }
    _renderAuthStatus();
  }
  $("#auth-seed-progress").close();
});

// ── 만료 모달 (P5.6) ─────────────────────────────────────────────────────
function _showExpiredDialog(name, reason) {
  $("#auth-expired-name").textContent = name || "—";
  $("#auth-expired-reason").textContent = reason
    ? `원인: ${reason}`
    : "원인: 세션 만료 또는 IP 변경.";
  $("#auth-expired-dialog").showModal();
}

$("#btn-auth-expired-cancel").addEventListener("click", () => {
  $("#auth-expired-dialog").close();
});

$("#btn-auth-expired-reseed").addEventListener("click", async () => {
  $("#auth-expired-dialog").close();
  // P2.1 — prefill 우선순위:
  //   (1) 방금 brower 세션에서 직접 seed 한 입력값 (_authState.reseedPrefill)
  //   (2) 카탈로그 detail fetch — 어제 시드한 / 다른 머신에서 가져온 프로파일
  //   (3) name 만 (최후의 fallback — detail 도 없는 경우)
  if (_authState.reseedPrefill) {
    _openSeedDialog(_authState.reseedPrefill);
    return;
  }
  const name = _authState.selected;
  if (!name) {
    _openSeedDialog({});
    return;
  }
  let detail = null;
  try {
    detail = await fetchAuthProfileDetail(name);
  } catch (e) {
    console.warn("auth profile detail 조회 실패:", e);
  }
  if (detail) {
    // seed_url 은 카탈로그에 저장되지 않아 verify_service_url 의 origin 으로 추정.
    // 사용자가 모달에서 직접 수정 가능.
    let seedUrlGuess = "";
    try {
      seedUrlGuess = new URL(detail.verify_service_url).origin + "/";
    } catch (_) {
      seedUrlGuess = detail.verify_service_url;
    }
    _openSeedDialog({
      name: detail.name,
      seed_url: seedUrlGuess,
      verify_service_url: detail.verify_service_url,
      verify_service_text: detail.verify_service_text,
      ttl_hint_hours: detail.ttl_hint_hours,
      naver_probe: detail.naver_probe_enabled,
    });
  } else {
    _openSeedDialog({ name });
  }
});

// ── 머신 불일치 모달 (P5.7) ──────────────────────────────────────────────
let _mmRetryFn = null;

function _showMachineMismatchDialog(retryFn) {
  _mmRetryFn = retryFn;
  $("#auth-machine-mismatch-dialog").showModal();
}

$("#btn-mm-cancel").addEventListener("click", () => {
  _mmRetryFn = null;
  $("#auth-machine-mismatch-dialog").close();
});

$("#btn-mm-proceed").addEventListener("click", () => {
  $("#auth-machine-mismatch-dialog").close();
  // 이미 200 으로 응답된 시점이라 재시도 필요 없음 — 사용자 자각만.
});

$("#btn-mm-reseed").addEventListener("click", () => {
  $("#auth-machine-mismatch-dialog").close();
  _openSeedDialog({ name: _authState.selected });
});

// ── Start Recording 결과 — 에러 분기 (P5.6/P5.7) ─────────────────────────
// 기존 #start-form submit 핸들러를 확장. auth_profile 인자 + 응답 분기.
const _origSubmitListenerNeedsRebind = false; // 가드 — 기존 핸들러 그대로 두고 보강만.

// 기존 handler 가 이미 등록됐으므로 capture 로 한 번 더 잡아 처리.
$("#start-form").addEventListener("submit", async (e) => {
  // 기본 핸들러보다 먼저 — capture 단계에서 처리.
  // 단, 기본 핸들러가 startRecording 호출하므로 여기선 *가로채서* auth_profile 만 추가.
}, true);

// 기존 handler 를 대체 (auth_profile 통합 + 에러 분기).
const _legacyStartFormSubmit = (() => {
  // 기존 listener 는 그대로 유지하되, 새 listener 가 capture 로 먼저 가로채 e.preventDefault + 직접 처리.
  // capture listener 안에서 startRecording 호출.
  return null;
})();

// 깔끔한 처리를 위해 form 의 handler 를 *제거하지 않고* 새 capture handler 가 모두 책임.
// 단, 첫 submit 시 기본 handler 도 실행돼 중복될 수 있어 초기 등록 시점에 stopImmediatePropagation.
$("#start-form").addEventListener("submit", async (e) => {
  if (e._authProfileHandled) return;
  e._authProfileHandled = true;
  e.preventDefault();
  e.stopImmediatePropagation();

  const fd = new FormData(e.target);
  const target_url = (fd.get("target_url") || "").trim();
  const planning = (fd.get("planning_doc_ref") || "").trim();
  const authProfile = (fd.get("auth_profile") || "").trim();
  if (!target_url) return;

  $("#btn-start").disabled = true;
  try {
    const data = await startRecording(target_url, planning || null, authProfile || null);
    showActivePanel(data);
    $("#result-section").hidden = true;
    $("#scenario-card").hidden = true;
    $("#original-card").hidden = true;
    $("#play-llm-log-card").hidden = true;
    $("#assertion-section").hidden = true;
    // Play & more 카드 자체는 항상 노출 (토글) — 세션 종속 영역만 비활성.
    _setRplusInactive();
    await loadSessions();
    if (data._machineMismatch) {
      _showMachineMismatchDialog(null);
    }
  } catch (err) {
    if (err.status === 409 && err.detail?.reason === "profile_expired") {
      const reason = err.detail.fail_reason || err.detail.reason;
      // 녹화 selector 에서 고른 프로파일이 만료됐을 때, 관리 상태에도 반영해
      // 재시드 다이얼로그가 detail fetch / prefill 을 정상 수행하도록 한다.
      if (authProfile) {
        _authState.selected = authProfile;
      }
      _showExpiredDialog(authProfile, reason);
    } else if (err.status === 404 && err.detail?.reason === "profile_not_found") {
      alert(`인증 프로파일 '${authProfile}' 를 찾을 수 없습니다 — 새로 시드하세요.`);
    } else {
      alert("Start 실패: " + err.message);
    }
  } finally {
    $("#btn-start").disabled = false;
  }
}, true);  // capture 로 먼저 처리

// 결과 카드의 인증 프로파일 필드 노출 — openSession 안에서 갱신.
async function _renderResultAuthProfile(sid) {
  try {
    const sess = await getSession(sid);
    const el = $("#result-auth-profile");
    if (el) {
      el.textContent = sess.auth_profile || "—";
    }
  } catch {
    // 무시 — 결과 카드 보조 정보일 뿐.
  }
}

// openSession 보강 — 결과 카드 노출 직후 인증 메타 갱신.
const _origOpenSession = window.openSession;
if (typeof _origOpenSession === "function") {
  window.openSession = async function patchedOpenSession(sid) {
    await _origOpenSession(sid);
    _renderResultAuthProfile(sid);
  };
}

// ── Discover URLs (URL 자동 수집) ───────────────────────────────────────────
//
// 보안: row.title / row.url 은 임의 사이트의 임의 문자열이므로 DOM 삽입 시
// 반드시 textContent 사용. innerHTML 금지. URL 컬럼의 클릭 가능한 링크는
// href 만 setAttribute 로 설정하되, javascript:/data: 스킴은 클라이언트에서도
// 한 번 더 거른다 (서버는 exclude_patterns 로 거름).

const _discover = {
  pollTimer: null,
  lastJobId: null,
  authTouched: false,  // 사용자가 discover-auth-profile 을 직접 바꾼 적 있는가
};

function _populateDiscoverAuthSelect(profiles) {
  const sel = $("#discover-auth-profile");
  if (!sel) return;
  const prev = sel.value;
  const opts = [`<option value="">(없음)</option>`].concat(
    (profiles || []).map((p) => {
      const warn = p.session_storage_warning ? " ⚠sessionStorage" : "";
      return `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)} — ${escapeHtml(p.service_domain)}${warn}</option>`;
    }),
  );
  sel.innerHTML = opts.join("");
  if (prev && (profiles || []).some((p) => p.name === prev)) {
    sel.value = prev;
  } else if (!_discover.authTouched) {
    // recording selector 와 동기화 (사용자가 손대기 전까지만)
    const main = $("#recording-auth-profile");
    if (main && (profiles || []).some((p) => p.name === main.value)) {
      sel.value = main.value;
    }
  }
}

function _setDiscoverStatus(text) {
  const el = $("#discover-status");
  if (el) el.textContent = text;
}

// 서버 에러 메시지(문자열)에 JSON 이 박혀 있으면 줄바꿈된 key: value 형태로 풀어준다.
// 예: '시작 실패: {"reason":"profile_expired","fail_reason":"..."}' →
//     '시작 실패:\n  reason: profile_expired\n  fail_reason: ...'
function _prettifyErrorMessage(msg) {
  if (msg == null) return "";
  const s = String(msg);
  const i = s.indexOf("{");
  if (i < 0) return s;
  const head = s.slice(0, i);
  const tail = s.slice(i);
  let obj;
  try { obj = JSON.parse(tail); } catch (_) { return s; }
  if (!obj || typeof obj !== "object") return s;
  const lines = Object.entries(obj).map(([k, v]) => {
    const vs = (v === null || v === undefined) ? "—" : (typeof v === "object" ? JSON.stringify(v) : String(v));
    return `  ${k}: ${vs}`;
  });
  return `${head.trimEnd()}\n${lines.join("\n")}`;
}

function _isHttpUrl(u) {
  try {
    const p = new URL(u);
    return p.protocol === "http:" || p.protocol === "https:";
  } catch (_) {
    return false;
  }
}

function _statePillHtml(status) {
  // status: number | null
  if (status == null) return `<span class="state-pill state-warn">—</span>`;
  if (status >= 400) return `<span class="state-pill state-err">${status}</span>`;
  return `<span class="state-pill state-ok">${status}</span>`;
}

// Discover 사이트 스캔 통계 카드 렌더 (R9) — cap 도달 / sitemap 커버리지 / 분포 mini-grid.
function _renderDiscoverStats(stats) {
  const card = document.getElementById("discover-stats");
  if (!card) return;
  if (!stats) {
    card.hidden = true;
    card.replaceChildren();
    return;
  }
  const dist = stats.distribution || {};
  const lines = [];
  // 핵심 라인 — 발견 N개 + cap 도달 시 노란 배지.
  const total = (dist.by_status && Object.values(dist.by_status).reduce((a, b) => a + b, 0)) || 0;
  const capWarn = stats.cap_reached
    ? ` <span class="stats-cap-warn">⚠ ${_escapeHtml(String(stats.abort_reason || "max_pages"))} 도달 — 잘렸을 수 있음</span>`
    : "";
  lines.push(`<div class="stats-row"><strong>발견 ${total}건</strong>${capWarn}</div>`);
  // sitemap 라인 — sitemap_total 있을 때만.
  if (stats.sitemap_total != null) {
    const total_st = Number(stats.sitemap_total);
    const pct = total_st > 0 ? Math.round((total / total_st) * 100) : 0;
    lines.push(
      `<div class="stats-row"><span class="stats-label">sitemap</span>` +
      `명시 ${total_st.toLocaleString()} → 발견 ${total.toLocaleString()} (${pct}%)</div>`
    );
  }
  // 분포 mini-grid.
  const buckets = (obj, order) => {
    if (!obj) return "";
    const keys = order ? order.filter((k) => k in obj) : Object.keys(obj).sort();
    return keys.map((k) =>
      `<span class="stats-bucket">${_escapeHtml(String(k))} <strong>${obj[k]}</strong></span>`
    ).join(" · ");
  };
  const depthHtml = buckets(dist.by_depth, Object.keys(dist.by_depth || {}).map(Number).sort((a, b) => a - b).map(String));
  const sourceHtml = buckets(dist.by_source);
  const statusHtml = buckets(dist.by_status, ["2xx", "3xx", "4xx", "5xx", "none"]);
  if (depthHtml || sourceHtml || statusHtml) {
    lines.push(
      '<div class="stats-mini-grid">' +
      (depthHtml ? `<div><span class="stats-label">depth</span>${depthHtml}</div>` : "") +
      (sourceHtml ? `<div><span class="stats-label">source</span>${sourceHtml}</div>` : "") +
      (statusHtml ? `<div><span class="stats-label">status</span>${statusHtml}</div>` : "") +
      '</div>'
    );
  }
  card.innerHTML = lines.join("");
  card.hidden = false;
}

// Discover 결과를 사이트 계층 트리로 렌더 — 두 탭 (크롤 / 경로) 동시 fetch.
// 결과 토글은 hidden=false 로 노출, 다운로드 anchor 도 wire.
async function _renderDiscoverTrees(jobId) {
  const toggle = document.getElementById("discover-tree-toggle");
  const dl = document.getElementById("dl-tree-html");
  const paneCrawl = document.getElementById("tree-pane-crawl");
  const panePath = document.getElementById("tree-pane-path");
  if (!toggle || !paneCrawl || !panePath) return;
  if (dl) dl.href = `/discover/${jobId}/tree.html`;
  toggle.hidden = false;
  paneCrawl.textContent = "— 로드 중 —";
  panePath.textContent = "— 로드 중 —";
  let crawl = null;
  let path = null;
  try { crawl = await api(`/discover/${jobId}/tree?type=crawl`); } catch (_) { /* 무시 */ }
  try { path  = await api(`/discover/${jobId}/tree?type=path`); } catch (_) { /* 무시 */ }
  paneCrawl.replaceChildren();
  panePath.replaceChildren();
  paneCrawl.appendChild(_renderTreeRoot(crawl, "crawl"));
  panePath.appendChild(_renderTreeRoot(path, "path"));
}

function _renderTreeRoot(tree, mode) {
  const wrap = document.createElement("div");
  if (!tree || !tree.root) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "— 트리 없음 —";
    wrap.appendChild(p);
    return wrap;
  }
  const ul = document.createElement("ul");
  ul.className = "tree";
  ul.appendChild(_renderTreeNode(tree.root));
  wrap.appendChild(ul);
  const extras = (mode === "crawl" ? tree.orphans : tree.external) || [];
  if (extras.length > 0) {
    const h = document.createElement("h4");
    h.className = "tree-extras-title muted";
    h.textContent = (mode === "crawl" ? "Orphans (sitemap 등)" : "외부 host")
                    + ` (${extras.length})`;
    wrap.appendChild(h);
    const eul = document.createElement("ul");
    eul.className = "tree";
    extras.forEach((n) => eul.appendChild(_renderTreeNode(n)));
    wrap.appendChild(eul);
  }
  return wrap;
}

function _renderTreeNode(node) {
  const li = document.createElement("li");
  const children = Array.isArray(node.children) ? node.children : [];
  const labelHtml = _treeNodeLabel(node);
  if (children.length === 0) {
    li.innerHTML = labelHtml;
    return li;
  }
  const det = document.createElement("details");
  if ((node.depth || 0) <= 1) det.open = true;
  const summary = document.createElement("summary");
  summary.innerHTML = labelHtml + ` <span class="tree-count muted">(${_treeCount(node)})</span>`;
  det.appendChild(summary);
  const ul = document.createElement("ul");
  ul.className = "tree";
  children.forEach((c) => ul.appendChild(_renderTreeNode(c)));
  det.appendChild(ul);
  li.appendChild(det);
  return li;
}

function _treeNodeLabel(node) {
  const url = node.url || "";
  const safeUrl = _escapeHtml(url);
  const title = node.title ? ` — ${_escapeHtml(node.title)}` : "";
  const status = (node.status === null || node.status === undefined) ? "" :
    ` <span class="tree-badge st-${_treeStatusClass(node.status)}">${_escapeHtml(String(node.status))}</span>`;
  const src = (node.source && node.source !== "(path)") ?
    ` <span class="tree-badge src">${_escapeHtml(node.source)}</span>` : "";
  return `<a href="${safeUrl}" target="_blank" rel="noopener">${safeUrl}</a>${title}${status}${src}`;
}

function _treeCount(node) {
  const c = Array.isArray(node.children) ? node.children : [];
  let n = c.length;
  for (const ch of c) n += _treeCount(ch);
  return n;
}

function _treeStatusClass(s) {
  const n = Number(s);
  if (!Number.isFinite(n)) return "unknown";
  if (n >= 200 && n < 300) return "ok";
  if (n >= 300 && n < 400) return "warn";
  return "fail";
}

function _escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// 트리 탭 전환 — 클릭 시 active 토글 + pane hidden 전환.
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".tree-tab");
  if (!btn) return;
  const which = btn.dataset.tree;
  document.querySelectorAll(".tree-tab").forEach((b) => {
    b.classList.toggle("active", b === btn);
    b.setAttribute("aria-selected", b === btn ? "true" : "false");
  });
  const paneCrawl = document.getElementById("tree-pane-crawl");
  const panePath = document.getElementById("tree-pane-path");
  if (paneCrawl) paneCrawl.hidden = which !== "crawl";
  if (panePath)  panePath.hidden  = which !== "path";
});

function _renderDiscoverTable(rootEl, list) {
  rootEl.replaceChildren();
  if (!Array.isArray(list) || list.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "— 결과 없음 —";
    rootEl.appendChild(p);
    return;
  }

  const wrap = document.createElement("div");
  wrap.className = "discover-table-wrap";
  const table = document.createElement("table");
  table.className = "discover-table";

  const thead = document.createElement("thead");
  thead.innerHTML = `
    <tr>
      <th style="width:36px;"><input type="checkbox" id="discover-th-check" title="전체 선택/해제"></th>
      <th style="width:60px;">status</th>
      <th style="width:50px;">depth</th>
      <th style="width:90px;">출처</th>
      <th>title</th>
      <th>URL</th>
    </tr>
  `;
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const row of list) {
    const tr = document.createElement("tr");

    const tdCheck = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "discover-url-check";
    cb.dataset.url = row.url || "";
    tdCheck.appendChild(cb);
    tr.appendChild(tdCheck);

    const tdStatus = document.createElement("td");
    tdStatus.innerHTML = _statePillHtml(row.status);  // 정적 HTML — 안전
    tr.appendChild(tdStatus);

    const tdDepth = document.createElement("td");
    tdDepth.textContent = row.depth == null ? "—" : String(row.depth);
    tr.appendChild(tdDepth);

    const tdSource = document.createElement("td");
    tdSource.textContent = row.source || "anchor";
    tr.appendChild(tdSource);

    const tdTitle = document.createElement("td");
    tdTitle.className = "ellipsis";
    tdTitle.textContent = row.title || "—";
    if (row.title) tdTitle.title = row.title;
    tr.appendChild(tdTitle);

    const tdUrl = document.createElement("td");
    tdUrl.className = "ellipsis";
    if (_isHttpUrl(row.url)) {
      const a = document.createElement("a");
      a.textContent = row.url;
      a.setAttribute("href", row.url);
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
      a.title = row.url;
      tdUrl.appendChild(a);
    } else {
      tdUrl.textContent = row.url || "";
    }
    tr.appendChild(tdUrl);

    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  rootEl.appendChild(wrap);

  // bind checkboxes
  const allChecks = wrap.querySelectorAll(".discover-url-check");
  const headerCheck = wrap.querySelector("#discover-th-check");
  function _updateCount() {
    const n = wrap.querySelectorAll(".discover-url-check:checked").length;
    const cntEl = $("#discover-selected-count");
    if (cntEl) cntEl.textContent = `${n}개 선택`;
    const btn = $("#btn-discover-tour-script");
    if (btn) btn.disabled = (n === 0);
  }
  allChecks.forEach((c) => c.addEventListener("change", _updateCount));
  if (headerCheck) {
    headerCheck.addEventListener("change", () => {
      allChecks.forEach((c) => { c.checked = headerCheck.checked; });
      _updateCount();
    });
  }
  _updateCount();
}

async function _pollDiscoverOnce(jobId) {
  let s;
  try {
    s = await api(`/discover/${jobId}`);
  } catch (e) {
    _setDiscoverStatus(_prettifyErrorMessage(`조회 실패: ${e.message || e}`));
    _stopDiscoverPolling();
    return;
  }
  let line = `[${s.state}] ${s.count}건`;
  if (s.last_url) line += ` · 최근: ${s.last_url}`;
  _setDiscoverStatus(line);

  if (s.state === "done" || s.state === "cancelled") {
    _stopDiscoverPolling();
    let list = [];
    try { list = await api(`/discover/${jobId}/json`); } catch (e) { /* 빈 결과 */ }
    const link = $("#discover-csv-link");
    if (link) link.setAttribute("href", `/discover/${jobId}/csv`);
    const actions = $("#discover-actions");
    if (actions) actions.hidden = false;
    _toggleDiscoverButtons({ running: false });
    let suffix = "";
    if (s.state === "cancelled") suffix += " · 취소됨";
    if (s.aborted_reason === "auth_drift") suffix += " · 세션 만료 자동 중단";
    if (suffix) _setDiscoverStatus(line + suffix);
    _renderDiscoverTable($("#discover-result"), list);
    _renderDiscoverStats(s.stats || null);
    await _renderDiscoverTrees(jobId);
    return;
  }
  if (s.state === "failed") {
    _stopDiscoverPolling();
    _setDiscoverStatus(_prettifyErrorMessage(`실패: ${s.error || "알 수 없는 오류"}`));
    _toggleDiscoverButtons({ running: false });
    return;
  }
}

function _startDiscoverPolling(jobId) {
  _stopDiscoverPolling();
  _discover.lastJobId = jobId;
  _pollDiscoverOnce(jobId);
  _discover.pollTimer = setInterval(() => _pollDiscoverOnce(jobId), 1500);
}

function _stopDiscoverPolling() {
  if (_discover.pollTimer) {
    clearInterval(_discover.pollTimer);
    _discover.pollTimer = null;
  }
}

function _toggleDiscoverButtons({ running }) {
  const start = $("#btn-discover-start");
  const cancel = $("#btn-discover-cancel");
  if (start) start.disabled = !!running;
  if (cancel) cancel.hidden = !running;
}

async function _onDiscoverSubmit(ev) {
  ev.preventDefault();
  const form = ev.target;
  const fd = new FormData(form);
  const seed = (fd.get("seed_url") || "").trim();
  if (!seed) return;
  const payload = {
    seed_url: seed,
    max_pages: Number(fd.get("max_pages") || 200),
    max_depth: Number(fd.get("max_depth") || 3),
    // 체크박스: FormData 는 unchecked 면 키 자체가 없으므로 element 직접 조회.
    use_sitemap: !!form.elements.use_sitemap?.checked,
    capture_requests: !!form.elements.capture_requests?.checked,
    spa_selectors: !!form.elements.spa_selectors?.checked,
    ignore_query: !!form.elements.ignore_query?.checked,
    include_subdomains: !!form.elements.include_subdomains?.checked,
    // BFS 크롤링 자체의 headless 토글 — 이전엔 tour-script 흐름에서만 쓰여
    // BFS 는 항상 백그라운드로 돌던 사고가 있었음.
    headless: !!($("#discover-headless") && $("#discover-headless").checked),
  };
  const ap = (fd.get("auth_profile") || "").trim();
  if (ap) payload.auth_profile = ap;

  // 새 작업 시작: 기존 결과 영역 초기화
  $("#discover-actions").hidden = true;
  $("#discover-result").replaceChildren();
  const treeToggle = document.getElementById("discover-tree-toggle");
  if (treeToggle) treeToggle.hidden = true;
  const statsCard = document.getElementById("discover-stats");
  if (statsCard) { statsCard.hidden = true; statsCard.replaceChildren(); }
  _setDiscoverStatus("시작 중...");
  _toggleDiscoverButtons({ running: true });

  let resp;
  try {
    resp = await api("/discover", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  } catch (e) {
    _setDiscoverStatus(_prettifyErrorMessage(`시작 실패: ${e.message || e}`));
    _toggleDiscoverButtons({ running: false });
    return;
  }
  if (resp.machine_mismatch) {
    _setDiscoverStatus(`⚠ machine_mismatch — 다른 머신에서 시드된 프로파일입니다`);
  }
  _startDiscoverPolling(resp.job_id);
}

async function _onDiscoverCancel() {
  if (!_discover.lastJobId) return;
  try {
    await api(`/discover/${_discover.lastJobId}/cancel`, { method: "POST" });
  } catch (e) {
    // 무시 — 다음 폴링에서 state 동기화됨
  }
}

async function _onDiscoverTourScript() {
  if (!_discover.lastJobId) return;
  const checked = $all(".discover-url-check");
  const urls = [];
  checked.forEach((c) => { if (c.checked && c.dataset.url) urls.push(c.dataset.url); });
  if (urls.length === 0) return;
  const ap = ($("#discover-auth-profile") || {}).value || "";
  const headless = !!($("#discover-headless") && $("#discover-headless").checked);
  const settle = ($("#discover-content-settle") || {}).value || "network";
  const payload = {
    urls,
    headless,
    preflight_verify: true,
    content_settle: settle,
  };
  if (ap) payload.auth_profile = ap;

  const btn = $("#btn-discover-tour-script");
  const orig = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "생성 중..."; }
  try {
    const r = await fetch(`/discover/${_discover.lastJobId}/tour-script`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        const j = await r.json();
        detail = (j && j.detail) ? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)) : detail;
      } catch (_) { /* ignore */ }
      _setDiscoverStatus(`tour-script 실패: ${detail}`);
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "tour_selected.py";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    _setDiscoverStatus(
      `tour_selected.py 생성됨 (${urls.length}개 URL) · ` +
      `실행 후 결과는 스크립트 옆 'tour_results.jsonl' + 'tour_screenshots/' 에 저장됩니다`,
    );
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig || "선택 URL Tour Script 생성"; }
  }
}

function _wireDiscover() {
  const form = $("#discover-form");
  if (form) form.addEventListener("submit", _onDiscoverSubmit);
  const cancel = $("#btn-discover-cancel");
  if (cancel) cancel.addEventListener("click", _onDiscoverCancel);
  const tour = $("#btn-discover-tour-script");
  if (tour) tour.addEventListener("click", _onDiscoverTourScript);
  const sa = $("#btn-discover-select-all");
  if (sa) sa.addEventListener("click", () => {
    $all(".discover-url-check").forEach((c) => { c.checked = true; });
    $all(".discover-url-check").forEach((c) => c.dispatchEvent(new Event("change")));
  });
  const sn = $("#btn-discover-select-none");
  if (sn) sn.addEventListener("click", () => {
    $all(".discover-url-check").forEach((c) => { c.checked = false; });
    $all(".discover-url-check").forEach((c) => c.dispatchEvent(new Event("change")));
  });
  const da = $("#discover-auth-profile");
  if (da) da.addEventListener("change", () => { _discover.authTouched = true; });
}

// loadAuthProfiles 의 결과(_authState.profiles)를 폴링해 discover selector 에 반영.
// 함수 재할당으로 hook 하지 않고 별도 인터벌로 동기화 — 모듈 경계가 깔끔.
let _lastAuthProfilesSnapshot = null;
function _syncDiscoverAuthSelect() {
  const profs = (typeof _authState !== "undefined" && _authState.profiles) || [];
  // 얕은 비교 — 길이 + 이름 join 으로 변경 감지 (충분).
  const sig = profs.length + ":" + profs.map((p) => p.name).join(",");
  if (sig === _lastAuthProfilesSnapshot) return;
  _lastAuthProfilesSnapshot = sig;
  _populateDiscoverAuthSelect(profs);
}

_wireDiscover();
setInterval(_syncDiscoverAuthSelect, 1500);
// 초기 1회는 loadAuthProfiles() 가 _authState.profiles 를 채운 직후 시도.
setTimeout(_syncDiscoverAuthSelect, 500);

// ── 시작 ─────────────────────────────────────────────────────────────────────
// Play & more 카드는 항상 노출 (토글). 페이지 진입 시 세션이 없으면 비활성 상태로.
_setRplusInactive();
loadHealth();
loadSessions();
loadAuthProfiles();
setInterval(loadSessions, 5000);
setInterval(loadHealth, 15000);
setInterval(loadAuthProfiles, 30000);
