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

// --- Bundle ------------------------------------------------------------------

async function loadBundles() {
  let data;
  try {
    const r = await fetch("/api/bundles");
    data = await r.json();
  } catch {
    return;
  }
  const tbody = $("#bundles-tbody");
  tbody.innerHTML = "";
  if (!Array.isArray(data) || data.length === 0) {
    tbody.innerHTML = '<tr class="muted"><td colspan="5">— 등록된 시나리오 묶음 없음 —</td></tr>';
    return;
  }
  for (const b of data) {
    const tr = document.createElement("tr");
    const runDisabled = !b.seeded;
    const tooltip = runDisabled ? `프로파일 '${b.alias}' 의 로그인 등록이 필요합니다` : "";
    tr.innerHTML = `
      <td><strong>${escapeHtml(b.name)}</strong></td>
      <td>${escapeHtml(b.alias || "-")}</td>
      <td>${fmtTime(b.uploaded_at)}</td>
      <td>${fmtBytes(b.size)}</td>
      <td>
        <button class="run-btn primary" data-name="${escapeHtml(b.name)}"
                ${runDisabled ? "disabled" : ""} title="${escapeHtml(tooltip)}">▶ 실행</button>
        <button class="del-bundle-btn ghost" data-name="${escapeHtml(b.name)}">🗑</button>
      </td>`;
    tbody.appendChild(tr);
  }
  $$(".run-btn").forEach((b) => {
    b.addEventListener("click", () => startRun(b.dataset.name));
  });
  $$(".del-bundle-btn").forEach((b) => {
    b.addEventListener("click", async () => {
      if (!confirm(`시나리오 묶음 '${b.dataset.name}' 을 삭제할까요?`)) return;
      const r = await fetch(`/api/bundles/${encodeURIComponent(b.dataset.name)}`, { method: "DELETE" });
      if (r.ok) loadBundles();
    });
  });
}

async function uploadBundle(file, overwrite = false) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`/api/bundles?overwrite=${overwrite ? 1 : 0}`, {
    method: "POST",
    body: fd,
  });
  if (r.status === 409) {
    if (confirm(`같은 이름의 시나리오 묶음 '${file.name}' 이 이미 있습니다. 덮어쓸까요?`)) {
      return uploadBundle(file, true);
    }
    return;
  }
  if (!r.ok) {
    alert(`업로드 실패: HTTP ${r.status}`);
    return;
  }
  loadBundles();
}

// --- Run ---------------------------------------------------------------------

let _currentEventSource = null;

async function startRun(bundleName) {
  if (_currentEventSource) {
    _currentEventSource.close();
    _currentEventSource = null;
  }
  $("#run-stream").textContent = "";
  $("#run-status").textContent = `시작 중: ${bundleName} ...`;
  let resp;
  try {
    resp = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bundle_name: bundleName }),
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
  $("#run-status").textContent = `진행중: ${r.bundle} (run_id=${r.run_id})`;
  // SSE 스트림 구독.
  _currentEventSource = new EventSource(`/api/runs/${encodeURIComponent(r.run_id)}/stream`);
  _currentEventSource.onmessage = (ev) => {
    const pre = $("#run-stream");
    pre.textContent += ev.data + "\n";
    pre.scrollTop = pre.scrollHeight;
  };
  _currentEventSource.addEventListener("done", () => {
    $("#run-status").textContent = `완료: ${r.bundle} (run_id=${r.run_id})`;
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
  if (!Array.isArray(data) || data.length === 0) {
    tbody.innerHTML = '<tr class="muted"><td colspan="5">— 실행 결과 없음 —</td></tr>';
    return;
  }
  for (const run of data) {
    const tr = document.createElement("tr");
    const result = renderResult(run);
    tr.innerHTML = `
      <td>${fmtTime(run.started_at)}</td>
      <td>${escapeHtml(run.bundle || "-")}</td>
      <td>${escapeHtml(run.alias || "-")}</td>
      <td>${result}</td>
      <td><button class="detail-btn ghost" data-run="${escapeHtml(run.run_id)}">상세 →</button></td>`;
    tbody.appendChild(tr);
  }
  $$(".detail-btn").forEach((b) => {
    b.addEventListener("click", () => openDetail(b.dataset.run));
  });
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

  $("#detail-title").textContent = `${meta.bundle || runId}`;
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
$("#btn-refresh-bundles")?.addEventListener("click", loadBundles);
$("#btn-refresh-runs")?.addEventListener("click", loadRuns);

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

$("#btn-upload-bundle")?.addEventListener("click", () => $("#bundle-file").click());
$("#bundle-file")?.addEventListener("change", (ev) => {
  const f = ev.target.files?.[0];
  if (f) uploadBundle(f);
  ev.target.value = "";  // reset.
});

$("#btn-wizard")?.addEventListener("click", () => {
  $("#wizard-modal").hidden = false;
});

// --- 초기 로드 + 폴링 ---------------------------------------------------------

loadProfiles();
loadBundles();
loadRuns();
setInterval(loadProfiles, 10000);
setInterval(loadBundles, 10000);
setInterval(loadRuns, 5000);

// --- util --------------------------------------------------------------------

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
