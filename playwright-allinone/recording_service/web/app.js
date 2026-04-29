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

async function loadSessions() {
  let sessions = [];
  try {
    sessions = await api("/recording/sessions");
  } catch (e) {
    console.warn("세션 목록 조회 실패:", e);
    return;
  }
  const tbody = $("#session-tbody");
  if (!sessions.length) {
    tbody.innerHTML = `<tr class="muted"><td colspan="6">— 세션 없음 —</td></tr>`;
    return;
  }
  tbody.innerHTML = sessions.map((s) => `
    <tr>
      <td><code>${s.id}</code></td>
      <td><span class="state-pill state-${s.state}">${s.state}</span></td>
      <td class="ellipsis" title="${escapeHtml(s.target_url || "")}">${escapeHtml(s.target_url || "")}</td>
      <td>${s.action_count || 0}</td>
      <td class="muted">${formatIso(s.created_at_iso)}</td>
      <td class="row-actions">
        <button data-act="open" data-sid="${s.id}">열기</button>
        <button data-act="del" data-sid="${s.id}" class="danger">삭제</button>
      </td>
    </tr>
  `).join("");
}

async function startRecording(target_url, planning_doc_ref) {
  const body = { target_url };
  if (planning_doc_ref) body.planning_doc_ref = planning_doc_ref;
  const data = await api("/recording/start", {
    method: "POST",
    body: JSON.stringify(body),
  });
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
async function annotateSession(sid) {
  return api(`/experimental/sessions/${sid}/annotate`, { method: "POST" });
}

async function playCodegen(sid) {
  return api(`/experimental/sessions/${sid}/play-codegen`, { method: "POST" });
}

async function playLLM(sid) {
  return api(`/experimental/sessions/${sid}/play-llm`, { method: "POST" });
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
  if (s.state === "done") {
    scenarioCard.hidden = false;
    $("#dl-scenario").href = `/recording/sessions/${sid}/scenario?download=1`;
    try {
      const scenario = await getSessionScenario(sid);
      $("#result-json").textContent = JSON.stringify(scenario, null, 2);
    } catch (err) {
      $("#result-json").textContent = `(scenario.json 로드 실패: ${err.message})`;
    }
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
  } catch (err) {
    originalCard.hidden = true;
  }

  // Assertion 추가 영역은 state=done 일 때만 노출.
  $("#assertion-section").hidden = s.state !== "done";
  $("#assertion-form").dataset.sid = sid;

  // R-Plus 섹션 — state=done 일 때 항상 노출 (게이트 폐기 — TR.4+.4).
  const rplus = $("#rplus-section");
  const showRplus = s.state === "done";
  rplus.hidden = !showRplus;
  if (showRplus) {
    rplus.dataset.sid = sid;
    $("#rplus-sid").textContent = sid;
    $("#rplus-output").hidden = true;
    $("#rplus-output").textContent = "—";
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
    $("#assertion-section").hidden = true;
    $("#rplus-section").hidden = true;
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

$("#assertion-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const sid = e.target.dataset.sid;
  if (!sid) { alert("세션 미선택."); return; }
  const fd = new FormData(e.target);
  const payload = {
    action: fd.get("action"),
    target: fd.get("target").trim(),
    value: fd.get("value").trim(),
    description: (fd.get("description") || "").trim(),
  };
  const cond = (fd.get("condition") || "").trim();
  if (cond) payload.condition = cond;

  try {
    const data = await addAssertion(sid, payload);
    e.target.reset();
    await openSession(sid);
    alert(`Step ${data.step_added} 추가됨 (총 ${data.step_count} 스텝)`);
  } catch (err) {
    alert("Assertion 추가 실패: " + err.message);
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

async function _runPlay(label, btnSel, fn) {
  const sid = _currentRplusSid();
  if (!sid) return;
  const btn = $(btnSel);
  btn.disabled = true;
  _rplusOutputBox().textContent =
    `⏳ ${label} 진행 중... (호스트 화면에 브라우저 창이 뜹니다 — 끝까지 닫지 마세요)`;
  try {
    const data = await fn(sid);
    const status = data.returncode === 0 ? `✓ ${label} 완료` : `✗ ${label} 실패`;
    _rplusOutputBox().textContent =
      `${status}\n\n` +
      `returncode: ${data.returncode}\n` +
      `elapsed: ${data.elapsed_ms.toFixed(0)}ms\n` +
      (data.stdout_tail ? `\n--- stdout (tail) ---\n${data.stdout_tail}` : "") +
      (data.stderr_tail ? `\n--- stderr (tail) ---\n${data.stderr_tail}` : "");
  } catch (err) {
    _rplusOutputBox().textContent = `✗ ${label} 실패: ` + err.message;
  } finally {
    btn.disabled = false;
  }
}

$("#btn-annotate").addEventListener("click", async () => {
  const sid = _currentRplusSid();
  if (!sid) return;
  const btn = $("#btn-annotate");
  btn.disabled = true;
  _rplusOutputBox().textContent = "⏳ codegen 원본 분석 중...";
  try {
    const data = await annotateSession(sid);
    if (data.injected === 0) {
      _rplusOutputBox().textContent =
        `✓ Annotate 완료 — 추가 hover 불필요\n` +
        `examined clicks: ${data.examined_clicks}, injected: 0\n` +
        `(드롭다운/메뉴 신호가 chain 안에서 발견되지 않음)`;
    } else {
      _rplusOutputBox().textContent =
        `✓ Annotate 완료 — original_annotated.py 생성\n\n` +
        `examined clicks: ${data.examined_clicks}\n` +
        `injected hovers: ${data.injected}\n\n` +
        `--- triggers ---\n` +
        data.triggers.map((t, i) => `${i + 1}. ${t}`).join("\n") +
        `\n\n다음 [▶ Codegen Output Replay] 가 annotated 본을 자동 사용합니다.`;
    }
  } catch (err) {
    _rplusOutputBox().textContent = "✗ Annotate 실패: " + err.message;
  } finally {
    btn.disabled = false;
  }
});

$("#btn-play-codegen").addEventListener("click", () =>
  _runPlay("Codegen Output Replay", "#btn-play-codegen", playCodegen),
);

$("#btn-play-llm").addEventListener("click", () =>
  _runPlay("Play with LLM", "#btn-play-llm", playLLM),
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

// ── 시작 ─────────────────────────────────────────────────────────────────────
loadHealth();
loadSessions();
setInterval(loadSessions, 5000);
setInterval(loadHealth, 15000);
