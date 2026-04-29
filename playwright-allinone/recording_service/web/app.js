// DSCORE Recording Service Web UI (TR.4)
// Vanilla JS — no framework. Assumes a single worker daemon.

const $ = (sel) => document.querySelector(sel);
const $all = (sel) => document.querySelectorAll(sel);

// ── Global state (assumes a single active session) ──────────────────────────
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
      badge.textContent = `⚠ codegen not installed · v${h.version}`;
      badge.className = "warn";
    }
  } catch (e) {
    $("#health-badge").textContent = "✗ unreachable";
    $("#health-badge").className = "err";
  }
}

// P3 — Session filter. Persists last value in localStorage.
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
    tbody.innerHTML = `<tr class="muted"><td colspan="7">— ${all > 0 ? "0 matches (out of " + all + ")" : "No sessions"} —</td></tr>`;
    return;
  }
  tbody.innerHTML = filtered.map((s) => `
    <tr>
      <td><code>${s.id}</code></td>
      <td><span class="state-pill state-${s.state}">${s.state}</span></td>
      <td class="ellipsis" title="${escapeHtml(s.target_url || "")}">${escapeHtml(s.target_url || "")}</td>
      <td class="muted">${escapeHtml(s.auth_profile || "—")}</td>
      <td>${s.action_count || 0}</td>
      <td class="muted">${formatIso(s.created_at_iso)}</td>
      <td class="row-actions">
        <button data-act="open" data-sid="${s.id}">Open</button>
        <button data-act="del" data-sid="${s.id}" class="danger">Delete</button>
      </td>
    </tr>
  `).join("");
}

async function loadSessions() {
  try {
    _sessionsCache = await api("/recording/sessions");
  } catch (e) {
    console.warn("Failed to fetch session list:", e);
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
  // Use raw fetch so we can read response headers (X-Auth-Machine-Mismatch).
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
  // The machine-mismatch header only carries meaning on a normal 200/201.
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
  // Text body — call fetch directly (api() tries to parse JSON).
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

// R-Plus — backend lives under /experimental/*. The UI shows it in the main result view.
// We need to preserve error status/detail (expired / machine mismatch) so _runPlay can
// branch on 409 → expiration modal, so we use raw fetch instead of api() and stash
// err.status / err.detail.
async function _playRawFetch(path) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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

async function playCodegen(sid) {
  return _playRawFetch(`/experimental/sessions/${sid}/play-codegen`);
}

async function playLLM(sid) {
  return _playRawFetch(`/experimental/sessions/${sid}/play-llm`);
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

// ── Active session panel ─────────────────────────────────────────────────────
function showActivePanel(session) {
  _state.activeSid = session.id;
  _state.startedAt = Date.now();

  $("#active-session").hidden = false;
  $("#active-id").textContent = session.id;
  $("#active-url").textContent = session.target_url;
  setStatePill("#active-state", session.state);

  // Polling + elapsed time
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
    // Session is gone (deleted)
    console.warn("Polling failed:", e);
    hideActivePanel();
  }
}

function updateElapsed() {
  if (_state.startedAt == null) return;
  const sec = Math.round((Date.now() - _state.startedAt) / 1000);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  $("#active-elapsed").textContent = m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// ── Result panel ─────────────────────────────────────────────────────────────
async function openSession(sid) {
  const s = await getSession(sid);
  $("#result-section").hidden = false;
  $("#result-id").textContent = s.id;
  setStatePill("#result-state", s.state);
  $("#result-step-count").textContent = s.action_count || 0;
  $("#result-path").textContent = `~/.dscore.ttc.playwright-agent/recordings/${s.id}/scenario.json`;

  // Scenario JSON card — only shown when state=done; refresh download link too (TR.4+.2).
  const scenarioCard = $("#scenario-card");
  if (s.state === "done") {
    scenarioCard.hidden = false;
    $("#dl-scenario").href = `/recording/sessions/${sid}/scenario?download=1`;
    try {
      const scenario = await getSessionScenario(sid);
      $("#result-json").textContent = JSON.stringify(scenario, null, 2);
    } catch (err) {
      $("#result-json").textContent = `(failed to load scenario.json: ${err.message})`;
    }
  } else {
    scenarioCard.hidden = true;
  }

  // Original .py card — shown when original.py exists (TR.4+.1).
  // Visible whenever the codegen artifact is present, including just before stop / done / error.
  const originalCard = $("#original-card");
  $("#dl-original").href = `/recording/sessions/${sid}/original?download=1`;
  try {
    const original = await getSessionOriginal(sid);
    originalCard.hidden = false;
    $("#result-original").textContent = original || "(empty)";
  } catch (err) {
    originalCard.hidden = true;
  }

  // P1 — Run-log (execution result) card. Only when run_log.jsonl exists.
  await _renderRunLog(sid);
  // Item 4 — Regression .py card and diff analysis card.
  await _renderRegression(sid);
  await _renderDiff(sid);

  // Assertion-add area is only shown when state=done.
  $("#assertion-section").hidden = s.state !== "done";
  $("#assertion-form").dataset.sid = sid;

  // R-Plus section — always shown when state=done (gating removed — TR.4+.4).
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

// ── Form handlers ────────────────────────────────────────────────────────────
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
    alert("Start failed: " + err.message);
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
    alert("Stop failed: " + err.message);
  } finally {
    $("#btn-stop").disabled = false;
  }
});

// On action change, help fill the value input — auto-fill into_view for scroll, clear for hover.
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
  if (!sid) { alert("No session selected."); return; }
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
    alert(`Step ${data.step_added} added (total ${data.step_count} steps)`);
  } catch (err) {
    alert("Failed to add step: " + err.message);
  }
});

// R-Plus handler — when RPLUS_ENABLED is unset the backend returns 404, so notify the user.
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
    return `\nannotate: examined ${a.examined_clicks} clicks → 0 hovers injected (using original as is)`;
  }
  const triggers = (a.triggers || []).map((t, i) => `  ${i + 1}. ${t}`).join("\n");
  return (
    `\nannotate: examined ${a.examined_clicks} clicks → ${a.injected} hovers injected\n` +
    triggers
  );
}

async function _runPlay(label, btnSel, fn, kind /* "llm" | "codegen" */) {
  const sid = _currentRplusSid();
  if (!sid) return;
  const btn = $(btnSel);
  btn.disabled = true;
  _rplusOutputBox().textContent =
    `⏳ ${label} running... (a browser window will open on the host — do not close it until it finishes)`;

  // P2 — initialize the live progress box and start 1s polling.
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
      // Ignore polling failures — keep retrying until the subprocess ends.
    }
  }, 1000);

  try {
    const data = await fn(sid);
    const status = data.returncode === 0 ? `✓ ${label} done` : `✗ ${label} failed`;
    _rplusOutputBox().textContent =
      `${status}\n\n` +
      `returncode: ${data.returncode}\n` +
      `elapsed: ${data.elapsed_ms.toFixed(0)}ms` +
      _annotateLine(data.annotate) +
      (data.stdout_tail ? `\n\n--- stdout (tail) ---\n${data.stdout_tail}` : "") +
      (data.stderr_tail ? `\n\n--- stderr (tail) ---\n${data.stderr_tail}` : "");
    // Run finished — refresh Run-log (PASS/FAIL/HEALED table).
    await _renderRunLog(sid);
    // Auto-collapse the progress box — user can expand manually if they want to look.
    details.open = false;
  } catch (err) {
    // Post-review fix — auth-profile expired/missing (the rplus router's 409) should
    // open the expiration modal instead of a generic failure message. The UI's
    // [Re-seed] button reuses the same catalog prefill flow.
    if (err.status === 409 && err.detail?.reason === "profile_expired") {
      const profName = err.detail.profile_name || _authState.selected || "—";
      const reason = err.detail.fail_reason || err.detail.reason || "verify failed";
      _rplusOutputBox().textContent =
        `⚠ ${label} aborted — auth session '${profName}' expired (${reason}). Re-seed and try again.`;
      _showExpiredDialog(profName, reason);
    } else {
      _rplusOutputBox().textContent = `✗ ${label} failed: ` + err.message;
    }
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
  _rplusOutputBox().textContent = "⏳ Ollama back-inference running... (may take tens of seconds to a few minutes)";
  try {
    const data = await enrichSession(sid);
    _rplusOutputBox().textContent =
      `✓ Generate Doc done (model=${data.model}, ${data.elapsed_ms.toFixed(0)}ms)\n` +
      `saved: ${data.saved_to}\n\n` +
      "─".repeat(40) + "\n\n" +
      data.markdown;
  } catch (err) {
    _rplusOutputBox().textContent = "✗ Back-inference failed: " + err.message;
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
    if (!Array.isArray(docDsl)) throw new Error("doc-DSL must be a JSON array.");
  } catch (err) {
    alert("JSON parse failed: " + err.message);
    return;
  }
  const threshold = fd.get("threshold") || 0.7;

  $("#btn-compare-submit").disabled = true;
  try {
    const data = await compareSession(sid, docDsl, threshold);
    const c = data.counts;
    _rplusOutputBox().textContent =
      `✓ Compare done\n\n` +
      `exact: ${c.exact} · value_diff: ${c.value_diff} · missing: ${c.missing} · ` +
      `extra: ${c.extra} · intent_only: ${c.intent_only}\n` +
      `report HTML: ${data.report_html_url}\n`;
    window.open(data.report_html_url, "_blank");
    $("#compare-dialog").close();
  } catch (err) {
    alert("Compare failed: " + err.message);
  } finally {
    $("#btn-compare-submit").disabled = false;
  }
});

// Row actions in the session table (Open / Delete)
$("#session-tbody").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const sid = btn.dataset.sid;
  if (btn.dataset.act === "open") {
    await openSession(sid);
  } else if (btn.dataset.act === "del") {
    if (!confirm(`Delete session ${sid}? (the host directory is removed too)`)) return;
    try {
      await deleteSession(sid);
      $("#result-section").hidden = true;
      $("#assertion-section").hidden = true;
      await loadSessions();
    } catch (err) {
      alert("Delete failed: " + err.message);
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
  // ISO8601 → show only HH:MM:SS (today) or MM-DD HH:MM
  try {
    const d = new Date(iso);
    const today = new Date();
    if (d.toDateString() === today.toDateString()) {
      return d.toLocaleTimeString();
    }
    return d.toLocaleString();
  } catch (_) { return iso; }
}

// ── P1 (item 5) — Run-log visualization + P4 (item 8) step JSON copy ────────
async function _renderRunLog(sid) {
  const card = $("#run-log-card");
  const container = $("#run-log-container");
  let records;
  try {
    records = await api(`/recording/sessions/${sid}/run-log`);
  } catch (err) {
    // 404 = no run_log (Play has not run) — hide the card.
    card.hidden = true;
    return;
  }
  card.hidden = false;
  if (!Array.isArray(records) || records.length === 0) {
    container.innerHTML = '<p class="muted">— Empty run-log —</p>';
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
                  title="Enlarge screenshot">📷</button>`
      : "—";
    const recJson = JSON.stringify(rec).replace(/'/g, "&#39;");
    const copyCell = `<button class="copy-step-btn" data-step-json='${recJson}' title="Copy this step JSON">📋</button>`;
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
  // Auto-scroll to the FAIL step.
  if (firstFailIdx >= 0) {
    const failRow = container.querySelector(`tr[data-step="${records[firstFailIdx].step}"]`);
    if (failRow) failRow.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

// ── Item 4 — codegen original ↔ LLM healed regression diff ──────────────────
function _renderUnifiedDiff(text) {
  if (!text) return '<span class="muted">(no differences)</span>';
  return text.split("\n").map((line) => {
    const safe = escapeHtml(line);
    if (line.startsWith("+++") || line.startsWith("---")) return `<span class="diff-meta">${safe}</span>`;
    if (line.startsWith("@@")) return `<span class="diff-hunk">${safe}</span>`;
    if (line.startsWith("+")) return `<span class="diff-add">${safe}</span>`;
    if (line.startsWith("-")) return `<span class="diff-del">${safe}</span>`;
    return `<span class="diff-ctx">${safe}</span>`;
  }).join("\n");
}

// Tiny markdown → HTML renderer (for analysis output, no external library).
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
  // Wrap consecutive <li>s in <ul> (simple fix-up)
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
  // Reset the analysis area (clear previous output when entering another session)
  $("#diff-analysis-output").innerHTML =
    "— Click <strong>🔎 LLM analysis</strong> to start the analysis —";
}

// LLM analysis button handler
$("#btn-analyze-diff").addEventListener("click", async () => {
  const sid = _currentRplusSid();
  if (!sid) return;
  const out = $("#diff-analysis-output");
  const btn = $("#btn-analyze-diff");
  btn.disabled = true;
  out.innerHTML =
    '<span class="muted">⏳ Calling Ollama... model inference takes ~30-60s.</span>';
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
      '<span class="err">✗ Analysis failed:</span> ' + escapeHtml(err.message);
  } finally {
    btn.disabled = false;
  }
});

// Regression Test Script (.py) card render — item #2 (UI improvement)
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

// Screenshot modal — click delegation.
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

// P4 — Per-step JSON copy. Reads directly from data-step-json.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".copy-step-btn");
  if (!btn) return;
  const text = btn.dataset.stepJson || "";
  try {
    await _copyToClipboard(text);
    btn.textContent = "✓";
    setTimeout(() => { btn.textContent = "📋"; }, 800);
  } catch (err) {
    alert("Step copy failed: " + err.message);
  }
});

// ── R-Plus dropdown (item 3 — hover-open + click-stick) ─────────────────────
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
  // Click outside the group → close all dropdowns.
  if (!e.target.closest(".dropdown-group")) {
    _closeAllDropdowns(null);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") _closeAllDropdowns(null);
});

// ── Clipboard copy (item 2) ─────────────────────────────────────────────────
async function _copyToClipboard(text) {
  if (!navigator.clipboard) {
    throw new Error("Browser does not support the clipboard API — HTTPS/localhost is required");
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
    alert("Copy failed: " + err.message);
  }
});

// ── import-script — upload user .py and auto-enter the result view ──────────
$("#btn-import-script").addEventListener("click", () => {
  $("#import-file-input").click();
});

$("#import-file-input").addEventListener("change", async (e) => {
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  const btn = $("#btn-import-script");
  btn.disabled = true;
  btn.textContent = "⏳ Uploading...";
  try {
    const r = await fetch("/recording/import-script", {
      method: "POST", body: fd,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const detail = (data && data.detail) || `HTTP ${r.status}`;
      alert("Upload failed: " + detail);
      return;
    }
    await loadSessions();
    await openSession(data.id);
    // Scroll to the result view
    $("#result-section").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    alert("Upload failed: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "📁 Play Script from File";
    e.target.value = "";  // reset so the same file can be uploaded again
  }
});

// ── Back button — always shown (Jenkins's Safe HTML policy strips
// target="_blank" so a new tab is impossible — same-tab users need a way back). ──
(function _initBackButton() {
  const btn = document.getElementById("back-btn");
  if (!btn) return;
  btn.addEventListener("click", () => {
    // Try 1: referrer (most reliable). 100% works if we came from a different origin.
    if (document.referrer) {
      window.location.href = document.referrer;
      return;
    }
    // Try 2: history.back. If history exists from before this page, go back.
    if (window.history.length > 1) {
      window.history.back();
      return;
    }
    // Try 3: starting point — notify, then go to the Jenkins default URL.
    alert("No previous page — going to Jenkins home.");
    window.location.href = "http://localhost:18080/";
  });
})();

// ─────────────────────────────────────────────────────────────────────────
// Auth Profile UI (P5.2 ~ P5.9)
// ─────────────────────────────────────────────────────────────────────────
// Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §3 + §4
//
// Flow:
//   1) Page load → GET /auth/profiles → populate dropdown (P5.2)
//   2) Dropdown selection → refresh label + enable verify button
//   3) ↻ verify → POST /auth/profiles/{name}/verify (P5.3)
//   4) + Seed new session → seed-input modal → POST /auth/profiles/seed (P5.4)
//   5) Seed-progress modal → 1s polling → ready/error (P5.5)
//   6) Start Recording 409 (expired) → expiration modal → re-seed prefill (P5.6)
//   7) Response header X-Auth-Machine-Mismatch=1 → machine-mismatch modal (P5.7)
//   8) sessionStorage warning (P5.9) — display the list response's session_storage_warning

async function fetchAuthProfiles() {
  return api("/auth/profiles");
}

async function fetchAuthProfileDetail(name) {
  // P2.1 — fetch a single profile detail so the [Re-seed] button on the expiration
  // modal can prefill verify_service_url / verify_service_text too.
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
  reseedPrefill: null,     // input form prefill for re-seed
};

function _formatRelative(iso) {
  if (!iso) return "—";
  try {
    const dt = new Date(iso);
    const sec = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 1000));
    if (sec < 60) return `${sec}s ago`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  } catch {
    return iso;
  }
}

async function loadAuthProfiles() {
  let profiles;
  try {
    profiles = await fetchAuthProfiles();
  } catch (e) {
    console.warn("Failed to fetch auth profiles:", e);
    return;
  }
  _authState.profiles = profiles;
  const sel = $("#auth-profile-select");
  const current = _authState.selected;
  sel.innerHTML = `<option value="">(none — record without login)</option>` +
    profiles.map((p) => {
      const warn = p.session_storage_warning ? " ⚠sessionStorage" : "";
      return `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)} — ${escapeHtml(p.service_domain)}${warn}</option>`;
    }).join("");
  // Restore previous selection.
  if (current && profiles.some((p) => p.name === current)) {
    sel.value = current;
    _renderAuthStatus();
  } else {
    _authState.selected = "";
    _renderAuthStatus();
  }
}

function _selectedProfile() {
  return _authState.profiles.find((p) => p.name === _authState.selected) || null;
}

function _renderAuthStatus() {
  const p = _selectedProfile();
  const status = $("#auth-status");
  const verifyBtn = $("#btn-auth-verify");
  if (!p) {
    status.textContent = "— Select a profile or seed a new one —";
    status.className = "auth-status muted";
    verifyBtn.disabled = true;
    return;
  }
  verifyBtn.disabled = false;
  let txt = `Profile "${p.name}" — ${p.service_domain}`;
  if (p.last_verified_at) {
    txt += ` · verified ${_formatRelative(p.last_verified_at)}`;
  } else {
    txt += " · not verified";
  }
  // P5.9 — warn for services suspected of relying on sessionStorage.
  if (p.session_storage_warning) {
    txt += " · ⚠ may rely on sessionStorage (re-seed more often)";
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

// Dropdown selection change.
$("#auth-profile-select").addEventListener("change", (e) => {
  _authState.selected = e.target.value;
  _authState.lastVerify = null;
  _renderAuthStatus();
});

// ↻ verify (P5.3).
$("#btn-auth-verify").addEventListener("click", async () => {
  const p = _selectedProfile();
  if (!p) return;
  const btn = $("#btn-auth-verify");
  btn.disabled = true;
  $("#auth-status").textContent = "verify running...";
  $("#auth-status").className = "auth-status muted";
  try {
    const result = await verifyAuthProfile(p.name);
    _authState.lastVerify = { profile: p.name, ...result };
    // Refresh catalog last_verified_at — re-fetch.
    await loadAuthProfiles();
  } catch (err) {
    _authState.lastVerify = { profile: p.name, ok: false, fail_reason: err.message };
    _renderAuthStatus();
  } finally {
    btn.disabled = false;
  }
});

// ── Seed input modal (P5.4) ─────────────────────────────────────────────────
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

// The cancel button is type="button" — it closes the dialog immediately even when
// required form fields are empty. (type="submit" lets HTML5 validation block submit
// itself, which would skip both our handler and the form method="dialog" close.)
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
  // Close the input modal, open the progress modal, and start seeding.
  $("#auth-seed-dialog").close();
  _authState.reseedPrefill = payload;  // prefill the same inputs on expired → re-seed.
  _startSeedFlow(payload);
});

// ── Seed progress polling (P5.5) ────────────────────────────────────────────
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
  status.textContent = "⏳ Waiting for the login window — once you see the logged-in screen, close the open browser window";
  elapsed.textContent = `elapsed 0s / limit ${payload.timeout_sec || 600}s`;
  hint.textContent = "When the window closes, we save the session, briefly show the verify target page, then finish.";
  cancelBtn.hidden = false;
  skipBtn.hidden = true;
  doneBtn.hidden = true;
  cancelBtn.textContent = "Cancel (close the window yourself)";
  cancelBtn.dataset.action = "cancel";
  skipBtn.dataset.profile = "";
  doneBtn.dataset.profile = "";
  dlg.showModal();

  let resp;
  try {
    resp = await seedAuthProfileStart(payload);
  } catch (err) {
    status.textContent = `✗ Failed to start: ${err.message}`;
    return;
  }
  const seedSid = resp.seed_sid;
  if (_authState.pollTimer) clearInterval(_authState.pollTimer);
  _authState.pollTimer = setInterval(async () => {
    let poll;
    try {
      poll = await seedAuthProfilePoll(seedSid);
    } catch (err) {
      console.warn("seed poll failed:", err);
      return;
    }
    elapsed.textContent =
      `elapsed ${Math.floor(poll.elapsed_sec)}s / limit ${poll.timeout_sec}s`;
    if (poll.message) status.textContent = poll.message;
    if (poll.phase === "verifying") {
      hint.textContent = "The verify browser shows the target page slowly, then closes automatically.";
    }
    if (poll.state === "ready") {
      clearInterval(_authState.pollTimer);
      status.textContent = poll.message || `✓ Seed complete — profile "${poll.profile_name}"`;
      hint.textContent = "Choose whether to use it for this recording. Even if you skip, the profile is saved.";
      await loadAuthProfiles();
      cancelBtn.hidden = true;
      skipBtn.hidden = false;
      doneBtn.hidden = false;
      skipBtn.dataset.profile = poll.profile_name || "";
      doneBtn.dataset.profile = poll.profile_name || "";
    } else if (poll.state === "error") {
      clearInterval(_authState.pollTimer);
      const kind = poll.error_kind ? `[${poll.error_kind}] ` : "";
      status.textContent = poll.message || `✗ Failed — ${kind}${poll.error}`;
      hint.textContent = "Check your inputs and seed again.";
      cancelBtn.textContent = "Re-enter";
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
    _renderAuthStatus();
  }
  $("#auth-seed-progress").close();
});

// ── Expiration modal (P5.6) ─────────────────────────────────────────────────
function _showExpiredDialog(name, reason) {
  $("#auth-expired-name").textContent = name || "—";
  $("#auth-expired-reason").textContent = reason
    ? `Reason: ${reason}`
    : "Reason: session expired or IP changed.";
  $("#auth-expired-dialog").showModal();
}

$("#btn-auth-expired-cancel").addEventListener("click", () => {
  $("#auth-expired-dialog").close();
});

$("#btn-auth-expired-reseed").addEventListener("click", async () => {
  $("#auth-expired-dialog").close();
  // P2.1 — prefill priority:
  //   (1) inputs the user just seeded directly in this browser session (_authState.reseedPrefill)
  //   (2) catalog detail fetch — profiles seeded yesterday or imported from another machine
  //   (3) name only (last fallback — when no detail is available either)
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
    console.warn("Failed to fetch auth profile detail:", e);
  }
  if (detail) {
    // seed_url is not stored in the catalog, so we infer it from the verify_service_url origin.
    // The user can edit it directly in the modal.
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

// ── Machine-mismatch modal (P5.7) ───────────────────────────────────────────
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
  // Already responded with 200, so no retry needed — this is just user awareness.
});

$("#btn-mm-reseed").addEventListener("click", () => {
  $("#auth-machine-mismatch-dialog").close();
  _openSeedDialog({ name: _authState.selected });
});

// ── Start Recording result — error branching (P5.6/P5.7) ────────────────────
// Extends the existing #start-form submit handler. Adds the auth_profile arg + response branching.
const _origSubmitListenerNeedsRebind = false; // guard — keep the existing handler, just reinforce.

// The original handler is already registered, so attach another one in capture phase.
$("#start-form").addEventListener("submit", async (e) => {
  // Runs before the default handler — handle in capture phase.
  // The default handler calls startRecording, so here we *intercept* and only inject auth_profile.
}, true);

// Replaces the existing handler (auth_profile integration + error branching).
const _legacyStartFormSubmit = (() => {
  // Keep the existing listener but have the new capture listener intercept it first via
  // e.preventDefault and handle it directly. The capture listener then calls startRecording.
  return null;
})();

// To keep this clean we *do not remove* the form's handler — the new capture handler takes
// full responsibility. To avoid double-submit on the first call, the initial registration
// uses stopImmediatePropagation.
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
    $("#assertion-section").hidden = true;
    $("#rplus-section").hidden = true;
    await loadSessions();
    if (data._machineMismatch) {
      _showMachineMismatchDialog(null);
    }
  } catch (err) {
    if (err.status === 409 && err.detail?.reason === "profile_expired") {
      const reason = err.detail.fail_reason || err.detail.reason;
      _showExpiredDialog(authProfile, reason);
    } else if (err.status === 404 && err.detail?.reason === "profile_not_found") {
      alert(`Auth profile '${authProfile}' not found — seed a new one.`);
    } else {
      alert("Start failed: " + err.message);
    }
  } finally {
    $("#btn-start").disabled = false;
  }
}, true);  // capture so we run first

// Show the auth profile field on the result card — refreshed inside openSession.
async function _renderResultAuthProfile(sid) {
  try {
    const sess = await getSession(sid);
    const el = $("#result-auth-profile");
    if (el) {
      el.textContent = sess.auth_profile || "—";
    }
  } catch {
    // Ignore — auxiliary info on the result card.
  }
}

// Augment openSession — refresh the auth meta right after the result card is shown.
const _origOpenSession = window.openSession;
if (typeof _origOpenSession === "function") {
  window.openSession = async function patchedOpenSession(sid) {
    await _origOpenSession(sid);
    _renderResultAuthProfile(sid);
  };
}

// ── Boot ────────────────────────────────────────────────────────────────────
loadHealth();
loadSessions();
loadAuthProfiles();
setInterval(loadSessions, 5000);
setInterval(loadHealth, 15000);
setInterval(loadAuthProfiles, 30000);
