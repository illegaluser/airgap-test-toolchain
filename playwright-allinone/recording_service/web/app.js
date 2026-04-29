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
    tbody.innerHTML = `<tr class="muted"><td colspan="6">— ${all > 0 ? "필터 일치 0건 (" + all + "건 중)" : "세션 없음"} —</td></tr>`;
    return;
  }
  tbody.innerHTML = filtered.map((s) => `
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

  // P1 — Run-log (실행 결과) 카드. run_log.jsonl 이 있을 때만.
  await _renderRunLog(sid);
  // 항목 4 — Regression .py 별도 카드 + 비교 분석 카드.
  await _renderRegression(sid);
  await _renderDiff(sid);

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
  _rplusOutputBox().textContent =
    `⏳ ${label} 진행 중... (호스트 화면에 브라우저 창이 뜹니다 — 끝까지 닫지 마세요)`;

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
    // 실행 끝 — Run-log 새로고침 (PASS/FAIL/HEALED 표 갱신).
    await _renderRunLog(sid);
    // 진행 박스는 자동 collapse — 사용자가 펼쳐 보고 싶으면 수동으로.
    details.open = false;
  } catch (err) {
    _rplusOutputBox().textContent = `✗ ${label} 실패: ` + err.message;
  } finally {
    stopped = true;
    clearInterval(tailTimer);
    btn.disabled = false;
  }
}

$("#btn-play-codegen").addEventListener("click", () =>
  _runPlay("Codegen Output Replay", "#btn-play-codegen", playCodegen, "codegen"),
);

$("#btn-play-llm").addEventListener("click", () =>
  _runPlay("Play with LLM", "#btn-play-llm", playLLM, "llm"),
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

// ── P1 (항목 5) — Run-log 시각화 + P4 (항목 8) step JSON 복사 ──────────────
async function _renderRunLog(sid) {
  const card = $("#run-log-card");
  const container = $("#run-log-container");
  let records;
  try {
    records = await api(`/recording/sessions/${sid}/run-log`);
  } catch (err) {
    // 404 = run_log 없음 (Play 미실행) — 카드 숨김.
    card.hidden = true;
    return;
  }
  card.hidden = false;
  if (!Array.isArray(records) || records.length === 0) {
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

// Regression Test Script (.py) 카드 렌더 — 항목 #2 (UI 개선)
async function _renderRegression(sid) {
  const card = $("#regression-card");
  let body;
  try {
    const r = await fetch(`/recording/sessions/${sid}/regression`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    body = await r.text();
  } catch (err) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  $("#dl-regression").href = `/recording/sessions/${sid}/regression?download=1`;
  $("#result-regression").textContent = body || "(empty)";
}

// 스크린샷 모달 — 클릭 위임.
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".shot-link");
  if (!btn) return;
  const sid = btn.dataset.shotSid;
  const name = btn.dataset.shot;
  const step = btn.dataset.shotStep;
  if (!sid || !name) return;
  $("#shot-img").src = `/recording/sessions/${sid}/screenshot/${encodeURIComponent(name)}`;
  $("#shot-caption").textContent = `Step ${step} — ${name}`;
  const dlg = $("#shot-dialog");
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
});

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

// ── 시작 ─────────────────────────────────────────────────────────────────────
loadHealth();
loadSessions();
setInterval(loadSessions, 5000);
setInterval(loadHealth, 15000);
