// Recording UI — minimal i18n.
// Default language is Korean (existing inline text in index.html).
// When lang=en, swap textContent / title / placeholder from the dictionary.
// Persists in localStorage("ui.lang"). Toggle button is rendered into #lang-toggle.

(function () {
  const STORAGE_KEY = "ui.lang";
  const DEFAULT_LANG = "ko";

  // English translations. Keys are stable identifiers, not Korean text.
  // Missing keys fall back to the original Korean inline text — never to the key.
  const EN = {
    // ── document ─────────────────────────────────────────────────────────────
    "doc.title": "Recording UI",

    // ── header ───────────────────────────────────────────────────────────────
    "header.back.label": "← Back",
    "header.back.title": "Back to previous page (e.g. Jenkins)",
    "header.title": "📹 Recording UI",
    "header.replay.label": "🎬 Replay UI ↗",
    "header.replay.title": "Open Replay UI in a new tab (port 18093)",
    "header.health.checking": "checking…",
    "header.lang.ko": "KO",
    "header.lang.en": "EN",
    "header.lang.title": "Switch interface language",

    // ── login profiles ───────────────────────────────────────────────────────
    "login.section.title": "🔐 Login Profiles",
    "login.section.desc":
      "Seed, verify, and delete login sessions for the service under test. " +
      "Seeded profiles can be reused from the Recording and Discover URLs " +
      "sections. The catalog is shared with Replay UI — profiles seeded on " +
      "either side appear on both.",
    "login.btn.new": "+ New profile",
    "login.btn.new.title":
      "Seed a new auth session — log in manually and pass 2FA in a dedicated browser window",
    "login.btn.refresh": "↻ Refresh",
    "login.btn.refresh.title": "Refresh login profile list",
    "login.col.name": "Profile name",
    "login.col.state": "Login state",
    "login.col.last": "Last checked",
    "login.col.site": "Site",
    "login.col.actions": "Actions",
    "login.row.loading": "— loading —",

    // ── login: seed dialog ───────────────────────────────────────────────────
    "login.seed.title": "Create new auth session",
    "login.seed.desc":
      "Run this once (re-seed when expired). A human logs in to Naver and " +
      "completes 2FA; the resulting session is saved and reused automatically " +
      "for later recording/replay.",
    "login.seed.name": "Name",
    "login.seed.name.title": "Letters/digits/_/- 1–64 chars; first char must be a letter or digit",
    "login.seed.url": "Start URL",
    "login.seed.url.note": "(⚠️ entry page of the *service under test* — NOT the Naver login page)",
    "login.seed.verifyUrl": "Verify URL",
    "login.seed.verifyText": "Verify text",
    "login.seed.verifyText.note": "(optional — leave blank to only check that the Verify URL loads)",
    "login.seed.verifyText.placeholder": "Welcome, QA Kim",
    "login.seed.ttl": "TTL hint (hours) — recommended: assume one re-seed per day",
    "login.seed.idp": "IdP domain",
    "login.seed.idp.note": "(optional — leave blank to skip IdP verification)",
    "login.seed.idp.title":
      "External IdP domain used for 2FA (e.g. naver.com / kakao.com / accounts.google.com). " +
      "Leave blank if the site uses only ID/password.",
    "login.seed.naverProbe": "Naver-side weak probe (best-effort, only meaningful when IdP=naver.com)",
    "login.seed.warning": "⚠ Do not use production accounts — use a test-only account",
    "login.seed.cancel": "Cancel",
    "login.seed.submit": "Open →",

    // ── login: seed progress ─────────────────────────────────────────────────
    "login.progress.title": "🪟 Saving login session",
    "login.progress.step1": "Click [Log in with Naver] on the target service",
    "login.progress.step2": "Enter your ID and password on the Naver login screen",
    "login.progress.step3": "Pass 2FA (SMS, etc.) yourself",
    "login.progress.step4": "Service redirects back → verify the target page loads",
    "login.progress.step5":
      "<strong>Confirm the post-login screen, then close the opened browser window</strong>",
    "login.progress.status.waiting": "⏳ Waiting for user input",
    "login.progress.elapsed": "Elapsed 0s / limit 600s",
    "login.progress.hint":
      "When the browser window closes, the session is saved, the verification page " +
      "is shown briefly, and the flow completes.",
    "login.progress.cancel": "Cancel (close the window yourself)",
    "login.progress.skip": "Don't use",
    "login.progress.done": "Use this profile",

    // ── login: expired ───────────────────────────────────────────────────────
    "login.expired.title": "⚠ Auth session expired",
    "login.expired.body.prefix": "Auth session ",
    "login.expired.body.suffix": " has expired.",
    "login.expired.reason": "Reason: session expired or IP changed.",
    "login.expired.hint":
      "Re-seeding preserves your Start URL / Verify URL / Verify text inputs.",
    "login.expired.cancel": "Cancel",
    "login.expired.reseed": "Re-seed",

    // ── login: machine mismatch ──────────────────────────────────────────────
    "login.mm.title": "⚠ Machine mismatch warning",
    "login.mm.body":
      "This auth session was seeded on a different machine. Naver invalidates " +
      "sessions aggressively when IP / device fingerprint changes — re-seeding " +
      "may be required almost immediately.",
    "login.mm.hint": "Recommended: seed fresh on this machine.",
    "login.mm.cancel": "Cancel",
    "login.mm.proceed": "Try anyway",
    "login.mm.reseed": "Seed fresh on this machine",

    // ── discover urls ────────────────────────────────────────────────────────
    "discover.section.title": "🔍 Discover URLs",
    "discover.section.desc":
      "Given one start URL and (optionally) a login profile, collect in-site " +
      "links via BFS. Receive results as CSV/JSON, and optionally generate a " +
      "Python Playwright tour script that walks the selected URLs.",
    "discover.seedUrl": "Start URL",
    "discover.authProfile": "Login profile",
    "discover.authProfile.opt": "(optional)",
    "discover.authProfile.none": "(none)",
    "discover.maxPages": "Max pages",
    "discover.maxDepth": "Max depth",
    "discover.adv.summary": "Advanced options",
    "discover.adv.sitemap": "Use sitemap.xml / robots.txt Sitemap (default ON)",
    "discover.adv.requests": "Also collect same-host request URLs (default ON)",
    "discover.adv.spa": "Collect SPA-signal selectors (data-href, role=link, etc.)",
    "discover.adv.ignoreQuery": "Ignore URL query strings (collapse pagination/filter variants)",
    "discover.adv.subdomains": "Include subdomains of the same root domain",
    "discover.btn.start": "Discover URLs",
    "discover.btn.cancel": "Cancel",
    "discover.status.idle": "— idle —",
    "discover.csv": "⬇ Download CSV",
    "discover.selectAll": "Select all",
    "discover.selectNone": "Clear selection",
    "discover.tourScript": "Generate Tour Script for selected URLs",
    "discover.headless.title":
      "Default OFF — a browser window opens so you can watch progress. " +
      "When ON, runs headless in the background.",
    "discover.headless": "Headless (background) run",
    "discover.settle.title":
      "How long to wait for content to settle before each screenshot. " +
      "'Strict' waits until network is idle AND DOM stops changing — " +
      "reduces blank screenshots but adds 0.5–5s per URL.",
    "discover.settle.label": "Content-settle wait",
    "discover.settle.off": "Off (fast)",
    "discover.settle.network": "Normal (network)",
    "discover.settle.strict": "Strict (network + DOM)",
    "discover.selectedCount": "0 selected",
    "discover.tree.title": "Site hierarchy tree",
    "discover.tree.download": "📤 Download tree (HTML)",
    "discover.tree.download.title":
      "Download both Crawl topology and URL path trees as a single shareable HTML",
    "discover.tree.tab.crawl": "Crawl topology",
    "discover.tree.tab.path": "URL path",
    "discover.tree.desc":
      "<strong>Crawl topology</strong> — which page discovered which link (BFS as-is). " +
      "<strong>URL path</strong> — IA grouped by <code>/a/b/c</code> path segments. " +
      "SPA / query-based identifiers may be inaccurate.",
    "discover.tree.loading": "— loading —",

    // ── recording ────────────────────────────────────────────────────────────
    "recording.section.title": "🎬 Recording",
    "recording.hoverHint":
      "💡 <strong>Tip — recording hover menus (dropdown / GNB)</strong> — codegen " +
      "records <em>clicks only</em>, never mouse hover. <strong>Hover, do not " +
      "click, the parent menu</strong> (clicking navigates the page and ruins " +
      "the scenario). When the submenu opens, click only the leaf item you want. " +
      "Multi-level menus (2–3 levels) work the same — only the leaf click is " +
      "recorded, and on replay the visibility healer cascade-hovers the parents " +
      "automatically.",
    "recording.targetUrl": "target_url",
    "recording.targetUrl.placeholder":
      "https://example.com/login or file:///app/test/fixtures/click.html",
    "recording.planning": "planning_doc_ref",
    "recording.planning.opt": "(optional)",
    "recording.auth": "Login profile",
    "recording.auth.opt": "(optional — register/verify/delete in the \"Login Profiles\" section above)",
    "recording.auth.none": "(none — anonymous recording)",
    "recording.btn.start": "▶ Start Recording",
    "recording.active.title": "Recording in progress",
    "recording.active.sid": "Session ID",
    "recording.active.state": "state",
    "recording.active.elapsed": "Elapsed",
    "recording.active.elapsed.init": "0s",
    "recording.btn.stop": "■ Stop & Convert",
    "recording.active.note":
      "Clicking Stop terminates codegen cleanly and runs the container-side " +
      "conversion. May take a moment.",

    // ── play & more ──────────────────────────────────────────────────────────
    "rplus.section.title": "▶️ Play & more",
    "rplus.upload.title": "Upload new script",
    "rplus.upload.desc":
      "Upload an existing Playwright .py to register a new session and replay it immediately.",
    "rplus.upload.btn": "📁 Play Script from File",
    "rplus.upload.btn.title":
      "Upload an existing Playwright .py script and replay it directly (bypasses codegen recording)",
    "rplus.session.prefix": "For the current session (",
    "rplus.session.suffix": "):",
    "rplus.session.empty":
      "— no session selected. Upload a .py above or pick one from 'Recent sessions'.",
    "rplus.opt.auth": "Login profile",
    "rplus.opt.auth.default": "(use session default)",
    "rplus.opt.auth.none": "(run without auth)",
    "rplus.opt.headed": "Show window (headed)",
    "rplus.opt.slowmo.title":
      "Delay between actions. Useful for visual debugging, but cumulative — " +
      "total run time grows with the number of actions.",
    "rplus.opt.slowmo": "Delay between actions",
    "rplus.opt.slowmo.ms": "ms",
    "rplus.btn.play": "▶ Play",
    "rplus.btn.play.codegen": "▶ Run test code as recorded",
    "rplus.btn.play.codegen.title":
      "Run codegen-recorded or uploaded test code on the host as-is " +
      "(codegen runs inject hover annotations; uploaded scripts bypass that)",
    "rplus.btn.play.llm": "▶ Run LLM-applied code",
    "rplus.btn.play.llm.title":
      "Run the 14-DSL converted scenario via executor (healing/verify/mock active, headed)",
    "rplus.btn.doc": "📝 Generate Doc",
    "rplus.btn.doc.enrich": "📝 Generate scenario from code",
    "rplus.btn.doc.enrich.title":
      "Use Ollama to reverse-engineer an IEEE 829-lite test plan (TR.5)",
    "rplus.btn.doc.compare": "⚖ Compare scenario doc ↔ JSON",
    "rplus.btn.doc.compare.title":
      "Upload doc-DSL JSON and run semantic comparison + 5-category HTML report (TR.6)",
    "rplus.output.title": "Run result",
    "rplus.output.copy": "📋 Copy",
    "rplus.output.copy.title": "Copy run result text to clipboard",
    "rplus.output.copied": "✓ Copied",
    "rplus.progress.summary": "Live progress log",

    // ── results area ─────────────────────────────────────────────────────────
    "result.area.title": "📊 Results and step additions",
    "result.meta.title": "Result",
    "result.meta.sid": "Session ID",
    "result.meta.state": "state",
    "result.meta.steps": "Step count",
    "result.meta.path": "scenario.json",
    "result.meta.auth": "Auth profile",

    // scenario card
    "scenario.title": "Scenario JSON",
    "scenario.copy.title": "Copy to clipboard",
    "scenario.copy": "📋 Copy",
    "scenario.copied": "✓ Copied",
    "scenario.download": "⬇ Download",
    "scenario.empty": "— scenario.json not loaded —",
    "scenario.toggle": "▾ Expand all",
    "scenario.healed.title": "After self-healing (scenario.healed.json)",
    "scenario.healed.desc":
      "Scenario after self-healing during ▶ <strong>Run LLM-applied code</strong> — " +
      "reflects selector substitutions, step additions, etc. Shown only when different from the original.",
    "scenario.healed.empty": "— scenario.healed.json not loaded —",

    // original script card
    "original.title": "Original Script (.py)",
    "original.empty": "— original.py not loaded —",
    "regression.title": "After self-healing (regression_test.py)",
    "regression.desc":
      "Regression test auto-generated after ▶ <strong>Run LLM-applied code</strong> " +
      "with the healed selectors. Adopt into your regression suite after review.",
    "regression.empty": "— regression_test.py not loaded —",

    // run log card
    "runlog.title": "Run result (run_log)",
    "runlog.download": "📤 Download report",
    "runlog.download.title":
      "Bundle steps + screenshots for both LLM and original modes into a single shareable HTML",
    "runlog.tab.llm": "LLM",
    "runlog.tab.codegen": "Original",
    "runlog.desc":
      "Step-by-step results + screenshots from the most recent ▶ Play run. The " +
      "<strong>LLM</strong> tab shows scenario executor PASS / HEALED / FAIL plus " +
      "<code>heal_stage</code>; the <strong>Original</strong> tab shows per-action " +
      "results captured via Playwright tracing of codegen <code>original.py</code>.",
    "runlog.empty": "— shown after ▶ Play runs —",

    // play-llm log card
    "playLlmLog.title": "LLM run log (play-llm.log)",
    "playLlmLog.desc":
      "Full log of ▶ <strong>Run LLM-applied code</strong> — per-step PASS/HEALED/FAIL, " +
      "LLM prompts/responses, selector-substitution rationale, etc.",
    "playLlmLog.empty": "— play-llm.log not loaded —",

    // diff card
    "diff.title": "Original ↔ Regression change analysis",
    "diff.btn.analyze": "🔎 LLM analysis",
    "diff.btn.analyze.title": "Use LLM to interpret the semantic meaning of changes",
    "diff.desc":
      "Ollama groups the differences between codegen original and LLM-healed regression " +
      "by meaning — selector substitution / hover injection / removed steps, etc. " +
      "Use the analysis to decide whether to adopt the regression test.",
    "diff.placeholder":
      "— Click <strong>🔎 LLM analysis</strong> to start —",
    "diff.raw.summary": "View raw unified diff",
    "diff.raw.empty": "— shown after Run LLM-applied code —",

    // ── screenshot modal ─────────────────────────────────────────────────────
    "shot.close.title": "Close (Esc)",
    "shot.close.aria": "Close modal",
    "shot.caption.empty": "—",

    // ── step add ─────────────────────────────────────────────────────────────
    "step.section.title": "＋ Add step",
    "step.section.note": "(actions codegen does not record)",
    "step.desc":
      "Codegen records only click / fill / press / select / check / navigate. " +
      "Add the following 14-DSL actions manually: " +
      "<strong>verify</strong> · <strong>mock_status</strong> · <strong>mock_data</strong> · " +
      "<strong>scroll</strong> (lazy / infinite scroll) · <strong>hover</strong> (dropdown menus).",
    "step.target": "target",
    "step.target.note": "(selector or URL pattern)",
    "step.target.placeholder":
      "#status / https://api.example.com/list / #footer / role=link, name=About",
    "step.value": "value",
    "step.value.note": "(empty for hover; into_view for scroll)",
    "step.condition": "condition",
    "step.condition.note": "(verify only, optional)",
    "step.position": "position",
    "step.position.note": "(1-based step number; leave blank to append)",
    "step.position.placeholder":
      "e.g. 4 — inserts at step 4, shifting existing step 4+ by +1",
    "step.description": "description",
    "step.description.note": "(optional)",
    "step.btn.submit": "＋ Add step",

    // ── compare dialog ───────────────────────────────────────────────────────
    "compare.title": "doc-DSL compare input",
    "compare.desc": "Paste a 14-DSL JSON array.",
    "compare.threshold": "Fuzzy threshold (0.0–1.0, default 0.7)",
    "compare.cancel": "Cancel",
    "compare.submit": "Run compare",

    // ── recent sessions ──────────────────────────────────────────────────────
    "sessions.title": "Recent sessions",
    "sessions.filter.placeholder": "Search target_url / id…",
    "sessions.state.any": "All states",
    "sessions.btn.selectAll": "Select all",
    "sessions.btn.selectNone": "Clear selection",
    "sessions.btn.deleteSelected": "Delete selected",
    "sessions.selectedCount": "0 selected",
    "sessions.th.checkbox.title": "Select / clear all currently visible sessions",
    "sessions.empty": "— no sessions —",

    // ── footer ───────────────────────────────────────────────────────────────
    "footer.text": "Recording UI · host daemon · air-gap compatible",
  };

  const DICT = { en: EN };

  function getLang() {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return v === "en" ? "en" : DEFAULT_LANG;
    } catch (_) {
      return DEFAULT_LANG;
    }
  }

  function setLang(lang) {
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (_) { /* ignore */ }
    apply(lang);
    document.documentElement.lang = lang;
    renderToggle(lang);
    document.dispatchEvent(new CustomEvent("i18n:change", { detail: { lang } }));
  }

  // Look up a key. Used by app.js for dynamic strings.
  function t(key, fallback) {
    const lang = getLang();
    if (lang === "ko") return fallback != null ? fallback : key;
    const v = DICT.en[key];
    return v != null ? v : (fallback != null ? fallback : key);
  }

  function _cacheOriginal(el, attr) {
    const k = "__i18nOrig" + (attr || "Text");
    if (el[k] == null) {
      el[k] = attr ? el.getAttribute(attr) : el.textContent;
    }
    return el[k];
  }

  function apply(lang) {
    // text content
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      const orig = _cacheOriginal(el, null);
      if (lang === "en" && DICT.en[key] != null) {
        el.textContent = DICT.en[key];
      } else {
        el.textContent = orig;
      }
    });
    // innerHTML for elements with markup
    document.querySelectorAll("[data-i18n-html]").forEach((el) => {
      const key = el.getAttribute("data-i18n-html");
      const k = "__i18nOrigHtml";
      if (el[k] == null) el[k] = el.innerHTML;
      if (lang === "en" && DICT.en[key] != null) {
        el.innerHTML = DICT.en[key];
      } else {
        el.innerHTML = el[k];
      }
    });
    // attributes — data-i18n-attr-<name>="key"
    document.querySelectorAll("*").forEach((el) => {
      for (const a of el.attributes) {
        if (!a.name.startsWith("data-i18n-attr-")) continue;
        const attrName = a.name.slice("data-i18n-attr-".length);
        const key = a.value;
        const orig = _cacheOriginal(el, attrName);
        if (lang === "en" && DICT.en[key] != null) {
          el.setAttribute(attrName, DICT.en[key]);
        } else if (orig != null) {
          el.setAttribute(attrName, orig);
        }
      }
    });
  }

  function renderToggle(lang) {
    const host = document.getElementById("lang-toggle");
    if (!host) return;
    host.innerHTML = "";
    const mk = (l, label) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "lang-pill" + (l === lang ? " active" : "");
      b.textContent = label;
      b.title = DICT.en["header.lang.title"];
      b.addEventListener("click", () => setLang(l));
      return b;
    };
    host.appendChild(mk("ko", "KO"));
    host.appendChild(mk("en", "EN"));
  }

  // Public API
  window.I18N = { t, getLang, setLang, apply };

  // Bootstrap: apply current lang ASAP, then render toggle on DOMContentLoaded.
  function boot() {
    const lang = getLang();
    document.documentElement.lang = lang;
    apply(lang);
    renderToggle(lang);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
