// Replay UI — minimal i18n.
// Same pattern as Recording UI: default ko (existing inline text), en switches
// via header toggle, lookup is dict-based with ko inline text as fallback.

(function () {
  const STORAGE_KEY = "ui.lang";
  const DEFAULT_LANG = "ko";

  const EN = {
    "doc.title": "Replay UI",

    // header
    "header.title": "🎬 Replay UI",
    "header.recording.label": "📹 Recording UI ↗",
    "header.recording.title": "Open Recording UI in a new tab (port 18092)",
    "header.alarm.title": "Number of profiles that need re-login",
    "header.wizard.label": "🧭 First-time guide",
    "header.wizard.title":
      "First-time guide (register login → upload scenario .py → run)",
    "header.lang.ko": "KO",
    "header.lang.en": "EN",
    "header.lang.title": "Switch interface language",

    // login profiles card
    "login.title": "🔐 Login Profiles",
    "login.btn.add": "+ New profile",
    "login.btn.refresh": "↻ Refresh",
    "login.col.name": "Profile name",
    "login.col.state": "Login state",
    "login.col.last": "Last checked",
    "login.col.site": "Site",
    "login.col.actions": "Actions",
    "login.row.loading": "— loading —",

    // scenario scripts card
    "scripts.title": "📄 Scenario Scripts",
    "scripts.desc":
      "Upload the <code>.py</code> script that was downloaded from the Recording UI via " +
      "<code>⬇ Download</code>. Pick the login profile to apply at run time, or leave it " +
      "blank for anonymous scenarios.",
    "scripts.btn.upload": "⬆ Upload (.py)",
    "scripts.btn.refresh": "↻ Refresh",
    "scripts.btn.bulkDelete": "🗑 Delete selected",
    "scripts.btn.bulkDelete.title": "Delete the checked scripts at once.",
    "scripts.run.profile": "Login profile to apply",
    "scripts.run.profile.none": "(no login — storage_state not injected)",
    "scripts.run.headed": "Show window (headed)",
    "scripts.run.slowmo.title":
      "Delay between actions. Useful for visual debugging, but cumulative — " +
      "total run time grows with the number of actions.",
    "scripts.run.slowmo": "Delay between actions",
    "scripts.run.slowmo.ms": "ms",
    "scripts.run.verifyUrl": "verify URL",
    "scripts.run.verifyUrl.opt": "(optional)",
    "scripts.run.verifyUrl.placeholder":
      "Leave blank to use the verify URL from the profile catalog",
    "scripts.th.checkbox.title": "Select / clear all",
    "scripts.th.script": "Script",
    "scripts.th.registered": "Registered",
    "scripts.th.size": "Size",
    "scripts.th.actions": "Actions",

    // run console
    "run.title": "▶ Run Console",
    "run.idle": "— idle —",

    // results card
    "results.title": "📊 Results",
    "results.btn.refresh": "↻ Refresh",
    "results.btn.bulkDelete": "🗑 Delete selected",
    "results.btn.bulkDelete.title":
      "Delete the checked run results at once (also removes disk folders).",
    "results.th.time": "Time",
    "results.th.scenario": "Scenario",
    "results.th.profile": "Profile name",
    "results.th.result": "Result",

    // seed input modal
    "seed.input.title": "+ New login profile",
    "seed.input.desc":
      "Run this once (re-login when expired). A human logs in manually and passes 2FA; " +
      "the result is saved and reused for later scenario runs.",
    "seed.input.name": "Profile name",
    "seed.input.name.title":
      "Letters/digits/_/- 1–64 chars; first char must be a letter or digit",
    "seed.input.url": "Start URL",
    "seed.input.url.hint":
      "⚠ Enter the entry page of the <strong>service under test</strong> (not the Naver login page).",
    "seed.input.verifyUrl": "Verify URL",
    "seed.input.verifyUrl.hint":
      "After seeding, this URL is fetched automatically to confirm login state.",
    "seed.input.verifyText": "Verify text",
    "seed.input.verifyText.opt": "(optional)",
    "seed.input.verifyText.placeholder": "Welcome, QA Kim",
    "seed.input.verifyText.hint":
      "Blank: only confirm that the verify URL loads. Filled: the text must also be present (stronger check).",
    "seed.input.ttl": "TTL hint (hours)",
    "seed.input.idp": "IdP domain",
    "seed.input.idp.opt": "(optional)",
    "seed.input.idp.title":
      "External IdP domain that handles 2FA (e.g. naver.com / kakao.com / accounts.google.com). " +
      "Leave blank to skip IdP verification — pure ID/PW services only.",
    "seed.input.idp.hint":
      "After seeding, storage must contain cookies for this domain. Leave blank for plain ID/PW sites.",
    "seed.input.naverProbe":
      "Naver-side weak probe (best-effort, only meaningful when IdP=naver.com)",
    "seed.input.warning": "⚠ Do not use production accounts — use a test-only account",
    "seed.input.cancel": "Cancel",
    "seed.input.submit": "Open →",

    // seed progress modal
    "seed.progress.title": "🪟 Saving login session",
    "seed.progress.step1":
      "Click [Log in with Naver] or the regular login button on the target service",
    "seed.progress.step2": "Enter ID / password on the login screen",
    "seed.progress.step3": "Pass 2FA (SMS / OTP, etc.) yourself",
    "seed.progress.step4": "Service redirects back → verify the target page loads",
    "seed.progress.step5":
      "<strong>Confirm the post-login screen, then close the opened browser window</strong>",
    "seed.progress.status.waiting": "⏳ Waiting for user input",
    "seed.progress.elapsed": "Elapsed 0s / limit 600s",
    "seed.progress.hint":
      "When the window closes, the session is saved, the verification page is shown briefly, and the flow completes.",
    "seed.progress.cancel": "Cancel (close the window yourself)",
    "seed.progress.done": "Close",

    // seed expired modal
    "seed.expired.title": "⚠ Login expired",
    "seed.expired.body.prefix": "Profile ",
    "seed.expired.body.suffix": " has expired.",
    "seed.expired.hint":
      "Re-logging in preserves the Start URL / Verify URL / Verify text inputs.",
    "seed.expired.cancel": "Cancel",
    "seed.expired.reseed": "Re-login",

    // detail modal
    "detail.back": "← Back to list",
    "detail.title": "Run detail",
    "detail.report": "📥 HTML report",

    // wizard modal
    "wizard.title": "🧭 First-time guide",
    "wizard.step1":
      "<strong>Step 1</strong> — On the Login Profiles card, click <code>+ New profile</code> " +
      "and enter the profile name and site URL.",
    "wizard.step2":
      "<strong>Step 2</strong> — A browser opens automatically. Log in directly and close " +
      "the window. The login state is saved on the monitoring PC.",
    "wizard.step3":
      "<strong>Step 3</strong> — On the Scenario Scripts card, click <code>⬆ Upload (.py)</code> " +
      "to upload the <code>original.py</code> or <code>regression_test.py</code> received from the recording PC.",
    "wizard.step4":
      "<strong>Step 4</strong> — Pick a registered alias (or anonymous) in the login profile " +
      "selector → click the row's <code>▶ Run</code>.",
    "wizard.start": "Get started",
  };

  const DICT = { en: EN };

  function getLang() {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return v === "en" ? "en" : DEFAULT_LANG;
    } catch (_) { return DEFAULT_LANG; }
  }

  function setLang(lang) {
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (_) { /* ignore */ }
    apply(lang);
    document.documentElement.lang = lang;
    renderToggle(lang);
    document.dispatchEvent(new CustomEvent("i18n:change", { detail: { lang } }));
  }

  function t(key, fallback, vars) {
    let v;
    if (typeof fallback === "object" && fallback !== null) {
      vars = fallback; fallback = null;
    }
    const lang = getLang();
    if (lang === "ko") v = fallback != null ? fallback : key;
    else v = DICT.en[key] != null ? DICT.en[key] : (fallback != null ? fallback : key);
    if (vars) {
      for (const k in vars) v = v.split("{" + k + "}").join(String(vars[k]));
    }
    return v;
  }

  function _cacheOriginal(el, attr) {
    const k = "__i18nOrig" + (attr || "Text");
    if (el[k] == null) el[k] = attr ? el.getAttribute(attr) : el.textContent;
    return el[k];
  }

  function apply(lang) {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      const orig = _cacheOriginal(el, null);
      el.textContent = (lang === "en" && DICT.en[key] != null) ? DICT.en[key] : orig;
    });
    document.querySelectorAll("[data-i18n-html]").forEach((el) => {
      const key = el.getAttribute("data-i18n-html");
      if (el.__i18nOrigHtml == null) el.__i18nOrigHtml = el.innerHTML;
      el.innerHTML = (lang === "en" && DICT.en[key] != null) ? DICT.en[key] : el.__i18nOrigHtml;
    });
    document.querySelectorAll("*").forEach((el) => {
      for (const a of el.attributes) {
        if (!a.name.startsWith("data-i18n-attr-")) continue;
        const attrName = a.name.slice("data-i18n-attr-".length);
        const key = a.value;
        const orig = _cacheOriginal(el, attrName);
        if (lang === "en" && DICT.en[key] != null) el.setAttribute(attrName, DICT.en[key]);
        else if (orig != null) el.setAttribute(attrName, orig);
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

  window.I18N = { t, getLang, setLang, apply };

  function boot() {
    const lang = getLang();
    document.documentElement.lang = lang;
    apply(lang);
    renderToggle(lang);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
