// Replay UI — 정적 클라이언트.
//
// 주요 흐름:
// - 5초 폴링으로 profiles / bundles / runs 갱신
// - 시드 / 실행 subprocess 진행상황은 별도 polling
// - Run 상세는 모달 + 스텝/스크린샷 lightbox
// - 글로벌 알람 인디케이터 = 시드 missing alias 수

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
    tbody.innerHTML = '<tr class="muted"><td colspan="5">— 등록된 alias 없음 —</td></tr>';
  } else {
    for (const p of data) {
      const tr = document.createElement("tr");
      const storageOk = p.storage === "ok";
      if (!storageOk) expiredCount += 1;
      tr.innerHTML = `
        <td><strong>${escapeHtml(p.alias)}</strong></td>
        <td>${storageOk ? "<span class='ok'>시드됨</span>" : "<span class='expired'>🔴 시드 필요</span>"}</td>
        <td>${escapeHtml(p.last_verified_at || "-")}</td>
        <td>${escapeHtml(p.service_domain || "-")}</td>
        <td>
          <button class="reseed-btn ghost" data-alias="${escapeHtml(p.alias)}">↻ Re-seed</button>
          <button class="del-profile-btn ghost" data-alias="${escapeHtml(p.alias)}">🗑</button>
        </td>`;
      tbody.appendChild(tr);
    }
  }
  // 글로벌 알람 인디케이터 (B3).
  const badge = $("#alarm-badge");
  if (expiredCount > 0) {
    badge.textContent = `🔴 ${expiredCount} 시드 만료`;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
  // 알라이아스 별 액션 wire.
  $$(".reseed-btn").forEach((b) => {
    b.addEventListener("click", () => {
      const alias = b.dataset.alias;
      const url = prompt(`alias '${alias}' 의 target URL 을 입력하세요`, "https://");
      if (url) startSeed(alias, url);
    });
  });
  $$(".del-profile-btn").forEach((b) => {
    b.addEventListener("click", async () => {
      if (!confirm(`alias '${b.dataset.alias}' 삭제할까요?`)) return;
      const r = await fetch(`/api/profiles/${encodeURIComponent(b.dataset.alias)}`, { method: "DELETE" });
      if (r.ok) loadProfiles();
      else alert(`삭제 실패: HTTP ${r.status}`);
    });
  });
}

async function startSeed(alias, target) {
  const r = await fetch(`/api/profiles/${encodeURIComponent(alias)}/seed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_url: target }),
  });
  if (!r.ok) {
    alert(`시드 시작 실패: HTTP ${r.status}`);
    return;
  }
  $("#seed-modal").hidden = false;
  $("#seed-message").textContent = "브라우저에서 직접 로그인 후 창을 닫아 주세요.";
  pollSeedStatus(alias);
}

async function pollSeedStatus(alias) {
  const interval = setInterval(async () => {
    const r = await fetch(`/api/profiles/${encodeURIComponent(alias)}/seed/status`);
    if (!r.ok) {
      clearInterval(interval);
      return;
    }
    const st = await r.json();
    if (st.finished) {
      clearInterval(interval);
      $("#seed-message").textContent = st.message || "완료";
      setTimeout(() => {
        $("#seed-modal").hidden = true;
        loadProfiles();
      }, 1500);
    } else if (st.message) {
      $("#seed-message").textContent = st.message;
    }
  }, 1500);
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
    tbody.innerHTML = '<tr class="muted"><td colspan="5">— 등록된 bundle 없음 —</td></tr>';
    return;
  }
  for (const b of data) {
    const tr = document.createElement("tr");
    const runDisabled = !b.seeded;
    const tooltip = runDisabled ? `alias '${b.alias}' 시드 필요` : "";
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
      if (!confirm(`bundle '${b.dataset.name}' 삭제할까요?`)) return;
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
    if (confirm(`'${file.name}' 이미 존재 — 덮어쓸까요?`)) {
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
  if (code === 3) return "<span class='warn'>⚠ 시드 만료</span>";
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
    `alias: ${escapeHtml(meta.alias || "-")}`,
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

$("#btn-add-alias")?.addEventListener("click", () => {
  $("#alias-name").value = "";
  $("#alias-target").value = "https://";
  $("#alias-modal").hidden = false;
});
$("#alias-confirm")?.addEventListener("click", () => {
  const name = $("#alias-name").value.trim();
  const target = $("#alias-target").value.trim();
  if (!name || !target) {
    alert("alias 이름과 target URL 을 입력해 주세요");
    return;
  }
  $("#alias-modal").hidden = true;
  startSeed(name, target);
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
