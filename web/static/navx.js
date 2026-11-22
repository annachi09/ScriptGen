/* =====================================================================
   ScriptGen - new navigation (RJ 2026-11-18): "make the menu better ... modern with the latest trend"
   -> picked "A + C" for the sidebar and "option 3" for the tabs inside pages.

   Sidebar (A + C):
     - Search box at the top (Ctrl+K): finds menus AND the tabs inside them (e.g. "case 5", "h2o");
       Enter / click opens the first / chosen hit (same navigation as the Overview cards).
     - Favorites as tiles (the ★ favorites saved per user, favState in app.js).
     - (2026-11-18, option C) Grouped collapsible sections - Workspace / Billing / Readings & MP /
       Statistics / Help & Admin; click a header to open / close (remembered per browser). The old
       spaces switcher is gone. Collapsed sidebar = icon rail with big icons (option D).
     - Live count badge on every menu = sum of its Overview counters (overviewLive in app.js).
   Tabs inside pages (option 3): every .da-subnav becomes a row of small cards - label, live count
   (from the Overview counter of that tab, when it has one) and a short description.
   Loaded after app.js; uses $, $$, escapeHtml, overviewLive, OVERVIEW_CARDS, overviewNavigateTo, favState.
   ===================================================================== */
(function () {
  // [key, short label, icon, full name]
  const SPACES = [
    ["all", "All", "🧭", "All menus"],
    ["billing", "Billing", "🧾", "Billing"],
    ["readings", "Reads", "📏", "Readings & MP"],
    ["stats", "Stats", "📊", "Statistics"],
    ["tools", "Tools", "🧰", "Tools & admin"],
  ];
  const SPACE_OF = {
    billissuance: "billing", incorrectbillingperiod: "billing", doubleitb: "billing", wrongbill: "billing",
    wrongbilledconsumption: "billing", fixbillingcampaign: "billing", rebillingmonitor: "billing",
    pendingsr: "billing", srquickfix: "billing",
    dateanomaly: "readings", hierarchy: "readings", readingvalidation: "readings", tnbcycledisc: "readings",
    wrongstuckhierarchy: "readings", disconnectiontnb: "readings", readingdiscrepancies: "readings",
    mptools: "readings", bulkchecker: "readings",
    dashboard: "stats", anomalystats: "stats", billingstats: "stats", tempdashboard: "stats",
  };
  // RJ 2026-11-18: "rename the menus with same format of upper and lower case, best practice" -
  // Title Case everywhere, acronyms kept (AI, ITB, MP, SR, TNB), plural nouns for lists, no slashes/caps-lock.
  const MENU_NAMES = {
    overview: "Overview", workspace: "Workspace", script: "Script", history: "History", dashboard: "Dashboard",
    ai: "AI Assist", dateanomaly: "Diff Dates Anomalies", hierarchy: "Hierarchy Analysis",
    readingvalidation: "Reading Validation", tnbcycledisc: "TNB Cycle & Disconnection",
    wrongstuckhierarchy: "Stuck Hierarchy ITBs", disconnectiontnb: "TNB Disconnections", doubleitb: "Double ITBs",
    wrongbill: "Wrong Bills", wrongbilledconsumption: "Wrong Billed Consumption", billissuance: "Bill Issuance Validator",
    incorrectbillingperiod: "Incorrect Billing Period", bulkchecker: "Bulk Checker", anomalystats: "Anomaly Statistics",
    rebillingmonitor: "Rebilling Monitor", pendingsr: "Pending Service Requests", tempdashboard: "Temp Dashboard",
    readingdiscrepancies: "Reading Discrepancies", fixbillingcampaign: "Fix Billing Campaign 2026",
    billingstats: "Billing Statistics", srquickfix: "SR Quick Fix", mptools: "MP Tools",
    documentation: "Documentation", tools: "Tools", settings: "Settings",
  };
  // RJ 2026-11-18: "when i hover on the menu, add a small description of what it is doing, make it user
  // friendly" - one plain-language line per menu, shown in a hover card next to the sidebar.
  const MENU_DESC = {
    overview: "Your home screen: live counts from every tool and the critical alerts at a glance.",
    workspace: "Run your own SQL, edit cells in the result and turn the changes into an update script.",
    script: "Review, copy or download the update script built from your Workspace edits.",
    history: "Every script ScriptGen generated - who, when, what for - with its rollback.",
    dashboard: "How ScriptGen is used: scripts generated, tables touched and activity over time.",
    ai: "Ask the AI to write, explain or speed up a SQL query.",
    dateanomaly: "Readings whose dates don't match their billing period - find them and build the full correction script.",
    hierarchy: "Primary / secondary hierarchies with a pending primary - see each secondary's readings and usage.",
    readingvalidation: "Look up a NISS or account's readings, fix values, dates, type or status and get update + rollback.",
    tnbcycledisc: "TNB cycle and disconnection readings side by side, per billing period.",
    wrongstuckhierarchy: "Items to bill stuck in hierarchy while the primary is already billed - release them to Pending.",
    disconnectiontnb: "TNB supplies with disconnection readings and where their billing stands.",
    doubleitb: "Supplies with two items to bill in the same period - spot the duplicate and cancel it safely.",
    wrongbill: "Bills that look wrong: unusually high sanitary, % distribution, sanitary tariff, first bill regularized.",
    wrongbilledconsumption: "Bills whose billed consumption doesn't match what the readings say.",
    billissuance: "Accounts whose bills didn't come out as expected - stuck rate bills, period mismatches, temp stops.",
    incorrectbillingperiod: "Readings and items billed in the wrong billing period.",
    bulkchecker: "Paste many accounts or NISS and see contracts, bills, readings and meters in one table.",
    anomalystats: "Charts of open billing and reading anomalies by type, status and age.",
    billingstats: "Bills issued per period, type and status, with fixed-billing and signal breakdowns.",
    srquickfix: "Quick fixes for service-request cases: incorrect billing period, sanitary anomaly with water pending.",
    mptools: "Measuring point tools - re-activate an inactive MP and release its calculated readings.",
    tempdashboard: "Working board: accounts and readings, sanitary status not matching water.",
    readingdiscrepancies: "Readings pending validation for more than 3 days and readings never billed.",
    fixbillingcampaign: "Fix Billing Campaign 2026 requests: who qualifies, bank / no bank, wallet payment day.",
    pendingsr: "Open service requests by type and status, oldest first.",
    rebillingmonitor: "Open rebillings (and the ones ScriptGen tagged) - follow them until they're billed.",
    documentation: "User guide, technical notes and the query library behind every page.",
    tools: "Handy utilities and housekeeping helpers.",
    settings: "Database connection, built-in SSH tunnel, AI, e-mail alerts, users and menu access - all in one place.",
  };
  // RJ 2026-11-18 (later): "i dont like the menu its too big the text looks like not done professionally ...
  // remove also the numbers ... and the yellow dot ... redesign it in a very good one industry standard".
  // Emoji icons -> one consistent line-icon set (Lucide-style, 24x24, stroke = currentColor).
  const ICONS = {
    overview: '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    workspace: '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.4 2.6a2.1 2.1 0 1 1 3 3L12 15l-4 1 1-4Z"/>',
    script: '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><path d="M14 2v6h6"/><path d="m10 13-2 2 2 2"/><path d="m14 17 2-2-2-2"/>',
    history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    dashboard: '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
    ai: '<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z"/>',
    dateanomaly: '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/><path d="m14 14-4 4M10 14l4 4"/>',
    hierarchy: '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3M12 12V8"/>',
    readingvalidation: '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    tnbcycledisc: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    wrongstuckhierarchy: '<path d="m12 2 10 5-10 5L2 7Z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
    disconnectiontnb: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
    doubleitb: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    wrongbill: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4M12 17h.01"/>',
    wrongbilledconsumption: '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10M12 3v18M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
    billissuance: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    incorrectbillingperiod: '<path d="M21 7.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3.5"/><path d="M16 2v4M8 2v4M3 10h5"/><circle cx="16" cy="16" r="6"/><path d="M16 14v2l1 1"/>',
    bulkchecker: '<path d="m3 17 2 2 4-4M3 7l2 2 4-4M13 6h8M13 12h8M13 18h8"/>',
    anomalystats: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    billingstats: '<path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-3"/>',
    srquickfix: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    mptools: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
    tempdashboard: '<path d="M10 2v7.5a2 2 0 0 1-.2.9L4.7 20.6a1 1 0 0 0 .9 1.4h12.8a1 1 0 0 0 .9-1.4l-5.1-10.2a2 2 0 0 1-.2-.9V2M8.5 2h7M7 16h10"/>',
    readingdiscrepancies: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    fixbillingcampaign: '<rect width="20" height="14" x="2" y="5" rx="2"/><path d="M2 10h20"/>',
    pendingsr: '<path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M13 5v2M13 17v2M13 11v2"/>',
    rebillingmonitor: '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    documentation: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    tools: '<path d="M20 7h-9M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
    settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  };
  const svgOf = (page) => ICONS[page] ? `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[page]}</svg>` : "";
  const iconHtml = (btn) => svgOf(btn.dataset.page) || esc(iconOf(btn));
  function applyIcons() {
    navItems().forEach((btn) => {
      btn.style.setProperty("--grp", btn.dataset.page === "overview" ? "#a5b4fc" : groupColor(btn.dataset.page));   // RJ 2026-11-19
      const ic = btn.querySelector(".nav-icon");
      if (!ic || ic.dataset.svg || !ICONS[btn.dataset.page]) return;
      ic.dataset.emoji = ic.textContent.trim();
      ic.innerHTML = svgOf(btn.dataset.page);
      ic.dataset.svg = "1";
    });
  }
  function applyNames() {
    applyIcons();
    navItems().forEach((btn) => {
      const n = MENU_NAMES[btn.dataset.page]; if (!n) return;
      const l = btn.querySelector(".nav-label");
      if (l && l.textContent !== n) l.textContent = n;
      if (btn.title) btn.removeAttribute("title");          // the hover card replaces the browser tooltip
      const d = MENU_DESC[btn.dataset.page];
      if (d && btn.getAttribute("aria-description") !== d) btn.setAttribute("aria-description", d);
    });
  }

  // ---------------- hover card ----------------
  let tipEl = null, tipTimer = null, tipFor = null;
  function hideTip() { clearTimeout(tipTimer); tipFor = null; tipEl?.classList.remove("is-on"); }
  function showTip(btn) {
    const page = btn.dataset.page, desc = MENU_DESC[page];
    if (!desc) return;
    if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "navx-tip"; tipEl.setAttribute("role", "tooltip"); document.body.appendChild(tipEl); }
    const n = badgeCount(page);
    const fav = btn.classList.contains("is-fav");
    tipEl.innerHTML =
      `<div class="navx-tip-head"><span class="navx-tip-ico">${iconHtml(btn)}</span><b>${esc(labelOf(btn))}</b>${fav ? `<span class="navx-tip-pin">Pinned</span>` : ""}</div>` +
      `<p>${esc(desc)}</p>`;   // RJ 2026-11-18 (later): no counts / pin hint - clean card
    void n;
    const r = btn.getBoundingClientRect(), side = $("#sidebar")?.getBoundingClientRect();
    tipEl.style.left = `${Math.round((side ? side.right : r.right) + 10)}px`;
    tipEl.classList.add("is-on");
    const h = tipEl.offsetHeight;
    tipEl.style.top = `${Math.round(Math.min(Math.max(8, r.top + r.height / 2 - h / 2), innerHeight - h - 8))}px`;
    tipEl.style.setProperty("--arrow-y", `${Math.round(r.top + r.height / 2 - parseFloat(tipEl.style.top))}px`);
  }
  document.addEventListener("mouseover", (e) => {
    const btn = e.target.closest?.(".sidebar .nav-item[data-page]");
    if (!btn) { if (tipFor && !e.target.closest?.(".sidebar .nav-item")) hideTip(); return; }
    if (tipFor === btn) return;
    clearTimeout(tipTimer); tipFor = btn;
    const wasOn = tipEl?.classList.contains("is-on");
    tipTimer = setTimeout(() => { if (tipFor === btn) showTip(btn); }, wasOn ? 60 : 380);   // quick once one is open
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target.closest?.(".sidebar .nav") && !e.relatedTarget?.closest?.(".sidebar .nav")) hideTip();
  });
  document.addEventListener("click", hideTip, true);
  $(".sidebar .nav")?.addEventListener("scroll", hideTip, { passive: true });
  // RJ 2026-11-18: "implement c" - grouped, collapsible sections (click a header to open / close it,
  // remembered per browser). Pinned favorites stay in their own group under Overview.
  // RJ 2026-11-19: "ok proceed [standard structure] and maybe colors to main menu and some icon" - menus grouped
  // by the JOB you do on them, each section with its own colour + icon (the colour also tints its menu icons and
  // the active bar). [key, label, colour, icon svg]
  const GROUPS = [
    ["monitor", "Monitor", "#38bdf8", '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'],
    ["billing", "Billing fixes", "#f59e0b", '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M8 9h8M8 13h6"/>'],
    ["readings", "Reading fixes", "#34d399", '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>'],
    ["quick", "Quick fixes", "#a78bfa", '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>'],
    ["lookup", "Lookup & SQL", "#818cf8", '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>'],
    ["admin", "Admin & help", "#94a3b8", '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'],
  ];
  const GROUP_OF = {
    anomalystats: "monitor", billingstats: "monitor", rebillingmonitor: "monitor", pendingsr: "monitor", tempdashboard: "monitor",
    billissuance: "billing", doubleitb: "billing", wrongbill: "billing", wrongbilledconsumption: "billing",
    incorrectbillingperiod: "billing", fixbillingcampaign: "billing",
    dateanomaly: "readings", readingvalidation: "readings", hierarchy: "readings", wrongstuckhierarchy: "readings",
    tnbcycledisc: "readings", disconnectiontnb: "readings", readingdiscrepancies: "readings",
    srquickfix: "quick", mptools: "quick",
    bulkchecker: "lookup", workspace: "lookup", script: "lookup", ai: "lookup",
    history: "admin", dashboard: "admin", documentation: "admin", tools: "admin", settings: "admin",
  };
  void SPACE_OF;
  const groupOf = (p) => GROUP_OF[p] || "admin";
  const groupColor = (p) => (GROUPS.find((g) => g[0] === groupOf(p)) || [])[2] || "#94a3b8";
  let closedGroups = new Set();
  try { closedGroups = new Set(JSON.parse(localStorage.getItem("scriptgen_nav_closed") || "[]")); } catch (_) { /* private mode */ }
  const favOnly = () => !!document.getElementById("fav-only-btn")?.classList.contains("is-active");
  const saveClosed = () => { try { localStorage.setItem("scriptgen_nav_closed", JSON.stringify([...closedGroups])); } catch (_) { /* ignore */ } };

  const esc = (s) => escapeHtml(String(s ?? ""));
  const navItems = () => $$(".nav-item[data-page]");
  const labelOf = (btn) => (btn.querySelector(".nav-label")?.textContent || btn.title || btn.dataset.page).trim();
  const iconOf = (btn) => (btn.querySelector(".nav-icon")?.dataset.emoji || btn.querySelector(".nav-icon")?.textContent || "•").trim();
  const subAttrOf = (b) => [...b.attributes].map((a) => a.name).find((n) => /^data-[a-z0-9]+-sub$/.test(n));
  const fmt = (n) => Number(n).toLocaleString();

  // ---------------- sidebar chrome ----------------
  function ensureChrome() {
    const nav = $(".sidebar .nav");
    if (!nav || $("#navx-top")) return;
    const top = document.createElement("div");
    top.id = "navx-top";
    top.innerHTML = `
      <div class="navx-search"><span class="navx-search-ico">⌕</span>
        <input id="navx-search" type="text" placeholder="Search menus, tabs…" autocomplete="off" />
        <kbd>Ctrl K</kbd></div>
      <div class="navx-results" id="navx-results" hidden></div>`;   // RJ 2026-11-18: spaces switcher replaced by grouped sections (option C)
    nav.prepend(top);
    // the "favorites only" toggle (app.js) becomes a star icon inside the search bar

    const input = $("#navx-search");
    input.addEventListener("input", renderResults);
    input.addEventListener("keydown", (e) => {
      const items = $$("#navx-results [data-navx-go]");
      const cur = items.findIndex((x) => x.classList.contains("is-hl"));
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = items.length ? (cur + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length : -1;
        items.forEach((x, i) => x.classList.toggle("is-hl", i === next));
      } else if (e.key === "Enter") {
        e.preventDefault();
        (items[cur] || items[0])?.click();
      } else if (e.key === "Escape") {
        input.value = ""; renderResults(); input.blur();
      }
    });
    $("#navx-results").addEventListener("click", (e) => {
      const b = e.target.closest("[data-navx-go]"); if (!b) return;
      const [page, attr, val] = b.dataset.navxGo.split("|");
      input.value = ""; renderResults();
      overviewNavigateTo(page, attr || null, val || null);
    });
  }

  // Ctrl+K / Cmd+K focuses the menu search from anywhere
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      const i = $("#navx-search"); if (!i) return;
      e.preventDefault();
      $("#sidebar")?.classList.remove("is-collapsed");
      i.focus(); i.select();
    }
  });

  // ---------------- search (menus + tabs) ----------------
  function searchIndex() {
    const out = [];
    navItems().forEach((btn) => {
      if (btn.hidden) return;
      const page = btn.dataset.page, label = labelOf(btn);
      out.push({ page, label, icon: iconOf(btn), sub: "", text: label.toLowerCase() });
      $$(`#page-${page} .da-subnav-btn`).forEach((t) => {
        const attr = subAttrOf(t); if (!attr) return;
        const tl = (t.querySelector(".navx-tab-label")?.textContent || t.firstChild?.textContent || t.textContent || "").trim();
        out.push({ page, label: tl, icon: iconOf(btn), sub: label, attr, val: t.getAttribute(attr),
          text: `${tl} ${label}`.toLowerCase() });
      });
    });
    return out;
  }
  function renderResults() {
    const q = ($("#navx-search").value || "").trim().toLowerCase();
    const box = $("#navx-results");
    if (!q) { box.hidden = true; box.innerHTML = ""; return; }
    const words = q.split(/\s+/);
    const hits = searchIndex().filter((x) => words.every((w) => x.text.includes(w))).slice(0, 12);
    box.hidden = false;
    box.innerHTML = hits.map((h, i) => `<button type="button" class="${i === 0 ? "is-hl" : ""}" data-navx-go="${esc(h.page)}|${esc(h.attr || "")}|${esc(h.val || "")}">` +
      `<span class="navx-r-ico">${svgOf(h.page) || esc(h.icon)}</span><span class="navx-r-txt">${esc(h.label)}${h.sub ? `<small>${esc(h.sub)} · tab</small>` : ""}</span></button>`).join("")
      || `<div class="navx-r-empty">No menu or tab matches "${esc(q)}"</div>`;
  }

  // ---------------- favorites tiles ----------------
  function renderFavs() {
    const box = $("#navx-favs"); if (!box) return;
    const favs = (typeof favState !== "undefined" ? favState.favorites : []) || [];
    const tiles = favs.map((p) => document.querySelector(`.nav-item[data-page="${p}"]`)).filter((b) => b && !b.hidden);
    const active = $(".page.is-active")?.id?.replace("page-", "");
    box.innerHTML = tiles.map((b) => {
      const n = badgeCount(b.dataset.page);
      return `<button type="button" class="navx-tile${b.dataset.page === active ? " is-active" : ""}" data-navx-fav="${esc(b.dataset.page)}" title="${esc(labelOf(b))}">` +
        `<span class="navx-tile-ico">${esc(iconOf(b))}</span><span class="navx-tile-lbl">${esc(labelOf(b))}</span>` +
        (n ? `<span class="navx-tile-n">${fmt(n)}</span>` : "") + `</button>`;
    }).join("") || `<div class="navx-fav-empty">Hover a menu and click ☆ to pin it here</div>`;
  }

  // ---------------- groups (option C) ----------------
  function applyGroups() {
    const active = $(".page.is-active")?.id?.replace("page-", "");
    GROUPS.forEach(([key]) => {
      const h = document.getElementById(`navx-grp-${key}`); if (!h) return;
      const members = navItems().filter((b) => (favOnly() || !b.classList.contains("is-fav")) && b.dataset.page !== "overview" && groupOf(b.dataset.page) === key);
      const visible = members.filter((b) => !b.hidden);
      // the group holding the open page never stays closed (so the active menu is always visible)
      if (visible.some((b) => b.dataset.page === active) && closedGroups.has(key)) { closedGroups.delete(key); saveClosed(); }
      const closed = closedGroups.has(key);
      h.hidden = !visible.length;
      h.classList.toggle("is-closed", closed);
      h.setAttribute("aria-expanded", String(!closed));
      const open = visible.reduce((s, b) => s + badgeCount(b.dataset.page), 0);
      const nEl = h.querySelector(".navx-grp-n");
      nEl.textContent = closed && open ? compact(open) : String(visible.length);
      nEl.classList.toggle("is-open-count", closed && open > 0);
      nEl.title = closed && open ? `${fmt(open)} open in this group` : `${visible.length} menus`;
      members.forEach((b) => b.classList.toggle("navx-out", closed));
    });
  }
  document.addEventListener("click", (e) => {
    const h = e.target.closest(".navx-grp"); if (!h) return;
    const key = h.dataset.navxGrp;
    if (closedGroups.has(key)) closedGroups.delete(key); else closedGroups.add(key);
    saveClosed(); applyGroups();
  });

  // ---------------- pinned favorites ----------------
  // RJ 2026-11-18: "be creative with the favorite" - starred menus float into a "Pinned" group right
  // under Overview (gold header + hairline), stay visible in every space, and go back to their
  // original place when un-starred. The ☆ itself only shows on hover.
  function pinFavorites() {
    const nav = $(".sidebar .nav"); if (!nav) return;
    const items = navItems();
    items.forEach((b, i) => { if (b.dataset.navxIdx === undefined) b.dataset.navxIdx = String(i); });
    const sorted = [...items].sort((a, b) => a.dataset.navxIdx - b.dataset.navxIdx);
    const isHome = (b) => b.dataset.page === "overview";
    const head = sorted.filter(isHome);
    // RJ 2026-11-18: "we lost the grouping?" - with "favorites only" (★ in the search bar) ON every
    // visible menu is a favorite, so they all sat in Pinned and every group looked empty. In that mode
    // the favorites are shown inside their groups instead (no Pinned block).
    const pinned = favOnly() ? [] : sorted.filter((b) => !isHome(b) && b.classList.contains("is-fav"));
    const rest = sorted.filter((b) => !isHome(b) && !pinned.includes(b));
    let sep = $("#navx-pin-head"), end = $("#navx-pin-end");
    if (!sep) { sep = document.createElement("div"); sep.id = "navx-pin-head"; sep.className = "navx-pin-head"; sep.innerHTML = `<span class="navx-grp-lbl">Pinned</span>`; }
    if (!end) { end = document.createElement("div"); end.id = "navx-pin-end"; end.className = "navx-pin-end"; }
    const anyPinned = pinned.some((b) => !b.hidden);
    // option C: the rest goes under its group header (headers created once, reused)
    const grouped = [];
    GROUPS.forEach(([key, label, color, ico]) => {
      let h = document.getElementById(`navx-grp-${key}`);
      if (!h) {
        h = document.createElement("button");
        h.type = "button"; h.id = `navx-grp-${key}`; h.className = "navx-grp"; h.dataset.navxGrp = key;
        h.style.setProperty("--grp", color);
        h.innerHTML = `<span class="navx-grp-ico"><svg viewBox="0 0 24 24" aria-hidden="true">${ico}</svg></span>` +
          `<span class="navx-grp-lbl">${esc(label)}</span><span class="navx-grp-n"></span>` +
          `<svg class="navx-grp-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>`;
      }
      rest.filter((b) => groupOf(b.dataset.page) === key).forEach((b) => b.style.setProperty("--grp", color));
      grouped.push(h, ...rest.filter((b) => groupOf(b.dataset.page) === key));
    });
    const want = [...head, ...(anyPinned ? [sep, ...pinned, end] : pinned), ...grouped];
    const cur = [...nav.children].filter((el) => el.id !== "navx-top");
    if (want.length === cur.length && want.every((el, i) => el === cur[i])) return;
    want.forEach((el) => nav.appendChild(el));
    if (!anyPinned) { sep.remove(); end.remove(); }
  }

  // ---------------- live badges ----------------
  // RJ 2026-11-18: "the numbers in the menu, make it smaller" - compact mini pills (1.2k / 12k / 1.2M)
  // with a severity dot: red >= 100, amber > 0.
  function compact(n) {
    if (n < 1000) return String(n);
    if (n < 1e6) return (n < 10000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") : Math.round(n / 1000)) + "k";
    return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  }
  function badgeCount(page) {
    if (typeof OVERVIEW_CARDS === "undefined" || typeof overviewLive === "undefined") return 0;
    return OVERVIEW_CARDS.filter((c) => c.page === page && !c.localValue && !c.nocount)
      .reduce((s, c) => s + (Number(overviewLive[c.key]) || 0), 0);
  }
  function renderBadges() {
    // RJ 2026-11-18 (later): "remove also the numbers it looks bad" - no counts in the menu any more
    $$(".navx-badge").forEach((b) => b.remove());
    return;
    navItems().forEach((btn) => {   // eslint-disable-line no-unreachable
      if (btn.dataset.page === "overview" || btn.dataset.page === "history") return;
      let b = btn.querySelector(".navx-badge");
      const n = badgeCount(btn.dataset.page);
      if (!n) { b?.remove(); return; }
      if (!b) { b = document.createElement("span"); b.className = "navx-badge"; btn.insertBefore(b, btn.querySelector(".nav-fav")); }
      b.textContent = compact(n);
      b.dataset.sev = n >= 100 ? "hot" : "warm";
      b.title = `${fmt(n)} open (Overview counters)`;
    });
  }

  // ---------------- tabs as cards (option 3) ----------------
  function tabCard(btn) {
    const attr = subAttrOf(btn); if (!attr || typeof OVERVIEW_CARDS === "undefined") return null;
    return OVERVIEW_CARDS.find((c) => c.subAttr === attr && c.subValue === btn.getAttribute(attr)) || null;
  }
  function decorateTabs() {
    $$(".da-subnav").forEach((nav) => nav.classList.add("navx-cards"));
    $$(".da-subnav-btn").forEach((btn) => {
      if (!btn.querySelector(".navx-tab-label")) {
        // move the plain text into a label span (other elements, e.g. Wrong Bill's own counts, stay)
        const lbl = document.createElement("span");
        lbl.className = "navx-tab-label";
        [...btn.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).forEach((n) => lbl.appendChild(n));
        btn.prepend(lbl);
        const meta = document.createElement("span");
        meta.className = "navx-tab-meta";
        meta.innerHTML = `<b class="navx-tab-n"></b><small class="navx-tab-sub"></small>`;
        btn.appendChild(meta);
      }
      const card = tabCard(btn);
      const own = btn.querySelector(".wb-tab-count");   // Wrong Bill already shows its own count
      const nEl = btn.querySelector(".navx-tab-n"), sEl = btn.querySelector(".navx-tab-sub");
      let n = card && !card.nocount ? overviewLive[card.key] : undefined;
      nEl.textContent = own ? "" : (n === undefined ? (card && !card.nocount ? "…" : "") : (n === null ? "—" : fmt(n)));
      nEl.classList.toggle("is-hot", Number(n) > 0);
      const sub = card ? card.desc : (btn.title || "");
      sEl.textContent = sub; sEl.title = sub;
      btn.style.setProperty("--tab-accent", card?.color || "var(--accent, #6366f1)");
    });
  }

  // ---------------- refresh ----------------
  let timer = null;
  function refresh() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      ensureChrome();
      applyNames();
      const fo = $("#fav-only-btn"), sb = $(".navx-search");   // created later by app.js - star icon in the search bar
      if (fo && sb && fo.parentElement !== sb) sb.insertBefore(fo, sb.querySelector("kbd"));
      if (fo) { const on = fo.classList.contains("is-active"); if (fo.textContent !== (on ? "★" : "☆")) fo.textContent = on ? "★" : "☆"; }
      pinFavorites(); renderBadges(); applyGroups(); decorateTabs();
      if ($("#navx-search")?.value) renderResults();
    }, 60);
  }
  window.navxRefresh = refresh;
  // nav clicks change the active tile; favorites / role menus change the lists
  document.addEventListener("click", (e) => { if (e.target.closest(".nav-item, .nav-fav, #fav-only-btn, .da-subnav-btn")) refresh(); });
  refresh();
  setTimeout(refresh, 1500);   // after /api/session + favorites load
  // RJ 2026-11-18: "we lost the grouping?" - after a login / server restart the role menu access shows the
  // menus again later than the timers above, so the group headers stayed hidden (no visible members when
  // they were last counted). Any show / hide of a menu now re-runs the layout.
  try {
    const navEl = $(".sidebar .nav");
    if (navEl) new MutationObserver((muts) => {
      if (muts.some((m) => m.target.classList?.contains("nav-item"))) refresh();
    }).observe(navEl, { subtree: true, attributes: true, attributeFilter: ["hidden"] });
  } catch (_) { /* old browser - timers still apply */ }
})();
