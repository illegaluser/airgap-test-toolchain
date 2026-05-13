// Replay UI — 정적 클라이언트.
//
// 주요 흐름:
// - 5초 폴링으로 프로파일 / 시나리오 묶음 / 실행 결과 갱신
// - 로그인 등록 / 실행 subprocess 진행상황은 별도 polling
// - 실행 상세는 모달 + 스텝/스크린샷 lightbox
// - 글로벌 알람 인디케이터 = 다시 로그인이 필요한 프로파일 수

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// --- 시간 포맷 ---------------------------------------------------------------

function fmtTime(iso) {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// --- Login Profile -----------------------------------------------------------

async function loadProfiles() {
  let data;
  try {
    const r = await fetch("/api/profiles");
    data = await r.json();
  } catch {
    return;
  }
  // run-profile-select 의 후보로 시드된 프로파일만 캐시.
  _availableProfiles = (data || []).filter((p) => p.storage === "ok");
  const tbody = $("#profiles-tbody");
  tbody.innerHTML = "";
  let expiredCount = 0;
  if (!Array.isArray(data) || data.length === 0) {
    tbody.innerHTML = '<tr class="muted"><td colspan="5">— 등록된 프로파일 없음 —</td></tr>';
  } else {
    for (const p of data) {
      const tr = document.createElement("tr");
      const storageOk = p.storage === "ok";
      if (!storageOk) expiredCount += 1;
      tr.innerHTML = `
        <td><strong>${escapeHtml(p.alias)}</strong></td>
        <td>${storageOk ? "<span class='ok'>등록됨</span>" : "<span class='expired'>🔴 다시 로그인 필요</span>"}</td>
        <td>${escapeHtml(p.last_verified_at || "-")}</td>
        <td>${escapeHtml(p.service_domain || "-")}</td>
        <td>
          <button class="reseed-btn ghost" data-alias="${escapeHtml(p.alias)}">↻ 다시 로그인</button>
          <button class="del-profile-btn ghost" data-alias="${escapeHtml(p.alias)}">🗑</button>
        </td>`;
      tbody.appendChild(tr);
    }
  }
  // 글로벌 알람 인디케이터.
  const badge = $("#alarm-badge");
  if (expiredCount > 0) {
    badge.textContent = `🔴 ${expiredCount} 만료`;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
  // 프로파일 행별 액션 wire.
  $$(".reseed-btn").forEach((b) => {
    b.addEventListener("click", () => openReseed(b.dataset.alias));
  });
  $$(".del-profile-btn").forEach((b) => {
    b.addEventListener("click", async () => {
      if (!confirm(`프로파일 '${b.dataset.alias}' 을 삭제할까요?`)) return;
      const r = await fetch(`/api/profiles/${encodeURIComponent(b.dataset.alias)}`, { method: "DELETE" });
      if (r.ok) loadProfiles();
      else alert(`삭제 실패: HTTP ${r.status}`);
    });
  });
}

// --- 시드 흐름 (Recording UI 미러) -------------------------------------------
//
//  (1) openSeedInput(prefill?)              — 입력 모달 (이름·시작URL·검증URL·검증텍스트·TTL·probe)
//  (2) form submit → startSeedFlow(payload) — POST /api/profiles/seed + 진행 모달 + 1초 폴링
//  (3) phase 추적: starting / login_waiting / verifying / ready / error
//  (4) openReseed(name)                     — GET /api/profiles/{name} → 입력 모달 prefill

let _seedPollTimer = null;
let _lastSeedPayload = null;  // 만료 → 다시 로그인 시 입력 prefill

function openSeedInput(prefill) {
  const form = $("#seed-input-form");
  form.reset();
  $("#seed-input-title").textContent = prefill ? "↻ 다시 로그인" : "+ 새 로그인 프로파일";
  if (prefill) {
    if (prefill.name) form.elements["name"].value = prefill.name;
    if (prefill.seed_url) form.elements["seed_url"].value = prefill.seed_url;
    if (prefill.verify_service_url) form.elements["verify_service_url"].value = prefill.verify_service_url;
    if (prefill.verify_service_text) form.elements["verify_service_text"].value = prefill.verify_service_text;
    if (prefill.ttl_hint_hours) form.elements["ttl_hint_hours"].value = prefill.ttl_hint_hours;
    if (prefill.naver_probe !== undefined) form.elements["naver_probe"].checked = !!prefill.naver_probe;
    if (prefill.idp_domain !== undefined) form.elements["idp_domain"].value = prefill.idp_domain;
  }
  $("#seed-input-modal").hidden = false;
}

async function openReseed(name) {
  // 카탈로그에 저장된 verify spec 을 가져와 입력 모달을 prefill.
  let detail = null;
  try {
    const r = await fetch(`/api/profiles/${encodeURIComponent(name)}`);
    if (r.ok) detail = await r.json();
  } catch {
    /* fallthrough — detail 없이 빈 폼 + name 만 prefill */
  }
  openSeedInput({
    name,
    seed_url: detail?.verify_service_url || "",
    verify_service_url: detail?.verify_service_url || "",
    verify_service_text: detail?.verify_service_text || "",
    ttl_hint_hours: detail?.ttl_hint_hours || 12,
    naver_probe: detail?.naver_probe_enabled !== undefined ? detail.naver_probe_enabled : true,
    idp_domain: detail?.idp_domain !== undefined ? (detail.idp_domain || "") : "naver.com",
  });
}

async function startSeedFlow(payload) {
  _lastSeedPayload = payload;
  $("#seed-input-modal").hidden = true;

  const status = $("#seed-progress-status");
  const elapsed = $("#seed-progress-elapsed");
  const hint = $("#seed-progress-hint");
  const cancelBtn = $("#btn-seed-cancel");
  const doneBtn = $("#btn-seed-done");

  status.textContent = "⏳ 로그인 창 대기 중 — 로그인 완료 화면 확인 후 열린 브라우저 창을 닫으세요";
  elapsed.textContent = `경과 0초 / 한도 ${payload.timeout_sec || 600}초`;
  hint.textContent = "창이 닫히면 세션을 저장하고, 검증 대상 페이지를 잠시 보여준 뒤 완료됩니다.";
  cancelBtn.hidden = false;
  cancelBtn.textContent = "취소 (창은 직접 닫으세요)";
  cancelBtn.dataset.action = "cancel";
  doneBtn.hidden = true;
  $("#seed-progress-modal").hidden = false;

  let resp;
  try {
    const r = await fetch("/api/profiles/seed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      status.textContent = `✗ 시작 실패: HTTP ${r.status} — ${body.detail || ""}`;
      cancelBtn.textContent = "닫기";
      return;
    }
    resp = await r.json();
  } catch (e) {
    status.textContent = `✗ 시작 실패: ${e.message}`;
    cancelBtn.textContent = "닫기";
    return;
  }

  const seedSid = resp.seed_sid;
  if (_seedPollTimer) clearInterval(_seedPollTimer);
  _seedPollTimer = setInterval(async () => {
    let poll;
    try {
      const r = await fetch(`/api/profiles/seed/${encodeURIComponent(seedSid)}`);
      if (!r.ok) return;
      poll = await r.json();
    } catch {
      return;
    }
    elapsed.textContent = `경과 ${Math.floor(poll.elapsed_sec)}초 / 한도 ${poll.timeout_sec}초`;
    if (poll.message) status.textContent = poll.message;
    if (poll.phase === "verifying") {
      hint.textContent = "검증 브라우저가 대상 페이지를 천천히 표시한 뒤 자동 종료됩니다.";
    }
    if (poll.state === "ready") {
      clearInterval(_seedPollTimer);
      _seedPollTimer = null;
      status.textContent = poll.message || `✓ 시드 완료 — 프로파일 "${poll.profile_name}"`;
      hint.textContent = "이 프로파일은 이제 시나리오 묶음 실행에 자동으로 재사용됩니다.";
      cancelBtn.hidden = true;
      doneBtn.hidden = false;
      loadProfiles();
    } else if (poll.state === "error") {
      clearInterval(_seedPollTimer);
      _seedPollTimer = null;
      const kind = poll.error_kind ? `[${poll.error_kind}] ` : "";
      status.textContent = poll.message || `✗ 실패 — ${kind}${poll.error}`;
      hint.textContent = "입력값을 확인한 뒤 다시 시도해 주세요.";
      cancelBtn.textContent = "다시 입력";
      cancelBtn.dataset.action = "retry";
    }
  }, 1000);
}

// --- 시드된 프로파일 목록 (script 카드용) -----------------------------------

let _availableProfiles = [];  // GET /api/profiles 결과 (storage=ok 만). loadProfiles 가 채움.

let _currentEventSource = null;

// 자동 새로고침(5~10초)이 tbody 를 다시 그리는 동안 사용자가 막 체크한 선택이
// 사라지지 않도록, 선택을 클라이언트측 Set 으로 보존하고 렌더 시 복원.
const _selectedScripts = new Set();  // script 파일명
const _selectedRuns = new Set();     // run_id

// --- Results -----------------------------------------------------------------

async function loadRuns() {
  let data;
  try {
    const r = await fetch("/api/runs");
    data = await r.json();
  } catch {
    return;
  }
  const tbody = $("#runs-tbody");
  tbody.innerHTML = "";
  // 목록에 더 이상 존재하지 않는 run_id 는 선택 Set 에서도 제거.
  const presentRuns = new Set((data || []).map((r) => r.run_id));
  for (const id of Array.from(_selectedRuns)) {
    if (!presentRuns.has(id)) _selectedRuns.delete(id);
  }
  if (!Array.isArray(data) || data.length === 0) {
    tbody.innerHTML = '<tr class="muted"><td colspan="6">— 실행 결과 없음 —</td></tr>';
    syncBulkDeleteRunsState();
    return;
  }
  for (const run of data) {
    const tr = document.createElement("tr");
    const result = renderResult(run);
    const running = run.state === "running";
    const checked = (!running && _selectedRuns.has(run.run_id)) ? "checked" : "";
    const checkCell = running
      ? `<td class="col-check" title="진행중 — 삭제 불가"><input type="checkbox" class="run-check" data-run="${escapeHtml(run.run_id)}" disabled /></td>`
      : `<td class="col-check"><input type="checkbox" class="run-check" data-run="${escapeHtml(run.run_id)}" ${checked} /></td>`;
    const delBtn = running
      ? `<button class="del-run-btn ghost" data-run="${escapeHtml(run.run_id)}" disabled title="진행중 — 삭제 불가">🗑</button>`
      : `<button class="del-run-btn ghost" data-run="${escapeHtml(run.run_id)}">🗑</button>`;
    tr.innerHTML = `
      ${checkCell}
      <td>${fmtTime(run.started_at)}</td>
      <td>${escapeHtml(run.script || run.bundle || "-")}</td>
      <td>${escapeHtml(run.alias || "(비로그인)")}</td>
      <td>${result}</td>
      <td>
        <button class="detail-btn ghost" data-run="${escapeHtml(run.run_id)}">상세 →</button>
        ${delBtn}
      </td>`;
    tbody.appendChild(tr);
  }
  $$(".detail-btn").forEach((b) => {
    b.addEventListener("click", () => openDetail(b.dataset.run));
  });
  $$(".del-run-btn").forEach((b) => {
    if (b.disabled) return;
    b.addEventListener("click", async () => {
      if (!confirm(`실행 결과 '${b.dataset.run}' 을 삭제할까요?\n(스크린샷·trace·리포트 산출물이 모두 제거됩니다.)`)) return;
      const r = await fetch(`/api/runs/${encodeURIComponent(b.dataset.run)}`, { method: "DELETE" });
      if (r.ok) {
        _selectedRuns.delete(b.dataset.run);
        loadRuns();
      } else {
        const body = await r.json().catch(() => ({}));
        alert(`삭제 실패: HTTP ${r.status} — ${body.detail || ""}`);
      }
    });
  });
  $$(".run-check").forEach((c) => {
    c.addEventListener("change", () => {
      if (c.checked) _selectedRuns.add(c.dataset.run);
      else _selectedRuns.delete(c.dataset.run);
      syncBulkDeleteRunsState();
    });
  });
  syncBulkDeleteRunsState();
}

function syncBulkDeleteRunsState() {
  const checks = $$(".run-check:not([disabled])");
  const checked = Array.from(checks).filter((c) => c.checked);
  const btn = $("#btn-bulk-delete-runs");
  if (btn) {
    btn.disabled = checked.length === 0;
    btn.textContent = checked.length === 0
      ? "🗑 선택 삭제"
      : `🗑 선택 삭제 (${checked.length})`;
  }
  const selectAll = $("#runs-select-all");
  if (selectAll) {
    selectAll.checked = checks.length > 0 && checked.length === checks.length;
    selectAll.indeterminate = checked.length > 0 && checked.length < checks.length;
  }
}

async function bulkDeleteRuns() {
  const ids = Array.from($$(".run-check:not([disabled])"))
    .filter((c) => c.checked)
    .map((c) => c.dataset.run);
  if (ids.length === 0) return;
  const sample = ids.slice(0, 5).join(", ") + (ids.length > 5 ? ` 외 ${ids.length - 5}개` : "");
  if (!confirm(`선택한 실행 결과 ${ids.length}개를 삭제할까요?\n(스크린샷·trace·리포트 산출물이 모두 제거됩니다.)\n${sample}`)) return;
  const btn = $("#btn-bulk-delete-runs");
  if (btn) btn.disabled = true;
  const failed = [];
  for (const id of ids) {
    try {
      const r = await fetch(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!r.ok && r.status !== 404) failed.push(id);
      else _selectedRuns.delete(id);
    } catch {
      failed.push(id);
    }
  }
  if (failed.length > 0) {
    alert(`다음 실행 결과 삭제 실패:\n${failed.join("\n")}`);
  }
  loadRuns();
}

function renderResult(run) {
  const code = run.exit_code;
  if (code === 0) return "<span class='ok'>✓ PASS</span>";
  if (code === 1) return "<span class='fail'>✗ FAIL</span>";
  if (code === 2) return "<span class='warn'>⚠ 시스템 오류</span>";
  if (code === 3) return "<span class='warn'>⚠ 로그인 만료</span>";
  if (run.state === "running") return "<span class='running'>… 진행중</span>";
  return "<span class='muted'>-</span>";
}

// --- Run 상세 ---------------------------------------------------------------

async function openDetail(runId) {
  const meta = await (await fetch(`/api/runs/${encodeURIComponent(runId)}`)).json();
  const stepsBody = await (await fetch(`/api/runs/${encodeURIComponent(runId)}/steps`)).json();
  const steps = stepsBody.steps || [];

  $("#detail-title").textContent = `${meta.script || meta.bundle || runId}`;
  const provenance = meta.script_provenance || {};
  $("#detail-meta").innerHTML = [
    `결과: ${renderResult(meta)}`,
    `소요: ${meta.started_at ? "" : "-"}${meta.finished_at && meta.started_at ? " (" + meta.started_at + " → " + meta.finished_at + ")" : ""}`,
    `프로파일: ${escapeHtml(meta.alias || "-")}`,
    `스크립트: ${escapeHtml(provenance.source_file || "-")} (${escapeHtml(provenance.source_kind || "-")})`,
  ].join("&nbsp;&nbsp;|&nbsp;&nbsp;");
  $("#detail-report").href = `/api/runs/${encodeURIComponent(runId)}/report.html`;

  const ul = $("#detail-steps");
  ul.innerHTML = "";
  let firstShot = "";
  for (const step of steps) {
    const li = document.createElement("li");
    const status = (step.status || "").toLowerCase();
    li.className = `step ${status}`;
    li.innerHTML = `
      <span class="step-no">${step.step}</span>
      <span class="step-act">${escapeHtml(step.action || "-")}</span>
      <span class="step-target">${escapeHtml(step.target || "")}</span>
      <span class="step-status">${escapeHtml(step.status || "")}</span>`;
    li.dataset.shot = step.screenshot || "";
    li.addEventListener("click", () => {
      const shot = li.dataset.shot;
      if (shot) showShot(runId, shot);
      $$("#detail-steps li").forEach((x) => x.classList.remove("active"));
      li.classList.add("active");
    });
    ul.appendChild(li);
    if (!firstShot && step.screenshot) firstShot = step.screenshot;
  }
  if (firstShot) showShot(runId, firstShot);
  else $("#detail-shot").src = "";
  $("#detail-modal").hidden = false;
}

function showShot(runId, name) {
  $("#detail-shot").src = `/api/runs/${encodeURIComponent(runId)}/screenshot/${encodeURIComponent(name)}`;
}

// --- 모달 close 일괄 ----------------------------------------------------------

document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-modal-close]");
  if (!t) return;
  const id = t.getAttribute("data-modal-close");
  document.getElementById(id).hidden = true;
});

// --- 헤더 / 카드 액션 wire ----------------------------------------------------

$("#btn-refresh-profiles")?.addEventListener("click", loadProfiles);
$("#btn-refresh-runs")?.addEventListener("click", loadRuns);
$("#btn-bulk-delete-runs")?.addEventListener("click", bulkDeleteRuns);
$("#runs-select-all")?.addEventListener("change", (ev) => {
  const on = ev.target.checked;
  $$(".run-check:not([disabled])").forEach((c) => {
    c.checked = on;
    if (on) _selectedRuns.add(c.dataset.run);
    else _selectedRuns.delete(c.dataset.run);
  });
  syncBulkDeleteRunsState();
});

$("#btn-add-alias")?.addEventListener("click", () => openSeedInput());

$("#seed-input-form")?.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const payload = {
    name: (fd.get("name") || "").trim(),
    seed_url: (fd.get("seed_url") || "").trim(),
    verify_service_url: (fd.get("verify_service_url") || "").trim(),
    verify_service_text: (fd.get("verify_service_text") || "").trim(),
    naver_probe: fd.get("naver_probe") === "on",
    // 빈 문자열은 서버에서 None 으로 정규화 — IdP 검증 skip.
    idp_domain: (fd.get("idp_domain") || "").trim(),
    ttl_hint_hours: parseInt(fd.get("ttl_hint_hours") || "12", 10),
    timeout_sec: 600,
  };
  startSeedFlow(payload);
});

$("#btn-seed-cancel")?.addEventListener("click", () => {
  if (_seedPollTimer) {
    clearInterval(_seedPollTimer);
    _seedPollTimer = null;
  }
  $("#seed-progress-modal").hidden = true;
  // 다시 입력 분기 — error 후 사용자가 값을 고쳐 다시 시도.
  if ($("#btn-seed-cancel").dataset.action === "retry" && _lastSeedPayload) {
    openSeedInput(_lastSeedPayload);
  }
});

$("#btn-seed-done")?.addEventListener("click", () => {
  $("#seed-progress-modal").hidden = true;
});

$("#btn-seed-expired-reseed")?.addEventListener("click", () => {
  const name = $("#seed-expired-name").textContent.trim();
  $("#seed-expired-modal").hidden = true;
  if (name && name !== "—") openReseed(name);
});


// ── D17 — 시나리오 스크립트 카드 (.py 일원화) ────────────────────────────────

async function loadScripts() {
  let data;
  try {
    const r = await fetch("/api/scripts");
    data = await r.json();
  } catch {
    return;
  }
  const tbody = $("#scripts-tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  refreshScriptProfileSelect();
  // 목록에 더 이상 존재하지 않는 이름은 선택 Set 에서도 제거.
  const present = new Set((data || []).map((s) => s.name));
  for (const n of Array.from(_selectedScripts)) {
    if (!present.has(n)) _selectedScripts.delete(n);
  }
  if (!Array.isArray(data) || data.length === 0) {
    tbody.innerHTML = '<tr class="muted"><td colspan="5">— 등록된 스크립트 없음 —</td></tr>';
    syncBulkDeleteState();
    return;
  }
  for (const s of data) {
    const tr = document.createElement("tr");
    const checked = _selectedScripts.has(s.name) ? "checked" : "";
    tr.innerHTML = `
      <td class="col-check"><input type="checkbox" class="script-check" data-name="${escapeHtml(s.name)}" ${checked} /></td>
      <td><strong>${escapeHtml(s.name)}</strong></td>
      <td>${fmtTime(s.uploaded_at)}</td>
      <td>${fmtBytes(s.size)}</td>
      <td>
        <button class="run-script-btn primary" data-name="${escapeHtml(s.name)}" title="단일 .py 시나리오 실행">▶ 실행</button>
        <button class="del-script-btn ghost" data-name="${escapeHtml(s.name)}">🗑</button>
      </td>`;
    tbody.appendChild(tr);
  }
  $$(".run-script-btn").forEach((b) => {
    b.addEventListener("click", () => startRunScript(b.dataset.name));
  });
  $$(".del-script-btn").forEach((b) => {
    b.addEventListener("click", async () => {
      if (!confirm(`스크립트 '${b.dataset.name}' 을 삭제할까요?`)) return;
      const r = await fetch(`/api/scripts/${encodeURIComponent(b.dataset.name)}`, { method: "DELETE" });
      if (r.ok) {
        _selectedScripts.delete(b.dataset.name);
        loadScripts();
      }
    });
  });
  $$(".script-check").forEach((c) => {
    c.addEventListener("change", () => {
      if (c.checked) _selectedScripts.add(c.dataset.name);
      else _selectedScripts.delete(c.dataset.name);
      syncBulkDeleteState();
    });
  });
  syncBulkDeleteState();
}

function syncBulkDeleteState() {
  const checks = $$(".script-check");
  const checked = Array.from(checks).filter((c) => c.checked);
  const btn = $("#btn-bulk-delete-scripts");
  if (btn) {
    btn.disabled = checked.length === 0;
    btn.textContent = checked.length === 0
      ? "🗑 선택 삭제"
      : `🗑 선택 삭제 (${checked.length})`;
  }
  const selectAll = $("#scripts-select-all");
  if (selectAll) {
    selectAll.checked = checks.length > 0 && checked.length === checks.length;
    selectAll.indeterminate = checked.length > 0 && checked.length < checks.length;
  }
}

async function bulkDeleteScripts() {
  const names = Array.from($$(".script-check"))
    .filter((c) => c.checked)
    .map((c) => c.dataset.name);
  if (names.length === 0) return;
  const sample = names.slice(0, 5).join(", ") + (names.length > 5 ? ` 외 ${names.length - 5}개` : "");
  if (!confirm(`선택한 스크립트 ${names.length}개를 삭제할까요?\n${sample}`)) return;
  const btn = $("#btn-bulk-delete-scripts");
  if (btn) btn.disabled = true;
  const failed = [];
  for (const name of names) {
    try {
      const r = await fetch(`/api/scripts/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!r.ok && r.status !== 404) failed.push(name);
      else _selectedScripts.delete(name);
    } catch {
      failed.push(name);
    }
  }
  if (failed.length > 0) {
    alert(`다음 스크립트 삭제 실패:\n${failed.join("\n")}`);
  }
  loadScripts();
}

async function uploadScript(file, overwrite = false) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`/api/scripts?overwrite=${overwrite ? 1 : 0}`, {
    method: "POST",
    body: fd,
  });
  if (r.status === 409) {
    if (confirm(`같은 이름의 스크립트 '${file.name}' 이 이미 있습니다. 덮어쓸까요?`)) {
      return uploadScript(file, true);
    }
    return;
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    alert(`업로드 실패: HTTP ${r.status} — ${body.detail || ""}`);
    return;
  }
  loadScripts();
}

function refreshScriptProfileSelect() {
  const sel = $("#run-script-profile-select");
  if (!sel) return;
  const previous = sel.value;
  // 첫 옵션 = "(비로그인)" 은 HTML 에 박혀 있으므로 그 이후만 갱신.
  // 기존 dynamic option 제거.
  const fixed = sel.querySelector('option[value=""]');
  sel.innerHTML = "";
  if (fixed) sel.appendChild(fixed); else {
    const o = document.createElement("option");
    o.value = ""; o.textContent = "(비로그인 — storage_state 미주입)";
    sel.appendChild(o);
  }
  for (const p of _availableProfiles) {
    const opt = document.createElement("option");
    opt.value = p.alias;
    opt.textContent = `${p.alias}  (${p.service_domain || "—"})`;
    sel.appendChild(opt);
  }
  // previous 가 여전히 유효하면 복원, 아니면 "(비로그인)" default.
  sel.value = (previous && Array.from(sel.options).some(o => o.value === previous))
    ? previous : "";
}

async function startRunScript(scriptName) {
  if (_currentEventSource) {
    _currentEventSource.close();
    _currentEventSource = null;
  }
  $("#run-stream").textContent = "";
  const alias = $("#run-script-profile-select")?.value || "";
  const verifyUrl = $("#run-script-verify-url")?.value?.trim() || "";
  const headed = !!$("#run-script-headed-toggle")?.checked;
  // 슬로모 — 체크박스가 켜져 있고 양수일 때만 payload 에 실음.
  let slowMoMs = null;
  const slowEnabled = !!$("#run-script-slowmo-enabled")?.checked;
  if (slowEnabled) {
    const n = Number($("#run-script-slowmo-ms")?.value || 0);
    if (Number.isFinite(n) && n > 0) slowMoMs = n;
  }
  const aliasNote = alias ? `  (프로파일: ${alias})` : "  (비로그인)";
  const slowNote = slowMoMs ? `  (slow_mo=${slowMoMs}ms)` : "";
  $("#run-status").textContent = `시작 중: ${scriptName}${aliasNote}${headed ? "  (화면 표시)" : ""}${slowNote} ...`;
  let resp;
  try {
    resp = await fetch("/api/runs/script", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        script_name: scriptName,
        alias: alias || null,
        verify_url: verifyUrl || null,
        headed: headed,
        slow_mo_ms: slowMoMs,
      }),
    });
  } catch (e) {
    $("#run-status").textContent = `실패: ${e.message}`;
    return;
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    $("#run-status").textContent = `실패: HTTP ${resp.status} — ${body.detail || ""}`;
    return;
  }
  const r = await resp.json();
  $("#run-status").textContent = `진행중: ${r.script} (run_id=${r.run_id})`;
  _currentEventSource = new EventSource(`/api/runs/${encodeURIComponent(r.run_id)}/stream`);
  _currentEventSource.onmessage = (ev) => {
    const pre = $("#run-stream");
    pre.textContent += ev.data + "\n";
    pre.scrollTop = pre.scrollHeight;
  };
  _currentEventSource.addEventListener("done", () => {
    $("#run-status").textContent = `완료: ${r.script} (run_id=${r.run_id})`;
    _currentEventSource.close();
    _currentEventSource = null;
    loadRuns();
  });
  _currentEventSource.onerror = () => {
    if (_currentEventSource) {
      _currentEventSource.close();
      _currentEventSource = null;
    }
  };
}

$("#btn-upload-script")?.addEventListener("click", () => $("#script-file").click());
$("#script-file")?.addEventListener("change", (ev) => {
  const f = ev.target.files?.[0];
  if (f) uploadScript(f);
  ev.target.value = "";
});
$("#btn-refresh-scripts")?.addEventListener("click", loadScripts);
$("#btn-bulk-delete-scripts")?.addEventListener("click", bulkDeleteScripts);
$("#scripts-select-all")?.addEventListener("change", (ev) => {
  const on = ev.target.checked;
  $$(".script-check").forEach((c) => {
    c.checked = on;
    if (on) _selectedScripts.add(c.dataset.name);
    else _selectedScripts.delete(c.dataset.name);
  });
  syncBulkDeleteState();
});

// 슬로모 체크박스 ↔ 숫자 입력 활성화 토글 (Recording UI 와 동일 패턴).
(function _wireScriptSlowmoToggle() {
  const cb = document.getElementById("run-script-slowmo-enabled");
  const num = document.getElementById("run-script-slowmo-ms");
  if (!cb || !num) return;
  const sync = () => { num.disabled = !cb.checked; };
  cb.addEventListener("change", sync);
  sync();
})();

$("#btn-wizard")?.addEventListener("click", () => {
  $("#wizard-modal").hidden = false;
});

// --- 초기 로드 + 폴링 ---------------------------------------------------------

loadProfiles();
loadScripts();
loadRuns();
setInterval(loadProfiles, 10000);
setInterval(loadScripts, 10000);
setInterval(loadRuns, 5000);
// loadProfiles 가 _availableProfiles 를 채운 직후 select 동기화.
setTimeout(refreshScriptProfileSelect, 500);
setInterval(refreshScriptProfileSelect, 10000);

// --- util --------------------------------------------------------------------

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
