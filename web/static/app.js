// ScriptGen web UI - Phase 1 client logic: auth, Workspace (query / grid
// edit / diff highlighting / live SQL preview), Script page. Talks only
// to the /api/* routes in web/server.py; all SQL-generation logic still
// lives in Python (app/core) - this file just renders what the backend
// computes and posts back whatever the user typed/edited.

const state = {
  username: null,
  role: null,               // "admin" | "editor" | "viewer"
  mustChangePassword: false,
  allowedMenus: null,       // null = show everything (no /api/session response yet); else array of visible data-page ids

  columns: [],
  displayRows: [],       // string grid as loaded from the server
  editedRows: [],        // current (possibly edited) string grid
  keyColumns: new Set(),
  changedByRow: {},      // row_index -> {changed_columns:[...], preview_line}
  sourceSchema: null,
  sourceTable: null,
  recentQueries: [],
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function showToast(message, isError = false) {
  const toast = $("#toast");
  // Errors render as a notification card pinned to the upper-left corner
  // (see .toast.is-error in styles.css) rather than the bottom-center
  // confirmation pill - the icon prefix here is part of that "this is a
  // distinct alert, not a passive confirmation" treatment. Also given a
  // longer/no auto-dismiss than a success toast, since an error is
  // something to actually read, not just glance at.
  toast.textContent = isError ? `⚠️ ${message}` : message;
  toast.classList.toggle("is-error", isError);
  toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { toast.hidden = true; }, isError ? 6000 : 3200);
}

// DIFF DATES Anomaly: shared row-highlight helper for "more than 1 distinct
// billing period of GCGT_RE_READING detected for the NISS" (analyst
// request) - used identically by the Single NISS anomaly table, the Detect
// All table, and the Batch results table (see .row-multi-period in
// styles.css for the actual orange styling, light/dark theme both
// covered). Returns a class string (possibly empty) rather than mutating
// the element directly, so every call site can just template it into its
// own tr.className.
function daRowClassForBillingPeriods(count) {
  return count > 1 ? "row-multi-period" : "";
}

// In-flight API call counter - lets the shared ⟳ Refresh button wait for a
// page's own scan to finish before it restores that page's filters.
let _apiInflight = 0;

async function api(path, options = {}) {
  _apiInflight++;
  try {
    const resp = await fetch(path, {
      method: options.method || "GET",
      headers: { "Content-Type": "application/json" },
      body: options.body ? JSON.stringify(options.body) : undefined,
      credentials: "same-origin",
    });
    let data = null;
    try { data = await resp.json(); } catch (_) { /* no body */ }
    if (!resp.ok) {
      const message = (data && data.detail) || `Request failed (${resp.status})`;
      throw new Error(message);
    }
    return data;
  } finally {
    _apiInflight--;
  }
}

// ---------------- Auth ----------------
async function checkSession() {
  try {
    const info = await api("/api/session");
    state.username = info.username;
    state.role = info.role;
    state.mustChangePassword = info.must_change_password;
    state.allowedMenus = info.allowed_menus || null;
    return true;
  } catch (_) {
    return false;
  }
}

function showLogin() {
  $("#login-screen").hidden = false;
  $("#force-password-screen").hidden = true;
  $("#app-shell").hidden = true;
}

function showForcePasswordScreen() {
  $("#login-screen").hidden = true;
  $("#force-password-screen").hidden = false;
  $("#app-shell").hidden = true;
}

function showApp() {
  if (state.mustChangePassword) { showForcePasswordScreen(); return; }
  $("#login-screen").hidden = true;
  $("#force-password-screen").hidden = true;
  $("#app-shell").hidden = false;
  $("#user-name").textContent = state.username;
  $("#user-avatar").textContent = state.username.slice(0, 1).toUpperCase();
  $("#profile-name").textContent = state.username;
  $("#profile-avatar").textContent = state.username.slice(0, 1).toUpperCase();
  $("#profile-role").textContent = state.role || "";
  $("#profile-menu-user").textContent = state.username;
  $("#account-username-text").textContent = state.username;
  $("#account-role-badge").textContent = state.role;
  applyRolePermissionsToUI();
  try { billissUpdateSelectionUI(); } catch (_) { /* Case 1 not rendered yet */ }
  refreshConnectionStatus();
  refreshSidebarConnectionName();
  loadRecentQueries();
  loadOverview(); // Overview is the default landing page - its own nav
  // click handler only fires on a manual click, so the initial render on
  // login/session-restore has to be kicked off here instead.
}

// Least-privilege UI: hides/disables the specific actions each role can't
// perform server-side anyway (see web/server.py's require_editor/
// require_admin gates) - this is a UX nicety so a Viewer doesn't hit a
// 403 toast on something they were never going to be allowed to do, NOT
// the actual security boundary (that's enforced server-side, since
// hiding a button client-side is trivially bypassed by calling the API
// directly - see web/auth.py's module docstring for the real gate).
// Matches web/server.py's require_editor gates exactly - snapshot
// diff/compare and AI Explain are deliberately NOT here since those
// stay open to Viewer server-side too (read-only browsing).
const EDITOR_ONLY_IDS = [
  "generate-update-btn", "generate-rollback-btn",
  "save-query-btn", "snapshot-export-btn",
  "ai-suggest-btn", "ai-optimize-btn", "ai-review-btn", "ai-where-btn",
  "da-generate-btn", "da-batch-run-btn", "da-cleanup-generate-btn",
  "biss2-generate-btn", "billiss-release-generate-btn",
  "ibp-generate-btn",
];

function applyRolePermissionsToUI() {
  const isAdmin = state.role === "admin";
  const isViewer = state.role === "viewer";
  $("#admin-settings-section").hidden = !isAdmin;
  EDITOR_ONLY_IDS.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = isViewer;
  });
  applyMenuVisibilityToUI();
}

// Hides sidebar nav items this role's menu-access config doesn't include
// (see web/menu_access.py - a visibility convenience, NOT a security
// boundary; every route behind a hidden page still enforces its own
// require_editor/require_admin gate regardless of what the sidebar shows).
// state.allowedMenus is null until the first successful /api/session or
// /api/login response - treated as "show everything" so a page never
// flashes hidden before that first response lands.
function applyMenuVisibilityToUI() {
  const allowed = state.allowedMenus;
  $$(".nav-item[data-page]").forEach((btn) => {
    btn.hidden = !!allowed && !allowed.includes(btn.dataset.page);
  });
  // If the page currently showing just got hidden out from under the
  // user (their role's config changed since this session started, or
  // they're mid-session when an admin saves a new config), jump to the
  // first still-visible page rather than leaving a page open with no way
  // to navigate away from it via the sidebar.
  if (allowed) {
    const activePage = $(".page.is-active")?.id?.replace("page-", "");
    if (activePage && !allowed.includes(activePage)) {
      const firstVisible = $$(".nav-item[data-page]").find((b) => !b.hidden);
      if (firstVisible) firstVisible.click();
    }
  }
}

async function refreshSidebarConnectionName() {
  try {
    const { connections } = await api("/api/config/connections");
    const active = connections.find((c) => c.is_active);
    $("#sidebar-conn-name").textContent = active ? active.name : "No connection configured";
  } catch (_) { /* not fatal - leave the placeholder text */ }
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("#login-username").value.trim();
  const password = $("#login-password").value;
  $("#login-error").hidden = true;
  try {
    const info = await api("/api/login", { method: "POST", body: { username, password } });
    state.username = info.username;
    state.role = info.role;
    state.mustChangePassword = info.must_change_password;
    state.allowedMenus = info.allowed_menus || null;
    showApp();
  } catch (err) {
    $("#login-error").textContent = err.message;
    $("#login-error").hidden = false;
  }
});

$("#force-password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorEl = $("#force-pw-error");
  errorEl.hidden = true;
  const oldPw = $("#force-pw-old").value;
  const newPw = $("#force-pw-new").value;
  const confirm = $("#force-pw-confirm").value;
  if (newPw !== confirm) {
    errorEl.textContent = "New passwords don't match.";
    errorEl.hidden = false;
    return;
  }
  try {
    await api("/api/account/change-password", { method: "POST", body: { old_password: oldPw, new_password: newPw } });
    state.mustChangePassword = false;
    $("#force-password-form").reset();
    showToast("Password updated.");
    showApp();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.hidden = false;
  }
});

async function signOut() {
  await api("/api/logout", { method: "POST" });
  state.username = null;
  state.role = null;
  state.allowedMenus = null;
  $("#profile-menu").hidden = true;
  showLogin();
}
$("#sign-out-btn").addEventListener("click", signOut);

// Profile menu (top of the sidebar) - RJ 2026-10-04.
function profileMenuToggle(open) {
  const menu = $("#profile-menu");
  const show = open ?? menu.hidden;
  menu.hidden = !show;
  $("#profile-chip").setAttribute("aria-expanded", String(show));
}
$("#profile-chip").addEventListener("click", (e) => { e.stopPropagation(); profileMenuToggle(); });
document.addEventListener("click", (e) => { if (!e.target.closest("#profile-wrap")) profileMenuToggle(false); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") profileMenuToggle(false); });
$("#profile-signout-btn").addEventListener("click", signOut);
$("#profile-theme-btn").addEventListener("click", () => { profileMenuToggle(false); $("#theme-toggle-btn").click(); });
$("#profile-settings-btn").addEventListener("click", () => {
  profileMenuToggle(false);
  document.querySelector('.nav-item[data-page="settings"]')?.click();
});
$("#profile-password-btn").addEventListener("click", () => {
  profileMenuToggle(false);
  document.querySelector('.nav-item[data-page="settings"]')?.click();
  setTimeout(() => {
    const f = $("#change-password-form");
    f?.scrollIntoView({ behavior: "smooth", block: "center" });
    f?.querySelector("input")?.focus();
  }, 150);
});

// ---------------- Overview (home dashboard across every menu) ----------------
// RJ, 2026-09-23, verbatim: "for the whole scriptgen, create a dashboard
// for all our menu, bulk checker, diffdates, bill validator, etc.. i
// want a good dashboard with modern graphs, pie chart... also when the
// graph or dashboard is clicked it will redirect you to the
// details/menu." NOT the same page as "Dashboard" (data-page="dashboard"
// - that one profiles the CURRENT query result grid via _dashboardStats/
// loadDashboard below) - this is the new default landing page, one card
// per other menu plus two charts built from GET /api/overview/stats,
// reusing this file's existing canvas-chart helpers (drawBarChart,
// _categoricalColor, _chartTooltip) rather than a new charting library -
// see drawPieChart below for the one genuinely new chart type this adds.
// `group` is the sidebar menu each card belongs to (RJ: "i like it to be
// grouped base on the menu") - overviewRenderCards renders one section per
// distinct group, in the order groups first appear here, so Bill
// Issuance's 3 cases land together under one "Bill Issuance Validator"
// heading instead of reading as 3 cards flatly mixed with unrelated areas.
const OVERVIEW_CARDS = [
  {
    // RJ 2026-09-30: critical - shown first, red, pulsing when > 0.
    key: "unusualsanitary", icon: "🚨", label: "Wrong Bill · Case 1 - Unusual high Sanitary", color: "#ef4444",
    desc: "CRITICAL: Sanitary bill > 14,158 and higher than the Water bill — rebill ASAP.",
    page: "wrongbill", subAttr: "data-wb-sub", subValue: "case1", group: "Critical", critical: true,
  },
  {
    key: "wrongbill_case2", icon: "🚨", label: "Wrong Bill · Case 2 - % Distribution with metered secondary", color: "#ef4444",
    desc: "CRITICAL: % distribution primaries with a metered Secondary.",
    page: "wrongbill", subAttr: "data-wb-sub", subValue: "case2", group: "Critical", critical: true,
  },
  {
    key: "wrongbill_case3", icon: "🚨", label: "Wrong Bill · Case 3 - Sanitary 0 with water consumption", color: "#ef4444",
    desc: "CRITICAL: charging-tariff sanitary bill = 0 while water was consumed (current period).",
    page: "wrongbill", subAttr: "data-wb-sub", subValue: "case3", group: "Critical", critical: true,
  },
  {
    key: "wrongbill_case4", icon: "🚨", label: "Wrong Bill · Case 4 - First bill regularized", color: "#ef4444",
    desc: "CRITICAL: first bill of a contracted service carrying a regularization concept (current period).",
    page: "wrongbill", subAttr: "data-wb-sub", subValue: "case4", group: "Critical", critical: true,
  },
  {
    key: "anomstats_billing", icon: "📈", label: "Open billing anomalies", color: "#f59e0b",
    desc: "GCCOM_ANOMALOUS pending + pending after batch (ESTAN00001 / ESTAN00009).",
    page: "anomalystats", subAttr: "data-as-kind", subValue: "billing", group: "Anomalies Statistics",
  },
  {
    key: "anomstats_reading", icon: "📈", label: "Open reading anomalies", color: "#ef4444",
    desc: "GCGT_RE_ANOMALOUS pending to resolve (1000ANMSTA).",
    page: "anomalystats", subAttr: "data-as-kind", subValue: "reading", group: "Anomalies Statistics",
  },
  {
    key: "dateanomaly", icon: "🩹", label: "DIFF DATES Anomaly", color: "#3b5bfd",
    desc: "Open billing/reading date anomalies system-wide.",
    page: "dateanomaly", subAttr: "data-da-sub", subValue: "detectall",
    group: "DIFF DATES Anomaly",
  },
  {
    key: "hierarchy", icon: "🗂️", label: "Hierarchy Analysis", color: "#8b5cf6",
    desc: "Pending primary measuring points awaiting review.",
    page: "hierarchy",
    group: "Hierarchy Analysis",
  },
  {
    key: "billissuance_case1", icon: "🧾", label: "Case 1", color: "#f59e0b",
    desc: "Rate bill matches: Stuck Bill + New Contract Match.",
    page: "billissuance", subAttr: "data-biss-sub", subValue: "case1",
    group: "Bill Issuance Validator",
  },
  {
    key: "billissuance_case2", icon: "🧾", label: "Case 2", color: "#f59e0b",
    desc: "Terminated accounts needing a billing-period fix.",
    page: "billissuance", subAttr: "data-biss-sub", subValue: "case2",
    group: "Bill Issuance Validator",
  },
  {
    key: "billissuance_case3", icon: "🧾", label: "Case 3", color: "#f59e0b",
    desc: "Accounts flagged: all contract status, bills complete.",
    page: "billissuance", subAttr: "data-biss-sub", subValue: "case3",
    group: "Bill Issuance Validator",
  },
  {
    key: "billissuance_case4", icon: "🧾", label: "Case 4 - Unclassified", color: "#f59e0b",
    desc: "Accounts not in Case 1, 2 or 3.",
    page: "billissuance", subAttr: "data-biss-sub", subValue: "case4",
    group: "Bill Issuance Validator",
  },
  // ---- Billing anomalies (RJ 2026-10-01: "and all that we have created")
  {
    key: "incorrectbillingperiod", icon: "📅", label: "Incorrect Billing Period", color: "#ec4899",
    desc: "Anomalies with a wrong billing period.",
    page: "incorrectbillingperiod", group: "Billing anomalies",
  },
  {
    key: "doubleitb", icon: "👯", label: "DOUBLE ITB", color: "#ec4899",
    desc: "Anomalous items to bill with a billed twin that need rebilling.",
    page: "doubleitb", group: "Billing anomalies",
  },
  {
    key: "wrongbilledconsumption", icon: "⚖️", label: "Wrong Billed Consumption", color: "#ec4899",
    desc: "Calc base vs ready usage, current period (heavy scan — run it on the page).",
    page: "wrongbilledconsumption", group: "Billing anomalies", nocount: true,
  },
  // ---- Hierarchy & readings
  {
    key: "wrongstuckhierarchy", icon: "🪜", label: "Wrong Stuck in Hierarchy ITB", color: "#8b5cf6",
    desc: "Billed primary with secondary items stuck.",
    page: "wrongstuckhierarchy", subAttr: "data-wsh-sub", subValue: "primary", group: "Hierarchy & readings",
  },
  {
    key: "wrongstuckhierarchy_sanitary", icon: "🪜", label: "Sanitary Stuck, Water Billed/Pending", color: "#8b5cf6",
    desc: "Sanitary item stuck while the Water item is billed or pending.",
    page: "wrongstuckhierarchy", subAttr: "data-wsh-sub", subValue: "sanitary", group: "Hierarchy & readings",
  },
  {
    key: "readingvalidation", icon: "📏", label: "Reading Validation/Modif", color: "#8b5cf6",
    desc: "Look up and correct readings by NISS / account.",
    page: "readingvalidation", group: "Hierarchy & readings", nocount: true,
  },
  // ---- TNB / disconnection
  {
    key: "tnbcycledisc", icon: "📍", label: "TNB CYCLE/DISC Analysis", color: "#14b8a6",
    desc: "TNB cycle / disconnection readings to review.",
    page: "tnbcycledisc", group: "TNB / Disconnection",
  },
  {
    key: "disconnectiontnb", icon: "⛔", label: "Disconnection TNB", color: "#14b8a6",
    desc: "Disconnection TNB cases.",
    page: "disconnectiontnb", group: "TNB / Disconnection",
  },
  {
    key: "bulkchecker", icon: "📋", label: "Bulk Checker", color: "#10b981",
    desc: "Searches run from this workstation.",
    page: "bulkchecker", localValue: (s) => s.bulk_checker_search_count,
    group: "Bulk Checker",
  },
  {
    key: "history", icon: "🕓", label: "Script History", color: "#06b6d4",
    desc: "Scripts generated/released, all tools combined.",
    page: "history", localValue: (s) => s.script_total,
    group: "History",
  },
];

// Clicks the sidebar nav button for `page` (same as a real user click -
// runs whatever page-specific load logic that click handler already
// does), then optionally clicks a sub-nav button inside it so the
// analyst lands on the exact case/tab the card was about, not just the
// page's own default sub-tab.
function overviewNavigateTo(page, subAttr, subValue) {
  const navBtn = document.querySelector(`.nav-item[data-page="${page}"]`);
  if (!navBtn) return;
  if (navBtn.hidden) { showToast("You don't have access to that page."); return; }
  navBtn.click();
  if (subAttr && subValue) {
    const subBtn = document.querySelector(`[${subAttr}="${subValue}"]`);
    if (subBtn) subBtn.click();
  }
}

// Live counts, filled card by card from /api/overview/count/{key}
// (RJ 2026-10-01: every menu on Overview - loaded in parallel, max 4 at a
// time, so one slow query never holds the others back).
// overviewLive[key]: undefined = loading, null = failed, number = count.
let overviewLive = {};
let overviewLiveErr = {};
let overviewGen = 0;

function overviewCardVisible(c) {
  const nav = document.querySelector(`.nav-item[data-page="${c.page}"]`);
  return !nav || !nav.hidden;
}

function overviewValueHtml(c, stats) {
  if (c.nocount) return `<span class="overview-card-value is-empty" title="Count runs on the page">Open</span>`;
  const value = c.localValue ? c.localValue(stats) : overviewLive[c.key];
  if (value === undefined && !c.localValue) return `<span class="overview-card-value is-empty overview-card-loading">…</span>`;
  if (value === null || value === undefined) {
    const err = overviewLiveErr[c.key];
    return `<span class="overview-card-value is-empty"${err ? ` title="${escapeHtml(err)}"` : ""}>—</span>`;
  }
  return `<span class="overview-card-value">${Number(value).toLocaleString()}</span>`;
}

function overviewUpdateCard(c, stats) {
  const el = document.querySelector(`[data-overview-card="${c.key}"]`);
  if (!el) return;
  const top = el.querySelector(".overview-card-top");
  top.innerHTML = `<span class="overview-card-icon">${c.icon}</span>${overviewValueHtml(c, stats)}`;
  el.classList.toggle("is-alarm", !!c.critical && Number(overviewLive[c.key]) > 0);
}

function overviewRenderCards(stats) {
  const grid = $("#overview-cards");
  const groups = []; // preserves first-appearance order from OVERVIEW_CARDS
  OVERVIEW_CARDS.filter(overviewCardVisible).forEach((c) => {
    let g = groups.find((x) => x.name === c.group);
    if (!g) { g = { name: c.group, cards: [] }; groups.push(g); }
    g.cards.push(c);
  });
  grid.innerHTML = groups.map((g) => {
    const cardsHtml = g.cards.map((c) => {
      const valueHtml = overviewValueHtml(c, stats);
      const value = overviewLive[c.key];
      return (
        `<button type="button" class="overview-card${c.critical ? " overview-card-critical" : ""}${c.critical && Number(value) > 0 ? " is-alarm" : ""}" style="--overview-accent:${c.color}" data-overview-card="${c.key}">` +
        `<div class="overview-card-top"><span class="overview-card-icon">${c.icon}</span>${valueHtml}</div>` +
        `<div class="overview-card-label">${escapeHtml(c.label)}</div>` +
        `<div class="overview-card-desc">${escapeHtml(c.desc)}</div>` +
        `<div class="overview-card-go">Open →</div>` +
        `</button>`
      );
    }).join("");
    return (
      `<div class="overview-group">` +
      `<h3 class="overview-group-title">${escapeHtml(g.name)}</h3>` +
      `<div class="overview-group-grid">${cardsHtml}</div>` +
      `</div>`
    );
  }).join("");
  OVERVIEW_CARDS.forEach((c) => {
    const el = grid.querySelector(`[data-overview-card="${c.key}"]`);
    if (el) el.addEventListener("click", () => overviewNavigateTo(c.page, c.subAttr, c.subValue));
  });
}

// RJ: "i am more interested on the data not on the number of runs" - both
// charts below now plot the same live open-item counts the cards already
// show (stats.live, straight from each area's own query), NOT script_kind_
// counts/script_trend (those count how many times someone clicked
// Generate - a usage metric, not the actual backlog). script_kind_counts/
// script_trend are still returned by the API and still drive History's
// own page - just no longer charted here.
function overviewRenderPie(stats) {
  const canvas = $("#overview-pie-canvas");
  // One slice per Overview group (sum of its cards' live counts).
  const areas = [];
  OVERVIEW_CARDS.filter((c) => !c.localValue && !c.nocount && overviewCardVisible(c)).forEach((c) => {
    const v = overviewLive[c.key];
    if (v === null || v === undefined) return;
    let a = areas.find((x) => x.label === c.group);
    if (!a) { a = { label: c.group, value: 0, page: c.page, subAttr: c.subAttr, subValue: c.subValue }; areas.push(a); }
    a.value += Number(v) || 0;
  });

  const labels = areas.map((a) => a.label);
  const values = areas.map((a) => a.value);
  const onSliceClick = (label) => {
    const area = areas.find((a) => a.label === label);
    if (area) overviewNavigateTo(area.page, area.subAttr, area.subValue);
  };
  // RJ: "graphs and pie chart that are very modern like half moon loading" -
  // drawGaugeChart is the half-moon/speedometer variant (drop-in swap for
  // drawPieChart, same labels/values/options contract).
  drawGaugeChart(canvas, labels, values, { colors: labels.map((lab) => _categoricalColor(lab)), onSliceClick });

  const legendEl = $("#overview-pie-canvas-legend");
  if (!labels.length) {
    legendEl.innerHTML = `<span class="hint-text">${stats.has_connection ? "Loading counts…" : "No database connection yet."}</span>`;
    return;
  }
  legendEl.innerHTML = labels.map((lab, i) => `
    <span class="chart-legend-item" data-legend-label="${escapeHtml(lab)}">
      <span class="chart-legend-swatch" style="background:${_categoricalColor(lab)}"></span>${escapeHtml(lab)} (${values[i]})
    </span>
  `).join("");
  legendEl.querySelectorAll("[data-legend-label]").forEach((el) => {
    el.addEventListener("click", () => onSliceClick(el.dataset.legendLabel));
  });
}

function overviewRenderTrend(stats) {
  const canvas = $("#overview-trend-canvas");
  const live = overviewLive;
  const cases = [
    { label: "Case 1", value: live.billissuance_case1, subValue: "case1" },
    { label: "Case 2", value: live.billissuance_case2, subValue: "case2" },
    { label: "Case 3", value: live.billissuance_case3, subValue: "case3" },
    { label: "Case 4", value: live.billissuance_case4, subValue: "case4" },
  ].filter((c) => c.value !== null && c.value !== undefined);
  drawBarChart(canvas, cases.map((c) => c.label), cases.map((c) => c.value), {
    colors: cases.map((c) => _categoricalColor(c.label)),
    onBarClick: (label) => {
      const c = cases.find((x) => x.label === label);
      if (c) overviewNavigateTo("billissuance", "data-biss-sub", c.subValue);
    },
  });
}

async function loadOverview() {
  const grid = $("#overview-cards");
  const refreshBtn = $("#overview-refresh-btn");
  const wasEmpty = !grid.children.length;
  if (wasEmpty) grid.innerHTML = `<div class="overview-empty-state">Loading…</div>`;
  if (refreshBtn) { refreshBtn.disabled = true; refreshBtn.textContent = "🔄 Refreshing…"; }
  const gen = ++overviewGen;
  try {
    const stats = await api("/api/overview/stats");
    $("#overview-connection-note").hidden = !!stats.has_connection;
    if (!stats.has_connection) {
      $("#overview-connection-note").textContent =
        "No database connection configured yet — connect one in Settings to see live counts.";
    }
    // Keep previous counts visible while refreshing; mark them loading only on first load.
    if (wasEmpty) { overviewLive = {}; overviewLiveErr = {}; }
    overviewRenderCards(stats);
    overviewRenderPie(stats);
    overviewRenderTrend(stats);
    if (stats.has_connection) {
      const queue = OVERVIEW_CARDS.filter((c) => !c.localValue && !c.nocount && overviewCardVisible(c));
      const worker = async () => {
        while (queue.length && gen === overviewGen) {
          const c = queue.shift();
          try {
            const r = await api(`/api/overview/count/${encodeURIComponent(c.key)}`);
            overviewLive[c.key] = r.count ?? null;
            if (r.error) overviewLiveErr[c.key] = r.error; else delete overviewLiveErr[c.key];
          } catch (err) {
            overviewLive[c.key] = null;
            overviewLiveErr[c.key] = err.message;
          }
          if (gen === overviewGen) overviewUpdateCard(c, stats);
        }
      };
      await Promise.all([worker(), worker(), worker(), worker()]);
      if (gen !== overviewGen) return;
      overviewRenderPie(stats);
      overviewRenderTrend(stats);
    }
    const lastUpdated = $("#overview-last-updated");
    if (lastUpdated) lastUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    grid.innerHTML = `<div class="overview-empty-state">Couldn't load overview: ${escapeHtml(err.message || "unknown error")}</div>`;
  } finally {
    if (refreshBtn && gen === overviewGen) { refreshBtn.disabled = false; refreshBtn.textContent = "🔄 Refresh"; }
  }
}
$("#overview-refresh-btn")?.addEventListener("click", () => loadOverview());

// ---------------- Nav ----------------
$$(".nav-item[data-page]").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".nav-item[data-page]").forEach((b) => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    const page = btn.dataset.page;
    $$(".page").forEach((p) => p.classList.remove("is-active"));
    $(`#page-${page}`).classList.add("is-active");
    if (page === "overview") loadOverview();
    if (page === "history") loadHistory();
    if (page === "dashboard") loadDashboard();
    if (page === "ai") loadAIPage();
    if (page === "tools") loadToolsPage();
    if (page === "settings") loadSettingsPage();
    if (page === "dateanomaly") { daBatchRefreshRecentRuns(); daHistoryRefresh(); }
    if (page === "bulkchecker") bcOnPageShown();
    if (page === "wrongbilledconsumption") wbcOnPageShown();
    if (page === "wrongbill") wbOnPageShown();
  });
});

// ---------------- Date Anomaly: in-page sub-nav (Single/Batch/Detect All/History) ----------------
// A lighter-weight tab switch scoped inside #page-dateanomaly, same
// is-active toggling idiom as the top-level nav above but keyed off
// data-da-sub instead of data-page. Defaults to "single" (the markup's
// own initial is-active classes) since that's the most common starting
// point - investigating one NISS you already know about.
$$(".da-subnav-btn[data-da-sub]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const sub = btn.dataset.daSub;
    $$(".da-subnav-btn[data-da-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    $$(".da-subpage[data-da-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.daSub === sub));
    if (sub === "history") daHistoryRefresh();
    if (sub === "batch") daBatchRefreshRecentRuns();
  });
});

// ---------------- Theme toggle (light/dark content area) ----------------
(function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("scriptgen_theme"); } catch (_) { /* private mode etc. */ }
  if (saved === "dark") document.documentElement.setAttribute("data-theme", "dark");
})();

$("#theme-toggle-btn").addEventListener("click", () => {
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  if (isDark) {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", "dark");
  }
  try { localStorage.setItem("scriptgen_theme", isDark ? "light" : "dark"); } catch (_) { /* ignore */ }
});

// ---------------- Sidebar collapse (reclaim horizontal space) ----------------
function applySidebarCollapseLabel(collapsed) {
  const btn = $("#sidebar-collapse-btn");
  if (!btn) return;
  btn.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
}

(function initSidebarCollapse() {
  let saved = null;
  try { saved = localStorage.getItem("scriptgen_sidebar_collapsed"); } catch (_) { /* private mode etc. */ }
  const sidebar = $("#sidebar");
  if (saved === "1" && sidebar) sidebar.classList.add("is-collapsed");
  applySidebarCollapseLabel(saved === "1");
})();

$("#sidebar-collapse-btn").addEventListener("click", () => {
  const sidebar = $("#sidebar");
  const isCollapsed = sidebar.classList.toggle("is-collapsed");
  applySidebarCollapseLabel(isCollapsed);
  try { localStorage.setItem("scriptgen_sidebar_collapsed", isCollapsed ? "1" : "0"); } catch (_) { /* ignore */ }
});

function goToScriptPage() {
  $$(".nav-item[data-page]").forEach((b) => b.classList.toggle("is-active", b.dataset.page === "script"));
  $$(".page").forEach((p) => p.classList.remove("is-active"));
  $("#page-script").classList.add("is-active");
}

// ---------------- Connection status ----------------
async function refreshConnectionStatus() {
  const dot = $("#dot-db");
  const text = $("#db-status-text");
  dot.className = "status-dot is-pending";
  text.textContent = "Checking connection…";
  try {
    const status = await api("/api/connection/status");
    dot.className = "status-dot " + (status.connected ? "is-ok" : "is-bad");
    text.textContent = status.connected
      ? `Connected (${status.elapsed_ms.toFixed(0)} ms)`
      : status.message;
  } catch (err) {
    dot.className = "status-dot is-bad";
    text.textContent = "Status check failed.";
  }
  $("#dot-backend").className = "status-dot is-ok";
}

// ---------------- Recent queries ----------------
async function loadRecentQueries() {
  try {
    const data = await api("/api/query/recent");
    state.recentQueries = data.queries || [];
    renderRecentMenu();
  } catch (_) { /* not fatal */ }
}

function renderRecentMenu() {
  const menu = $("#recent-menu");
  menu.innerHTML = "";
  if (!state.recentQueries.length) {
    const item = document.createElement("div");
    item.className = "dropdown-item";
    item.textContent = "(no queries run yet)";
    menu.appendChild(item);
    return;
  }
  state.recentQueries.forEach((sql) => {
    const item = document.createElement("div");
    item.className = "dropdown-item";
    item.textContent = sql.replace(/\s+/g, " ");
    item.title = sql;
    item.addEventListener("click", () => {
      $("#sql-editor").value = sql;
      menu.hidden = true;
    });
    menu.appendChild(item);
  });
}

$("#recent-btn").addEventListener("click", () => {
  $("#recent-menu").hidden = !$("#recent-menu").hidden;
});
document.addEventListener("click", (e) => {
  if (!$("#recent-dropdown").contains(e.target)) $("#recent-menu").hidden = true;
});

// ---------------- Format query ----------------
$("#format-btn").addEventListener("click", async () => {
  const editor = $("#sql-editor");
  const current = editor.value;
  if (!current.trim()) return;
  try {
    const { sql } = await api("/api/query/format", { method: "POST", body: { sql: current } });
    if (sql.trim() === current.trim()) {
      showToast("Query already formatted.");
    } else {
      editor.value = sql;
      showToast("Query formatted.");
    }
  } catch (err) {
    showToast(err.message, true);
  }
});

// ---------------- Run query ----------------
$("#run-btn").addEventListener("click", runQuery);

async function runQuery() {
  const sql = $("#sql-editor").value.trim();
  if (!sql) return;
  const runBtn = $("#run-btn");
  runBtn.disabled = true;
  $("#query-status").textContent = "Running query…";
  try {
    const result = await api("/api/query/run", { method: "POST", body: { sql } });
    state.columns = result.columns;
    state.displayRows = result.display_rows;
    state.editedRows = result.display_rows.map((row) => row.slice());
    state.keyColumns = new Set();
    state.changedByRow = {};
    state.sourceSchema = result.source_schema;
    state.sourceTable = result.source_table;

    $("#target-schema").value = result.source_schema || "dbo";
    $("#target-table").value = result.source_table || "";

    $("#query-status").textContent =
      `${result.row_count} row(s) in ${result.elapsed_ms.toFixed(0)} ms.`;

    renderKeyChips();
    renderGrid();
    loadRecentQueries();
  } catch (err) {
    $("#query-status").textContent = "Query failed.";
    showToast(err.message, true);
  } finally {
    runBtn.disabled = false;
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key === "F5") {
    e.preventDefault();
    runQuery();
  }
});

// ---------------- Key column chips ----------------
function renderKeyChips() {
  const row = $("#key-chip-row");
  row.innerHTML = "";
  state.columns.forEach((col) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip" + (state.keyColumns.has(col) ? " is-selected" : "");
    chip.textContent = col;
    chip.addEventListener("click", () => {
      if (state.keyColumns.has(col)) state.keyColumns.delete(col);
      else state.keyColumns.add(col);
      renderKeyChips();
      refreshDiff();
    });
    row.appendChild(chip);
  });
}

$("#select-all-keys-btn").addEventListener("click", () => {
  state.keyColumns = new Set(state.columns);
  renderKeyChips();
  refreshDiff();
});
$("#clear-keys-btn").addEventListener("click", () => {
  state.keyColumns = new Set();
  renderKeyChips();
  refreshDiff();
});
$("#autodetect-key-btn").addEventListener("click", async () => {
  const schema = $("#target-schema").value.trim() || "dbo";
  const table = $("#target-table").value.trim();
  if (!table) { showToast("Set a target table first.", true); return; }
  try {
    const { key_columns } = await api("/api/keys/lookup", { method: "POST", body: { schema, table } });
    if (!key_columns.length) {
      showToast("No primary key found for that table - pick key column(s) by hand.", true);
      return;
    }
    state.keyColumns = new Set(key_columns);
    renderKeyChips();
    refreshDiff();
  } catch (err) {
    showToast(err.message, true);
  }
});

["target-schema", "target-table", "program-field"].forEach((id) => {
  $(`#${id}`).addEventListener("input", () => refreshDiff());
});

// ---------------- Grid ----------------
function renderGrid() {
  const thead = document.querySelector("#data-grid thead tr");
  const tbody = document.querySelector("#data-grid tbody");
  thead.innerHTML = '<th class="preview-col">🔗 SQL Preview</th><th>#</th>' +
    state.columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("");

  tbody.innerHTML = "";
  state.editedRows.forEach((row, rowIndex) => {
    const tr = document.createElement("tr");
    tr.dataset.rowIndex = String(rowIndex);

    const previewTd = document.createElement("td");
    previewTd.className = "preview-col";
    previewTd.dataset.role = "preview";
    tr.appendChild(previewTd);

    const idxTd = document.createElement("td");
    idxTd.className = "row-idx";
    idxTd.textContent = String(rowIndex + 1);
    tr.appendChild(idxTd);

    row.forEach((val, colIndex) => {
      const td = document.createElement("td");
      td.textContent = val;
      td.contentEditable = "true";
      td.spellcheck = false;
      td.dataset.row = String(rowIndex);
      td.dataset.col = String(colIndex);
      td.addEventListener("blur", onCellEdited);
      td.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); td.blur(); }
      });
      tr.appendChild(td);
    });

    tbody.appendChild(tr);
  });
}

function onCellEdited(e) {
  const td = e.target;
  const rowIndex = Number(td.dataset.row);
  const colIndex = Number(td.dataset.col);
  state.editedRows[rowIndex][colIndex] = td.textContent;
  refreshDiff();
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ---------------- Diff / live preview (debounced) ----------------
let diffTimer = null;
function refreshDiff() {
  clearTimeout(diffTimer);
  diffTimer = setTimeout(doRefreshDiff, 180);
}

async function doRefreshDiff() {
  if (!state.columns.length) return;
  const body = {
    edited_rows: state.editedRows,
    key_columns: Array.from(state.keyColumns),
    schema: $("#target-schema").value.trim(),
    table: $("#target-table").value.trim(),
    program: $("#program-field").value.trim() || undefined,
  };
  try {
    const { changes } = await api("/api/grid/diff", { method: "POST", body });
    applyDiffToGrid(changes);
  } catch (err) {
    // A transient 400 (e.g. mid-edit row-length mismatch) isn't worth
    // surfacing to the user - the next edit event will settle it, same
    // spirit as the desktop app's ValueError-swallow in _refresh_grid_highlights.
  }
}

function applyDiffToGrid(changes) {
  state.changedByRow = {};
  changes.forEach((c) => { state.changedByRow[c.row_index] = c; });

  $$("#data-grid tbody tr").forEach((tr) => {
    const rowIndex = Number(tr.dataset.rowIndex);
    const change = state.changedByRow[rowIndex];
    tr.classList.toggle("row-is-changed", Boolean(change));
    const previewTd = tr.querySelector('[data-role="preview"]');
    previewTd.textContent = change ? change.preview_line : "";

    tr.querySelectorAll("td[data-col]").forEach((td) => {
      const colName = state.columns[Number(td.dataset.col)];
      const isChanged = change && change.changed_columns.includes(colName);
      td.classList.toggle("is-changed", Boolean(isChanged));
    });
  });
}

// ---------------- Script generation ----------------
async function generateScript(kind) {
  const table = $("#target-table").value.trim();
  if (!table) { showToast("Set a target table first.", true); return; }
  const body = {
    edited_rows: state.editedRows,
    key_columns: Array.from(state.keyColumns),
    schema: $("#target-schema").value.trim(),
    table,
    program: $("#program-field").value.trim() || undefined,
    kind,
  };
  try {
    const result = await api("/api/script/generate", { method: "POST", body });
    $("#script-title").textContent = kind === "rollback" ? "Rollback script" : "Generated script";
    $("#script-output").textContent = result.sql_text;
    $("#script-output").dataset.filename = kind === "rollback" ? "rollback_script.sql" : "update_script.sql";
    goToScriptPage();
    if (result.warning_count) {
      showToast(`Generated with ${result.warning_count} warning(s) - review before running.`, true);
    }
  } catch (err) {
    showToast(err.message, true);
  }
}

$("#generate-update-btn").addEventListener("click", () => generateScript("update"));
$("#generate-rollback-btn").addEventListener("click", () => generateScript("rollback"));

$("#copy-script-btn").addEventListener("click", async () => {
  const text = $("#script-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Script copied to clipboard.");
  } catch (_) {
    showToast("Couldn't copy - select and copy manually.", true);
  }
});

$("#download-script-btn").addEventListener("click", () => {
  const text = $("#script-output").textContent;
  const filename = $("#script-output").dataset.filename || "script.sql";
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

// ---------------- History ----------------
async function loadHistory() {
  const status = $("#history-status");
  const tbody = document.querySelector("#history-table tbody");
  status.textContent = "Loading…";
  try {
    const mineOnly = !$("#history-team-toggle").checked;
    const { entries } = await api(`/api/history?mine_only=${mineOnly}`);
    renderHistory(entries);
    status.textContent = entries.length
      ? `${entries.length} script(s).`
      : "Nothing generated yet — go to Workspace and generate an Update or Rollback script.";
  } catch (err) {
    status.textContent = "Couldn't load history.";
    showToast(err.message, true);
  }
}

function renderHistory(entries) {
  const tbody = document.querySelector("#history-table tbody");
  tbody.innerHTML = "";
  entries.forEach((e) => {
    const tr = document.createElement("tr");
    const table = e.schema_name ? `${e.schema_name}.${e.table_name}` : e.table_name;
    tr.innerHTML = `
      <td>${escapeHtml(e.created_at_utc.slice(0, 16).replace("T", " "))}</td>
      <td>${escapeHtml(e.username)}</td>
      <td>${escapeHtml(e.kind)}</td>
      <td>${escapeHtml(table)}</td>
      <td>${e.statement_count}</td>
      <td>${e.warning_count || ""}</td>
      <td>${escapeHtml(e.source)}</td>
      <td></td>
    `;
    const viewTd = tr.lastElementChild;
    const viewBtn = document.createElement("button");
    viewBtn.type = "button";
    viewBtn.className = "btn btn-pill-sm";
    viewBtn.textContent = "View";
    viewBtn.addEventListener("click", () => viewHistoryEntry(e.id));
    viewTd.appendChild(viewBtn);
    tbody.appendChild(tr);
  });
}

async function viewHistoryEntry(id) {
  try {
    const entry = await api(`/api/history/${id}`);
    $("#script-title").textContent =
      `History #${entry.id} — ${entry.kind} — ${entry.table_name} (${entry.created_at_utc.slice(0, 16).replace("T", " ")} UTC, ${entry.username})`;
    $("#script-output").textContent = entry.sql_text;
    $("#script-output").dataset.filename = `history_${entry.id}.sql`;
    goToScriptPage();
  } catch (err) {
    showToast(err.message, true);
  }
}

$("#history-refresh-btn").addEventListener("click", loadHistory);
$("#history-team-toggle").addEventListener("change", loadHistory);

// ---------------- Settings: connections + AI config ----------------
let _editingConnectionKey = null; // null = the form is in "Add" mode

async function loadSettingsPage() {
  const tasks = [];
  if (state.role === "admin") {
    tasks.push(refreshConnectionsTable(), refreshAIConfigForm(), refreshEmailSettingsForm(), refreshUsersTable(), refreshMenuAccessTable());
  }
  await Promise.all(tasks);
}

// ---------------- Menu Access (admin only) ----------------
// Lets an admin pick which sidebar pages each role sees - see web/
// menu_access.py's module docstring for why this is a visibility
// convenience layered on top of the existing role gates, not a second
// permission system. The table is rebuilt from the server's own
// {menus, access} response each time the Settings page loads (rather
// than assuming the DOM's checkbox state is still correct) so a change
// another admin made in a different session is never silently clobbered
// by a stale Save from this one.
let _menuAccessAdminRequired = "settings";

async function refreshMenuAccessTable() {
  try {
    const { menus, access, admin_required_menu } = await api("/api/config/menu-access");
    _menuAccessAdminRequired = admin_required_menu;
    const tbody = document.querySelector("#menu-access-table tbody");
    tbody.innerHTML = "";
    menus.forEach((menu) => {
      const tr = document.createElement("tr");
      const roleCell = (role) => {
        const locked = role === "admin" && menu.id === admin_required_menu;
        const checked = locked || (access[role] || []).includes(menu.id);
        const lockAttrs = locked ? ` disabled title="Admin must always keep this - otherwise no admin could get back here to fix it."` : "";
        return `<td><input type="checkbox" data-menu-role-cb data-menu-id="${escapeHtml(menu.id)}" data-role="${role}" ${checked ? "checked" : ""}${lockAttrs} /></td>`;
      };
      tr.innerHTML = `<td>${escapeHtml(menu.label)}</td>${roleCell("viewer")}${roleCell("editor")}${roleCell("admin")}`;
      tbody.appendChild(tr);
    });
  } catch (err) {
    showToast(err.message, true);
  }
}

$("#menu-access-save-btn").addEventListener("click", async () => {
  const access = { viewer: [], editor: [], admin: [] };
  $$("#menu-access-table [data-menu-role-cb]").forEach((cb) => {
    // A disabled checkbox (the locked admin/settings cell) doesn't
    // participate in a form's values normally, but this isn't a <form> -
    // read .checked directly regardless of .disabled so the locked cell
    // still gets submitted as checked rather than silently dropping the
    // one entry the server-side guard requires.
    if (cb.checked) access[cb.dataset.role].push(cb.dataset.menuId);
  });
  try {
    await api("/api/config/menu-access", { method: "PUT", body: { access } });
    showToast("Menu access saved. Takes effect for each role's next login (or page refresh).");
  } catch (err) {
    showToast(err.message, true);
  }
});

// ---------------- My Account (self-service password change) ----------------
$("#change-password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const resultEl = $("#cp-result");
  resultEl.textContent = "";
  const oldPw = $("#cp-old").value;
  const newPw = $("#cp-new").value;
  const confirmPw = $("#cp-confirm").value;
  if (newPw !== confirmPw) { resultEl.textContent = "New passwords don't match."; return; }
  try {
    await api("/api/account/change-password", { method: "POST", body: { old_password: oldPw, new_password: newPw } });
    $("#change-password-form").reset();
    showToast("Password changed.");
  } catch (err) {
    resultEl.textContent = err.message;
  }
});

// ---------------- Users (admin only) ----------------
const ROLE_LABELS = { viewer: "Viewer", editor: "Editor", admin: "Admin" };

async function refreshUsersTable() {
  try {
    const { users } = await api("/api/users");
    const tbody = document.querySelector("#users-table tbody");
    tbody.innerHTML = "";
    users.forEach((u) => {
      const tr = document.createElement("tr");
      const statusBadge = u.active
        ? '<span class="badge">Active</span>'
        : '<span class="hint-text">Deactivated</span>';
      const changeFlag = u.must_change_password ? " <span class=\"hint-text\">(must change password)</span>" : "";
      tr.innerHTML = `
        <td>${escapeHtml(u.username)}${changeFlag}</td>
        <td></td>
        <td>${statusBadge}</td>
        <td>${escapeHtml((u.created_at_utc || "").slice(0, 10))}</td>
        <td></td>
      `;
      const roleTd = tr.children[1];
      const roleSelect = document.createElement("select");
      roleSelect.className = "text-input text-input-sm";
      Object.entries(ROLE_LABELS).forEach(([value, label]) => {
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = label;
        if (value === u.role) opt.selected = true;
        roleSelect.appendChild(opt);
      });
      roleSelect.disabled = u.username === state.username; // can't demote/promote yourself by accident
      roleSelect.addEventListener("change", async () => {
        try {
          await api(`/api/users/${encodeURIComponent(u.username)}/role`, { method: "PUT", body: { role: roleSelect.value } });
          showToast(`'${u.username}' is now ${ROLE_LABELS[roleSelect.value]}.`);
          refreshUsersTable();
        } catch (err) {
          showToast(err.message, true);
          refreshUsersTable();
        }
      });
      roleTd.appendChild(roleSelect);

      const actionsTd = tr.lastElementChild;
      const isSelf = u.username === state.username;

      const toggleBtn = document.createElement("button");
      toggleBtn.className = "row-action-btn";
      toggleBtn.textContent = u.active ? "Deactivate" : "Activate";
      toggleBtn.disabled = isSelf && u.active;
      toggleBtn.addEventListener("click", async () => {
        try {
          const action = u.active ? "deactivate" : "reactivate";
          await api(`/api/users/${encodeURIComponent(u.username)}/${action}`, { method: "POST" });
          showToast(`'${u.username}' ${u.active ? "deactivated" : "activated"}.`);
          refreshUsersTable();
        } catch (err) {
          showToast(err.message, true);
        }
      });
      actionsTd.appendChild(toggleBtn);

      const resetBtn = document.createElement("button");
      resetBtn.className = "row-action-btn";
      resetBtn.textContent = "Reset Password";
      resetBtn.addEventListener("click", async () => {
        const newPw = prompt(`New temporary password for '${u.username}' (min 8 characters):`);
        if (newPw === null) return;
        if (newPw.length < 8) { showToast("Password must be at least 8 characters.", true); return; }
        try {
          await api(`/api/users/${encodeURIComponent(u.username)}/reset-password`, { method: "POST", body: { new_password: newPw } });
          showToast(`Password reset for '${u.username}' - they'll be asked to change it on next login.`);
          refreshUsersTable();
        } catch (err) {
          showToast(err.message, true);
        }
      });
      actionsTd.appendChild(resetBtn);

      const deleteBtn = document.createElement("button");
      deleteBtn.className = "row-action-btn is-danger";
      deleteBtn.textContent = "Delete";
      deleteBtn.disabled = isSelf;
      deleteBtn.addEventListener("click", async () => {
        if (!confirm(`Permanently delete user '${u.username}'? This can't be undone.`)) return;
        try {
          await api(`/api/users/${encodeURIComponent(u.username)}`, { method: "DELETE" });
          showToast(`'${u.username}' deleted.`);
          refreshUsersTable();
        } catch (err) {
          showToast(err.message, true);
        }
      });
      actionsTd.appendChild(deleteBtn);

      tbody.appendChild(tr);
    });
  } catch (err) {
    showToast(err.message, true);
  }
}

$("#add-user-btn").addEventListener("click", () => { $("#user-form").hidden = false; });
$("#user-form-cancel-btn").addEventListener("click", () => { $("#user-form").hidden = true; $("#user-form").reset(); });

$("#user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    username: $("#user-username").value.trim(),
    password: $("#user-password").value,
    role: $("#user-role").value,
  };
  if (!body.username) { showToast("Enter a username.", true); return; }
  try {
    await api("/api/users", { method: "POST", body });
    showToast(`User '${body.username}' created.`);
    $("#user-form").hidden = true;
    $("#user-form").reset();
    refreshUsersTable();
  } catch (err) {
    showToast(err.message, true);
  }
});

async function refreshConnectionsTable() {
  try {
    const { connections } = await api("/api/config/connections");
    const tbody = document.querySelector("#connections-table tbody");
    tbody.innerHTML = "";
    connections.forEach((c) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(c.name)}</td>
        <td>${escapeHtml(c.server)}:${c.port}</td>
        <td>${escapeHtml(c.database || "(default)")}</td>
        <td>${escapeHtml(c.username)}</td>
        <td>${c.is_active ? '<span class="badge">Active</span>' : ""}</td>
        <td></td>
      `;
      const actionsTd = tr.lastElementChild;
      if (!c.is_active) {
        const activateBtn = document.createElement("button");
        activateBtn.className = "row-action-btn";
        activateBtn.textContent = "Set Active";
        activateBtn.addEventListener("click", () => activateConnection(c.key));
        actionsTd.appendChild(activateBtn);
      }
      const editBtn = document.createElement("button");
      editBtn.className = "row-action-btn";
      editBtn.textContent = "Edit";
      editBtn.addEventListener("click", () => openConnectionForm(c));
      actionsTd.appendChild(editBtn);
      const deleteBtn = document.createElement("button");
      deleteBtn.className = "row-action-btn is-danger";
      deleteBtn.textContent = "Delete";
      deleteBtn.addEventListener("click", () => deleteConnection(c.key, c.is_active));
      actionsTd.appendChild(deleteBtn);
      tbody.appendChild(tr);
    });
  } catch (err) {
    showToast(err.message, true);
  }
}

async function activateConnection(key) {
  try {
    await api(`/api/config/connections/${encodeURIComponent(key)}/activate`, { method: "POST" });
    showToast("Active connection switched.");
    refreshConnectionsTable();
    refreshConnectionStatus();
    refreshSidebarConnectionName();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function deleteConnection(key, isActive) {
  if (isActive) { showToast("Can't remove the active connection - switch to another one first.", true); return; }
  try {
    await api(`/api/config/connections/${encodeURIComponent(key)}`, { method: "DELETE" });
    showToast("Connection deleted.");
    refreshConnectionsTable();
  } catch (err) {
    showToast(err.message, true);
  }
}

function openConnectionForm(conn) {
  const form = $("#connection-form");
  form.hidden = false;
  $("#conn-test-result").textContent = "";
  if (conn) {
    _editingConnectionKey = conn.key;
    $("#connection-form-title").textContent = `Edit Connection — ${conn.name}`;
    $("#conn-name").value = conn.name;
    $("#conn-server").value = conn.server;
    $("#conn-port").value = conn.port;
    $("#conn-database").value = conn.database;
    $("#conn-username").value = conn.username;
    $("#conn-password").value = "";
    $("#conn-timeout").value = conn.timeout_seconds;
    $("#conn-notes").value = conn.notes;
  } else {
    _editingConnectionKey = null;
    $("#connection-form-title").textContent = "Add Connection";
    ["conn-name", "conn-server", "conn-database", "conn-username", "conn-password", "conn-notes"].forEach((id) => { $(`#${id}`).value = ""; });
    $("#conn-port").value = 1433;
    $("#conn-timeout").value = 10;
  }
}

$("#add-connection-btn").addEventListener("click", () => openConnectionForm(null));
$("#conn-cancel-btn").addEventListener("click", () => { $("#connection-form").hidden = true; });

function _connectionFormBody() {
  return {
    name: $("#conn-name").value.trim(),
    server: $("#conn-server").value.trim(),
    port: Number($("#conn-port").value) || 1433,
    database: $("#conn-database").value.trim(),
    username: $("#conn-username").value.trim(),
    password: $("#conn-password").value,
    timeout_seconds: Number($("#conn-timeout").value) || 10,
    notes: $("#conn-notes").value.trim(),
  };
}

$("#conn-test-btn").addEventListener("click", async () => {
  const resultEl = $("#conn-test-result");
  resultEl.textContent = "Testing…";
  try {
    const body = _editingConnectionKey
      ? { key: _editingConnectionKey, password: $("#conn-password").value }
      : _connectionFormBody();
    const result = await api("/api/config/connections/test", { method: "POST", body });
    resultEl.textContent = result.connected
      ? `✓ ${result.message} (${result.elapsed_ms.toFixed(0)} ms)`
      : `✗ ${result.message}`;
  } catch (err) {
    resultEl.textContent = `✗ ${err.message}`;
  }
});

$("#connection-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = _connectionFormBody();
  if (!body.name || !body.server) { showToast("Name and server are required.", true); return; }
  try {
    if (_editingConnectionKey) {
      await api(`/api/config/connections/${encodeURIComponent(_editingConnectionKey)}`, { method: "PUT", body });
      showToast("Connection updated.");
    } else {
      await api("/api/config/connections", { method: "POST", body });
      showToast("Connection added.");
    }
    $("#connection-form").hidden = true;
    refreshConnectionsTable();
    refreshConnectionStatus();
    refreshSidebarConnectionName();
  } catch (err) {
    showToast(err.message, true);
  }
});

async function refreshAIConfigForm() {
  try {
    const cfg = await api("/api/config/ai");
    $("#ai-model-field").value = cfg.model;
    $("#ai-enabled-checkbox").checked = cfg.enabled;
    $("#ai-key-field").value = "";
    $("#ai-key-field").placeholder = cfg.has_key ? "(leave blank to keep existing key)" : "(no key saved yet)";
    $("#ai-key-status-text").textContent = cfg.has_key ? (cfg.enabled ? "✓ Key saved, AI Assist enabled" : "Key saved, but disabled") : "✗ No key saved";
  } catch (err) {
    showToast(err.message, true);
  }
}

// ---------------- Email alerts (admin; RJ 2026-09-30) ----------------
async function refreshEmailSettingsForm() {
  try {
    const c = await api("/api/settings/email");
    $("#em-host").value = c.smtp_host || "";
    $("#em-port").value = c.smtp_port || 587;
    $("#em-username").value = c.username || "";
    $("#em-password").value = "";
    $("#em-password").placeholder = c.has_password ? "(saved — leave blank to keep)" : "(no password saved yet)";
    $("#em-from").value = c.from_addr || "";
    $("#em-recipients").value = (c.recipients || []).join("; ");
    $("#em-hour").value = c.send_hour ?? 7;
    $("#em-retry").value = c.retry_minutes ?? 15;
    $("#em-tls").checked = !!c.use_tls;
    $("#em-enabled").checked = !!c.alerts_enabled;
    $("#em-result").textContent = c.smtp_host ? "" : "Not configured yet.";
  } catch (err) {
    showToast(err.message, true);
  }
}
async function saveEmailSettings() {
  const body = {
    smtp_host: $("#em-host").value.trim(),
    smtp_port: Number($("#em-port").value) || 587,
    use_tls: $("#em-tls").checked,
    username: $("#em-username").value.trim(),
    password: $("#em-password").value || null,
    from_addr: $("#em-from").value.trim(),
    recipients: $("#em-recipients").value.split(/[;,\s]+/).map((s) => s.trim()).filter(Boolean),
    alerts_enabled: $("#em-enabled").checked,
    send_hour: Number($("#em-hour").value),
    retry_minutes: Number($("#em-retry").value) || 15,
  };
  await api("/api/settings/email", { method: "POST", body });
}
$("#em-save-btn").addEventListener("click", async () => {
  try {
    await saveEmailSettings();
    showToast("Email alert settings saved.");
    refreshEmailSettingsForm();
  } catch (err) {
    showToast(err.message, true);
  }
});
$("#em-test-btn").addEventListener("click", async () => {
  $("#em-result").textContent = "Saving + sending test…";
  try {
    await saveEmailSettings();
    const r = await api("/api/settings/email/test", { method: "POST" });
    $("#em-result").textContent = `✓ Test e-mail sent to ${(r.sent_to || []).join(", ")}`;
    refreshEmailSettingsForm();
  } catch (err) {
    $("#em-result").textContent = `✗ ${err.message}`;
  }
});

$("#ai-key-field").addEventListener("input", () => {
  if ($("#ai-key-field").value) $("#ai-enabled-checkbox").checked = true;
});

$("#ai-save-btn").addEventListener("click", async () => {
  const body = {
    model: $("#ai-model-field").value.trim() || "gemini-3.6-flash",
    api_key: $("#ai-key-field").value,
    enabled: $("#ai-enabled-checkbox").checked,
  };
  try {
    await api("/api/config/ai", { method: "POST", body });
    showToast("AI settings saved.");
    refreshAIConfigForm();
  } catch (err) {
    showToast(err.message, true);
  }
});

$("#ai-test-key-btn").addEventListener("click", async () => {
  const statusEl = $("#ai-key-status-text");
  statusEl.textContent = "Testing…";
  try {
    const body = { api_key: $("#ai-key-field").value, model: $("#ai-model-field").value.trim() };
    const result = await api("/api/config/ai/test", { method: "POST", body });
    statusEl.textContent = result.success ? "✓ Key works" : `✗ ${result.text}`;
  } catch (err) {
    statusEl.textContent = `✗ ${err.message}`;
  }
});

// ---------------- Tools: Saved Queries / Schema Validation / Snapshot Diff ----------------
async function loadToolsPage() {
  await Promise.all([refreshSavedQueriesTable(), refreshSnapshotSelects()]);
}

async function refreshSavedQueriesTable() {
  try {
    const { queries } = await api("/api/queries/saved");
    const tbody = document.querySelector("#saved-queries-table tbody");
    tbody.innerHTML = "";
    queries.forEach((q) => {
      const tr = document.createElement("tr");
      const preview = q.sql.replace(/\s+/g, " ");
      tr.innerHTML = `<td>${escapeHtml(q.name)}</td><td title="${escapeHtml(q.sql)}">${escapeHtml(preview.slice(0, 90))}${preview.length > 90 ? "…" : ""}</td><td></td>`;
      const actionsTd = tr.lastElementChild;
      const loadBtn = document.createElement("button");
      loadBtn.className = "row-action-btn";
      loadBtn.textContent = "Load";
      loadBtn.addEventListener("click", () => {
        $("#sql-editor").value = q.sql;
        $$(".nav-item[data-page]").forEach((b) => b.classList.toggle("is-active", b.dataset.page === "workspace"));
        $$(".page").forEach((p) => p.classList.remove("is-active"));
        $("#page-workspace").classList.add("is-active");
        showToast(`Loaded '${q.name}'.`);
      });
      const deleteBtn = document.createElement("button");
      deleteBtn.className = "row-action-btn is-danger";
      deleteBtn.textContent = "Delete";
      deleteBtn.disabled = state.role === "viewer";
      deleteBtn.addEventListener("click", async () => {
        await api(`/api/queries/saved/${encodeURIComponent(q.name)}`, { method: "DELETE" });
        refreshSavedQueriesTable();
      });
      actionsTd.appendChild(loadBtn);
      actionsTd.appendChild(deleteBtn);
      tbody.appendChild(tr);
    });
  } catch (err) {
    showToast(err.message, true);
  }
}

$("#save-query-btn").addEventListener("click", async () => {
  const name = $("#save-query-name").value.trim();
  const sql = $("#sql-editor").value.trim();
  if (!name || !sql) { showToast("Enter a name (the current Workspace query is used as-is).", true); return; }
  try {
    await api("/api/queries/saved", { method: "POST", body: { name, sql } });
    $("#save-query-name").value = "";
    showToast(`Saved '${name}'.`);
    refreshSavedQueriesTable();
  } catch (err) {
    showToast(err.message, true);
  }
});

$("#validate-schema-btn").addEventListener("click", async () => {
  const out = $("#schema-validate-output");
  out.classList.remove("is-error");
  out.textContent = "Validating…";
  try {
    const body = {
      schema: $("#target-schema").value.trim() || "dbo",
      table: $("#target-table").value.trim(),
      key_columns: Array.from(state.keyColumns),
    };
    const result = await api("/api/schema/validate", { method: "POST", body });
    if (!result.table_exists) {
      out.classList.add("is-error");
      out.textContent = `Table '${body.schema}.${body.table}' was not found.`;
    } else if (result.ok) {
      out.textContent = `✓ Looks good — every query and key column exists on ${body.schema}.${body.table}.` +
        (result.extra_table_columns.length ? `\n\nColumns on the table but not in your query (informational): ${result.extra_table_columns.join(", ")}` : "");
    } else {
      out.classList.add("is-error");
      let msg = `Problems found on ${body.schema}.${body.table}:`;
      if (result.missing_columns.length) msg += `\n- Query column(s) not on the table: ${result.missing_columns.join(", ")}`;
      if (result.missing_key_columns.length) msg += `\n- Key column(s) not on the table: ${result.missing_key_columns.join(", ")}`;
      out.textContent = msg;
    }
  } catch (err) {
    out.classList.add("is-error");
    out.textContent = err.message;
  }
});

let _snapshotsCache = [];

async function refreshSnapshotSelects() {
  try {
    const { snapshots } = await api("/api/snapshot/list");
    _snapshotsCache = snapshots;
    const opts = snapshots
      .map((s) => `<option value="${escapeHtml(s.snapshot_table)}">${escapeHtml(s.display_name)} · ${escapeHtml(s.created_at_utc.slice(0, 16).replace("T", " "))} · ${s.row_count} rows</option>`)
      .join("");
    $("#snapshot-a-select").innerHTML = opts;
    $("#snapshot-b-select").innerHTML = opts;
  } catch (err) {
    showToast(err.message, true);
  }
}

$("#snapshot-export-btn").addEventListener("click", async () => {
  const name = $("#snapshot-export-name").value.trim();
  if (!name) { showToast("Enter a name for the snapshot.", true); return; }
  try {
    await api("/api/snapshot/export", { method: "POST", body: { name } });
    $("#snapshot-export-name").value = "";
    showToast(`Exported snapshot '${name}'.`);
    refreshSnapshotSelects();
  } catch (err) {
    showToast(err.message, true);
  }
});

$("#snapshot-refresh-btn").addEventListener("click", refreshSnapshotSelects);

$("#snapshot-compare-btn").addEventListener("click", async () => {
  const snapA = $("#snapshot-a-select").value;
  const snapB = $("#snapshot-b-select").value;
  if (!snapA || !snapB) { showToast("Pick both an Older and a Newer snapshot first.", true); return; }
  const keyColumns = $("#snapshot-key-columns").value.split(",").map((s) => s.trim()).filter(Boolean);
  try {
    const result = await api("/api/snapshot/diff", { method: "POST", body: { snapshot_a: snapA, snapshot_b: snapB, key_columns: keyColumns } });
    renderSnapshotDiff(result);
  } catch (err) {
    showToast(err.message, true);
  }
});

function renderSnapshotDiff(result) {
  const tbody = document.querySelector("#snapshot-diff-table tbody");
  tbody.innerHTML = "";
  const keyText = (k) => Object.entries(k).map(([col, v]) => `${col}=${v}`).join(", ");
  [...result.added, ...result.removed, ...result.changed].forEach((row) => {
    const detail = row.cell_changes.map((c) => `${c.column}: ${c.old_value} → ${c.new_value}`).join("; ");
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(keyText(row.key))}</td><td>${row.status}</td><td>${escapeHtml(detail)}</td>`;
    tbody.appendChild(tr);
  });
  let summary = `${result.added.length} added, ${result.removed.length} removed, ${result.changed.length} changed, ${result.unchanged_count} unchanged`;
  if (result.key_is_full_row) summary += " (no key column given - matched on the full row)";
  $("#snapshot-diff-summary").textContent = summary;
}

// ---------------- Date Anomaly page ----------------
// Web port of the desktop app's "🩹 Date Anomaly" tab - same 3-step
// Detect -> Resolve Bill Links -> Generate Correction Script flow. All
// state (which readings are anomalous, the correct date, the item/XML
// mapping) lives server-side per session (see web/session_store.py's
// DateAnomalyState) - this file only sends the NISS/threshold/program
// inputs and renders whatever comes back.
let daHasAnomalies = false;
let daHasCorrectDate = false;

function daSetResolveEnabled() {
  $("#da-resolve-btn").disabled = !(daHasAnomalies && daHasCorrectDate);
}

$("#da-detect-btn").addEventListener("click", async () => {
  const niss = $("#da-niss").value.trim();
  if (!niss) { showToast("Enter the NISS to investigate.", true); return; }
  const thresholdRaw = $("#da-threshold").value.trim();
  const threshold = thresholdRaw ? parseInt(thresholdRaw, 10) : 0;
  if (Number.isNaN(threshold)) { showToast("Billing period floor must be a whole number.", true); return; }

  // A fresh Detect invalidates anything already resolved/generated for a
  // previous NISS - same reasoning as the desktop app's _da_detect.
  daHasAnomalies = false;
  daHasCorrectDate = false;
  $("#da-resolve-btn").disabled = true;
  $("#da-generate-btn").disabled = true;
  $("#da-explain-ai-btn").disabled = true;
  $("#da-links-summary").textContent = "";
  $("#da-output").textContent = "No correction script generated yet — run Detect and Resolve Bill Links first.";
  const tbody = document.querySelector("#da-anomaly-table tbody");
  tbody.innerHTML = "";
  $("#da-summary").textContent = "Detecting…";
  $("#da-explanation").textContent = "";
  $("#da-explain-ai-output").textContent = "";

  try {
    const result = await api("/api/date-anomaly/detect", { method: "POST", body: { niss, threshold } });
    // Applies to every row, not per-row - the flag is NISS-level (do this
    // NISS's readings span more than one billing period), not per-reading.
    const multiPeriodClass = daRowClassForBillingPeriods(result.billing_period_count);
    result.rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.className = multiPeriodClass;
      if (multiPeriodClass) tr.title = `${result.billing_period_count} distinct billing periods detected for this NISS`;
      // RJ, 2026-09-13: "if not all cycle, i need to know if it contains
      // removal or not" - shows the actual GCGT_RE_READING_TYPE.DESCRIPTION
      // per reading (falls back to the bare TIPTL code if this reading's
      // type isn't in that lookup), with a non-cycle reading (r.is_cycle_reading
      // false - e.g. Removal, Reconnection) visually flagged so it's not
      // just buried in a plain list.
      const typeLabel = r.reading_type_desc || r.reading_type || "";
      const typeCell = r.is_cycle_reading
        ? escapeHtml(typeLabel)
        : `<span class="badge-noncycle" title="Non-cycle reading type - not Cycle/Direct Connection">${escapeHtml(typeLabel)}</span>`;
      tr.innerHTML = `<td>${escapeHtml(r.id_reading)}</td><td>${escapeHtml(r.id_billing_period)}</td><td>${escapeHtml(r.reading_date)}</td><td>${escapeHtml(r.read_status)}</td><td>${typeCell}</td>`;
      tbody.appendChild(tr);
    });

    let summary = `${result.rows.length} anomalous reading(s) found.`;
    if (result.account) summary += `  Account: ${result.account}.`;
    if (result.billing_period_count > 1) {
      summary += `  ⚠ ${result.billing_period_count} distinct billing periods detected for this NISS.`;
    }
    if (result.correct_date !== null) {
      summary += `  Correct date: ${result.correct_date}  (from ID_READING ${result.correct_date_reading}).`;
    } else {
      summary += "  No correctly-billed reading found above this threshold - can't determine the correct date.";
    }
    $("#da-summary").textContent = summary;
    $("#da-explanation").textContent = result.explanation || "";

    daHasAnomalies = result.rows.length > 0;
    daHasCorrectDate = result.correct_date !== null;
    daSetResolveEnabled();
    $("#da-explain-ai-btn").disabled = false;
    daHistoryRefresh();

    if (result.rows.length === 0) {
      showToast(`No anomalous readings found for NISS ${niss} above billing period ${threshold}.`, true);
    } else if (result.correct_date === null) {
      showToast("Anomalies found, but no correctly-billed reading exists above this threshold to source the date from.", true);
    }
  } catch (err) {
    $("#da-summary").textContent = "";
    showToast(err.message, true);
  }
});

$("#da-resolve-btn").addEventListener("click", async () => {
  $("#da-resolve-btn").disabled = true;
  $("#da-links-summary").textContent = "Resolving…";
  try {
    const result = await api("/api/date-anomaly/resolve", { method: "POST" });
    const itemsPhrase = result.total_items === result.distinct_items
      ? `${result.distinct_items} item-to-bill id(s)`
      : `${result.distinct_items} item-to-bill id(s) (${result.total_items} mapping row(s) - a reading can link to more than one)`;
    $("#da-links-summary").textContent =
      `${result.linked}/${result.total_readings} anomalous reading(s) linked to ${itemsPhrase}; ${result.xml_count} XML row(s) found; ` +
      `${result.item_status_count} item(s) to advance status; ${result.anomalous_count} item(s) with open anomalies to cancel.`;
    if (result.explanation) $("#da-explanation").textContent = result.explanation;
    // Viewers never get this re-enabled - see applyRolePermissionsToUI's
    // EDITOR_ONLY_IDS comment on why the real gate is server-side anyway.
    $("#da-generate-btn").disabled = state.role === "viewer";
  } catch (err) {
    $("#da-links-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    $("#da-resolve-btn").disabled = !(daHasAnomalies && daHasCorrectDate);
  }
});

$("#da-generate-btn").addEventListener("click", async () => {
  const program = $("#da-program").value.trim();
  if (!program) { showToast("Enter the Jira/Program # this change is for.", true); return; }
  try {
    const clean = $("#da-clean-toggle").checked;
    const lowest_billing_period_only = $("#da-lowest-period-toggle").checked;
    const result = await api("/api/date-anomaly/generate", { method: "POST", body: { program, clean, lowest_billing_period_only } });
    $("#da-output").textContent = result.sql_text;
    let msg = `Correction script generated: ${result.reading_count} reading, ${result.item_count} item, ${result.item_status_count} status-transition, ${result.xml_count} XML, ${result.anomalous_count} anomaly-cancel statement(s).`;
    if (result.scoped_to_lowest_period && result.billing_period_count > 1) msg += `  Scoped to the lowest of ${result.billing_period_count} billing periods.`;
    if (result.orphan_reading_count) msg += `  ${result.orphan_reading_count} non-cycle (TIPTL00011) reading(s) also reset.`;
    if (result.warnings.length) msg += `  ${result.warnings.length} warning(s) - see comments at the top of the script.`;
    showToast(msg);
    if (result.explanation) $("#da-explanation").textContent = result.explanation;
    daHistoryRefresh();
  } catch (err) {
    showToast(err.message, true);
  }
});

$("#da-copy-btn").addEventListener("click", async () => {
  const text = $("#da-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Correction script copied to clipboard.");
  } catch (_) {
    showToast("Couldn't copy - select and copy manually.", true);
  }
});

// "Analyze with AI" - a Gemini second opinion on top of the deterministic
// #da-explanation text, pulled entirely from server-side session state
// (see web/server.py's date_anomaly_explain_ai), so no body is sent here.
$("#da-explain-ai-btn").addEventListener("click", async () => {
  const out = $("#da-explain-ai-output");
  out.classList.remove("is-error");
  out.textContent = "Asking AI Assist…";
  try {
    const result = await api("/api/date-anomaly/explain-ai", { method: "POST" });
    _renderAiOutput(out, result);  // shared helper, defined below - hoisted, safe to call here
  } catch (err) {
    out.textContent = err.message;
    out.classList.add("is-error");
  }
});

$("#da-download-btn").addEventListener("click", () => {
  const text = $("#da-output").textContent;
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "date_anomaly_correction.sql";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

// ---------------- Date Anomaly page: Batch / Multi-NISS ----------------
// A batch runs on a background thread server-side (see web/batch_jobs.py)
// instead of inside one blocking request - a long NISS list against a
// real DB could take minutes. This section starts the job
// (/api/date-anomaly/batch/start), then polls
// GET /api/date-anomaly/batch/{job_id} every DA_BATCH_POLL_MS until it
// reaches a terminal status, updating the progress line + results table
// as results come in. daBatchPollTimer/daBatchCurrentJobId track the
// in-flight poll so navigating to another page (or starting a new run)
// can cancel the old poll loop without leaking timers - the background
// job itself keeps running server-side either way, and "Recent runs"
// (populated from /api/date-anomaly/batch/jobs) lets you reattach to it.
const DA_BATCH_POLL_MS = 1500;
const DA_BATCH_TERMINAL_STATUSES = new Set(["done", "cancelled", "failed"]);
let daBatchPollTimer = null;
let daBatchCurrentJobId = null;
// Separate from daBatchCurrentJobId, which is deliberately nulled out once
// a job reaches a terminal status (see daBatchRenderJob) to stop polling -
// this one stays set to whichever job was rendered LAST, so "Analyze with
// AI" keeps working after a run finishes, or after reattaching to a
// recent (already-finished) run from the dropdown below.
let daBatchLastJobId = null;

function daParseBatchNissList() {
  return $("#da-batch-niss").value
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

const DA_BATCH_STATUS_LABEL = {
  ok: "✅ OK",
  no_anomalies: "— No anomalies",
  error: "❌ Error",
};

const DA_BATCH_JOB_STATUS_LABEL = {
  queued: "Queued…",
  running: "Running…",
  done: "Done",
  cancelled: "Cancelled",
  failed: "Failed",
};

function daBatchStopPolling() {
  if (daBatchPollTimer) { clearTimeout(daBatchPollTimer); daBatchPollTimer = null; }
}

function daBatchRenderResultsTable(results) {
  const tbody = document.querySelector("#da-batch-table tbody");
  tbody.innerHTML = "";
  results.forEach((r) => {
    const tr = document.createElement("tr");
    tr.className = daRowClassForBillingPeriods(r.billing_period_count);
    if (tr.className) tr.title = `${r.billing_period_count} distinct billing periods detected for this NISS`;
    const scopedNote = r.scoped_to_lowest_period && r.billing_period_count > 1
      ? ` <span title="Scoped to the lowest of ${r.billing_period_count} billing periods">🔽 lowest only</span>` : "";
    tr.innerHTML = `<td>${escapeHtml(r.niss)}</td><td>${DA_BATCH_STATUS_LABEL[r.status] || r.status}${r.error ? ` — ${escapeHtml(r.error)}` : ""}${scopedNote}</td><td>${r.reading_count}</td><td>${r.item_count}</td><td>${r.item_status_count}</td><td>${r.xml_count}</td><td>${r.anomalous_count}</td><td>${r.orphan_reading_count ?? 0}</td><td>${r.billing_period_count ?? ""}</td><td>${r.warning_count}</td>`;
    tbody.appendChild(tr);
  });
}

function daBatchSetRunningUI(isRunning) {
  $("#da-batch-run-btn").disabled = isRunning || state.role === "viewer";
  $("#da-batch-cancel-btn").hidden = !isRunning;
}

// Seconds since the job actually started running (job.started_at_utc,
// set by the background thread itself - distinct from created_at_utc,
// which is when it was merely queued) up to now (still running) or
// finished_at_utc (terminal) - the denominator for items/sec below.
function daBatchElapsedSeconds(job) {
  if (!job.started_at_utc) return null;
  const start = new Date(job.started_at_utc).getTime();
  const end = job.finished_at_utc ? new Date(job.finished_at_utc).getTime() : Date.now();
  return Math.max((end - start) / 1000, 0.001); // avoid a divide-by-zero flash right at start
}

// Processed/pending/percent/rate strip (RJ, 2026-09-13: "show how many
// processed and how many pending and percentage, with item per second
// processed") - a thin progress bar plus 4 compact KPI cards, both
// hidden until the job has actually started (a still-queued job has no
// started_at_utc yet, nothing meaningful to show).
function daBatchRenderProgress(job) {
  const track = $("#da-batch-progress-track");
  const kpiRow = $("#da-batch-progress-kpi-row");
  if (!job.started_at_utc || job.niss_total === 0) {
    track.hidden = true;
    kpiRow.hidden = true;
    return;
  }
  const pending = Math.max(job.niss_total - job.processed, 0);
  const percent = (job.processed / job.niss_total) * 100;
  const elapsed = daBatchElapsedSeconds(job);
  const rate = elapsed && job.processed > 0 ? job.processed / elapsed : 0;

  track.hidden = false;
  $("#da-batch-progress-fill").style.width = `${Math.min(percent, 100).toFixed(1)}%`;

  kpiRow.hidden = false;
  const cards = [
    ["Processed", job.processed],
    ["Pending", pending],
    ["Complete", `${percent.toFixed(1)}%`],
    ["Rate", rate > 0 ? `${rate.toFixed(2)}/sec` : "—"],
  ];
  kpiRow.innerHTML = cards.map(([label, value]) =>
    `<div class="kpi-card"><div class="kpi-value">${escapeHtml(String(value))}</div><div class="kpi-label">${escapeHtml(label)}</div></div>`
  ).join("");
}

async function daBatchRenderJob(job) {
  daBatchLastJobId = job.job_id;
  $("#da-batch-explain-ai-btn").disabled = job.results.length === 0;
  daBatchRenderResultsTable(job.results);
  daBatchRenderProgress(job);
  const label = DA_BATCH_JOB_STATUS_LABEL[job.status] || job.status;
  let summary = `${label} — ${job.processed}/${job.niss_total} NISS processed.`;
  if (job.error) summary += `  Job failed: ${job.error}`;
  $("#da-batch-summary").textContent = summary;
  const hasScript = job.combined_sql !== null && job.combined_sql !== undefined && job.combined_sql !== "";
  if (hasScript) {
    $("#da-batch-output").textContent = job.combined_sql;
  } else if (job.results.length === 0) {
    $("#da-batch-output").textContent = "Running…";
  }
  ["#da-batch-copy-btn", "#da-batch-copy-btn-2", "#da-batch-download-btn", "#da-batch-download-btn-2"].forEach((sel) => {
    const btn = $(sel);
    btn.disabled = !hasScript;
    btn.title = hasScript ? "" : "Run a batch first";
  });

  const isRunning = !DA_BATCH_TERMINAL_STATUSES.has(job.status);
  daBatchSetRunningUI(isRunning);

  if (isRunning) {
    daBatchPollTimer = setTimeout(() => daBatchPoll(job.job_id), DA_BATCH_POLL_MS);
    return;
  }

  // Terminal - stop polling and give a final toast.
  daBatchCurrentJobId = null;
  if (job.status === "done") {
    const okCount = job.results.filter((r) => r.status === "ok").length;
    const errCount = job.results.filter((r) => r.status === "error").length;
    showToast(`Batch complete: ${okCount}/${job.results.length} NISS corrected.`, errCount > 0);
  } else if (job.status === "cancelled") {
    showToast(`Batch cancelled after ${job.processed}/${job.niss_total} NISS.`, true);
  } else if (job.status === "failed") {
    showToast(`Batch failed: ${job.error || "unknown error"}`, true);
  }
  daBatchRefreshRecentRuns();
  daHistoryRefresh();
}

async function daBatchPoll(jobId) {
  if (jobId !== daBatchCurrentJobId) return; // a newer run superseded this poll loop
  try {
    const job = await api(`/api/date-anomaly/batch/${jobId}`);
    if (jobId !== daBatchCurrentJobId) return;
    await daBatchRenderJob(job);
  } catch (err) {
    if (jobId !== daBatchCurrentJobId) return;
    daBatchSetRunningUI(false);
    showToast(err.message, true);
  }
}

async function daBatchRefreshRecentRuns() {
  const select = $("#da-batch-recent");
  try {
    const { jobs } = await api("/api/date-anomaly/batch/jobs");
    const current = select.value;
    select.innerHTML = '<option value="">Recent runs…</option>';
    jobs.forEach((j) => {
      const opt = document.createElement("option");
      opt.value = j.job_id;
      const when = new Date(j.created_at_utc).toLocaleString();
      opt.textContent = `${when} — ${DA_BATCH_JOB_STATUS_LABEL[j.status] || j.status} (${j.processed}/${j.niss_total})`;
      select.appendChild(opt);
    });
    select.value = current;
  } catch (_) { /* not fatal - the dropdown just stays as-is */ }
}

// ---------------- Date Anomaly: Analysis History (card 5) ----------------

async function daHistoryRefresh() {
  const tbody = document.querySelector("#da-history-table tbody");
  try {
    const mineOnly = !$("#da-history-team-toggle").checked;
    const { entries } = await api(`/api/date-anomaly/history?mine_only=${mineOnly}`);
    tbody.innerHTML = "";
    $("#da-history-detail").textContent = "";
    entries.forEach((e) => {
      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.innerHTML = `
        <td>${escapeHtml(e.updated_at_utc.slice(0, 16).replace("T", " "))}</td>
        <td>${escapeHtml(e.niss)}</td>
        <td>${escapeHtml(e.username)}</td>
        <td>${escapeHtml(e.source)}</td>
        <td>${e.anomaly_count}</td>
        <td>${e.item_count ?? ""}</td>
        <td>${e.item_status_count ?? ""}</td>
        <td>${e.xml_count ?? ""}</td>
        <td>${e.anomalous_count ?? ""}</td>
        <td>${e.generated ? "✓" : ""}</td>
      `;
      tr.addEventListener("click", () => {
        $("#da-history-detail").textContent = e.explanation || "No explanation recorded for this analysis.";
      });
      tbody.appendChild(tr);
    });
  } catch (err) {
    // Not fatal - the rest of the Date Anomaly page still works without
    // history loading successfully.
    tbody.innerHTML = "";
  }
}

$("#da-history-refresh-btn").addEventListener("click", daHistoryRefresh);
$("#da-history-team-toggle").addEventListener("change", daHistoryRefresh);

// Shared by the Batch tab's own Run Batch button AND Detect All's
// "Generate Script for Selected" (see daCleanupGenerateViaBatch below) -
// factored out so Detect All can kick off a real batch run without
// simulating a button click or duplicating the start/poll wiring.
async function daBatchStartRun({ niss_list, threshold, program, clean, lowest_billing_period_only = false }) {
  daBatchStopPolling();
  daBatchSetRunningUI(true);
  $("#da-batch-summary").textContent = `Starting batch for ${niss_list.length} NISS…`;
  $("#da-batch-progress-track").hidden = true;
  $("#da-batch-progress-kpi-row").hidden = true;
  document.querySelector("#da-batch-table tbody").innerHTML = "";
  $("#da-batch-output").textContent = "Running…";
  $("#da-batch-explain-ai-btn").disabled = true;
  $("#da-batch-explain-ai-output").textContent = "";
  ["#da-batch-copy-btn", "#da-batch-copy-btn-2", "#da-batch-download-btn", "#da-batch-download-btn-2"].forEach((sel) => {
    const btn = $(sel);
    btn.disabled = true;
    btn.title = "Run a batch first";
  });

  try {
    const started = await api("/api/date-anomaly/batch/start", { method: "POST", body: { niss_list, threshold, program, clean, lowest_billing_period_only } });
    daBatchCurrentJobId = started.job_id;
    daBatchPoll(started.job_id);
    return true;
  } catch (err) {
    daBatchSetRunningUI(false);
    $("#da-batch-summary").textContent = "";
    $("#da-batch-output").textContent = "No batch run yet.";
    showToast(err.message, true);
    return false;
  }
}

$("#da-batch-run-btn").addEventListener("click", () => {
  const niss_list = daParseBatchNissList();
  if (!niss_list.length) { showToast("Enter at least one NISS (one per line, or comma-separated).", true); return; }
  const thresholdRaw = $("#da-batch-threshold").value.trim();
  const threshold = thresholdRaw ? parseInt(thresholdRaw, 10) : 0;
  if (Number.isNaN(threshold)) { showToast("Billing period floor must be a whole number.", true); return; }
  const program = $("#da-batch-program").value.trim();
  if (!program) { showToast("Enter the Jira/Program # this change is for.", true); return; }
  const clean = $("#da-batch-clean-toggle").checked;
  const lowest_billing_period_only = $("#da-batch-lowest-period-toggle").checked;
  daBatchStartRun({ niss_list, threshold, program, clean, lowest_billing_period_only });
});

$("#da-batch-cancel-btn").addEventListener("click", async () => {
  if (!daBatchCurrentJobId) return;
  try {
    await api(`/api/date-anomaly/batch/${daBatchCurrentJobId}/cancel`, { method: "POST" });
    showToast("Cancel requested - the batch will stop before its next NISS.");
  } catch (err) {
    showToast(err.message, true);
  }
});

$("#da-batch-recent").addEventListener("change", () => {
  const jobId = $("#da-batch-recent").value;
  if (!jobId) return;
  daBatchStopPolling();
  daBatchCurrentJobId = jobId;
  $("#da-batch-explain-ai-output").textContent = "";
  daBatchPoll(jobId);
});

// Two identical-looking pairs of Copy/Download buttons exist for this one
// script: one up in the card header (same convention as every other
// script-output card in this app - Single NISS, Detect All), one right
// next to #da-batch-output itself. The header pair is easy to lose track
// of once the NISS list/threshold/Jira fields and the results table sit
// between it and the actual output below, especially on a long batch run -
// the second pair exists purely so "copy this" is never more than a
// glance away from the text being copied. Both call the same two
// functions so there's one source of truth for the copy/download logic.
async function daBatchCopyScript() {
  const text = $("#da-batch-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Batch script copied to clipboard.");
  } catch (_) {
    showToast("Couldn't copy - select and copy manually.", true);
  }
}

function daBatchDownloadScript() {
  const text = $("#da-batch-output").textContent;
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "date_anomaly_correction_batch.sql";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

["#da-batch-copy-btn", "#da-batch-copy-btn-2"].forEach((sel) => $(sel).addEventListener("click", daBatchCopyScript));
["#da-batch-download-btn", "#da-batch-download-btn-2"].forEach((sel) => $(sel).addEventListener("click", daBatchDownloadScript));

// "Analyze with AI" - summarizes the whole batch run (no per-row selection
// exists here, unlike Detect All) via daBatchLastJobId, which stays set
// after the job finishes even though daBatchCurrentJobId is nulled out to
// stop polling - see daBatchLastJobId's own comment above.
$("#da-batch-explain-ai-btn").addEventListener("click", async () => {
  if (!daBatchLastJobId) return;
  const out = $("#da-batch-explain-ai-output");
  out.classList.remove("is-error");
  out.textContent = "Asking AI Assist…";
  try {
    const result = await api(`/api/date-anomaly/batch/${daBatchLastJobId}/explain-ai`, { method: "POST" });
    _renderAiOutput(out, result);  // shared helper, defined below - hoisted, safe to call here
  } catch (err) {
    out.textContent = err.message;
    out.classList.add("is-error");
  }
});

// ---------------- Date Anomaly: Detect All / bulk cleanup (card 6) ----------------
// Unlike every other Date Anomaly workflow, this one is NISS-less and
// stateless: the analyst clicks Detect All, gets a system-wide list of
// open anomalies, checks the ones they want, and generates a script for
// just those - all client-side selection state, nothing kept server-side
// between requests (see web/server.py's date_anomaly_detect_all route
// docstring for why).
let daCleanupRows = [];       // last /detect-all response, keyed by array index
let daCleanupSelected = new Set(); // selected row indices - into daCleanupRows, NOT the filtered view
let daCleanupFilters = { offeredService: "", anomalousType: "", needsAdvance: "", allCycle: "", multiPeriod: "", search: "" };

function daCleanupTypeText(r) {
  // Prefer the description, prefix with the short code when both are
  // present (e.g. "26 — Diff Date System") - falls back to the raw type
  // id if neither lookup resolved. See web/server.py's date_anomaly_
  // detect_all docstring for why codes are still sent alongside text.
  const parts = [r.anomalous_type_code, r.anomalous_type_description].filter(Boolean);
  return parts.length ? parts.join(" — ") : (r.anomalous_type || "");
}

// Rows passing the current filter set - selection (daCleanupSelected)
// deliberately is NOT reset by filtering: a row checked, then hidden by a
// filter, stays checked (same convention as most filter+select UIs) - see
// the "Generate Cleanup Script" card's own hint text about this.
// RJ, 2026-09-15: "if i filter, i want to see how many rows filtered, do
// this also for all of the project." Shared helper used by every
// filterable table's own RenderTable() function (called on both a fresh
// detect AND every filter/search change, so this always reflects the
// current filter state) - blank when no filter is actually narrowing the
// rows, so the unfiltered case isn't cluttered with a redundant "N of N".
function renderFilteredCount(elId, shownCount, totalCount) {
  const el = $(elId);
  if (!el) return;
  el.textContent = (totalCount > 0 && shownCount < totalCount)
    ? `Showing ${shownCount} of ${totalCount} row(s) with current filters.`
    : "";
}

function daCleanupVisibleIndices(ignoreFilters = false) {
  return daCleanupRows
    .map((_, idx) => idx)
    .filter((idx) => {
      if (ignoreFilters) return true;
      const r = daCleanupRows[idx];
      if (daCleanupFilters.offeredService && (r.offered_service || "") !== daCleanupFilters.offeredService) return false;
      if (daCleanupFilters.anomalousType && daCleanupTypeText(r) !== daCleanupFilters.anomalousType) return false;
      if (daCleanupFilters.needsAdvance === "yes" && !r.needs_status_advance) return false;
      if (daCleanupFilters.needsAdvance === "no" && r.needs_status_advance) return false;
      // r.all_cycle can be true/false/null (null = unknown, e.g. the SQL
      // column wasn't there yet) - neither "Yes only" nor "No only" should
      // match a null, same as how a blank/unresolved lookup value doesn't
      // match a specific-value filter elsewhere on this page.
      if (daCleanupFilters.allCycle === "yes" && r.all_cycle !== true) return false;
      if (daCleanupFilters.allCycle === "no" && r.all_cycle !== false) return false;
      // RJ, 2026-09-13: "add cycle with removal, and cycle with
      // reconnection, or cycle with both removal and reconnection" - a
      // finer breakdown of the non-cycle composition than the plain
      // Yes/No above, keyed off r.non_cycle_reading_types (the comma-
      // separated description text from the query's STUFF/FOR XML
      // column - e.g. "Removal", "Reconnection", "Removal, Reconnection").
      // Substring match on the human-readable description, not a code,
      // since that's what this field actually carries.
      if (["removal", "reconnection", "both", "other"].includes(daCleanupFilters.allCycle)) {
        const types = (r.non_cycle_reading_types || "");
        const hasRemoval = types.includes("Removal");
        const hasReconnection = types.includes("Reconnection");
        if (daCleanupFilters.allCycle === "removal" && !(hasRemoval && !hasReconnection)) return false;
        if (daCleanupFilters.allCycle === "reconnection" && !(hasReconnection && !hasRemoval)) return false;
        if (daCleanupFilters.allCycle === "both" && !(hasRemoval && hasReconnection)) return false;
        if (daCleanupFilters.allCycle === "other" && (!types || hasRemoval || hasReconnection)) return false;
      }
      // billing_period_count can be null (see the field's own comment on
      // the /detect-all response shape) - neither option should match a
      // null, same "don't guess" stance as allCycle just above.
      if (daCleanupFilters.multiPeriod === "yes" && !(r.billing_period_count > 1)) return false;
      if (daCleanupFilters.multiPeriod === "no" && (r.billing_period_count == null || r.billing_period_count > 1)) return false;
      if (daCleanupFilters.search) {
        const needle = daCleanupFilters.search.trim().toLowerCase();
        if (needle) {
          const haystack = `${r.account || ""} ${r.supply || ""} ${r.id_item_to_bill || ""}`.toLowerCase();
          if (!haystack.includes(needle)) return false;
        }
      }
      return true;
    });
}

function daCleanupSetGenerateEnabled() {
  $("#da-cleanup-generate-btn").disabled = daCleanupSelected.size === 0 || state.role === "viewer";
  // Explain is deliberately single-row only - the AI prompt is built
  // around one item's context (account/supply/service/anomaly), not a
  // meaningful summary of an arbitrary multi-row selection.
  $("#da-cleanup-explain-btn").disabled = daCleanupSelected.size !== 1;
  if (daCleanupSelected.size !== 1) $("#da-cleanup-explain-output").textContent = "";

  // Both buttons start disabled with no rows checked, which reads as
  // "broken" without an explanation right next to them - this hint makes
  // the actual reason (and how to fix it) visible instead of a tooltip
  // that's easy to miss, and updates live as the selection changes.
  const hint = $("#da-cleanup-selection-hint");
  const n = daCleanupSelected.size;
  if (n === 0) {
    hint.textContent = "☝️ Check one or more rows in the table above to enable Generate — check exactly one to also enable Explain.";
  } else if (n === 1) {
    hint.textContent = "✅ 1 row selected — Generate and Explain are both enabled.";
  } else {
    hint.textContent = `✅ ${n} rows selected — Generate is enabled. Explain needs exactly 1 row selected.`;
  }
}

function daCleanupRenderTable() {
  const tbody = document.querySelector("#da-cleanup-table tbody");
  tbody.innerHTML = "";
  const visible = daCleanupVisibleIndices();
  renderFilteredCount("#da-cleanup-filtered-count", visible.length, daCleanupRows.length);
  visible.forEach((idx) => {
    const r = daCleanupRows[idx];
    const tr = document.createElement("tr");
    tr.className = daRowClassForBillingPeriods(r.billing_period_count);
    if (tr.className) tr.title = `${r.billing_period_count} distinct billing periods detected for this NISS`;
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = daCleanupSelected.has(idx);
    cb.addEventListener("change", () => {
      if (cb.checked) daCleanupSelected.add(idx); else daCleanupSelected.delete(idx);
      $("#da-cleanup-select-all").checked = visible.length > 0 && visible.every((i) => daCleanupSelected.has(i));
      daCleanupSetGenerateEnabled();
    });
    const cbTd = document.createElement("td");
    cbTd.appendChild(cb);
    tr.appendChild(cbTd);
    const statusText = r.anomalous_status_description || r.anomalous_status || "";
    // insertAdjacentHTML, NOT tr.innerHTML += - the latter re-serializes
    // and re-parses the WHOLE row (including the checkbox <td> appended
    // above), which silently destroys the checkbox's addEventListener
    // binding and resets its .checked property (a JS property, not a
    // reflected HTML attribute, so it doesn't survive the round-trip
    // through markup either) - the checkbox would still be visible but
    // permanently inert, exactly the "Generate/Explain never become
    // clickable" bug this comment is here to stop from recurring.
    // insertAdjacentHTML only parses and appends the NEW markup, leaving
    // the already-attached checkbox node untouched.
    tr.insertAdjacentHTML("beforeend", `
      <td>${escapeHtml(r.id_item_to_bill)}</td>
      <td>${escapeHtml(r.account ?? "")}</td>
      <td>${escapeHtml(r.supply ?? "")}</td>
      <td>${escapeHtml(r.offered_service ?? "")}</td>
      <td>${escapeHtml(r.contract_status ?? "")}</td>
      <td>${escapeHtml(daCleanupTypeText(r))}</td>
      <td>${escapeHtml(statusText)}</td>
      <td>${escapeHtml(r.item_status ?? "")}</td>
      <td>${r.needs_status_advance ? "Yes" : ""}</td>
      <td>${r.all_cycle === true ? "Yes" : r.all_cycle === false ? "No" : ""}</td>
      <td>${r.non_cycle_reading_types ? `<span class="badge-noncycle">${escapeHtml(r.non_cycle_reading_types)}</span>` : ""}</td>
      <td>${r.billing_period_count ?? ""}</td>
    `);
    tbody.appendChild(tr);
  });
  $("#da-cleanup-select-all").checked = visible.length > 0 && visible.every((i) => daCleanupSelected.has(i));
  daCleanupRenderDashboard(visible.map((idx) => daCleanupRows[idx]));
}

// ---------------- Detect All: filter dropdowns ----------------
// Populated from the FULL scan result (not the filtered view) each time a
// fresh Detect All completes, so switching one filter doesn't shrink the
// options available in the others.
function daCleanupPopulateFilterOptions() {
  const services = [...new Set(daCleanupRows.map((r) => r.offered_service).filter(Boolean))].sort();
  const types = [...new Set(daCleanupRows.map((r) => daCleanupTypeText(r)).filter(Boolean))].sort();
  const opt = (v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`;
  $("#da-cleanup-filter-service").innerHTML = `<option value="">All</option>` + services.map(opt).join("");
  $("#da-cleanup-filter-type").innerHTML = `<option value="">All</option>` + types.map(opt).join("");
  // Rebuilding innerHTML resets a <select>'s own selectedIndex, so
  // re-apply whatever's still in daCleanupFilters (relevant after the
  // silent post-generate rescan, which intentionally keeps filters set -
  // see daCleanupRunDetect) so the dropdown's displayed value doesn't
  // drift from what's actually being filtered.
  $("#da-cleanup-filter-service").value = daCleanupFilters.offeredService;
  $("#da-cleanup-filter-type").value = daCleanupFilters.anomalousType;
  $("#da-cleanup-filter-advance").value = daCleanupFilters.needsAdvance;
  $("#da-cleanup-filter-cycle").value = daCleanupFilters.allCycle;
  $("#da-cleanup-filter-multiperiod").value = daCleanupFilters.multiPeriod;
  $("#da-cleanup-filter-row").hidden = daCleanupRows.length === 0;
  $("#da-cleanup-chart-grid").hidden = daCleanupRows.length === 0;
  $("#da-cleanup-export-csv-btn").hidden = daCleanupRows.length === 0;
  $("#da-cleanup-export-xlsx-btn").hidden = daCleanupRows.length === 0;
  $("#da-cleanup-export-all-wrap").hidden = daCleanupRows.length === 0;
}

// Exports whatever's currently VISIBLE (i.e. filtered/searched), not the
// full scan - matches the export button living right next to the filter
// bar, and matches the #dashboard-export-csv-btn convention elsewhere on
// this page (build lines, Blob, temp <a download>, revoke).
$("#da-cleanup-export-csv-btn").addEventListener("click", () => {
  const exportAll = !!$("#da-cleanup-export-all")?.checked;
  const visible = daCleanupVisibleIndices(exportAll);
  if (!visible.length) return;
  const header = ["id_item_to_bill", "account", "supply", "offered_service", "contract_status", "anomalous_type", "anomalous_status", "item_status", "needs_status_advance", "all_cycle", "non_cycle_reading_types", "billing_period_count"];
  const lines = [header.join(",")];
  visible.forEach((idx) => {
    const r = daCleanupRows[idx];
    const row = [
      r.id_item_to_bill, r.account, r.supply, r.offered_service, r.contract_status,
      daCleanupTypeText(r), r.anomalous_status_description || r.anomalous_status || "",
      r.item_status, r.needs_status_advance ? "Yes" : "",
      r.all_cycle === true ? "Yes" : r.all_cycle === false ? "No" : "",
      r.non_cycle_reading_types ?? "",
      r.billing_period_count ?? "",
    ].map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`);
    lines.push(row.join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = exportAll ? "detect_all_anomalies_all.csv" : "detect_all_anomalies.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// Excel counterpart to the CSV export above - same "whatever's currently
// visible" rows, same columns, but a browser can't write a real .xlsx
// binary on its own, so this one round-trips through the server
// (POST /api/date-anomaly/detect-all/export-xlsx, openpyxl-built) instead
// of building a Blob client-side like the CSV button does.
$("#da-cleanup-export-xlsx-btn").addEventListener("click", async () => {
  const exportAll = !!$("#da-cleanup-export-all")?.checked;
  const visible = daCleanupVisibleIndices(exportAll);
  if (!visible.length) return;
  const btn = $("#da-cleanup-export-xlsx-btn");
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Exporting…";
  try {
    const rows = visible.map((idx) => {
      const r = daCleanupRows[idx];
      return {
        id_item_to_bill: String(r.id_item_to_bill ?? ""),
        account: String(r.account ?? ""),
        supply: String(r.supply ?? ""),
        offered_service: String(r.offered_service ?? ""),
        contract_status: String(r.contract_status ?? ""),
        anomalous_type: daCleanupTypeText(r),
        anomalous_status: String(r.anomalous_status_description || r.anomalous_status || ""),
        item_status: String(r.item_status ?? ""),
        needs_status_advance: !!r.needs_status_advance,
        all_cycle: r.all_cycle === true || r.all_cycle === false ? r.all_cycle : null,
        non_cycle_reading_types: String(r.non_cycle_reading_types ?? ""),
        billing_period_count: r.billing_period_count ?? null,
      };
    });
    const resp = await fetch("/api/date-anomaly/detect-all/export-xlsx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Export failed (HTTP ${resp.status})`);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = exportAll ? "detect_all_anomalies_all.xlsx" : "detect_all_anomalies.xlsx";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showToast(err.message || "Excel export failed.", true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

["#da-cleanup-filter-service", "#da-cleanup-filter-type", "#da-cleanup-filter-advance", "#da-cleanup-filter-cycle", "#da-cleanup-filter-multiperiod"].forEach((sel) => {
  $(sel).addEventListener("change", () => {
    daCleanupFilters = {
      ...daCleanupFilters,
      offeredService: $("#da-cleanup-filter-service").value,
      anomalousType: $("#da-cleanup-filter-type").value,
      needsAdvance: $("#da-cleanup-filter-advance").value,
      allCycle: $("#da-cleanup-filter-cycle").value,
      multiPeriod: $("#da-cleanup-filter-multiperiod").value,
    };
    daCleanupRenderTable();
  });
});

// Debounced (not by timer, just by the fact that typing fires this a lot) -
// input rather than change so results narrow live as the analyst types,
// which matters more here than on the dropdowns since a search box is
// meant to feel instant.
$("#da-cleanup-filter-search").addEventListener("input", () => {
  daCleanupFilters = { ...daCleanupFilters, search: $("#da-cleanup-filter-search").value };
  daCleanupRenderTable();
});

$("#da-cleanup-filter-clear-btn").addEventListener("click", () => {
  daCleanupFilters = { offeredService: "", anomalousType: "", needsAdvance: "", allCycle: "", multiPeriod: "", search: "" };
  ["#da-cleanup-filter-service", "#da-cleanup-filter-type", "#da-cleanup-filter-advance", "#da-cleanup-filter-cycle", "#da-cleanup-filter-multiperiod"].forEach((sel) => { $(sel).value = ""; });
  $("#da-cleanup-filter-search").value = "";
  daCleanupRenderTable();
});

// ---------------- Detect All: mini dashboard (KPI cards + 2 bar charts) --
// Deliberately reacts to the FILTERED view (rows passed in), not the full
// scan, so narrowing by offered service etc. immediately updates the
// summary too - filters and dashboard are one connected view, not two
// separate features bolted together. Reuses the Dashboard page's own
// .kpi-card markup and drawBarChart() canvas helper (one hue per chart,
// no library) rather than introducing new chart machinery.
function daCleanupRenderDashboard(rows) {
  const total = rows.length;
  const needsAdvance = rows.filter((r) => r.needs_status_advance).length;
  const distinctServices = new Set(rows.map((r) => r.offered_service).filter(Boolean)).size;
  const distinctAccounts = new Set(rows.map((r) => r.account).filter(Boolean)).size;

  const advanceActive = daCleanupFilters.needsAdvance === "yes";
  // "Needs status advance" (RJ, 2026-09-13: "not clear what that is") means
  // this item's GCCOM_ITEMS_TO_BILL.STATUS is still ITEM_STATUS_FROM
  // (STTOBILL00, "pending") and hasn't been moved to ITEM_STATUS_TO
  // (STTOBILL01) yet - see app/core/date_anomaly.py's Part 3 comment. The
  // Generate Cleanup Script button below does that move for whatever's
  // checked, so this count is "how many rows still need that one-time
  // status bump" - spelled out in the card label itself now instead of
  // relying on a hover tooltip nobody may notice.
  // hx dashboard (RJ 2026-09-27, app-wide redesign) - gauges are KPIs
  // only; the old clickable KPI row below stays in the DOM but hidden (its
  // "needs advance" filter is still on the filter row's own select).
  hxRenderDashboard("da-cleanup-dash", {
    gauges: total ? [
      { label: "Billing status still pending", count: needsAdvance, total, c1: "#f59e0b", c2: "#f97316",
        hint: "GCCOM_ITEMS_TO_BILL.STATUS still STTOBILL00 - not yet advanced to STTOBILL01" },
      { label: "All cycle readings", count: rows.filter((r) => r.all_cycle === true).length, total, c1: "#10b981", c2: "#22c55e" },
      { label: "More than one billing period", count: rows.filter((r) => Number(r.billing_period_count) > 1).length, total, c1: "#ef4444", c2: "#ec4899" },
    ] : [],
    tiles: [
      { icon: "🩹", label: "Anomalies shown", value: total.toLocaleString(), accent: true },
      { icon: "👤", label: "Distinct accounts", value: distinctAccounts.toLocaleString() },
      { icon: "🧩", label: "Offered services", value: distinctServices.toLocaleString() },
    ],
  });
  const advanceTitle = "Billing STATUS is still STTOBILL00 (“pending”) - hasn't been advanced to STTOBILL01 yet. Generate Cleanup Script does that for whatever's checked. Click to toggle this filter.";
  $("#da-cleanup-kpi-row").innerHTML = [
    ["Shown", total, false, ""],
    ["Billing status still pending (STTOBILL00)", needsAdvance, true, advanceTitle],
    ["Distinct offered services", distinctServices, false, ""],
    ["Distinct accounts", distinctAccounts, false, ""],
  ].map(([label, value, clickable, title]) =>
    `<div class="kpi-card${clickable ? " kpi-card-clickable" : ""}${clickable && advanceActive ? " is-active" : ""}"${clickable ? ` id="da-cleanup-kpi-advance" title="${title}"` : ""}><div class="kpi-value">${value}</div><div class="kpi-label">${label}</div></div>`
  ).join("");

  const advanceCard = $("#da-cleanup-kpi-advance");
  if (advanceCard) {
    advanceCard.addEventListener("click", () => {
      daCleanupFilters = { ...daCleanupFilters, needsAdvance: advanceActive ? "" : "yes" };
      $("#da-cleanup-filter-advance").value = daCleanupFilters.needsAdvance;
      daCleanupRenderTable();
      $("#da-cleanup-table").scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  daCleanupDrawBreakdown("#da-cleanup-chart-service", rows, (r) => r.offered_service, "offeredService");
  daCleanupDrawBreakdown("#da-cleanup-chart-type", rows, daCleanupTypeText, "anomalousType");
}

// Top-8-plus-"Other" bar chart of how many rows fall under each distinct
// value of keyFn - capped so a service/type list with hundreds of
// distinct values doesn't render an unreadable wall of bars. filterKey
// names which daCleanupFilters field a bar click should set ("Other" and
// "(none)" are excluded from click-to-filter - see drawBarChart's
// onBarClick - since they don't map to one real filterable value.
function daCleanupDrawBreakdown(canvasSel, rows, keyFn, filterKey) {
  const canvas = $(canvasSel);
  const counts = new Map();
  rows.forEach((r) => {
    const key = keyFn(r) || "(none)";
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const top = entries.slice(0, 8);
  const otherCount = entries.slice(8).reduce((sum, [, n]) => sum + n, 0);
  if (otherCount > 0) top.push(["Other", otherCount]);
  if (!top.length) { _clearCanvas(canvas); return; }
  const labels = top.map(([k]) => k);
  const onBarClick = (label) => {
    if (label === "Other" || label === "(none)") {
      showToast(`"${label}" groups multiple values — use the dropdown filter above to narrow further.`);
      return;
    }
    daCleanupFilters = { ...daCleanupFilters, [filterKey]: label };
    $(filterKey === "offeredService" ? "#da-cleanup-filter-service" : "#da-cleanup-filter-type").value = label;
    daCleanupRenderTable();
    $("#da-cleanup-table").scrollIntoView({ behavior: "smooth", block: "center" });
  };
  drawBarChart(canvas, labels, top.map(([, n]) => n), {
    colors: labels.map((lab) => _categoricalColor(lab)),
    onBarClick,
  });

  const legendEl = $(`${canvasSel}-legend`);
  if (legendEl) {
    legendEl.innerHTML = labels.map((lab) => `
      <span class="chart-legend-item" data-legend-label="${escapeHtml(lab)}">
        <span class="chart-legend-swatch" style="background:${_categoricalColor(lab)}"></span>${escapeHtml(lab)}
      </span>
    `).join("");
    legendEl.querySelectorAll("[data-legend-label]").forEach((el) => {
      el.addEventListener("click", () => onBarClick(el.dataset.legendLabel));
    });
  }
}

// Shared by the Detect All button and the post-generate auto-refresh below
// (silent=true skips the "Scanning…" placeholder and toast-on-error, since
// the auto-refresh runs right after a success toast already fired and a
// stale scan just means "no rows to show below" - not something the
// analyst needs to be alerted about again).
async function daCleanupRunDetect({ silent = false } = {}) {
  if (!silent) $("#da-cleanup-summary").textContent = "Scanning…";
  daCleanupSelected = new Set();
  // A manual re-scan starts clean; the silent post-generate auto-rescan
  // deliberately KEEPS whatever filters were active, so the analyst's
  // narrowed-down view (e.g. one offered service) doesn't reset out from
  // under them right after they just used it to pick what to generate.
  if (!silent) {
    daCleanupFilters = { offeredService: "", anomalousType: "", needsAdvance: "", allCycle: "", multiPeriod: "", search: "" };
    ["#da-cleanup-filter-service", "#da-cleanup-filter-type", "#da-cleanup-filter-advance", "#da-cleanup-filter-cycle", "#da-cleanup-filter-multiperiod"].forEach((sel) => { $(sel).value = ""; });
    $("#da-cleanup-filter-search").value = "";
  }
  try {
    const { rows, possibly_truncated, limit } = await api("/api/date-anomaly/detect-all", { method: "POST" });
    daCleanupRows = rows;
    daCleanupPopulateFilterOptions();
    daCleanupRenderTable();
    let summary = rows.length
      ? `${rows.length} open anomaly/anomalies found.`
      : "No open Diff Date System anomalies found system-wide.";
    if (possibly_truncated) {
      summary += ` Showing the first ${limit} — there may be more; narrow this down or re-run after processing some.`;
    }
    $("#da-cleanup-summary").textContent = summary;
    daCleanupSetGenerateEnabled();
    return true;
  } catch (err) {
    if (!silent) {
      $("#da-cleanup-summary").textContent = "";
      showToast(err.message, true);
    }
    return false;
  }
}

$("#da-cleanup-detect-btn").addEventListener("click", () => daCleanupRunDetect());

$("#da-cleanup-select-all").addEventListener("change", (e) => {
  // Only the currently visible (filtered) rows - a filtered-out row's
  // selection state, checked or not, is left untouched by this control.
  const visible = daCleanupVisibleIndices();
  if (e.target.checked) visible.forEach((idx) => daCleanupSelected.add(idx));
  else visible.forEach((idx) => daCleanupSelected.delete(idx));
  daCleanupRenderTable();
  daCleanupSetGenerateEnabled();
});

// "Generate Script for Selected" used to call /api/date-anomaly/detect-
// all/generate, which only emits the narrow STATUS-advance + anomaly-
// cancel script (build_cleanup_script) - deliberately, since that route
// has no NISS to run the full Detect->Resolve->Generate pipeline against,
// only item-to-bill ids. Per explicit user request ("i want it to do the
// original, fix all"), this now instead runs the SAME full correction as
// Single NISS / Batch (READING_PREV_DATE, INI_DATE, XML_TO_BILL, STATUS,
// GCCOM_ANOMALOUS cancel) by delegating to the Batch pipeline: each
// selected row's own SUPPLY/NISS (already present from the detect-all
// query's billing-service enrichment join) is collected into a NISS list
// and handed to daBatchStartRun - reuses the already-built, already-
// tested batch job machinery rather than duplicating it, and the analyst
// lands on the Batch tab to watch it run/poll exactly like a normal batch.
// The old narrow route/build_cleanup_script still exist server-side
// (unused by this button now) in case a future round wants a
// "bookkeeping only" quick-cleanup option back.
$("#da-cleanup-generate-btn").addEventListener("click", async () => {
  const program = $("#da-cleanup-program").value.trim();
  if (!program) { showToast("Enter the Jira/Program # this change is for.", true); return; }
  const clean = $("#da-cleanup-clean-toggle").checked;
  const lowest_billing_period_only = $("#da-cleanup-lowest-period-toggle").checked;

  const selectedRows = [...daCleanupSelected].map((idx) => daCleanupRows[idx]);
  const withNiss = selectedRows.filter((r) => (r.supply || "").trim());
  const skipped = selectedRows.length - withNiss.length;
  if (!withNiss.length) {
    showToast("None of the selected rows have a known Supply/NISS - can't run the full correction without one.", true);
    return;
  }
  // Dedup NISS, preserving first-seen order - the same NISS can show up
  // more than once if it has multiple open anomalies selected.
  const niss_list = [...new Set(withNiss.map((r) => r.supply.trim()))];

  // Switch to the Batch tab and populate it exactly like a manual batch
  // run would be - so what's about to happen (and its results) is visible
  // in the same place Batch results always show up, not hidden behind a
  // Detect All button click.
  document.querySelector('.da-subnav-btn[data-da-sub="batch"]').click();
  $("#da-batch-niss").value = niss_list.join("\n");
  $("#da-batch-threshold").value = "0";
  $("#da-batch-program").value = program;
  $("#da-batch-clean-toggle").checked = clean;
  $("#da-batch-lowest-period-toggle").checked = lowest_billing_period_only;

  showToast(
    skipped > 0
      ? `Running the full correction for ${niss_list.length} NISS (${skipped} selected row(s) skipped - no Supply/NISS known).`
      : `Running the full correction for ${niss_list.length} NISS…`
  );
  await daBatchStartRun({ niss_list, threshold: 0, program, clean, lowest_billing_period_only });
});

$("#da-cleanup-explain-btn").addEventListener("click", async () => {
  // Button is only enabled when daCleanupSelected.size === 1 (see
  // daCleanupSetGenerateEnabled), so this is safe without a re-check -
  // but guard anyway in case a stray click races a selection change.
  if (daCleanupSelected.size !== 1) return;
  const row = daCleanupRows[[...daCleanupSelected][0]];
  const out = $("#da-cleanup-explain-output");
  out.textContent = "Asking AI Assist…";
  out.classList.remove("is-error");
  try {
    const result = await api("/api/date-anomaly/detect-all/explain", { method: "POST", body: row });
    _renderAiOutput(out, result);  // shared helper, defined below - hoisted, safe to call here
  } catch (err) {
    out.textContent = err.message;
    out.classList.add("is-error");
  }
});

$("#da-cleanup-copy-btn").addEventListener("click", async () => {
  const text = $("#da-cleanup-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Cleanup script copied to clipboard.");
  } catch (_) {
    showToast("Couldn't copy - select and copy manually.", true);
  }
});

$("#da-cleanup-download-btn").addEventListener("click", () => {
  const text = $("#da-cleanup-output").textContent;
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "date_anomaly_bulk_cleanup.sql";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

// ---------------- AI Assist page ----------------
async function loadAIPage() {
  try {
    const cfg = await api("/api/config/ai");
    $("#ai-not-configured").hidden = cfg.enabled && cfg.has_key;
  } catch (_) { /* not fatal */ }
}

$("#ai-goto-settings-link").addEventListener("click", (e) => {
  e.preventDefault();
  $$(".nav-item[data-page]").forEach((b) => b.classList.toggle("is-active", b.dataset.page === "settings"));
  $$(".page").forEach((p) => p.classList.remove("is-active"));
  $("#page-settings").classList.add("is-active");
  loadSettingsPage();
});

let _lastAiExtractedSql = "";

function _renderAiOutput(el, result) {
  el.classList.toggle("is-error", !result.success);
  el.textContent = result.text;
}

async function _runAiAction(path, body, outputEl, showApply) {
  outputEl.classList.remove("is-error");
  outputEl.textContent = "Thinking…";
  $("#ai-apply-row").hidden = true;
  try {
    const result = await api(path, { method: "POST", body });
    _renderAiOutput(outputEl, result);
    if (showApply && result.success && result.extracted_sql) {
      _lastAiExtractedSql = result.extracted_sql;
      $("#ai-apply-row").hidden = false;
    }
  } catch (err) {
    outputEl.classList.add("is-error");
    outputEl.textContent = err.message;
  }
}

$("#ai-suggest-btn").addEventListener("click", () => {
  const sql = $("#sql-editor").value;
  const intent = $("#ai-intent-field").value.trim() || "improve this query";
  _runAiAction("/api/ai/suggest", { sql, intent }, $("#ai-main-output"), true);
});
$("#ai-optimize-btn").addEventListener("click", () => {
  _runAiAction("/api/ai/optimize", { sql: $("#sql-editor").value }, $("#ai-main-output"), true);
});
$("#ai-explain-btn").addEventListener("click", () => {
  _runAiAction("/api/ai/explain", { sql: $("#sql-editor").value }, $("#ai-main-output"), false);
});
$("#ai-apply-btn").addEventListener("click", () => {
  if (!_lastAiExtractedSql) return;
  $("#sql-editor").value = _lastAiExtractedSql;
  showToast("Applied to Workspace query.");
});

$("#ai-where-btn").addEventListener("click", async () => {
  const request = $("#ai-where-field").value.trim();
  if (!request) { showToast("Describe the rows you want first.", true); return; }
  await _runAiAction("/api/ai/nl_where", { request }, $("#ai-where-output"), false);
});

// ---------------- Script page: AI Pre-Flight Review ----------------
$("#ai-review-btn").addEventListener("click", async () => {
  const text = $("#script-output").textContent.trim();
  if (!text) { showToast("Generate a script first.", true); return; }
  $("#ai-review-card").hidden = false;
  const out = $("#ai-review-output");
  out.classList.remove("is-error");
  out.textContent = "Reviewing…";
  try {
    const result = await api("/api/ai/review", { method: "POST", body: { sql_text: text } });
    _renderAiOutput(out, result);
  } catch (err) {
    out.classList.add("is-error");
    out.textContent = err.message;
  }
});

// ---------------- Dashboard ----------------
let _dashboardStats = null;
let _statsSortKey = null;
let _statsSortDir = 1; // 1 = ascending, -1 = descending

async function loadDashboard() {
  try {
    _dashboardStats = await api("/api/dashboard/stats");
  } catch (_) {
    $("#dashboard-empty").hidden = false;
    $("#dashboard-body").hidden = true;
    return;
  }
  $("#dashboard-empty").hidden = true;
  $("#dashboard-body").hidden = false;
  renderKpiRow(_dashboardStats);
  renderQualityFlags(_dashboardStats);
  renderStatsTable(_dashboardStats);
  populateDashboardColumnSelects(_dashboardStats);
  drawBoxPlots($("#boxplot-canvas"), _dashboardStats.columns.filter((c) => c.kind === "numeric"));
  $("#trend-hint").textContent = _dashboardStats.source_table
    ? `Table: ${_dashboardStats.source_table}` : "No source table detected for this query.";
}

$("#dashboard-refresh-btn").addEventListener("click", loadDashboard);

// data-kpi drives the colored top-accent bar in CSS (see .kpi-card[data-
// kpi=...]::before) - color follows the metric's meaning, not decorative
// rank, same stance as the rest of this app's charts.
function renderKpiRow(stats) {
  const totalCells = stats.row_count * stats.column_count;
  const completenessPct = totalCells > 0
    ? Math.round(100 * (1 - stats.summary.total_nulls / totalCells)) : 100;
  const cards = [
    ["rows", "📄", "Rows", stats.row_count],
    ["columns", "📐", "Columns", stats.column_count],
    ["numeric", "🔢", "Numeric", stats.summary.numeric_columns],
    ["categorical", "🏷️", "Categorical", stats.summary.categorical_columns],
    ["empty", "🕳️", "Empty", stats.summary.empty_columns],
    ["nulls", "❔", "Total NULLs", stats.summary.total_nulls],
    ["duplicates", "🧬", "Duplicate rows", stats.duplicate_row_count],
    ["completeness", "✅", "Completeness", `${completenessPct}%`],
  ];
  $("#kpi-row").innerHTML = cards.map(([kpi, icon, label, value]) =>
    `<div class="kpi-card" data-kpi="${kpi}">
      <div class="kpi-value">${value}</div>
      <div class="kpi-label"><span class="kpi-card-icon">${icon}</span>${label}</div>
    </div>`
  ).join("");
}

const QUALITY_FLAG_ICON = { likely_key: "🔑", constant: "🧱", high_nulls: "❔", has_outliers: "📤" };
const QUALITY_FLAG_LABEL = { likely_key: "Likely key", constant: "Constant", high_nulls: "High NULLs", has_outliers: "Has outliers" };

function renderQualityFlags(stats) {
  const flags = stats.quality_flags || [];
  $("#quality-flags").innerHTML = flags.map((f) =>
    `<span class="quality-flag" data-kind="${f.kind}" title="${escapeHtml(f.detail)}">
      ${QUALITY_FLAG_ICON[f.kind] || "ℹ️"} <b>${escapeHtml(f.column)}</b> — ${QUALITY_FLAG_LABEL[f.kind] || f.kind}
    </span>`
  ).join("");
}

function _fmtNum(v) {
  return v === null || v === undefined ? "" : (Number.isInteger(v) ? String(v) : v.toFixed(2));
}

function _fmtPct(v) {
  return v === null || v === undefined ? "" : `${Math.round(v * 100)}%`;
}

// Sort comparator over the same field names the table renders - null/
// undefined always sorts last regardless of direction, so switching
// direction never buries real values under a wall of blanks.
function _statsSortedColumns(columns) {
  if (!_statsSortKey) return columns;
  const key = _statsSortKey;
  return [...columns].sort((a, b) => {
    const av = a[key], bv = b[key];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "string") return _statsSortDir * av.localeCompare(bv);
    return _statsSortDir * (av - bv);
  });
}

function renderStatsTable(stats) {
  const tbody = document.querySelector("#stats-table tbody");
  tbody.innerHTML = "";
  _statsSortedColumns(stats.columns).forEach((c) => {
    const topValues = c.top_values && c.top_values.length
      ? c.top_values.slice(0, 3).map(([v, n]) => `${v} (${n})`).join(", ") : "";
    const completenessPct = c.row_count > 0 ? Math.round(100 * c.non_null_count / c.row_count) : 0;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(c.name)}</td>
      <td><span class="kind-badge" data-kind="${c.kind}">${c.kind}</span></td>
      <td>
        <span class="completeness-bar" title="${c.non_null_count} of ${c.row_count} non-null, ${c.null_count} NULL">
          <span class="completeness-bar-track"><span class="completeness-bar-fill" style="width:${completenessPct}%;"></span></span>
          <span class="completeness-bar-text">${completenessPct}%</span>
        </span>
      </td>
      <td>${c.distinct_count}</td>
      <td>${_fmtPct(c.unique_ratio)}</td>
      <td>${_fmtNum(c.min_value)}</td><td>${_fmtNum(c.max_value)}</td>
      <td>${_fmtNum(c.mean_value)}</td><td>${_fmtNum(c.median_value)}</td><td>${_fmtNum(c.stdev_value)}</td>
      <td>${c.outlier_count === null || c.outlier_count === undefined ? "" : c.outlier_count}</td>
      <td title="${escapeHtml(topValues)}">${escapeHtml(topValues.slice(0, 60))}</td>
    `;
    tbody.appendChild(tr);
  });
}

document.querySelectorAll("#stats-table .stats-table-th-sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    _statsSortDir = _statsSortKey === key ? -_statsSortDir : 1;
    _statsSortKey = key;
    document.querySelectorAll("#stats-table .stats-table-th-sortable").forEach((h) => {
      h.querySelector(".stats-table-sort-arrow")?.remove();
    });
    th.insertAdjacentHTML("beforeend", `<span class="stats-table-sort-arrow">${_statsSortDir === 1 ? "▲" : "▼"}</span>`);
    if (_dashboardStats) renderStatsTable(_dashboardStats);
  });
});

$("#dashboard-export-csv-btn").addEventListener("click", () => {
  if (!_dashboardStats) return;
  const header = ["column", "kind", "non_null", "null", "distinct", "unique_ratio",
    "min", "max", "mean", "median", "stdev", "outliers", "top_values"];
  const lines = [header.join(",")];
  _dashboardStats.columns.forEach((c) => {
    const top = (c.top_values || []).map(([v, n]) => `${v}:${n}`).join("|");
    const row = [c.name, c.kind, c.non_null_count, c.null_count, c.distinct_count, _fmtPct(c.unique_ratio),
      _fmtNum(c.min_value), _fmtNum(c.max_value), _fmtNum(c.mean_value), _fmtNum(c.median_value),
      _fmtNum(c.stdev_value), c.outlier_count ?? "", top]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`);
    lines.push(row.join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "dashboard_stats.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

function populateDashboardColumnSelects(stats) {
  const numericCols = stats.columns.filter((c) => c.kind === "numeric").map((c) => c.name);
  const opts = numericCols.map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
  $("#hist-column-select").innerHTML = opts;
  $("#corr-a-select").innerHTML = opts;
  $("#corr-b-select").innerHTML = opts;
  if (numericCols.length > 1) $("#corr-b-select").selectedIndex = 1;
}

$("#hist-draw-btn").addEventListener("click", async () => {
  const column = $("#hist-column-select").value;
  if (!column) { showToast("No numeric columns in this result.", true); return; }
  const bins = Math.max(2, Math.min(40, Number($("#hist-bins").value) || 12));
  try {
    const { labels, counts } = await api("/api/dashboard/histogram", { method: "POST", body: { column, bins } });
    $("#hist-empty-hint").hidden = counts.length > 0;
    drawBarChart($("#hist-canvas"), labels, counts);
  } catch (err) {
    showToast(err.message, true);
  }
});

$("#corr-draw-btn").addEventListener("click", async () => {
  const columnA = $("#corr-a-select").value;
  const columnB = $("#corr-b-select").value;
  if (!columnA || !columnB) { showToast("Pick two numeric columns.", true); return; }
  try {
    const { r, points } = await api("/api/dashboard/correlation", { method: "POST", body: { column_a: columnA, column_b: columnB } });
    $("#corr-r-text").textContent = r === null
      ? "Correlation undefined (not enough variance/data)." : `Pearson r = ${r.toFixed(3)} (${_correlationStrength(r)})`;
    drawScatter($("#corr-canvas"), points);
  } catch (err) {
    showToast(err.message, true);
  }
});

$("#trend-draw-btn").addEventListener("click", async () => {
  const table = (_dashboardStats && _dashboardStats.source_table) || "";
  try {
    const { points } = await api("/api/dashboard/trend?" + new URLSearchParams({ table }));
    if (!points.length) {
      $("#trend-hint").textContent = "No exported snapshots of this table yet - use Tools > Snapshot Diff Viewer to export one.";
    }
    drawLineChart($("#trend-canvas"), points.map((p) => p.row_count), points.map((p) => p.created_at_utc.slice(0, 16).replace("T", " ")));
  } catch (err) {
    showToast(err.message, true);
  }
});

// ---------------- Canvas chart helpers (no library - plain 2D canvas) ----------------
function _canvasColors() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  return {
    bar: dark ? "#5b7cfa" : "#3b5bfd",
    axis: dark ? "#4a4d63" : "#d7dbe6",
    text: dark ? "#9aa0b4" : "#6b7280",
    point: dark ? "#5b7cfa" : "#3b5bfd",
    line: dark ? "#5b7cfa" : "#3b5bfd",
  };
}

// Distinct-hue palette for CATEGORICAL bar charts only (e.g. Detect All's
// "by offered service"/"by anomaly type" breakdowns, where each bar is a
// different real-world category the analyst can click into) - kept
// separate from _canvasColors()'s single accent hue, which stays the only
// color used for non-categorical charts (the numeric histogram, scatter,
// trend line) where a rainbow would be decorative noise, not information
// (per this app's established dataviz stance - see the KPI-card round's
// notes). "Other" and "(none)" always render in a muted gray (assigned
// below, not from this array) since they're catch-all buckets, not a
// single real category.
const CATEGORICAL_PALETTE = {
  light: ["#3b5bfd", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16"],
  dark: ["#5b7cfa", "#fbbf24", "#34d399", "#f87171", "#a78bfa", "#22d3ee", "#f472b6", "#a3e635"],
};
const _categoricalColorCache = new Map();
function _categoricalColor(label) {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  if (label === "Other" || label === "(none)") return dark ? "#5a5f75" : "#b7bcc9";
  // Stable per-label assignment (not just per-index) so the same category
  // keeps the same color across re-renders/filter changes, not just
  // across one chart's own bars.
  const key = (dark ? "d:" : "l:") + label;
  if (!_categoricalColorCache.has(key)) {
    const palette = CATEGORICAL_PALETTE[dark ? "dark" : "light"];
    _categoricalColorCache.set(key, palette[_categoricalColorCache.size % palette.length]);
  }
  return _categoricalColorCache.get(key);
}

function _clearCanvas(canvas) {
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  return ctx;
}

// Shared floating tooltip element for all canvas charts - created once,
// repositioned/shown on hover. A single div reused across every chart
// rather than one per canvas, since only one can be hovered at a time.
function _chartTooltip() {
  let el = document.getElementById("chart-tooltip");
  if (!el) {
    el = document.createElement("div");
    el.id = "chart-tooltip";
    el.className = "chart-tooltip";
    el.hidden = true;
    document.body.appendChild(el);
  }
  return el;
}

// Attaches hover-tooltip (+ optional click-to-filter) behavior to a
// canvas, reading whatever bar geometry drawBarChart last stored on
// canvas._bars. Bound once per canvas element (guarded by a flag) since
// drawBarChart runs on every redraw but listeners should not stack.
function _bindChartInteractivity(canvas, onBarClick) {
  canvas._onBarClick = onBarClick || null; // always refresh to the latest closure (captures current rows/filters)
  if (canvas._chartInteractiveBound) return;
  canvas._chartInteractiveBound = true;
  const tooltip = _chartTooltip();

  function barAt(evtX, evtY) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
    const x = (evtX - rect.left) * scaleX, y = (evtY - rect.top) * scaleY;
    return (canvas._bars || []).find((b) => x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h);
  }

  canvas.addEventListener("mousemove", (e) => {
    const bar = barAt(e.clientX, e.clientY);
    canvas.style.cursor = bar && canvas._onBarClick ? "pointer" : "default";
    if (!bar) { tooltip.hidden = true; return; }
    tooltip.hidden = false;
    tooltip.textContent = `${bar.label}: ${bar.value}`;
    tooltip.style.left = `${e.clientX + 12}px`;
    tooltip.style.top = `${e.clientY + 12}px`;
  });
  canvas.addEventListener("mouseleave", () => { tooltip.hidden = true; canvas.style.cursor = "default"; });
  canvas.addEventListener("click", (e) => {
    if (!canvas._onBarClick) return;
    const bar = barAt(e.clientX, e.clientY);
    if (bar) canvas._onBarClick(bar.label, bar.value);
  });
}

// options.colors: optional array, one fill color per bar (categorical
// charts only - see _categoricalColor); falls back to the single accent
// hue when omitted, unchanged from before this round (the Dashboard
// page's numeric histogram never passes this).
// options.onBarClick(label, value): optional click-to-filter callback.
function drawBarChart(canvas, labels, values, options = {}) {
  const ctx = _clearCanvas(canvas);
  const colors = _canvasColors();
  const W = canvas.width, H = canvas.height;
  const padL = 36, padB = 28, padT = 10, padR = 10;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxVal = Math.max(1, ...values);

  ctx.strokeStyle = colors.axis;
  ctx.beginPath();
  ctx.moveTo(padL, padT); ctx.lineTo(padL, H - padB); ctx.lineTo(W - padR, H - padB);
  ctx.stroke();

  const n = values.length || 1;
  const barW = plotW / n;
  const bars = [];
  values.forEach((v, i) => {
    const h = (v / maxVal) * (plotH - 6);
    const x = padL + i * barW + barW * 0.12;
    const w = barW * 0.76;
    const y = H - padB - h;
    ctx.fillStyle = (options.colors && options.colors[i]) || colors.bar;
    ctx.fillRect(x, y, w, h);
    // Hit box is the bar's full column width (not just the drawn bar),
    // and extends up through the empty space above a short bar, so
    // hovering/clicking near a small value is just as easy as a tall one.
    bars.push({ x: padL + i * barW, y: padT, w: barW, h: H - padB - padT, label: String(labels[i]), value: v });
  });
  canvas._bars = bars;
  _bindChartInteractivity(canvas, options.onBarClick);

  ctx.fillStyle = colors.text;
  ctx.font = "10px sans-serif";
  ctx.textAlign = "center";
  const labelStep = Math.max(1, Math.ceil(n / 8));
  labels.forEach((lab, i) => {
    if (i % labelStep !== 0) return;
    ctx.fillText(String(lab), padL + i * barW + barW / 2, H - padB + 14);
  });
  ctx.textAlign = "left";
  ctx.fillText(String(maxVal), 2, padT + 8);
}

// Pie-slice equivalent of _bindChartInteractivity above - separate
// because hit-testing a slice is angle/radius-based, not the rectangular
// box test drawBarChart's bars use. Reads canvas._slices/_pieCenter,
// last set by drawPieChart. Bound once per canvas (guarded the same way).
function _bindPieInteractivity(canvas, onSliceClick) {
  canvas._onSliceClick = onSliceClick || null;
  if (canvas._pieInteractiveBound) return;
  canvas._pieInteractiveBound = true;
  const tooltip = _chartTooltip();

  function sliceAt(evtX, evtY) {
    const center = canvas._pieCenter;
    if (!center) return null;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
    const x = (evtX - rect.left) * scaleX, y = (evtY - rect.top) * scaleY;
    const dx = x - center.cx, dy = y - center.cy;
    if (Math.sqrt(dx * dx + dy * dy) > center.r) return null;
    let angle = Math.atan2(dy, dx);
    if (angle < -Math.PI / 2) angle += Math.PI * 2; // match drawPieChart's slice range: [-PI/2, 3PI/2)
    return (canvas._slices || []).find((s) => angle >= s.start && angle < s.end);
  }

  canvas.addEventListener("mousemove", (e) => {
    const slice = sliceAt(e.clientX, e.clientY);
    canvas.style.cursor = slice && canvas._onSliceClick ? "pointer" : "default";
    if (!slice) { tooltip.hidden = true; return; }
    tooltip.hidden = false;
    tooltip.textContent = `${slice.label}: ${slice.value}`;
    tooltip.style.left = `${e.clientX + 12}px`;
    tooltip.style.top = `${e.clientY + 12}px`;
  });
  canvas.addEventListener("mouseleave", () => { tooltip.hidden = true; canvas.style.cursor = "default"; });
  canvas.addEventListener("click", (e) => {
    if (!canvas._onSliceClick) return;
    const slice = sliceAt(e.clientX, e.clientY);
    if (slice) canvas._onSliceClick(slice.label, slice.value);
  });
}

// options.colors: array, one fill color per slice (categorical - see
// _categoricalColor, same convention drawBarChart's own options.colors
// uses). options.onSliceClick(label, value): optional click-to-navigate
// callback (see the Overview page's own use of this, below).
function drawPieChart(canvas, labels, values, options = {}) {
  const ctx = _clearCanvas(canvas);
  const colors = _canvasColors();
  const W = canvas.width, H = canvas.height;
  const cx = W / 2, cy = H / 2;
  const r = Math.min(W, H) / 2 - 8;
  const total = values.reduce((a, b) => a + b, 0);

  if (!total) {
    canvas._slices = [];
    canvas._pieCenter = { cx, cy, r };
    _bindPieInteractivity(canvas, options.onSliceClick);
    ctx.fillStyle = colors.text;
    ctx.font = "12px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No data yet", cx, cy);
    return;
  }

  let start = -Math.PI / 2; // 12 o'clock
  const slices = [];
  labels.forEach((lab, i) => {
    const value = values[i];
    const end = start + (value / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, r, start, end);
    ctx.closePath();
    ctx.fillStyle = (options.colors && options.colors[i]) || colors.bar;
    ctx.fill();
    slices.push({ label: String(lab), value, start, end });
    start = end;
  });
  canvas._slices = slices;
  canvas._pieCenter = { cx, cy, r };
  _bindPieInteractivity(canvas, options.onSliceClick);
}

// Ring-band hit-testing for drawGaugeChart - same idea as
// _bindPieInteractivity but the hit test is an annulus (distance from
// center within half the ring's stroke width of the radius) rather than
// "anywhere inside r", since a gauge is a stroked arc band, not a filled
// wedge. Angle convention matches drawPieChart (0 = 3 o'clock, increasing
// clockwise); the gauge only ever occupies [PI, 2*PI) (the top half), so
// clicks on the empty bottom half simply match no slice.
function _bindGaugeInteractivity(canvas, onSliceClick) {
  canvas._onSliceClick = onSliceClick || null;
  if (canvas._gaugeInteractiveBound) return;
  canvas._gaugeInteractiveBound = true;
  const tooltip = _chartTooltip();

  function sliceAt(evtX, evtY) {
    const center = canvas._gaugeCenter;
    if (!center) return null;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
    const x = (evtX - rect.left) * scaleX, y = (evtY - rect.top) * scaleY;
    const dx = x - center.cx, dy = y - center.cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (Math.abs(dist - center.r) > center.ringWidth / 2 + 2) return null;
    let angle = Math.atan2(dy, dx);
    if (angle < 0) angle += Math.PI * 2; // normalize into [0, 2*PI)
    return (canvas._slices || []).find((s) => angle >= s.start && angle < s.end);
  }

  canvas.addEventListener("mousemove", (e) => {
    const slice = sliceAt(e.clientX, e.clientY);
    canvas.style.cursor = slice && canvas._onSliceClick ? "pointer" : "default";
    if (!slice) { tooltip.hidden = true; return; }
    tooltip.hidden = false;
    tooltip.textContent = `${slice.label}: ${slice.value}`;
    tooltip.style.left = `${e.clientX + 12}px`;
    tooltip.style.top = `${e.clientY + 12}px`;
  });
  canvas.addEventListener("mouseleave", () => { tooltip.hidden = true; canvas.style.cursor = "default"; });
  canvas.addEventListener("click", (e) => {
    if (!canvas._onSliceClick) return;
    const slice = sliceAt(e.clientX, e.clientY);
    if (slice) canvas._onSliceClick(slice.label, slice.value);
  });
}

// "Half moon" gauge chart - a modern speedometer-style ring spanning the
// top 180 degrees only (from 9 o'clock, over 12 o'clock, to 3 o'clock),
// each category drawn as a rounded-cap arc segment proportional to its
// share of the total, with the running total shown as a big number in
// the center. Same options.colors/options.onSliceClick contract as
// drawPieChart, so the two are drop-in swappable at call sites.
function drawGaugeChart(canvas, labels, values, options = {}) {
  const ctx = _clearCanvas(canvas);
  const colors = _canvasColors();
  const W = canvas.width, H = canvas.height;
  const cx = W / 2, cy = H - 14;
  const r = Math.max(20, Math.min(W / 2, H) - 18);
  const ringWidth = Math.max(12, r * 0.34);
  const total = values.reduce((a, b) => a + b, 0);

  // Background track for the full half-circle, faint, drawn first so
  // segments layer cleanly on top of it.
  ctx.beginPath();
  ctx.arc(cx, cy, r, Math.PI, Math.PI * 2, false);
  ctx.lineWidth = ringWidth;
  ctx.lineCap = "round";
  ctx.strokeStyle = colors.axis;
  ctx.globalAlpha = 0.35;
  ctx.stroke();
  ctx.globalAlpha = 1;

  canvas._gaugeCenter = { cx, cy, r, ringWidth };

  if (!total) {
    canvas._slices = [];
    _bindGaugeInteractivity(canvas, options.onSliceClick);
    ctx.fillStyle = colors.text;
    ctx.font = "12px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No data yet", cx, cy - r / 2);
    return;
  }

  const gap = labels.length > 1 ? 0.035 : 0; // small visual seam between segments
  let start = Math.PI; // 9 o'clock
  const slices = [];
  labels.forEach((lab, i) => {
    const value = values[i];
    const end = start + (value / total) * Math.PI;
    const isFirst = i === 0, isLast = i === labels.length - 1;
    const drawStart = start + (isFirst ? 0 : gap / 2);
    const drawEnd = end - (isLast ? 0 : gap / 2);
    if (drawEnd > drawStart) {
      ctx.beginPath();
      ctx.arc(cx, cy, r, drawStart, drawEnd, false);
      ctx.lineWidth = ringWidth;
      ctx.lineCap = "round";
      ctx.strokeStyle = (options.colors && options.colors[i]) || colors.bar;
      ctx.stroke();
    }
    slices.push({ label: String(lab), value, start, end });
    start = end;
  });
  canvas._slices = slices;
  _bindGaugeInteractivity(canvas, options.onSliceClick);

  // Center readout: total across all segments, the number a "how much
  // is outstanding right now" gauge is really answering.
  ctx.textAlign = "center";
  ctx.fillStyle = colors.text;
  ctx.font = "bold 20px sans-serif";
  ctx.fillText(String(total), cx, cy - ringWidth - 6);
  ctx.font = "10px sans-serif";
  ctx.globalAlpha = 0.75;
  ctx.fillText("total open", cx, cy - ringWidth + 10);
  ctx.globalAlpha = 1;
}

function drawScatter(canvas, points) {
  const ctx = _clearCanvas(canvas);
  const colors = _canvasColors();
  const W = canvas.width, H = canvas.height;
  const padL = 44, padB = 28, padT = 10, padR = 10;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  ctx.strokeStyle = colors.axis;
  ctx.beginPath();
  ctx.moveTo(padL, padT); ctx.lineTo(padL, H - padB); ctx.lineTo(W - padR, H - padB);
  ctx.stroke();

  if (!points.length) return;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const spanX = maxX - minX || 1, spanY = maxY - minY || 1;

  ctx.fillStyle = colors.point;
  points.forEach(([x, y]) => {
    const px = padL + ((x - minX) / spanX) * plotW;
    const py = H - padB - ((y - minY) / spanY) * plotH;
    ctx.beginPath();
    ctx.arc(px, py, 2.6, 0, Math.PI * 2);
    ctx.fill();
  });

  ctx.fillStyle = colors.text;
  ctx.font = "10px sans-serif";
  ctx.fillText(minX.toFixed(1), padL, H - padB + 14);
  ctx.textAlign = "right";
  ctx.fillText(maxX.toFixed(1), W - padR, H - padB + 14);
  ctx.textAlign = "left";
  ctx.fillText(maxY.toFixed(1), 2, padT + 8);
  ctx.fillText(minY.toFixed(1), 2, H - padB);
}

function drawLineChart(canvas, values, labels) {
  const ctx = _clearCanvas(canvas);
  const colors = _canvasColors();
  const W = canvas.width, H = canvas.height;
  const padL = 40, padB = 28, padT = 10, padR = 10;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  ctx.strokeStyle = colors.axis;
  ctx.beginPath();
  ctx.moveTo(padL, padT); ctx.lineTo(padL, H - padB); ctx.lineTo(W - padR, H - padB);
  ctx.stroke();

  if (values.length < 1) return;
  const maxV = Math.max(1, ...values);
  const minV = Math.min(0, ...values);
  const spanV = (maxV - minV) || 1;
  const n = values.length;
  const stepX = n > 1 ? plotW / (n - 1) : 0;

  ctx.strokeStyle = colors.line;
  ctx.lineWidth = 2;
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = padL + i * stepX;
    const y = H - padB - ((v - minV) / spanV) * (plotH - 6);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.lineWidth = 1;

  ctx.fillStyle = colors.line;
  values.forEach((v, i) => {
    const x = padL + i * stepX;
    const y = H - padB - ((v - minV) / spanV) * (plotH - 6);
    ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
  });

  ctx.fillStyle = colors.text;
  ctx.font = "10px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(String(maxV), 2, padT + 8);
  if (labels && labels.length) {
    ctx.textAlign = "center";
    const step = Math.max(1, Math.ceil(n / 6));
    labels.forEach((lab, i) => {
      if (i % step !== 0) return;
      ctx.fillText(lab, padL + i * stepX, H - padB + 14);
    });
    ctx.textAlign = "left";
  }
}

// Plain-language reading of a Pearson r - purely a label next to the
// number the API already returns, no new backend field needed. Thresholds
// are the common rule-of-thumb bands (Cohen-ish), not a statistical claim.
function _correlationStrength(r) {
  const a = Math.abs(r);
  const dir = r >= 0 ? "positive" : "negative";
  if (a >= 0.7) return `strong ${dir}`;
  if (a >= 0.4) return `moderate ${dir}`;
  if (a >= 0.2) return `weak ${dir}`;
  return "negligible";
}

// Horizontal box-and-whisker plot, one row per numeric column, drawn from
// the SAME per-column stats /api/dashboard/stats already returns (min/p25/
// median/p75/max/outlier_count) - no extra request. Whiskers extend to
// min/max; the box spans P25-P75 with a line at the median; a small
// outlier count badge renders next to a row that has any, since plotting
// the individual outlier VALUES would need row-level data this endpoint
// intentionally doesn't return (it's a column-stats summary, not raw
// rows) - the count is enough to flag "look at the Outliers column above".
function drawBoxPlots(canvas, numericColumns) {
  const ctx = _clearCanvas(canvas);
  const colors = _canvasColors();
  const W = canvas.width, H = canvas.height;
  $("#boxplot-empty-hint").hidden = numericColumns.length > 0;
  if (!numericColumns.length) return;

  const padL = 110, padR = 50, padT = 14, padB = 24;
  const plotW = W - padL - padR;
  const rowH = Math.min(34, (H - padT - padB) / numericColumns.length);

  const globalMin = Math.min(...numericColumns.map((c) => c.min_value));
  const globalMax = Math.max(...numericColumns.map((c) => c.max_value));
  const span = (globalMax - globalMin) || 1;
  const xFor = (v) => padL + ((v - globalMin) / span) * plotW;

  ctx.font = "10.5px sans-serif";
  numericColumns.forEach((c, i) => {
    const cy = padT + i * rowH + rowH / 2;
    const boxTop = cy - rowH * 0.28, boxBottom = cy + rowH * 0.28;

    // Whisker (min -> max)
    ctx.strokeStyle = colors.axis;
    ctx.beginPath();
    ctx.moveTo(xFor(c.min_value), cy); ctx.lineTo(xFor(c.max_value), cy);
    ctx.stroke();
    [c.min_value, c.max_value].forEach((v) => {
      ctx.beginPath();
      ctx.moveTo(xFor(v), boxTop); ctx.lineTo(xFor(v), boxBottom);
      ctx.stroke();
    });

    // Box (P25 -> P75)
    const bx0 = xFor(c.p25_value), bx1 = xFor(c.p75_value);
    ctx.fillStyle = colors.bar;
    ctx.globalAlpha = 0.28;
    ctx.fillRect(bx0, boxTop, Math.max(1, bx1 - bx0), boxBottom - boxTop);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = colors.bar;
    ctx.strokeRect(bx0, boxTop, Math.max(1, bx1 - bx0), boxBottom - boxTop);

    // Median line
    ctx.beginPath();
    ctx.moveTo(xFor(c.median_value), boxTop); ctx.lineTo(xFor(c.median_value), boxBottom);
    ctx.stroke();

    // Row label (left) and outlier badge (right), truncated so a long
    // column name never overlaps the plot area itself.
    ctx.fillStyle = colors.text;
    ctx.textAlign = "right";
    let label = c.name;
    if (label.length > 16) label = label.slice(0, 15) + "…";
    ctx.fillText(label, padL - 8, cy + 3);
    if (c.outlier_count) {
      ctx.fillStyle = "#f59e0b";
      ctx.textAlign = "left";
      ctx.fillText(`⚠ ${c.outlier_count}`, W - padR + 6, cy + 3);
    }
  });
  ctx.textAlign = "left";
}

// ---------------- Hierarchy Analysis ----------------
// System-wide scan for PRIMARY meter hierarchies (GCGT_RE_MEASUREMENT_
// POINT.IND_DIST_PPAL = 1) with a not-yet-billed reading - see app/core/
// hierarchy_analysis.py's module docstring for the full background and
// the "KNOWN ASSUMPTIONS" this whole page is built against (still a
// draft pending the analyst's own review). Structurally mirrors Detect
// All (daCleanup* above): a full-scan array kept client-side, a filters
// object + visible-indices function, a KPI row, and CSV/Excel export of
// whatever's currently visible - same conventions, new page.
let hierRows = [];
let hierFilters = { search: "", status: "", type: "", mpStatus: "", billingPeriod: "", primaryOnly: false, periodBilled: "" };
// Column-sort state for the main Pending Primaries table (task 2026-09-
// 11: "allow to order by the different columns the main and detail").
// null key = no manual sort, table keeps its natural query order.
let hierSortKey = null;
let hierSortDir = 1; // 1 = ascending, -1 = descending
// Drill-down (Hierarchy Detail) state - separate from the main hierRows/
// hierFilters above since it's a different result set (one hierarchy's
// members, not the system-wide primaries scan).
let hierDetailRows = [];
let hierDetailNotBilledOnly = false;
// Detail table's own column-sort state - independent of the main
// table's. When set, it REPLACES the "not-billed members sort to top"
// default (see hierRenderDetailTable) with a plain sort on the chosen
// column - picking a column is an explicit request to see THAT order.
let hierDetailSortKey = null;
let hierDetailSortDir = 1;

// Generic column-sort comparator shared by both Hierarchy Analysis
// tables. Values here always arrive as already-formatted strings
// (diff_engine.cell_display on the server), not raw numbers/dates, so
// this tries a numeric compare first (Number() on both sides - works
// for ids/counts/billing periods) and falls back to a locale string
// compare otherwise. Blank/null always sorts last regardless of
// direction, same convention the Dashboard stats table's own
// _statsSortedColumns uses, so switching direction never buries real
// values under a wall of blanks.
function _hierCompareValues(av, bv, dir) {
  const aBlank = av === null || av === undefined || av === "";
  const bBlank = bv === null || bv === undefined || bv === "";
  if (aBlank && bBlank) return 0;
  if (aBlank) return 1;
  if (bBlank) return -1;
  const an = Number(av), bn = Number(bv);
  if (!Number.isNaN(an) && !Number.isNaN(bn)) return dir * (an - bn);
  return dir * String(av).localeCompare(String(bv));
}

// Wires click-to-sort onto every `th[data-sort]` inside `tableSelector`
// (both Hierarchy tables reuse the Dashboard stats table's own
// `.stats-table-th-sortable`/`.stats-table-sort-arrow` CSS - it isn't
// actually specific to that one table). `getKey`/`setKey`/`getDir`/
// `setDir` read/write whichever module-level sort state belongs to this
// table; `rerender` redraws it afterward.
function hierWireSortableHeaders(tableSelector, getKey, setKey, getDir, setDir, rerender) {
  document.querySelectorAll(`${tableSelector} thead th[data-sort]`).forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      setDir(getKey() === key ? -getDir() : 1);
      setKey(key);
      document.querySelectorAll(`${tableSelector} thead th[data-sort]`).forEach((h) => {
        h.querySelector(".stats-table-sort-arrow")?.remove();
      });
      th.insertAdjacentHTML("beforeend", `<span class="stats-table-sort-arrow">${getDir() === 1 ? "▲" : "▼"}</span>`);
      rerender();
    });
  });
}

// "Billed" (for highlight/sort/filter purposes, Hierarchy Detail only) =
// READ_STATUS 7000STSRED "Facturada", 7001STSRED "UAU - Billed" (both
// literally named "Billed" on GCGT_RE_READ_STATUS, live-verified earlier
// this project - see app/core/hierarchy_analysis.py's module docstring
// for the full lookup), 8000STSRED "Terminated Not Billed" - per the
// analyst's own direction to treat THAT one the same as billed here even
// though its own name says "Not Billed": it's a closed/resolved state
// (won't ever be billed), not a pending one that needs a second look -
// and, per a later analyst clarification, 6000STSRED "Enviado a
// Facturar" (Sent to Bill) too: "sent to bill status is already ok,
// only count as not ok status Anomalous and Available" - it's moving on
// its own, out of the analyst's queue, same "already handled" reasoning
// as SECONDARY_NOT_SENT_STATUSES uses for the main table's green
// highlight (see app/core/hierarchy_analysis.py). So the red "not
// billed" flag here now fires ONLY for 1000STSRED (Available) and
// 5000STSRED (Anomalous) - or no reading at all (blank read_status, from
// the query's LEFT JOIN, which still counts as needing attention).
const HIER_BILLED_READ_STATUSES = new Set(["6000STSRED", "7000STSRED", "7001STSRED", "8000STSRED"]);
function hierIsBilled(readStatus) {
  return HIER_BILLED_READ_STATUSES.has(readStatus);
}

function hierVisibleIndices(ignoreFilters = false) {
  const search = hierFilters.search.trim().toLowerCase();
  const filtered = hierRows
    .map((r, i) => [r, i])
    .filter(([r]) => {
      if (ignoreFilters) return true;
      if (hierFilters.status && r.read_status !== hierFilters.status) return false;
      if (hierFilters.type && r.reading_type !== hierFilters.type) return false;
      if (hierFilters.mpStatus && r.mp_status !== hierFilters.mpStatus) return false;
      if (hierFilters.billingPeriod && String(r.id_billing_period ?? "") !== hierFilters.billingPeriod) return false;
      if (hierFilters.primaryOnly && Number(r.secondaries_not_sent_count ?? 0) !== 0) return false;
      if (hierFilters.periodBilled && String(r.period_billed ?? "0") !== hierFilters.periodBilled) return false;
      if (search) {
        const haystack = `${r.niss || ""} ${r.id_measuring_point || ""}`.toLowerCase();
        if (!haystack.includes(search)) return false;
      }
      return true;
    });
  if (hierSortKey) {
    filtered.sort(([a], [b]) => _hierCompareValues(a[hierSortKey], b[hierSortKey], hierSortDir));
  }
  return filtered.map(([, i]) => i);
}

function hierPopulateFilterOptions() {
  const statuses = [...new Set(hierRows.map((r) => r.read_status).filter(Boolean))].sort();
  const types = [...new Set(hierRows.map((r) => r.reading_type).filter(Boolean))].sort();
  const mpStatuses = [...new Set(hierRows.map((r) => r.mp_status).filter(Boolean))].sort();
  // Sorted numerically (billing periods are numeric ids like 10000000194),
  // not lexically - a string sort would put "10000000199" before
  // "10000000200".
  const billingPeriods = [...new Set(hierRows.map((r) => String(r.id_billing_period ?? "")).filter(Boolean))]
    .sort((a, b) => Number(a) - Number(b));
  const opt = (v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`;
  $("#hier-filter-status").innerHTML = `<option value="">All</option>` + statuses.map(opt).join("");
  $("#hier-filter-type").innerHTML = `<option value="">All</option>` + types.map(opt).join("");
  $("#hier-filter-mpstatus").innerHTML = `<option value="">All</option>` + mpStatuses.map(opt).join("");
  $("#hier-filter-billingperiod").innerHTML = `<option value="">All</option>` + billingPeriods.map(opt).join("");
  $("#hier-filter-status").value = hierFilters.status;
  $("#hier-filter-type").value = hierFilters.type;
  $("#hier-filter-mpstatus").value = hierFilters.mpStatus;
  $("#hier-filter-billingperiod").value = hierFilters.billingPeriod;
  $("#hier-filter-primaryonly").checked = hierFilters.primaryOnly;
  hierSyncPeriodBilledSeg();
  $("#hier-filter-row").hidden = hierRows.length === 0;
  $("#hier-export-all-wrap").hidden = hierRows.length === 0;
  $("#hier-dashboard-card").hidden = hierRows.length === 0;
}

// Segmented "Period billed" control (All / Billed / Not billed) - replaces
// the old <select>; hierFilters.periodBilled keeps the same "", "1", "0".
function hierSyncPeriodBilledSeg() {
  $$("#hier-filter-periodbilled button").forEach((b) =>
    b.classList.toggle("is-active", b.dataset.value === (hierFilters.periodBilled || "")));
}

// Active-filter count badge on the Filters panel header.
function hierUpdateFilterBadge() {
  const f = hierFilters;
  const n = [f.search.trim(), f.status, f.type, f.mpStatus, f.billingPeriod, f.periodBilled].filter(Boolean).length
    + (f.primaryOnly ? 1 : 0);
  const badge = $("#hier-filter-badge");
  badge.textContent = String(n);
  badge.hidden = n === 0;
}

// --- Modern dashboard helpers (RJ 2026-09-27: "modern, using gauge meter
// style") ---------------------------------------------------------------
// Semicircle gauge: arc filled to `pct` (0-100) plus a needle. pathLength
// =100 lets stroke-dasharray take the percentage directly.
function hxGaugeSvg(pct, colorFrom, colorTo, id) {
  const p = Math.max(0, Math.min(100, pct));
  const angle = -90 + (p * 180) / 100;
  return `
    <svg viewBox="0 0 120 72" class="hx-gauge-svg" aria-hidden="true">
      <defs><linearGradient id="hxg-${id}" x1="0" x2="1" y1="0" y2="0">
        <stop offset="0%" stop-color="${colorFrom}"/><stop offset="100%" stop-color="${colorTo}"/>
      </linearGradient></defs>
      <path d="M12 62 A48 48 0 0 1 108 62" class="hx-gauge-track" pathLength="100"/>
      <path d="M12 62 A48 48 0 0 1 108 62" class="hx-gauge-fill" pathLength="100"
            stroke="url(#hxg-${id})" stroke-dasharray="${p} 100"/>
      <g transform="rotate(${angle} 60 62)">
        <line x1="60" y1="62" x2="60" y2="22" class="hx-gauge-needle"/>
      </g>
      <circle cx="60" cy="62" r="4.5" class="hx-gauge-hub"/>
    </svg>`;
}

function hierRenderGauges(visibleRows) {
  const n = visibleRows.length || 0;
  const billed = visibleRows.filter((r) => String(r.period_billed) === "1").length;
  const primaryOnly = visibleRows.filter((r) => Number(r.secondaries_not_sent_count ?? NaN) === 0).length;
  const anomalous = visibleRows.filter((r) => r.read_status === "5000STSRED").length;
  const pct = (x) => (n ? Math.round((x / n) * 1000) / 10 : 0);
  // KPI-only (RJ 2026-09-27: "i dont want it to be filters the gauge, i
  // want it to be used in kpis") - display, no click behaviour.
  $("#hier-gauge-row").innerHTML = hxGaugesHtml([
    { label: "Period already billed", count: billed, total: n, c1: "#f97316", c2: "#ef4444",
      hint: "Pending reading whose period already has a matching bill" },
    { label: "Primary-only fixes", count: primaryOnly, total: n, c1: "#10b981", c2: "#22c55e",
      hint: "Every secondary already sent — only the primary is left" },
    { label: "Anomalous reads", count: anomalous, total: n, c1: "#f59e0b", c2: "#eab308",
      hint: "Primary reading in 5000STSRED (Anomalous)" },
  ]);
}

// ---------------------------------------------------------------------
// Shared "hx" dashboard toolkit (RJ 2026-09-27: "apply the same design to
// all of my menus"). Every page builds its KPI strip with these:
//   hxGaugesHtml([{label, count, total, c1, c2, hint}])  -> gauge cards
//   hxTilesHtml([{icon, label, value, accent, sub}])      -> stat tiles
//   hxRenderDashboard(containerId, {gauges, tiles, split}) -> both, into
//     a .hx-dashboard container (split = {title, entries:[[label,count]]})
// Gauges are KPIs only - never filters.
// ---------------------------------------------------------------------
let _hxGaugeSeq = 0;
function hxGaugesHtml(gauges) {
  return gauges.map((g) => {
    const total = Number(g.total) || 0;
    const count = Number(g.count) || 0;
    const pct = total ? Math.round((count / total) * 1000) / 10 : 0;
    const id = `g${++_hxGaugeSeq}`;
    return `
    <div class="hx-gauge" title="${escapeHtml(g.hint || "")}">
      ${hxGaugeSvg(pct, g.c1 || "#6366f1", g.c2 || "#06b6d4", id)}
      <div class="hx-gauge-value">${pct}<small>%</small></div>
      <div class="hx-gauge-label">${escapeHtml(g.label)}</div>
      <div class="hx-gauge-count">${count.toLocaleString()} of ${total.toLocaleString()}</div>
    </div>`;
  }).join("");
}

function hxTilesHtml(tiles) {
  return tiles.map((t) => `
    <div class="hx-tile${t.accent ? " hx-tile-accent" : ""}">
      <div class="hx-tile-icon">${t.icon || "•"}</div>
      <div><div class="hx-tile-value">${t.value}${t.sub ? ` <small>${escapeHtml(t.sub)}</small>` : ""}</div>
      <div class="hx-tile-label">${escapeHtml(t.label)}</div></div>
    </div>`).join("");
}

const HX_SPLIT_PALETTE = ["#6366f1", "#06b6d4", "#f59e0b", "#10b981", "#ec4899", "#8b5cf6", "#ef4444", "#94a3b8"];
function hxSplitHtml(title, entries) {
  if (!entries || !entries.length) return "";
  const total = entries.reduce((s, [, c]) => s + c, 0) || 1;
  const color = (i) => HX_SPLIT_PALETTE[i % HX_SPLIT_PALETTE.length];
  return `
    <div class="hx-split-title">${escapeHtml(title)}</div>
    <div class="hx-split-bar">${entries.map(([label, count], i) =>
      `<span style="width:${(count / total) * 100}%;background:${color(i)}" title="${escapeHtml(label)}: ${count}"></span>`).join("")}</div>
    <div class="hx-split-legend">${entries.map(([label, count], i) =>
      `<span><i style="background:${color(i)}"></i>${escapeHtml(label)} <b>${count.toLocaleString()}</b> <em>${Math.round((count / total) * 100)}%</em></span>`).join("")}</div>`;
}

// Counts rows by a key function -> [[label, count], ...] sorted desc.
function hxCountBy(rows, keyFn, emptyLabel = "(none)") {
  const m = new Map();
  rows.forEach((r) => { const k = keyFn(r) || emptyLabel; m.set(k, (m.get(k) || 0) + 1); });
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function hxSum(rows, key) {
  return rows.reduce((s, r) => s + (Number(r[key]) || 0), 0);
}

// Collapses every page's static explanation text by default (RJ
// 2026-09-27: "by default the information about the functionality should
// be collapsed, it is eating space" - now app-wide). Wraps:
//   - .warning-banner                      -> "⚠ Notes"
//   - long static <p class="hint-text">    -> "ⓘ How this works"
// Only STATIC text is touched: anything with an id, or containing an
// element with an id, is left alone (those are live status lines the code
// updates). Consecutive paragraphs in the same spot share one pill.
// Modals and the already-collapsed Hierarchy "About" section are skipped.
function hxCollapseInfo() {
  const skip = (el) => el.closest(".hier-reading-modal, .hx-info, .hx-hint, .hx-modal, #login-overlay, .login-card");
  const wrap = (el, summaryText, warn) => {
    const prev = el.previousElementSibling;
    if (prev && prev.matches("details.hx-hint[data-hx-auto]") && prev.classList.contains("hx-hint-warn") === warn) {
      prev.appendChild(el);
      return;
    }
    const d = document.createElement("details");
    d.className = "hx-hint" + (warn ? " hx-hint-warn" : "");
    d.dataset.hxAuto = "1";
    const s = document.createElement("summary");
    s.textContent = summaryText;
    el.parentNode.insertBefore(d, el);
    d.appendChild(s);
    d.appendChild(el);
  };
  $$(".page .warning-banner").forEach((el) => {
    if (skip(el) || el.id || el.querySelector("[id]")) return;
    wrap(el, "⚠ Notes", true);
  });
  $$(".page p.hint-text, .page div.hint-text").forEach((el) => {
    if (skip(el) || el.id || el.querySelector("[id], input, select, button")) return;
    if (el.textContent.trim().length < 90) return;
    wrap(el, "ⓘ How this works", false);
  });
  // RJ 2026-09-30: "again the infos, should be minimized by default" - the
  // Bill Issuance "How this works" banners and long page subtitles were
  // still always open.
  $$(".page .biss-info-banner").forEach((el) => {
    if (skip(el) || el.id || el.querySelector("[id]")) return;
    wrap(el, "ⓘ How this works", false);
  });
  $$(".page p.page-subtitle").forEach((el) => {
    if (skip(el) || el.id || el.querySelector("[id]")) return;
    const text = el.textContent.trim().replace(/\s+/g, " ");
    if (text.length < 160) return;
    const first = (text.match(/^.*?[.!?](\s|$)/) || [text])[0].trim();
    const summary = first.length > 120 ? first.slice(0, 117) + "…" : first;
    wrap(el, `ⓘ ${summary}`, false);
    el.closest("details")?.classList.add("hx-hint-subtitle");
  });
}

// For pages that already have their own KPI row (Bill Issuance cases, Bulk
// Checker): puts a gauge strip directly above that row, reusing one
// container per row so re-renders replace rather than stack.
function hxGaugesAbove(kpiRowSelector, gauges) {
  const row = $(kpiRowSelector);
  if (!row) return;
  let strip = row.previousElementSibling;
  if (!strip || !strip.classList.contains("hx-gauge-strip")) {
    strip = document.createElement("div");
    strip.className = "hx-gauges hx-gauge-strip";
    row.parentNode.insertBefore(strip, row);
  }
  const shown = gauges.filter((g) => Number(g.total) > 0);
  strip.hidden = shown.length === 0;
  strip.classList.remove("hx-gauges-1", "hx-gauges-2", "hx-gauges-3", "hx-gauges-4");
  strip.classList.add(`hx-gauges-${Math.min(Math.max(shown.length, 1), 4)}`);
  strip.innerHTML = hxGaugesHtml(shown);
}

function hxRenderDashboard(containerId, { gauges = [], tiles = [], split = null } = {}) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.hidden = false;
  el.classList.toggle("hx-dashboard-nogauges", gauges.length === 0);
  el.innerHTML =
    (gauges.length ? `<div class="hx-gauges hx-gauges-${Math.min(gauges.length, 4)}">${hxGaugesHtml(gauges)}</div>` : "") +
    `<div class="hx-tiles">${hxTilesHtml(tiles)}</div>` +
    (split && split.entries && split.entries.length ? `<div class="hx-split">${hxSplitHtml(split.title, split.entries)}</div>` : "");
}

const HX_READ_STATUS = {
  "1000STSRED": ["Available", "hx-pill-blue"],
  "5000STSRED": ["Anomalous", "hx-pill-amber"],
  "6000STSRED": ["Sent to bill", "hx-pill-violet"],
  "7000STSRED": ["Billed", "hx-pill-green"],
  "7001STSRED": ["UAU billed", "hx-pill-green"],
  "8000STSRED": ["TNB", "hx-pill-gray"],
};
function hxStatusPill(code) {
  if (!code) return `<span class="hx-pill hx-pill-gray">—</span>`;
  const [label, cls] = HX_READ_STATUS[code] || [code, "hx-pill-gray"];
  return `<span class="hx-pill ${cls}" title="${escapeHtml(code)}">${escapeHtml(label)}</span>`;
}
function hxDate(v) {
  return escapeHtml(String(v ?? "").replace(/ 00:00:00(\.0+)?$/, ""));
}

// (hierRenderKpiRow replaced by hierRenderGauges + the tiles in
// hierRenderDashboard - RJ 2026-09-27 redesign.)

// Small "Overview" dashboard shown above the main card - same "recompute
// from whatever's currently visible" convention as hierRenderKpiRow just
// above, kept as its own function/row (rather than folded into that one)
// since it's meant to stay visible even while scrolled past the filter
// row, and because the type breakdown below it needs its own strip.
function hierRenderDashboard(visibleRows) {
  // READY_USAGE is the analyst's own most-important field on this page
  // (see the matching comment on the server-side response mapping) -
  // its total leads the row, ahead of the structural counts.
  const totalReadyUsage = visibleRows.reduce((sum, r) => sum + (Number(r.ready_usage) || 0), 0);
  const totalSecondaries = visibleRows.reduce((sum, r) => sum + (Number(r.secondary_count) || 0), 0);
  const avgSecondaries = visibleRows.length ? (totalSecondaries / visibleRows.length).toFixed(1) : "0.0";
  const distinctNiss = new Set(visibleRows.map((r) => r.niss).filter(Boolean)).size;
  const periodRows = visibleRows.filter((r) => r.id_billing_period);
  const oldest = periodRows.reduce((m, r) => (!m || Number(r.id_billing_period) < Number(m.id_billing_period) ? r : m), null);
  const tiles = [
    ["★", "Total ready usage", totalReadyUsage.toLocaleString(), "hx-tile-accent"],
    ["🗂️", "Pending primaries", visibleRows.length.toLocaleString(), ""],
    ["🔌", "Distinct NISS", distinctNiss.toLocaleString(), ""],
    ["🔗", "Secondaries", `${totalSecondaries.toLocaleString()} <small>avg ${avgSecondaries}</small>`, ""],
    ["📅", "Oldest period", oldest ? escapeHtml(oldest.billing_period_desc || oldest.id_billing_period) : "—", ""],
  ];
  $("#hier-dashboard-kpi-row").innerHTML = tiles.map(([icon, label, value, cls]) =>
    `<div class="hx-tile ${cls}">
      <div class="hx-tile-icon">${icon}</div>
      <div><div class="hx-tile-value">${value}</div><div class="hx-tile-label">${label}</div></div>
    </div>`).join("");

  // Calc-module split as one stacked bar + legend (was a row of cards).
  // Unlabeled rows grouped as "Unclassified" so the parts add up.
  const typeCounts = new Map();
  visibleRows.forEach((r) => {
    const label = r.calc_module_type || "Unclassified";
    typeCounts.set(label, (typeCounts.get(label) || 0) + 1);
  });
  const entries = [...typeCounts.entries()].sort((a, b) => b[1] - a[1]);
  const total = visibleRows.length || 1;
  const palette = ["#6366f1", "#06b6d4", "#f59e0b", "#10b981", "#ec4899", "#94a3b8"];
  $("#hier-dashboard-type-row").innerHTML = entries.length ? `
    <div class="hx-split-title">Calculation module</div>
    <div class="hx-split-bar">${entries.map(([label, count], i) =>
      `<span style="width:${(count / total) * 100}%;background:${palette[i % palette.length]}" title="${escapeHtml(label)}: ${count}"></span>`).join("")}</div>
    <div class="hx-split-legend">${entries.map(([label, count], i) =>
      `<span><i style="background:${palette[i % palette.length]}"></i>${escapeHtml(label)} <b>${count}</b> <em>${Math.round((count / total) * 100)}%</em></span>`).join("")}</div>`
    : "";
}

function hierRenderTable() {
  const visible = hierVisibleIndices();
  const tbody = $("#hier-table tbody");
  tbody.innerHTML = "";
  renderFilteredCount("#hier-filtered-count", visible.length, hierRows.length);
  visible.forEach((idx) => {
    const r = hierRows[idx];
    const tr = document.createElement("tr");
    // Primary-only (green) takes priority over the orange "Anomalous"
    // flag below - a green row means the WHOLE hierarchy needs no more
    // than this one primary reading fixed, which is more useful signal
    // to lead with than the primary's own read_status. Reuses the same
    // orange row-highlight class Date Anomaly's multi-period rows use
    // for the non-green case - a different condition (5000STSRED reads)
    // but the same "needs a second look" visual intent, so no new CSS
    // was needed for that one.
    // Redesign (RJ 2026-09-27): identity + what-to-act-on first (NISS,
    // MP, period, status, billed?, ready usage), structural detail after.
    // Primary-only rows get a green left accent instead of a full-row
    // fill; anomalous an amber accent. Whole row opens the detail pop-out.
    const isPrimaryOnly = Number(r.secondaries_not_sent_count ?? NaN) === 0;
    tr.className = "hx-row" + (isPrimaryOnly ? " hx-row-primaryonly" : (r.read_status === "5000STSRED" ? " hx-row-anomalous" : ""));
    tr.dataset.idx = String(idx);
    const notSent = Number(r.secondaries_not_sent_count ?? 0);
    tr.innerHTML = (
      `<td><span class="hx-niss">${escapeHtml(r.niss ?? "")}</span></td>` +
      `<td class="hx-mono">${escapeHtml(r.id_measuring_point ?? "")}${r.id_main_mp ? `<div class="hx-sub">main ${escapeHtml(r.id_main_mp)}</div>` : ""}</td>` +
      `<td title="${escapeHtml(r.id_billing_period ?? "")}">${escapeHtml(r.billing_period_desc || r.id_billing_period || "")}</td>` +
      `<td>${hxStatusPill(r.read_status)}</td>` +
      `<td>${String(r.period_billed) === "1" ? `<span class="hx-pill hx-pill-billed">Billed</span>` : `<span class="hx-pill hx-pill-ghost">Open</span>`}</td>` +
      `<td class="hx-num"><strong>${escapeHtml(r.ready_usage ?? "")}</strong></td>` +
      `<td class="hx-num">${escapeHtml(r.secondary_count ?? "0")}</td>` +
      `<td class="hx-num">${notSent === 0 ? `<span class="hx-pill hx-pill-green">0</span>` : escapeHtml(String(notSent))}</td>` +
      `<td>${hxDate(r.reading_date)}</td>` +
      `<td title="${escapeHtml(r.reading_type ?? "")}">${escapeHtml(r.reading_type_desc || r.reading_type || "")}</td>` +
      `<td class="hx-clip" title="${escapeHtml(r.calc_module_type ?? "")}">${escapeHtml(r.calc_module_type ?? "")}</td>` +
      `<td title="${escapeHtml(r.mp_status ?? "")}">${escapeHtml(r.mp_status_desc || r.mp_status || "")}</td>` +
      `<td class="hx-mono">${escapeHtml(r.id_reading ?? "")}</td>` +
      `<td><button type="button" class="hx-icon-btn hier-drill-btn" data-idx="${idx}" title="Open hierarchy detail">🔗</button></td>`
    );
    tbody.appendChild(tr);
  });
  const visibleRows = visible.map((idx) => hierRows[idx]);
  hierRenderGauges(visibleRows);
  hierRenderDashboard(visibleRows);
  hierUpdateFilterBadge();
}

hierWireSortableHeaders(
  "#hier-table",
  () => hierSortKey, (k) => { hierSortKey = k; },
  () => hierSortDir, (d) => { hierSortDir = d; },
  hierRenderTable,
);
hierWireSortableHeaders(
  "#hier-detail-table",
  () => hierDetailSortKey, (k) => { hierDetailSortKey = k; },
  () => hierDetailSortDir, (d) => { hierDetailSortDir = d; },
  hierRenderDetailTable,
);

$("#hier-detect-btn").addEventListener("click", async () => {
  const btn = $("#hier-detect-btn");
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Scanning…";
  try {
    const data = await api("/api/hierarchy-analysis/detect", { method: "POST" });
    hierRows = data.rows;
    hierFilters = { search: "", status: "", type: "", mpStatus: "", billingPeriod: "", primaryOnly: false, periodBilled: "" };
    $("#hier-filter-search").value = "";
    hierPopulateFilterOptions();
    hierRenderTable();
    $("#hier-summary").textContent = data.possibly_truncated
      ? `${hierRows.length}+ pending primaries found (capped at ${data.limit} - list may be incomplete).`
      : `${hierRows.length} pending primary/primaries found.`;
    $("#hier-detail-card").hidden = true;
  } catch (err) {
    showToast(err.message || "Detect failed.", true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

$("#hier-filter-search").addEventListener("input", () => {
  hierFilters = { ...hierFilters, search: $("#hier-filter-search").value };
  hierRenderTable();
});
["#hier-filter-status", "#hier-filter-type", "#hier-filter-mpstatus", "#hier-filter-billingperiod"].forEach((sel) => {
  $(sel).addEventListener("change", () => {
    hierFilters = {
      ...hierFilters,
      status: $("#hier-filter-status").value,
      type: $("#hier-filter-type").value,
      mpStatus: $("#hier-filter-mpstatus").value,
      billingPeriod: $("#hier-filter-billingperiod").value,
    };
    hierRenderTable();
  });
});
$("#hier-filter-primaryonly").addEventListener("change", () => {
  hierFilters = { ...hierFilters, primaryOnly: $("#hier-filter-primaryonly").checked };
  hierRenderTable();
});
$("#hier-filter-periodbilled").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-value]");
  if (!b) return;
  hierFilters = { ...hierFilters, periodBilled: b.dataset.value };
  hierSyncPeriodBilledSeg();
  hierRenderTable();
});

$("#hier-filter-clear-btn").addEventListener("click", (ev) => {
  // Lives inside the Filters <summary> - don't let the click also
  // collapse/expand the panel.
  ev.preventDefault();
  ev.stopPropagation();
  hierFilters = { search: "", status: "", type: "", mpStatus: "", billingPeriod: "", primaryOnly: false, periodBilled: "" };
  hierSyncPeriodBilledSeg();
  $("#hier-filter-search").value = "";
  $("#hier-filter-status").value = "";
  $("#hier-filter-type").value = "";
  $("#hier-filter-mpstatus").value = "";
  $("#hier-filter-billingperiod").value = "";
  $("#hier-filter-primaryonly").checked = false;
  hierRenderTable();
});

// Renders #hier-detail-table from hierDetailRows/hierDetailNotBilledOnly.
// Default order: not-billed members sort to the top (stable sort keeps
// the query's own order - primary first, then children - within each of
// the two groups); toggling "Not billed only" narrows the same in-memory
// set rather than re-fetching. Clicking a column header (hierDetailSort
// Key) REPLACES this default with a plain sort on that column instead -
// picking a column is an explicit request for that order, so the not-
// billed-to-top grouping steps aside rather than fighting it. Either way
// the .row-not-billed highlight itself is unconditional - only the ORDER
// changes.
function hierRenderDetailTable() {
  const rows = hierDetailNotBilledOnly
    ? hierDetailRows.filter((r) => !hierIsBilled(r.read_status))
    : hierDetailRows;
  let sorted;
  if (hierDetailSortKey) {
    sorted = rows
      .map((r, i) => [r, i])
      .sort(([a], [b]) => _hierCompareValues(a[hierDetailSortKey], b[hierDetailSortKey], hierDetailSortDir))
      .map(([r]) => r);
  } else {
    sorted = rows
      .map((r, i) => [r, i])
      .sort(([a, ai], [b, bi]) => {
        const aNotBilled = !hierIsBilled(a.read_status);
        const bNotBilled = !hierIsBilled(b.read_status);
        if (aNotBilled !== bNotBilled) return aNotBilled ? -1 : 1;
        return ai - bi; // stable within each group
      })
      .map(([r]) => r);
  }

  const tbody = $("#hier-detail-table tbody");
  tbody.innerHTML = "";
  sorted.forEach((r) => {
    const tr = document.createElement("tr");
    tr.className = hierIsBilled(r.read_status) ? "" : "row-not-billed";
    // "Primary" follows the corrected definition (MP_TYPE IN Principal /
    // Principal acoplado), not the IND_DIST_PPAL flag this drill-down
    // query still happens to return - see hierarchy_analysis.py.
    const isPrimary = ["TIPEQM0003", "TIPEQM0005"].includes(r.mp_type);
    tr.innerHTML = (
      `<td><span class="hx-niss">${escapeHtml(r.niss ?? "")}</span></td>` +
      `<td class="hx-mono">${escapeHtml(r.id_measuring_point ?? "")}</td>` +
      `<td>${isPrimary ? `<span class="hx-pill hx-pill-violet">Primary</span>` : `<span class="hx-pill hx-pill-ghost">Secondary</span>`}</td>` +
      `<td class="hx-num">${escapeHtml(r.perc_dist ?? "")}</td>` +
      `<td>${hxStatusPill(r.read_status)}</td>` +
      `<td class="hx-num"><strong>${escapeHtml(r.ready_usage ?? "")}</strong></td>` +
      `<td class="hx-num">${escapeHtml(r.value ?? "")}</td>` +
      `<td>${hxDate(r.reading_date)}</td>` +
      `<td title="${escapeHtml(r.reading_type ?? "")}">${escapeHtml(r.reading_type_desc || r.reading_type || "")}</td>` +
      `<td title="${escapeHtml(r.id_billing_period ?? "")}">${escapeHtml(r.billing_period_desc || r.id_billing_period || "")}</td>` +
      `<td>${escapeHtml(r.status ?? "")}</td>` +
      `<td class="hx-mono">${escapeHtml(r.id_reading ?? "")}</td>` +
      // Opens the reading-history popup (task #143) for this row's own
      // supply, highlighting the billing period this row itself is at -
      // the analyst's own ask ("highlight the reading in the pop up
      // window when we click it from the details"). No button when the
      // row has no ID_SECTOR_SUPPLY to look up (shouldn't normally
      // happen - every hierarchy member has one - but guards against a
      // blank LEFT JOIN member row).
      (r.id_sector_supply
        ? `<td><button type="button" class="btn btn-pill-sm hier-detail-reading-btn" ` +
          `data-sector-supply="${escapeHtml(r.id_sector_supply)}" ` +
          `data-billing-period="${escapeHtml(r.id_billing_period ?? "")}">📖 Readings</button></td>`
        : `<td></td>`)
    );
    tbody.appendChild(tr);
  });
}

// ---------------- Reading-history popup (task #143) ----------------
function hierRenderReadingModal(rows, checkingBillingPeriod) {
  const tbody = $("#hier-reading-modal-table tbody");
  tbody.innerHTML = "";
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    if (checkingBillingPeriod && String(r.billing_period) === String(checkingBillingPeriod)) {
      tr.className = "row-checking-period";
    }
    tr.innerHTML = (
      `<td>${escapeHtml(r.billing_period ?? "")}</td>` +
      `<td>${escapeHtml(r.id_reading ?? "")}</td>` +
      `<td>${escapeHtml(r.reading_type ?? "")}</td>` +
      `<td>${escapeHtml(r.usage_type ?? "")}</td>` +
      `<td>${escapeHtml(r.read_status ?? "")}</td>` +
      `<td>${hxDate(r.prev_date)}</td>` +
      `<td>${hxDate(r.reading_date)}</td>` +
      `<td>${escapeHtml(r.prev_value ?? "")}</td>` +
      `<td>${escapeHtml(r.value ?? "")}</td>` +
      `<td>${escapeHtml(r.reading_usage ?? "")}</td>` +
      `<td><strong>${escapeHtml(r.ready_usage ?? "")}</strong></td>` +
      `<td>${escapeHtml(r.ind_estimate ?? "")}</td>`
    );
    tbody.appendChild(tr);
  });
}

$("#hier-detail-table tbody").addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".hier-detail-reading-btn");
  if (!btn) return;
  const sectorSupply = btn.dataset.sectorSupply;
  const billingPeriod = btn.dataset.billingPeriod;
  if (!sectorSupply) return;
  btn.disabled = true;
  try {
    const data = await api("/api/hierarchy-analysis/reading-history", {
      method: "POST",
      body: { id_sector_supply: sectorSupply },
    });
    hierRenderReadingModal(data.rows, billingPeriod);
    $("#hier-reading-modal-title").textContent =
      `Supply ${sectorSupply} · ${data.rows.length} reading(s)` +
      (billingPeriod ? ` · checking period ${billingPeriod}` : "");
    hxOpenModal("hier-reading-modal-overlay");
  } catch (err) {
    showToast(err.message || "Could not load reading history.", true);
  } finally {
    btn.disabled = false;
  }
});

$("#hier-reading-modal-close-btn").addEventListener("click", () => hxCloseModal("hier-reading-modal-overlay"));
$("#hier-reading-modal-overlay").addEventListener("click", (ev) => {
  if (ev.target.id === "hier-reading-modal-overlay") hxCloseModal("hier-reading-modal-overlay");
});

$("#hier-table tbody").addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".hier-drill-btn");
  if (!btn) return;
  const row = hierRows[Number(btn.dataset.idx)];
  if (!row) return;
  btn.disabled = true;
  try {
    const data = await api("/api/hierarchy-analysis/detail", {
      method: "POST",
      // Scope every child's reading to the SAME billing period the
      // primary row was pending in, not its full reading history.
      body: { id_measuring_point: row.id_measuring_point, id_billing_period: row.id_billing_period },
    });
    hierDetailRows = data.rows;
    hierDetailNotBilledOnly = false;
    $("#hier-detail-filter-notbilled").checked = false;
    hierRenderDetailTable();
    const notBilledCount = hierDetailRows.filter((r) => !hierIsBilled(r.read_status)).length;
    const totalReady = hierDetailRows.reduce((s, r) => s + (Number(r.ready_usage) || 0), 0);
    $("#hier-detail-title").textContent =
      `${row.niss || ""} · measuring point ${row.id_measuring_point}` +
      (row.id_billing_period ? ` · ${row.billing_period_desc || row.id_billing_period}` : "");
    const stat = (label, value, cls = "") => `<div class="hx-mstat ${cls}"><b>${value}</b><span>${label}</span></div>`;
    $("#hier-detail-stats").innerHTML =
      stat("Members", hierDetailRows.length) +
      stat("Not billed", notBilledCount, notBilledCount ? "hx-mstat-warn" : "hx-mstat-ok") +
      stat("Ready usage", totalReady.toLocaleString()) +
      stat("Period", String(row.period_billed) === "1" ? "Billed" : "Open", String(row.period_billed) === "1" ? "hx-mstat-warn" : "");
    hxOpenModal("hier-detail-card");
  } catch (err) {
    showToast(err.message || "Could not load hierarchy detail.", true);
  } finally {
    btn.disabled = false;
  }
});

$("#hier-detail-filter-notbilled").addEventListener("change", () => {
  hierDetailNotBilledOnly = $("#hier-detail-filter-notbilled").checked;
  hierRenderDetailTable();
});

$("#hier-detail-close-btn").addEventListener("click", () => hxCloseModal("hier-detail-card"));
$("#hier-detail-card").addEventListener("click", (ev) => {
  if (ev.target.id === "hier-detail-card") hxCloseModal("hier-detail-card");
});

// Whole-row click opens the detail pop-out (the 🔗 button still works via
// the handler above; this covers clicks anywhere else on the row).
$("#hier-table tbody").addEventListener("click", (ev) => {
  if (ev.target.closest(".hier-drill-btn")) return;
  const tr = ev.target.closest("tr.hx-row");
  if (!tr) return;
  tr.querySelector(".hier-drill-btn")?.click();
});

// --- Pop-out helpers: open/close, maximize, Esc (RJ 2026-09-27: "a
// popout when you press it that can be maximized") -------------------
function hxOpenModal(id) {
  $(`#${id}`).hidden = false;
  document.body.classList.add("hx-modal-open");
}
function hxCloseModal(id) {
  $(`#${id}`).hidden = true;
  if (!$$(".hier-reading-modal-overlay").some((o) => !o.hidden)) document.body.classList.remove("hx-modal-open");
}
document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-maximize]");
  if (!btn) return;
  const box = document.getElementById(btn.dataset.maximize);
  if (!box) return;
  const max = box.classList.toggle("is-max");
  btn.textContent = max ? "⤡" : "⤢";
});
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  // Close the top-most open pop-out only (readings sits above detail).
  if (!$("#hier-reading-modal-overlay").hidden) { hxCloseModal("hier-reading-modal-overlay"); return; }
  if (!$("#hier-detail-card").hidden) hxCloseModal("hier-detail-card");
});

$("#hier-export-csv-btn").addEventListener("click", () => {
  const exportAll = !!$("#hier-export-all")?.checked;
  const visible = hierVisibleIndices(exportAll);
  if (!visible.length) return;
  const header = ["id_measuring_point", "id_main_mp", "niss", "mp_type", "mp_status", "secondary_count", "secondaries_not_sent_count", "id_calculation_module", "calc_module_type", "id_billing_period", "billing_period_desc", "id_reading", "reading_date", "reading_type", "reading_type_desc", "read_status", "period_billed", "ready_usage", "reading_usage"];
  const lines = [header.join(",")];
  visible.forEach((idx) => {
    const r = { ...hierRows[idx] };
    r.period_billed = String(r.period_billed) === "1" ? "Yes" : "No";
    lines.push(header.map((k) => `"${String(r[k] ?? "").replace(/"/g, '""')}"`).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = exportAll ? "hierarchy_analysis_all.csv" : "hierarchy_analysis.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

$("#hier-export-xlsx-btn").addEventListener("click", async () => {
  const exportAll = !!$("#hier-export-all")?.checked;
  const visible = hierVisibleIndices(exportAll);
  if (!visible.length) return;
  const btn = $("#hier-export-xlsx-btn");
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Exporting…";
  try {
    const rows = visible.map((idx) => {
      const r = hierRows[idx];
      return {
        id_measuring_point: String(r.id_measuring_point ?? ""),
        id_main_mp: String(r.id_main_mp ?? ""),
        niss: String(r.niss ?? ""),
        mp_type: String(r.mp_type ?? ""),
        mp_status: String(r.mp_status ?? ""),
        secondary_count: String(r.secondary_count ?? ""),
        secondaries_not_sent_count: String(r.secondaries_not_sent_count ?? ""),
        id_calculation_module: String(r.id_calculation_module ?? ""),
        calc_module_type: String(r.calc_module_type ?? ""),
        id_billing_period: String(r.id_billing_period ?? ""),
        billing_period_desc: String(r.billing_period_desc ?? ""),
        id_reading: String(r.id_reading ?? ""),
        reading_date: String(r.reading_date ?? ""),
        reading_type: String(r.reading_type ?? ""),
        reading_type_desc: String(r.reading_type_desc ?? ""),
        read_status: String(r.read_status ?? ""),
        ready_usage: String(r.ready_usage ?? ""),
        reading_usage: String(r.reading_usage ?? ""),
      };
    });
    const resp = await fetch("/api/hierarchy-analysis/export-xlsx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Export failed (HTTP ${resp.status})`);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = exportAll ? "hierarchy_analysis_all.xlsx" : "hierarchy_analysis.xlsx";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showToast(err.message || "Excel export failed.", true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

// ---------------- Bill Issuance Validator: in-page sub-nav (Case 1/2/...) ----------------
// Same "lighter-weight tab switch" idiom as DIFF DATES Anomaly's own
// sub-nav (see that comment above), but keyed off data-biss-sub instead
// of data-da-sub and with its OWN listener - deliberately NOT reusing
// the data-da-sub selector/listener, which is scoped by attribute value
// but still queries ALL `.da-subnav-btn`/`.da-subpage` elements in the
// document; if Bill Issuance's buttons carried data-da-sub too, clicking
// one would strip `is-active` from every DA subnav button (none of
// which match this page's sub value), silently breaking DIFF DATES
// Anomaly's own sub-nav state next time that page is opened. Reuses the
// same `.da-subnav`/`.da-subnav-btn`/`.da-subpage` CSS classes for
// identical styling - those rules are generic tab-bar styling, nothing
// DA-specific about them.
$$(".da-subnav-btn[data-biss-sub]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const sub = btn.dataset.bissSub;
    $$(".da-subnav-btn[data-biss-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    $$(".da-subpage[data-biss-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.bissSub === sub));
  });
});

// ---------------- Bill Issuance Validator ----------------
// RJ, 2026-09-15: accounts whose next Electricity/Water bill is stuck
// (BILLING_STATUS ESTFAC0015) behind a Rate (176) bill still being put to
// collection - see app/core/bill_issuance_validator.py's module docstring
// for the full query chain. Stateless, single flat result set.
//
// RJ, 2026-09-14: "enhancing Case 1, I found cases that the next water or
// ele bills can be up to 11 billing period ahead of the rate bills, and
// they are valid... add a column on how much months and then a filter as
// well, i noticed that the excel download is also missing." The query
// itself now scans up to 11 periods ahead by default (see build_stuck_
// bills_query's own docstring - live-confirmed the exact-next-period-only
// version currently finds ZERO accounts, the widened one finds 74) and
// returns `periods_ahead` per row; this adds a client-side min/max filter
// over that already-fetched column (same "filter what's already on
// screen, don't re-query" convention as every other client-side filter in
// this app) plus a real .xlsx export next to the existing CSV one.
//
// RJ, 2026-09-14 (later same day), verbatim: "I wanted the 2 cases merged
// in 1 table, maybe you can do union but there will be clear identifier
// of the case that i can use to filter." Stuck Bill and New Contract
// Match (formerly a second, separate table - see app/core/bill_issuance_
// validator.py's "Case 1: New Contract Match" comment block for that
// pattern's own business rule and the 1102978994 period-uniqueness fix)
// are now merged into ONE `billissRows` array by the server (see
// bill_issuance_detect in web/server.py), each row tagged `pattern` -
// "stuck_bill" or "new_contract". Common columns (reference, notice_
// update_date, id_bill_rate, period_rate) are populated for both
// patterns; pattern-specific columns are blank ("") on rows where they
// don't apply - the table just renders "-" for those cells. The old
// standalone New Contract Match card/table/detect-button and its own
// `billissNc*` state are gone; the standalone API route
// (/api/bill-issuance/case1/new-contract-match/detect) is kept as-is for
// programmatic access to just that one pattern (Case 4 "Unclassified"
// still calls the query builder function directly, not this route).
let billissRows = [];
let billissSortKey = null;
let billissSortDir = 1;

// ---- Bill Issuance Validator: click a KPI card to filter (RJ 2026-10-04) ----
// One active KPI per case. Clicking a filterable card toggles it; clicking
// the "total" card clears it. Cards with no row-level meaning (averages,
// excluded counts) are not clickable.
const bissKpi = { billiss: null, biss2: null, biss3: null, biss4: null };
const BISS_KPI_PRED = {
  billiss: {
    stuck: (r) => r.pattern === "stuck_bill",
    newcontract: (r) => r.pattern === "new_contract",
    electricity: (r) => r.pattern === "stuck_bill" && String(r.offered_service_next) === "1",
    water: (r) => r.pattern === "stuck_bill" && String(r.offered_service_next) === "19",
  },
  biss2: {
    needsaction: (a) => !a.complete,
    missingbill: (a) => a.missing_bill_count > 0,
    complete: (a) => a.complete,
    services: (a) => a.needs_update_count > 0,
  },
  biss3: {
    active: (r) => r.with_active_contract === "YES",
    inactive: (r) => r.with_active_contract === "NO",
  },
  biss4: {},
};
const BISS_KPI_CLEAR = { billiss: ["rows"], biss2: ["accounts"], biss3: ["rows", "accounts"], biss4: [] };

function bissKpiApply(key, rows, indices) {
  const pred = bissKpi[key] && BISS_KPI_PRED[key][bissKpi[key]];
  return pred ? indices.filter((i) => pred(rows[i])) : indices;
}

function bissKpiCardsHtml(key, cards) {
  return cards.map(([kpi, icon, label, value]) => {
    const filt = !!BISS_KPI_PRED[key][kpi];
    const clear = BISS_KPI_CLEAR[key].includes(kpi);
    const active = filt ? bissKpi[key] === kpi : (clear && !bissKpi[key]);
    const tip = filt ? (active ? "Click to clear this filter" : "Click to show only these") : clear ? "Click to show all" : "";
    return `<div class="kpi-card${filt || clear ? " kpi-card-clickable" : ""}${active ? " is-active" : ""}" data-kpi="${kpi}"${tip ? ` title="${tip}"` : ""}>
      <div class="kpi-value">${value}</div>
      <div class="kpi-label"><span class="kpi-card-icon">${icon}</span>${label}</div>
    </div>`;
  }).join("");
}

function bissKpiWire(key, rowSel, rerender, beforeApply) {
  $(rowSel)?.addEventListener("click", (e) => {
    const card = e.target.closest(".kpi-card[data-kpi]");
    if (!card) return;
    const kpi = card.dataset.kpi;
    if (BISS_KPI_PRED[key][kpi]) bissKpi[key] = bissKpi[key] === kpi ? null : kpi;
    else if (BISS_KPI_CLEAR[key].includes(kpi)) bissKpi[key] = null;
    else return;
    if (beforeApply) beforeApply(bissKpi[key]);
    rerender();
  });
}

function billissVisibleIndices(ignoreFilters = false) {
  let indices = billissRows.map((_, i) => i);
  if (!ignoreFilters) {
    const min = Number($("#billiss-ahead-min")?.value) || 1;
    const max = Number($("#billiss-ahead-max")?.value) || 11;
    // Months-ahead filter only makes sense for Stuck Bill rows -
    // New Contract Match rows have no periods_ahead value and always pass.
    indices = indices.filter((i) => {
      const r = billissRows[i];
      if (r.pattern !== "stuck_bill") return true;
      const n = Number(r.periods_ahead);
      return Number.isFinite(n) && n >= min && n <= max;
    });
    const pattern = $("#billiss-pattern-filter")?.value || "";
    if (pattern) indices = indices.filter((i) => billissRows[i].pattern === pattern);
    const term = ($("#billiss-search")?.value || "").trim().toLowerCase();
    if (term) indices = indices.filter((i) => String(billissRows[i].reference ?? "").toLowerCase().includes(term));
    indices = bissKpiApply("billiss", billissRows, indices);
  }
  if (billissSortKey) {
    indices.sort((a, b) => _hierCompareValues(billissRows[a][billissSortKey], billissRows[b][billissSortKey], billissSortDir));
  }
  return indices;
}

function billissRenderKpiRow() {
  // No duplicates by design (RJ, 2026-09-15: "i dont want duplicates") -
  // one row per account, Electricity preferred over Water - so among
  // Stuck Bill rows, "rows" and "distinct accounts" are always the same
  // number; only the Electricity/Water split is worth its own card.
  const stuckRows = billissRows.filter((r) => r.pattern === "stuck_bill");
  const ncRows = billissRows.filter((r) => r.pattern === "new_contract");
  const electricityCount = stuckRows.filter((r) => String(r.offered_service_next) === "1").length;
  const waterCount = stuckRows.filter((r) => String(r.offered_service_next) === "19").length;
  const aheadValues = stuckRows.map((r) => Number(r.periods_ahead)).filter((n) => Number.isFinite(n));
  const avgAhead = aheadValues.length ? (aheadValues.reduce((a, b) => a + b, 0) / aheadValues.length).toFixed(1) : "—";
  const cards = [
    ["rows", "🧾", "Total rows", billissRows.length],
    ["stuck", "⛔", "Stuck Bill", stuckRows.length],
    ["newcontract", "🆕", "New Contract Match", ncRows.length],
    ["electricity", "⚡", "Electricity blocked", electricityCount],
    ["water", "💧", "Water blocked (Electricity OK)", waterCount],
    ["avgahead", "📅", "Avg. months ahead (Stuck Bill)", avgAhead],
  ];
  hxGaugesAbove("#billiss-kpi-row", [
    { label: "Stuck Bill", count: stuckRows.length, total: billissRows.length, c1: "#ef4444", c2: "#f97316" },
    { label: "New Contract Match", count: ncRows.length, total: billissRows.length, c1: "#6366f1", c2: "#06b6d4" },
    { label: "Electricity blocked (of stuck)", count: electricityCount, total: stuckRows.length, c1: "#f59e0b", c2: "#eab308" },
  ]);
  $("#billiss-kpi-row").innerHTML = bissKpiCardsHtml("billiss", cards);
}
bissKpiWire("billiss", "#billiss-kpi-row", () => billissRenderTable());

const _billissPatternLabel = { stuck_bill: "Stuck Bill", new_contract: "New Contract Match" };

// RJ 2026-10-04: Case 1 row selection - only checked bills go into the
// release script. Keyed by ID_BILL_RATE so it survives sort/filter.
let billissSelected = new Set();
const _billissKey = (r) => String(r.id_bill_rate ?? "");

function billissUpdateSelectionUI(visible) {
  visible = visible || billissVisibleIndices();
  const all = $("#billiss-select-all");
  const nVis = visible.filter((i) => billissSelected.has(_billissKey(billissRows[i]))).length;
  if (all) {
    all.checked = visible.length > 0 && nVis === visible.length;
    all.indeterminate = nVis > 0 && nVis < visible.length;
  }
  const n = billissSelected.size;
  $("#billiss-selection-hint").textContent = n
    ? `${n} bill(s) selected for the release script.`
    : "☝️ Check one or more rows in the table above to enable Generate.";
  $("#billiss-release-generate-btn").disabled = n === 0 || state.role === "viewer";
}

function billissRenderTable() {
  const visible = billissVisibleIndices();
  const tbody = $("#billiss-table tbody");
  tbody.innerHTML = "";
  renderFilteredCount("#billiss-filtered-count", visible.length, billissRows.length);
  visible.forEach((idx) => {
    const r = billissRows[idx];
    const tr = document.createElement("tr");
    const key = _billissKey(r);
    tr.innerHTML = (
      `<td><input type="checkbox" class="billiss-row-cb" data-key="${escapeHtml(key)}"${billissSelected.has(key) ? " checked" : ""} /></td>` +
      `<td>${escapeHtml(r.reference ?? "")}</td>` +
      `<td>${escapeHtml(_billissPatternLabel[r.pattern] ?? r.pattern ?? "")}</td>` +
      `<td>${escapeHtml(r.notice_update_date ?? "")}</td>` +
      `<td>${escapeHtml(r.id_bill_rate ?? "")}</td>` +
      `<td>${escapeHtml(r.period_rate ?? "")}</td>` +
      `<td>${escapeHtml(r.id_bill_next ?? "") || "-"}</td>` +
      `<td title="ID_OFFERED_SERVICE ${escapeHtml(r.offered_service_next ?? "")}">${escapeHtml(r.offered_service_next_desc ?? r.offered_service_next ?? "") || "-"}</td>` +
      `<td>${escapeHtml(r.period_next ?? "") || "-"}</td>` +
      `<td>${escapeHtml(r.periods_ahead ?? "") || "-"}</td>` +
      `<td title="${escapeHtml(r.status_next ?? "")}">${escapeHtml(r.status_next_desc ?? r.status_next ?? "") || "-"}</td>` +
      `<td>${escapeHtml(r.billing_status_desc ?? "") || "-"}</td>` +
      `<td>${escapeHtml(r.last_billing_date ?? "") || "-"}</td>` +
      `<td>${escapeHtml(r.contract_from_date ?? "") || "-"}</td>` +
      `<td>${escapeHtml(r.contract_status ?? "") || "-"}</td>`
    );
    if (billissSelected.has(key)) tr.classList.add("is-selected");
    tbody.appendChild(tr);
  });
  billissUpdateSelectionUI(visible);
  billissRenderKpiRow();
  $("#billiss-export-csv-btn").hidden = billissRows.length === 0;
  $("#billiss-export-xlsx-btn").hidden = billissRows.length === 0;
  $("#billiss-export-all-wrap").hidden = billissRows.length === 0;
}

hierWireSortableHeaders(
  "#billiss-table",
  () => billissSortKey, (k) => { billissSortKey = k; },
  () => billissSortDir, (d) => { billissSortDir = d; },
  billissRenderTable,
);

$("#billiss-table tbody").addEventListener("change", (e) => {
  const cb = e.target.closest(".billiss-row-cb");
  if (!cb) return;
  if (cb.checked) billissSelected.add(cb.dataset.key); else billissSelected.delete(cb.dataset.key);
  cb.closest("tr").classList.toggle("is-selected", cb.checked);
  billissUpdateSelectionUI();
});
$("#billiss-select-all").addEventListener("change", (e) => {
  const visible = billissVisibleIndices();
  visible.forEach((i) => {
    const k = _billissKey(billissRows[i]);
    if (e.target.checked) billissSelected.add(k); else billissSelected.delete(k);
  });
  billissRenderTable();
});
$("#billiss-ahead-min").addEventListener("input", () => billissRenderTable());
$("#billiss-ahead-max").addEventListener("input", () => billissRenderTable());
$("#billiss-search").addEventListener("input", () => billissRenderTable());
$("#billiss-pattern-filter").addEventListener("change", () => billissRenderTable());

$("#billiss-detect-btn").addEventListener("click", async () => {
  const btn = $("#billiss-detect-btn");
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Scanning…";
  try {
    const data = await api("/api/bill-issuance/detect", { method: "POST" });
    billissRows = data.rows;
    billissSelected = new Set();
    billissSortKey = null;
    billissSortDir = 1;
    $("#billiss-ahead-min").value = "1";
    $("#billiss-ahead-max").value = String(data.max_periods_ahead || 11);
    $("#billiss-search").value = "";
    $("#billiss-pattern-filter").value = "";
    document.querySelectorAll("#billiss-table thead th[data-sort]").forEach((h) => {
      h.querySelector(".stats-table-sort-arrow")?.remove();
    });
    billissRenderTable();
    $("#billiss-summary").textContent = data.possibly_truncated
      ? `${billissRows.length} row(s) found (${data.stuck_bill_count} Stuck Bill, ${data.new_contract_match_count} New Contract Match - one or both may be capped at their own limit, list may be incomplete).`
      : `${billissRows.length} row(s) found: ${data.stuck_bill_count} Stuck Bill (up to ${data.max_periods_ahead} billing period(s) ahead), ${data.new_contract_match_count} New Contract Match.`;
  } catch (err) {
    showToast(err.message || "Scan failed.", true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

const _billissCsvHeader = ["id_payment_form", "reference", "pattern", "notice_update_date", "id_bill_rate", "period_rate", "id_bill_next", "offered_service_next", "offered_service_next_desc", "period_next", "periods_ahead", "status_next", "status_next_desc", "billing_status_desc", "last_billing_date", "contract_from_date", "contract_status"];

$("#billiss-export-csv-btn").addEventListener("click", () => {
  const exportAll = !!$("#billiss-export-all")?.checked;
  const visible = billissVisibleIndices(exportAll);
  if (!visible.length) return;
  const lines = [_billissCsvHeader.join(",")];
  visible.forEach((idx) => {
    const r = billissRows[idx];
    lines.push(_billissCsvHeader.map((k) => `"${String(r[k] ?? "").replace(/"/g, '""')}"`).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = exportAll ? "bill_issuance_validator_all.csv" : "bill_issuance_validator.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

$("#billiss-export-xlsx-btn").addEventListener("click", async () => {
  const exportAll = !!$("#billiss-export-all")?.checked;
  const visible = billissVisibleIndices(exportAll);
  if (!visible.length) return;
  const btn = $("#billiss-export-xlsx-btn");
  btn.disabled = true;
  try {
    const rows = visible.map((idx) => {
      const r = billissRows[idx];
      return {
        reference: r.reference ?? "", pattern: r.pattern ?? "", notice_update_date: r.notice_update_date ?? "",
        id_bill_rate: r.id_bill_rate ?? "", period_rate: r.period_rate ?? "",
        id_bill_next: r.id_bill_next ?? "", offered_service_next_desc: r.offered_service_next_desc ?? "",
        period_next: r.period_next ?? "", periods_ahead: r.periods_ahead ?? "",
        status_next_desc: r.status_next_desc ?? "",
        billing_status_desc: r.billing_status_desc ?? "", last_billing_date: r.last_billing_date ?? "",
        contract_from_date: r.contract_from_date ?? "", contract_status: r.contract_status ?? "",
      };
    });
    const res = await fetch("/api/bill-issuance/export-xlsx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Export failed.");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = exportAll ? "bill_issuance_case1_all.xlsx" : "bill_issuance_case1.xlsx";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showToast(err.message || "Excel export failed.", true);
  } finally {
    btn.disabled = false;
  }
});

// Case 1 "Generate Release Script" - RJ, 2026-09-14: "for case 1, create
// script for all detected, 'Generate Release script'" with RJ's own exact
// UPDATE GCCOM_NOTICE_TMP template. Always acts on every currently
// detected Rate bill (the server re-runs the Stuck Bills scan fresh right
// before generating - same "re-verify at generate time" pattern as Case
// 2's own Generate button) - no row-selection UI here, unlike Case 2/4,
// since RJ's own request was "for all detected", not a per-row pick.
$("#billiss-release-generate-btn").addEventListener("click", async () => {
  const program = $("#billiss-release-program").value.trim();
  const audit_user = $("#billiss-release-user").value.trim();
  const clean = $("#billiss-release-clean-toggle").checked;
  const ids = [...billissSelected].filter(Boolean);
  if (!ids.length) { showToast("Check one or more rows first (or use the select-all box).", true); return; }
  const btn = $("#billiss-release-generate-btn");
  btn.disabled = true;
  try {
    const result = await api("/api/bill-issuance/generate-release", {
      method: "POST",
      body: { program, audit_user, clean, id_bill_rates: ids, max_periods_ahead: Number($("#billiss-ahead-max")?.value) || 11 },
    });
    $("#billiss-release-output").textContent = result.sql_text;
    let msg = `Release script generated: ${result.bill_count} of ${ids.length} selected bill(s).`;
    if (result.warnings.length) msg += `  ${result.warnings.length} warning(s) - see comments at the top of the script.`;
    showToast(msg);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    billissUpdateSelectionUI();
  }
});

$("#billiss-release-copy-btn").addEventListener("click", async () => {
  const text = $("#billiss-release-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Release script copied to clipboard.");
  } catch (_) {
    showToast("Couldn't copy - select and copy manually.", true);
  }
});

$("#billiss-release-download-btn").addEventListener("click", () => {
  const text = $("#billiss-release-output").textContent;
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "bill_issuance_case1_release.sql";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// Case 1 "New Contract Match" - RJ, 2026-09-14 (later same day), verbatim:
// "incorporate in case 1, the existing case 1 is ok, now i only want to
// add the case that its is only rate which is in pending validation
// notice_tmp and invoicing gccom_bill, the contract start (from_date) of
// gccom_contracted service is same as last_billing_date of gccom_bill" -
// see app/core/bill_issuance_validator.py's own "Case 1: New Contract
// Match" comment block for RJ's exact starting SQL and the uniqueness-
// filter fix he explicitly asked for. Originally its own second, flat
// table within the Case 1 tab; RJ, 2026-09-14 (later still, same day)
// then asked to merge it with Stuck Bill into one table with a filterable
// case identifier (see the "billissRows" comment block above) - the
// billissNc* UI state/table/detect-button that used to live here is gone,
// its rows now render as `pattern: "new_contract"` inside #billiss-table.
// The standalone API route (/api/bill-issuance/case1/new-contract-match/
// detect) is unchanged and still callable directly.

// ---------------- Bill Issuance Validator: Case 2 (terminated account, --
// billing-period mismatch) ----------------
// RJ, 2026-09-15, redesigned 2026-09-16/17 (RJ's own words: "i want to
// see clearly the grouped account and which one has an issue... the idea
// is i only want to see the accounts, maybe its a drill down to show the
// bills"). See app/core/bill_issuance_validator.py's Case 2 comment
// block for the full business-rule narrative, RJ's own 342702 example,
// and the 2026-09-17 performance fix (index seeks defeated by N'...'
// string literals - see app/core/sql_format.py). One row per ACCOUNT
// (not per bill/service) with an expandable drill-down for per-service
// detail - reuses the same sort-header convention as Case 1/Hierarchy
// Analysis, plus a checkbox column (same idiom as Detect All's
// da-cleanup-select-all/daCleanupSelected) so the analyst can scope the
// generated script to specific accounts, or leave nothing checked to
// generate for every flagged account (see biss2-generate-btn).
let biss2Accounts = [];
let biss2SortKey = null;
let biss2SortDir = 1;
let biss2Selected = new Set(); // selected row indices - into biss2Accounts
const bissRelScan = {}; // last scan scope per case - reused by the complete-account release script
let biss2Expanded = new Set(); // expanded row indices - into biss2Accounts

// RJ, 2026-09-17: "add the filters and sort" - sort (column headers) was
// already wired below; this is the missing filter half. Plain substring
// match against the account REFERENCE, same "search box narrows the
// visible rows, doesn't re-query" convention as Detect All's own search
// box (see daCleanupSearchTerm elsewhere in this file).
function biss2VisibleIndices(ignoreFilters = false) {
  // RJ, 2026-09-17: "I need to have a filter to see the complete one, or
  // to see only the ones with missing" - replaced the old single "Show
  // Complete accounts too" checkbox with a proper 3-way status filter.
  // RJ, 2026-09-14: "you did not add the filter to see the ones that need
  // action where the bill is missing" - "needs action" was conflating two
  // different reasons (no bill found at all vs. a bill that just needs
  // its period moved - see _case2_group_rows_by_account's own docstring
  // server-side), so this now has dedicated missing-bill/period-mismatch
  // options alongside the original "either reason" one.
  // RJ, 2026-09-14 (later same day): "add additional filter on case 2,
  // check box to say with missing bill or not" - added as a standalone
  // checkbox that ANDs on top of whatever the status dropdown already
  // shows (e.g. "All" + checked narrows to every account, complete or
  // not, that has ever had a missing bill), rather than folding it into
  // the dropdown's own mutually-exclusive options.
  // `ignoreFilters=true` is used by the Export All button (see
  // biss2AllIndices) to get every row in current sort order, skipping
  // the status/search/missing-bill filters entirely.
  let indices = biss2Accounts.map((_, i) => i);
  if (!ignoreFilters) {
    // RJ 2026-09-30 sub-tabs: the active tab is the primary filter; the
    // Status dropdown only applies on the "All" tab.
    const tabFn = BISS2_TABS[biss2Tab]?.match;
    if (tabFn) indices = indices.filter((i) => tabFn(biss2Accounts[i]));
    const statusFilter = biss2Tab === "all" ? ($("#biss2-status-filter")?.value || "needs-action") : "all";
    const term = ($("#biss2-search")?.value || "").trim().toLowerCase();
    const missingBillOnly = !!$("#biss2-missing-bill-only")?.checked;
    if (statusFilter === "needs-action") indices = indices.filter((i) => !biss2Accounts[i].complete);
    else if (statusFilter === "missing-bill") indices = indices.filter((i) => biss2Accounts[i].missing_bill_count > 0);
    else if (statusFilter === "period-mismatch") indices = indices.filter((i) => biss2Accounts[i].period_mismatch_count > 0);
    else if (statusFilter === "complete") indices = indices.filter((i) => biss2Accounts[i].complete);
    if (missingBillOnly) indices = indices.filter((i) => biss2Accounts[i].missing_bill_count > 0);
    // RJ 2026-09-30: "a filter on which bill is missing".
    const missingSvc = $("#biss2-missing-service")?.value || "";
    if (missingSvc) indices = indices.filter((i) => (biss2Accounts[i].missing_services || []).includes(missingSvc));
    if (term) indices = indices.filter((i) => String(biss2Accounts[i].reference ?? "").toLowerCase().includes(term));
    indices = bissKpiApply("biss2", biss2Accounts, indices);
  }
  if (biss2SortKey) {
    indices.sort((a, b) => _hierCompareValues(biss2Accounts[a][biss2SortKey], biss2Accounts[b][biss2SortKey], biss2SortDir));
  }
  return indices;
}

// Case 2 sub-tabs (RJ 2026-09-30): "1. Accounts with missing rate bill,
// 2 is accounts needing action period mismatch, 3 accounts already complete".
const BISS2_TABS = {
  "missing-rate": {
    match: (a) => (a.missing_services || []).includes("Rate"),
    mode: "missing_rate",
    hint: "Terminated accounts whose Rate final bill is missing. Generate = anomaly INSERTs (GCCOM_ANOMALOUS + GCCOM_DETECTED_ANOMALY) + COD_PERIODICITY fix where NULL.",
  },
  "period-mismatch": {
    match: (a) => !a.complete && a.period_mismatch_count > 0,
    mode: "period_mismatch",
    hint: "Accounts with a final bill in the wrong billing period (still Invoicing). Generate = GCCOM_BILL + GCCOM_ITEMS_TO_BILL period UPDATEs.",
  },
  "complete": {
    match: (a) => a.complete,
    mode: null,
    hint: "Every service's final bill already aligned (or already invoiced). Generate = release script for the pending notices (5000NOTEMP -> 1000NOTEMP).",
  },
  "all": { match: null, mode: "", hint: "Every scanned account - use the Status dropdown to narrow." },
};
let biss2Tab = "missing-rate";

function biss2UpdateTabs() {
  Object.entries(BISS2_TABS).forEach(([k, t]) => {
    const el = document.querySelector(`#biss2-tabs [data-count="${k}"]`);
    if (el) el.textContent = (t.match ? biss2Accounts.filter(t.match) : biss2Accounts).length;
  });
  $$("#biss2-tabs [data-biss2-tab]").forEach((b) => b.classList.toggle("is-active", b.dataset.biss2Tab === biss2Tab));
  $("#biss2-tab-hint").textContent = BISS2_TABS[biss2Tab].hint;
  $("#biss2-status-wrap").hidden = biss2Tab !== "all";
  const gen = $("#biss2-generate-btn");
  if (gen) gen.disabled = state.role === "viewer" || BISS2_TABS[biss2Tab].mode === null;
  // RJ 2026-10-04: show only the script card that fits the selected tab -
  // Complete = release script; every other tab = update script.
  const isComplete = biss2Tab === "complete";
  const upd = $("#biss2-update-card");
  const rel = document.querySelector('.biss-rel-card[data-biss-rel="case2"]');
  if (upd) upd.hidden = isComplete;
  if (rel) rel.hidden = !isComplete;
}

biss2UpdateTabs(); // initial state (before the first scan)

$("#biss2-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-biss2-tab]");
  if (!b) return;
  biss2Tab = b.dataset.biss2Tab;
  bissKpi.biss2 = null; // picking a tab replaces any KPI filter
  biss2Selected = new Set();
  biss2RenderTable();
});

function biss2RenderKpiRow() {
  const total = biss2Accounts.length;
  const needsAction = biss2Accounts.filter((a) => !a.complete).length;
  const complete = total - needsAction;
  const servicesNeedingUpdate = biss2Accounts.reduce((sum, a) => sum + a.needs_update_count, 0);
  // RJ, 2026-09-14: surface the missing-bill reason on its own card too,
  // not just as a filter option - see biss2VisibleIndices' own comment.
  const missingBillAccounts = biss2Accounts.filter((a) => a.missing_bill_count > 0).length;
  const cards = [
    ["accounts", "🧾", "Terminated accounts with a pending notice", total],
    ["needsaction", "⚠️", "Accounts needing action", needsAction],
    ["missingbill", "❓", "Accounts with a missing bill", missingBillAccounts],
    ["complete", "✅", "Accounts already Complete", complete],
    ["services", "🔧", "Services needing a period update", servicesNeedingUpdate],
  ];
  hxGaugesAbove("#biss2-kpi-row", [
    { label: "Accounts needing action", count: needsAction, total, c1: "#f59e0b", c2: "#f97316" },
    { label: "Accounts already complete", count: complete, total, c1: "#10b981", c2: "#22c55e" },
    { label: "Accounts with a missing bill", count: missingBillAccounts, total, c1: "#ef4444", c2: "#ec4899" },
  ]);
  $("#biss2-kpi-row").innerHTML = bissKpiCardsHtml("biss2", cards);
}
// KPIs count across every account, so a KPI filter switches to the "All"
// tab (Status = All) - otherwise e.g. "Complete" on the Missing Rate tab
// would always show 0 rows.
bissKpiWire("biss2", "#biss2-kpi-row", () => { biss2Selected = new Set(); biss2RenderTable(); }, (kpi) => {
  biss2Tab = "all";
  const sf = $("#biss2-status-filter");
  if (sf) sf.value = kpi === "needsaction" ? "needs-action" : "all";
});

function biss2RenderServiceRows(idx) {
  const acct = biss2Accounts[idx];
  const rows = acct.services.map((s) => (
    `<tr class="biss2-service-row ${s.needs_update ? "row-needs-action" : ""}">` +
      `<td></td><td></td>` +
      `<td title="ID_OFFERED_SERVICE ${escapeHtml(s.id_offered_service ?? "")}">${escapeHtml(s.offered_service_desc || s.id_offered_service || "")}</td>` +
      `<td colspan="3">Final bill (termination date ${escapeHtml(s.end_date ?? "")}): ` +
        (s.id_bill
          ? `Bill ${escapeHtml(s.id_bill)}, period ${escapeHtml(s.id_billing_period ?? "")}, status ${escapeHtml(s.billing_status ?? "")}`
          : `<span class="hx-pill hx-pill-billed">❓ ${escapeHtml(s.offered_service_desc || "Bill")} bill missing</span> - no bill dated this service's termination date`) +
      `</td>` +
      `<td>${s.needs_update ? "⚠️ Needs update" : "✅ OK"}</td>` +
    `</tr>`
  )).join("");
  return rows;
}

// Case 2 row markup - RJ, 2026-09-13: "its difficult to copy the account,
// also can you use a different look and feel, a modern one where each row
// is clearly recognized the design is so bad." Account number now gets
// its own dedicated copy button (data-biss2-copy) instead of relying on
// manual text selection inside a click-to-toggle cell, and status is a
// pill badge (.biss2-status-pill) instead of plain text. The account
// text itself still expands the row on click (data-biss2-toggle) since
// that's a bigger, more forgiving click target than just the chevron -
// the copy button uses stopPropagation so it never triggers the toggle.
function biss2RenderTable() {
  const visible = biss2VisibleIndices();
  const tbody = $("#biss2-table tbody");
  tbody.innerHTML = "";
  renderFilteredCount("#biss2-filtered-count", visible.length, biss2Accounts.length);
  visible.forEach((idx) => {
    const acct = biss2Accounts[idx];
    const expanded = biss2Expanded.has(idx);
    const tr = document.createElement("tr");
    tr.className = `biss2-account-row ${acct.complete ? "is-complete" : "is-needs-action"} ${expanded ? "is-expanded" : ""}`;
    const td0 = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = biss2Selected.has(idx);
    cb.addEventListener("change", () => {
      if (cb.checked) biss2Selected.add(idx); else biss2Selected.delete(idx);
      $("#biss2-select-all").checked = visible.length > 0 && visible.every((i) => biss2Selected.has(i));
    });
    td0.appendChild(cb);
    tr.appendChild(td0);
    const ref = escapeHtml(acct.reference ?? "");
    tr.insertAdjacentHTML("beforeend", (
      `<td class="biss2-toggle-cell" data-biss2-toggle="${idx}">${expanded ? "▾" : "▸"}</td>` +
      `<td><div class="biss2-account-cell">` +
        `<span class="biss2-account-num" data-biss2-toggle="${idx}">${ref}</span>` +
        `<button type="button" class="biss2-copy-btn" data-biss2-copy="${ref}" title="Copy account number">📋</button>` +
      `</div></td>` +
      `<td>${acct.service_count}</td>` +
      `<td>${acct.needs_update_count}</td>` +
      // RJ 2026-09-30: "an indicator to see immediately what is the missing bill"
      `<td>${(acct.missing_services || []).map((s) =>
        `<span class="hx-pill ${s === "Rate" ? "hx-pill-billed" : "hx-pill-amber"}" title="${escapeHtml(s)} final bill missing${s === "Rate" ? " - Generate adds the anomaly INSERTs" : ""}">❓ ${escapeHtml(s)}</span>`).join(" ") || '<span class="hint-text">—</span>'}</td>` +
      `<td>${escapeHtml(acct.target_period ?? "")}</td>` +
      `<td><span class="biss2-status-pill ${acct.complete ? "is-complete" : "is-needs-action"}">${acct.complete ? "✅ Complete" : "⚠️ Needs action"}</span></td>`
    ));
    tbody.appendChild(tr);
    if (expanded) {
      tbody.insertAdjacentHTML("beforeend", biss2RenderServiceRows(idx));
    }
  });
  tbody.querySelectorAll("[data-biss2-toggle]").forEach((el) => {
    el.addEventListener("click", () => {
      const idx = Number(el.dataset.biss2Toggle);
      if (biss2Expanded.has(idx)) biss2Expanded.delete(idx); else biss2Expanded.add(idx);
      biss2RenderTable();
    });
  });
  tbody.querySelectorAll("[data-biss2-copy]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const value = btn.dataset.biss2Copy || "";
      biss2CopyAccount(value, btn);
    });
  });
  biss2RenderKpiRow();
  biss2UpdateTabs();
  $("#biss2-export-csv-btn").hidden = biss2Accounts.length === 0;
  $("#biss2-export-all-wrap").hidden = biss2Accounts.length === 0;
  $("#biss2-select-all").checked = visible.length > 0 && visible.every((i) => biss2Selected.has(i));
}

function biss2CopyAccount(value, btn) {
  const done = (ok) => {
    if (!btn) return;
    const original = btn.textContent;
    btn.textContent = ok ? "✓" : "✕";
    btn.classList.toggle("is-copied", ok);
    setTimeout(() => {
      btn.textContent = original;
      btn.classList.remove("is-copied");
    }, 1200);
  };
  // Fallback for contexts without the async Clipboard API, or where it's
  // present but permission is denied (some embedded/kiosk browsers block
  // it outright even for a genuine user click) - a plain textarea +
  // execCommand("copy") still works in those cases.
  const legacyFallback = () => {
    try {
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      done(ok);
    } catch {
      done(false);
    }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(value).then(() => done(true)).catch(legacyFallback);
  } else {
    legacyFallback();
  }
}

hierWireSortableHeaders(
  "#biss2-table",
  () => biss2SortKey, (k) => { biss2SortKey = k; },
  () => biss2SortDir, (d) => { biss2SortDir = d; },
  biss2RenderTable,
);

$("#biss2-select-all").addEventListener("change", (e) => {
  const visible = biss2VisibleIndices();
  if (e.target.checked) visible.forEach((idx) => biss2Selected.add(idx));
  else visible.forEach((idx) => biss2Selected.delete(idx));
  biss2RenderTable();
});

$("#biss2-status-filter").addEventListener("change", () => biss2RenderTable());
$("#biss2-search").addEventListener("input", () => biss2RenderTable());
$("#biss2-missing-bill-only").addEventListener("change", () => biss2RenderTable());
$("#biss2-missing-service").addEventListener("change", () => biss2RenderTable());

// Missing-bill filter options = service names actually missing in this scan (with counts).
function biss2FillMissingServiceOptions() {
  const sel = $("#biss2-missing-service");
  const cur = sel.value;
  const counts = new Map();
  biss2Accounts.forEach((a) => (a.missing_services || []).forEach((s) => counts.set(s, (counts.get(s) || 0) + 1)));
  sel.innerHTML = `<option value="">Any</option>` + [...counts.entries()].sort((a, b) => b[1] - a[1])
    .map(([s, n]) => `<option value="${escapeHtml(s)}">${escapeHtml(s)} (${n})</option>`).join("");
  sel.value = counts.has(cur) ? cur : "";
}

$("#biss2-detect-btn").addEventListener("click", async () => {
  const btn = $("#biss2-detect-btn");
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Scanning…";
  try {
    const daysBack = Number($("#biss2-days-back").value) || 0; // 0 = "All time" (no lookback filter)
    const data = await api(`/api/bill-issuance/case2/detect?days_back=${daysBack}`, { method: "POST" });
    bissRelScan.case2 = { days_back: daysBack };
    biss2Accounts = data.accounts;
    biss2Selected = new Set();
    biss2Expanded = new Set();
    biss2SortKey = null;
    biss2SortDir = 1;
    $("#biss2-search").value = "";
    biss2FillMissingServiceOptions();
    document.querySelectorAll("#biss2-table thead th[data-sort]").forEach((h) => {
      h.querySelector(".stats-table-sort-arrow")?.remove();
    });
    biss2RenderTable();
    $("#biss2-summary").textContent = data.possibly_truncated
      ? `${data.account_count}+ account(s) with a pending notice found (row cap of ${data.limit} hit - list may be incomplete, try a shorter lookback), ${data.accounts_needing_action} needing action (${data.accounts_with_missing_bill} with a missing bill).`
      : `${data.account_count} terminated account(s) with a pending notice found, ${data.accounts_needing_action} needing action (${data.accounts_with_missing_bill} with a missing bill).`;
    if (data.excluded_case1_accounts) $("#biss2-summary").textContent += ` ${data.excluded_case1_accounts} account(s) hidden because they're already in Case 1.`;
  } catch (err) {
    showToast(err.message || "Scan failed.", true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

$("#biss2-export-csv-btn").addEventListener("click", () => {
  // RJ, 2026-09-14 (later same day): "for case 2, i need the bills and
  // status to be included in the export, now it only gives me the
  // account and service count" - the export was account-level only
  // (service_count etc.), with the actual per-service bill/status detail
  // (acct.services[]) only visible in the UI's own drill-down, never in
  // the CSV. Added joined-string columns built from acct.services -
  // same "row per account, bill-level detail as semicolon-joined
  // columns" convention Case 4's own CSV export already uses (see
  // biss4-export-csv-btn below), so each account still exports as one
  // row but now carries every service's bill id, billing period, and
  // status alongside it.
  const exportAll = !!$("#biss2-export-all")?.checked;
  const visible = biss2VisibleIndices(exportAll);
  if (!visible.length) return;
  const header = [
    "id_payment_form", "reference", "niss", "service_count", "needs_update_count", "missing_bill_count",
    "missing_bills", "period_mismatch_count", "target_period", "complete",
    "offered_services", "service_niss", "bills", "billing_periods", "billing_statuses",
  ];
  const lines = [header.join(",")];
  visible.forEach((idx) => {
    const a = biss2Accounts[idx];
    // Positionally aligned (NOT independently filtered) - a service with
    // no matching bill (the "missing bill" case) still gets a slot so
    // the Nth entry in every column below is still the same service,
    // rather than the arrays silently drifting out of sync with each
    // other once one column drops an empty value the others don't.
    const services = a.services || [];
    const row = {
      ...a,
      missing_bills: (a.missing_services || []).join("; "),
      niss: (a.niss || []).join("; "),
      service_niss: services.map((s) => s.niss || "-").join("; "),
      offered_services: services.map((s) => s.offered_service_desc || "?").join("; "),
      bills: services.map((s) => s.id_bill || "(none)").join("; "),
      billing_periods: services.map((s) => s.id_billing_period || "-").join("; "),
      billing_statuses: services.map((s) => s.billing_status || "-").join("; "),
    };
    lines.push(header.map((k) => `"${String(row[k] ?? "").replace(/"/g, '""')}"`).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a2 = document.createElement("a");
  a2.href = url; a2.download = exportAll ? "bill_issuance_validator_case2_all.csv" : "bill_issuance_validator_case2.csv";
  document.body.appendChild(a2); a2.click(); a2.remove();
  URL.revokeObjectURL(url);
});

$("#biss2-generate-btn").addEventListener("click", async () => {
  const program = $("#biss2-program").value.trim();
  if (!program) { showToast("Enter the Jira/Program # this change is for.", true); return; }
  const clean = $("#biss2-clean-toggle").checked;
  // Sub-tabs (RJ 2026-09-30): nothing selected = every account on the
  // ACTIVE tab; the tab also picks which script parts are generated (mode).
  const tab = BISS2_TABS[biss2Tab];
  if (tab.mode === null) { showToast("Nothing to generate for complete accounts.", true); return; }
  const picked = biss2Selected.size ? [...biss2Selected] : (biss2Tab === "all" ? [] : biss2VisibleIndices());
  if (biss2Tab !== "all" && !picked.length) { showToast("No accounts on this tab.", true); return; }
  const selectedForms = [...new Set(picked.map((idx) => String(biss2Accounts[idx].id_payment_form)))];
  const btn = $("#biss2-generate-btn");
  btn.disabled = true;
  try {
    const result = await api("/api/bill-issuance/case2/generate", {
      method: "POST",
      body: { id_payment_forms: selectedForms, program, clean, mode: tab.mode },
    });
    $("#biss2-output").textContent = result.sql_text;
    let msg = `Script generated: ${result.update_count} bill update(s)` +
      (result.anomaly_count ? `, ${result.anomaly_count} missing-Rate-bill anomaly insert(s)` : "") + ".";
    if (result.warnings.length) msg += `  ${result.warnings.length} warning(s) - see comments at the top of the script.`;
    showToast(msg);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = state.role === "viewer";
  }
});

$("#biss2-copy-btn").addEventListener("click", async () => {
  const text = $("#biss2-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Update script copied to clipboard.");
  } catch (_) {
    showToast("Couldn't copy - select and copy manually.", true);
  }
});

$("#biss2-download-btn").addEventListener("click", () => {
  const text = $("#biss2-output").textContent;
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "bill_issuance_case2_update.sql";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// ---------------- Bill Issuance Validator: Case 3 (All Contract Status -
// Bills Complete) ----------------
// RJ, 2026-09-14: per-account, per-2026-billing-period rows where the
// account's total contracted-service count already exactly equals its
// still-invoicing cycle-bill count for that period - see
// app/core/bill_issuance_validator.py's Case 3 comment block for RJ's own
// verbatim SQL and the "1 by 1, to obtain the correct result" per-period
// reasoning. Read-only/informational (no Generate button - these accounts
// have nothing wrong to fix), flat one-row-per-(account,period) table,
// same sort-header/CSV-export/client-side-search convention as Case 1/2,
// plus a server-side period + With-Active-Contract filter (re-scans on
// Scan click, same idiom as Case 2's days-back dropdown) since those two
// filters change which SQL actually runs, not just which already-fetched
// rows are visible.
let biss3Rows = [];
let biss3SortKey = null;
let biss3SortDir = 1;

async function biss3LoadPeriods() {
  const yearSel = $("#biss3-year");
  const periodSel = $("#biss3-period-filter");
  const year = Number(yearSel.value) || bill_issuance_case3_default_year;
  try {
    const data = await api(`/api/bill-issuance/case3/billing-periods?year=${year}`, { method: "GET" });
    const current = periodSel.value;
    periodSel.innerHTML = `<option value="">All ${year} periods</option>` + data.periods.map((p) =>
      `<option value="${escapeHtml(p.id_billing_period)}">${escapeHtml(p.period_name)}</option>`
    ).join("");
    if ([...periodSel.options].some((o) => o.value === current)) periodSel.value = current;
  } catch (_) { /* not fatal - period dropdown just stays on "All periods" */ }
}
const bill_issuance_case3_default_year = 2026;
$("#biss3-year").addEventListener("change", biss3LoadPeriods);

function biss3VisibleIndices(ignoreFilters = false) {
  let indices = biss3Rows.map((_, i) => i);
  if (!ignoreFilters) {
    const term = ($("#biss3-search")?.value || "").trim().toLowerCase();
    if (term) indices = indices.filter((i) => String(biss3Rows[i].reference ?? "").toLowerCase().includes(term));
    indices = bissKpiApply("biss3", biss3Rows, indices);
  }
  if (biss3SortKey) {
    indices.sort((a, b) => _hierCompareValues(biss3Rows[a][biss3SortKey], biss3Rows[b][biss3SortKey], biss3SortDir));
  }
  return indices;
}

function biss3RenderKpiRow(data) {
  const cards = [
    ["rows", "🧾", "Account/period rows", biss3Rows.length],
    ["accounts", "🏠", "Distinct accounts", data?.account_count ?? new Set(biss3Rows.map((r) => r.id_payment_form)).size],
    ["active", "✅", "With active contract (YES)", data?.with_active_contract_count ?? biss3Rows.filter((r) => r.with_active_contract === "YES").length],
    ["inactive", "⛔", "No active contract (NO)", biss3Rows.filter((r) => r.with_active_contract === "NO").length],
  ];
  hxGaugesAbove("#biss3-kpi-row", [
    { label: "Rows with an active contract", count: biss3Rows.filter((r) => r.with_active_contract === "YES").length, total: biss3Rows.length, c1: "#10b981", c2: "#22c55e" },
  ]);
  $("#biss3-kpi-row").innerHTML = bissKpiCardsHtml("biss3", cards);
}
bissKpiWire("biss3", "#biss3-kpi-row", () => biss3RenderTable());

function biss3RenderTable() {
  const visible = biss3VisibleIndices();
  const tbody = $("#biss3-table tbody");
  tbody.innerHTML = "";
  renderFilteredCount("#biss3-filtered-count", visible.length, biss3Rows.length);
  visible.forEach((idx) => {
    const r = biss3Rows[idx];
    const tr = document.createElement("tr");
    tr.innerHTML = (
      `<td>${escapeHtml(r.reference ?? "")}</td>` +
      `<td title="ID_BILLING_PERIOD ${escapeHtml(r.id_billing_period ?? "")}">${escapeHtml(r.billing_period_name ?? r.id_billing_period ?? "")}</td>` +
      `<td>${escapeHtml(r.contracted_services ?? "")}</td>` +
      `<td>${escapeHtml(r.bills ?? "")}</td>` +
      `<td><span class="biss2-status-pill ${r.with_active_contract === "YES" ? "is-complete" : "is-needs-action"}">${escapeHtml(r.with_active_contract ?? "")}</span></td>`
    );
    tbody.appendChild(tr);
  });
  biss3RenderKpiRow();
  $("#biss3-export-csv-btn").hidden = biss3Rows.length === 0;
  $("#biss3-export-all-wrap").hidden = biss3Rows.length === 0;
}

hierWireSortableHeaders(
  "#biss3-table",
  () => biss3SortKey, (k) => { biss3SortKey = k; },
  () => biss3SortDir, (d) => { biss3SortDir = d; },
  biss3RenderTable,
);

$("#biss3-search").addEventListener("input", () => biss3RenderTable());

$("#biss3-detect-btn").addEventListener("click", async () => {
  const btn = $("#biss3-detect-btn");
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Scanning…";
  try {
    const year = Number($("#biss3-year").value) || bill_issuance_case3_default_year;
    const periodId = $("#biss3-period-filter").value;
    const activeFilter = $("#biss3-active-filter").value;
    const params = new URLSearchParams({ year: String(year) });
    if (periodId) params.set("billing_period_id", periodId);
    if (activeFilter) params.set("with_active_contract", activeFilter);
    const data = await api(`/api/bill-issuance/case3/detect?${params.toString()}`, { method: "POST" });
    bissRelScan.case3 = { year, billing_period_id: periodId || null, with_active_contract: activeFilter || null };
    biss3Rows = data.rows;
    biss3SortKey = null;
    biss3SortDir = 1;
    $("#biss3-search").value = "";
    document.querySelectorAll("#biss3-table thead th[data-sort]").forEach((h) => {
      h.querySelector(".stats-table-sort-arrow")?.remove();
    });
    biss3RenderTable(data);
    $("#biss3-summary").textContent = data.possibly_truncated
      ? `${data.row_count}+ row(s) found (capped at ${data.limit} - list may be incomplete, try narrowing the period filter), ${data.account_count} distinct account(s).`
      : `${data.row_count} row(s) found, ${data.account_count} distinct account(s), ${data.with_active_contract_count} with an active contract.`;
  } catch (err) {
    showToast(err.message || "Scan failed.", true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

$("#biss3-export-csv-btn").addEventListener("click", () => {
  const exportAll = !!$("#biss3-export-all")?.checked;
  const visible = biss3VisibleIndices(exportAll);
  if (!visible.length) return;
  const header = ["reference", "id_payment_form", "contracted_services", "bills", "missing_bills", "id_billing_period", "billing_period_name", "with_active_contract"];
  const lines = [header.join(",")];
  visible.forEach((idx) => {
    const r = biss3Rows[idx];
    lines.push(header.map((k) => `"${String(r[k] ?? "").replace(/"/g, '""')}"`).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = exportAll ? "bill_issuance_validator_case3_all.csv" : "bill_issuance_validator_case3.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// Populate the period-filter dropdown once at load (default year 2026) -
// same "fetch lookup data once, page opens ready to filter" convention as
// Bulk Checker's own billing-period picker.
biss3LoadPeriods();

// ---------------- Bill Issuance Validator: Case 4 (Unclassified) --------
// RJ, 2026-09-14 (same day), own words: "create a 4th case,
// 'Unclassified' those that are pending validation in notice TMP, and
// not in case 1, case 2, case 3, and any other case that we will add in
// the future. Make it look like case 2, where there is a drill down on
// the bills and just showing the accounts on the row and option to copy
// and export to excel." No select-all/checkbox column and no Generate
// button here (unlike Case 2) - Case 4 is read-only/informational, same
// as Case 3: every account here needs its own manual investigation,
// there's nothing this tool could safely auto-correct.
let biss4Accounts = [];
let biss4SortKey = null;
let biss4SortDir = 1;
let biss4Expanded = new Set(); // expanded row indices - into biss4Accounts

function biss4VisibleIndices(ignoreFilters = false) {
  let indices = biss4Accounts.map((_, i) => i);
  if (!ignoreFilters) {
    const term = ($("#biss4-search")?.value || "").trim().toLowerCase();
    if (term) indices = indices.filter((i) => String(biss4Accounts[i].reference ?? "").toLowerCase().includes(term));
  }
  if (biss4SortKey) {
    indices.sort((a, b) => _hierCompareValues(biss4Accounts[a][biss4SortKey], biss4Accounts[b][biss4SortKey], biss4SortDir));
  }
  return indices;
}

let biss4LastData = null; // keeps the excluded-case counts across re-renders
function biss4RenderKpiRow(data) {
  if (data) biss4LastData = data; else data = biss4LastData;
  const totalBills = biss4Accounts.reduce((sum, a) => sum + a.bill_count, 0);
  const cards = [
    ["accounts", "🧾", "Unclassified accounts", biss4Accounts.length],
    ["bills", "📄", "Pending bills across them", totalBills],
    ["case1", "1️⃣", "Excluded - Case 1", data?.case1_account_count ?? "—"],
    ["case1nc", "1️⃣", "Excluded - New Contract Match", data?.new_contract_match_account_count ?? "—"],
    ["case2", "2️⃣", "Excluded - Case 2", data?.case2_account_count ?? "—"],
    ["case3", "3️⃣", "Excluded - Case 3", data?.case3_account_count ?? "—"],
  ];
  $("#biss4-kpi-row").innerHTML = bissKpiCardsHtml("biss4", cards);
}

function biss4RenderBillRows(idx) {
  const acct = biss4Accounts[idx];
  return acct.bills.map((b) => (
    `<tr class="biss2-service-row">` +
      `<td></td>` +
      `<td colspan="2">Bill ${escapeHtml(b.id_bill ?? "")}, period ${escapeHtml(b.id_billing_period ?? "")}` +
        (b.bill_type ? `, type ${escapeHtml(b.bill_type)}` : "") +
        ` — ${escapeHtml(b.offered_service_desc || "")}` +
        ` — ${escapeHtml(b.billing_status_desc || "")}` +
        (b.billing_date ? `, billed ${escapeHtml(b.billing_date)}` : "") +
      `</td>` +
    `</tr>`
  )).join("");
}

// Row markup mirrors Case 2's own (biss2RenderTable) - dedicated copy
// button (reuses biss2CopyAccount, which isn't actually Case-2-specific
// in what it does), click-to-expand account cell, drill-down rows for
// the account's pending bills.
function biss4RenderTable(data) {
  const visible = biss4VisibleIndices();
  const tbody = $("#biss4-table tbody");
  tbody.innerHTML = "";
  renderFilteredCount("#biss4-filtered-count", visible.length, biss4Accounts.length);
  visible.forEach((idx) => {
    const acct = biss4Accounts[idx];
    const expanded = biss4Expanded.has(idx);
    const tr = document.createElement("tr");
    tr.className = `biss2-account-row is-needs-action ${expanded ? "is-expanded" : ""}`;
    const ref = escapeHtml(acct.reference ?? "");
    tr.innerHTML = (
      `<td class="biss2-toggle-cell" data-biss4-toggle="${idx}">${expanded ? "▾" : "▸"}</td>` +
      `<td><div class="biss2-account-cell">` +
        `<span class="biss2-account-num" data-biss4-toggle="${idx}">${ref}</span>` +
        `<button type="button" class="biss2-copy-btn" data-biss4-copy="${ref}" title="Copy account number">📋</button>` +
      `</div></td>` +
      `<td>${acct.bill_count}</td>`
    );
    tbody.appendChild(tr);
    if (expanded) {
      tbody.insertAdjacentHTML("beforeend", biss4RenderBillRows(idx));
    }
  });
  tbody.querySelectorAll("[data-biss4-toggle]").forEach((el) => {
    el.addEventListener("click", () => {
      const idx = Number(el.dataset.biss4Toggle);
      if (biss4Expanded.has(idx)) biss4Expanded.delete(idx); else biss4Expanded.add(idx);
      biss4RenderTable();
    });
  });
  tbody.querySelectorAll("[data-biss4-copy]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const value = btn.dataset.biss4Copy || "";
      biss2CopyAccount(value, btn);
    });
  });
  biss4RenderKpiRow(data);
  $("#biss4-export-csv-btn").hidden = biss4Accounts.length === 0;
  $("#biss4-export-xlsx-btn").hidden = biss4Accounts.length === 0;
  $("#biss4-export-all-wrap").hidden = biss4Accounts.length === 0;
}

hierWireSortableHeaders(
  "#biss4-table",
  () => biss4SortKey, (k) => { biss4SortKey = k; },
  () => biss4SortDir, (d) => { biss4SortDir = d; },
  biss4RenderTable,
);

$("#biss4-search").addEventListener("input", () => biss4RenderTable());

$("#biss4-detect-btn").addEventListener("click", async () => {
  const btn = $("#biss4-detect-btn");
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Scanning…";
  try {
    const data = await api("/api/bill-issuance/case4/detect", { method: "POST" });
    biss4Accounts = data.accounts;
    biss4SortKey = null;
    biss4SortDir = 1;
    biss4Expanded = new Set();
    $("#biss4-search").value = "";
    document.querySelectorAll("#biss4-table thead th[data-sort]").forEach((h) => {
      h.querySelector(".stats-table-sort-arrow")?.remove();
    });
    biss4RenderTable(data);
    $("#biss4-summary").textContent = data.possibly_truncated
      ? `${data.account_count}+ unclassified account(s) found (bill scan capped at ${data.limit} rows - list may be incomplete).`
      : `${data.account_count} unclassified account(s), ${data.bill_count} pending bill(s) total.`;
  } catch (err) {
    showToast(err.message || "Scan failed.", true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});

$("#biss4-export-csv-btn").addEventListener("click", () => {
  const exportAll = !!$("#biss4-export-all")?.checked;
  const visible = biss4VisibleIndices(exportAll);
  if (!visible.length) return;
  const header = ["reference", "id_payment_form", "bill_count", "offered_services", "billing_periods"];
  const lines = [header.join(",")];
  visible.forEach((idx) => {
    const acct = biss4Accounts[idx];
    const row = {
      reference: acct.reference,
      id_payment_form: acct.id_payment_form,
      bill_count: acct.bill_count,
      offered_services: acct.bills.map((b) => b.offered_service_desc).filter(Boolean).join("; "),
      billing_periods: acct.bills.map((b) => b.id_billing_period).filter(Boolean).join("; "),
    };
    lines.push(header.map((k) => `"${String(row[k] ?? "").replace(/"/g, '""')}"`).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = exportAll ? "bill_issuance_validator_case4_all.csv" : "bill_issuance_validator_case4.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

$("#biss4-export-xlsx-btn").addEventListener("click", async () => {
  const exportAll = !!$("#biss4-export-all")?.checked;
  const visible = biss4VisibleIndices(exportAll);
  if (!visible.length) return;
  const btn = $("#biss4-export-xlsx-btn");
  btn.disabled = true;
  try {
    const rows = visible.map((idx) => {
      const acct = biss4Accounts[idx];
      return {
        reference: acct.reference ?? "",
        id_payment_form: acct.id_payment_form ?? "",
        bill_count: String(acct.bill_count ?? ""),
        offered_services: acct.bills.map((b) => b.offered_service_desc).filter(Boolean).join("; "),
        billing_periods: acct.bills.map((b) => b.id_billing_period).filter(Boolean).join("; "),
      };
    });
    const res = await fetch("/api/bill-issuance/case4/export-xlsx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Export failed.");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = exportAll ? "bill_issuance_case4_all.xlsx" : "bill_issuance_case4.xlsx";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showToast(err.message || "Excel export failed.", true);
  } finally {
    btn.disabled = false;
  }
});

// ---------------- Server-start diagnostic (login screen + sidebar) ------
// Unauthenticated /api/server-info - see server.py's SERVER_STARTED_AT
// comment. Fetched once at boot, before login, so it's visible even on the
// login screen: if this timestamp doesn't move after "restarting" the app,
// an old process is still bound to port 8420 and none of your code changes
// are actually being served - that's a server/process problem, not a
// browser-cache or code problem, and no amount of hard-refreshing fixes it.
async function loadServerInfo() {
  try {
    const info = await api("/api/server-info");
    const started = new Date(info.started_at);
    const text = `Server started ${started.toLocaleString()} · PID ${info.pid}`;
    const loginEl = $("#login-server-info");
    const sidebarEl = $("#sidebar-server-info");
    if (loginEl) loginEl.textContent = text;
    if (sidebarEl) sidebarEl.textContent = text;
  } catch (_) { /* not fatal - just no diagnostic line shown */ }
}

// ---------------- Incorrect Billing Period ----------------
// RJ, 2026-09-23, own SQL (RATE INCORRECT BILLING PERIOD) - see
// app/core/incorrect_billing_period.py's module docstring for the full
// query and correction-script provenance. Grouped by ID_OFFERED_SERVICE
// (RJ: "Group it with id_offered_service, and can be filtered with
// ID_OFFERED_SERVICE description") via group-divider rows inserted right
// into the one flat <table> - same select-all-visible/filter/checkbox
// machinery as da-cleanup-table, just with a divider row wherever the
// group changes instead of a second nested table.
let ibpGroups = [];          // raw groups from the API: [{id_offered_service, offered_service_desc, anomalies:[...]}]
let ibpRows = [];            // flattened anomalies, each carrying its own offered_service_desc
const ibpSelected = new Set(); // selected id_anomalous values, as strings
const IBP_COL_COUNT = 17;

function ibpFlattenGroups() {
  const out = [];
  ibpGroups.forEach((g) => {
    (g.anomalies || []).forEach((a) => out.push({ ...a, offered_service_desc: g.offered_service_desc || "(none)" }));
  });
  return out;
}

function ibpVisibleRows() {
  const service = $("#ibp-service-filter").value;
  const search = $("#ibp-search").value.trim().toLowerCase();
  return ibpRows.filter((r) => {
    if (service && r.offered_service_desc !== service) return false;
    if (search) {
      const hay = `${r.account || ""} ${r.niss || ""} ${r.id_anomalous || ""}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });
}

function ibpUpdateGenerateEnabled() {
  $("#ibp-generate-btn").disabled = ibpSelected.size === 0 || state.role === "viewer";
  $("#ibp-selection-hint").textContent = ibpSelected.size
    ? `${ibpSelected.size} anomaly(ies) selected.`
    : "☝️ Check one or more rows in the table above to enable Generate.";
}

function ibpRenderTable() {
  const tbody = document.querySelector("#ibp-table tbody");
  const visible = ibpVisibleRows();
  let lastGroup = null;
  const parts = [];
  visible.forEach((r) => {
    if (r.offered_service_desc !== lastGroup) {
      lastGroup = r.offered_service_desc;
      const count = visible.filter((x) => x.offered_service_desc === lastGroup).length;
      parts.push(`<tr class="ibp-group-row"><td colspan="${IBP_COL_COUNT}">${escapeHtml(lastGroup)} (${count})</td></tr>`);
    }
    const idStr = String(r.id_anomalous);
    const checked = ibpSelected.has(idStr) ? "checked" : "";
    parts.push(
      `<tr data-ibp-id="${escapeHtml(idStr)}">` +
      `<td><input type="checkbox" class="ibp-row-check" ${checked} /></td>` +
      `<td>${escapeHtml(r.id_anomalous ?? "")}</td>` +
      `<td>${escapeHtml(r.anomalous_status ?? "")}</td>` +
      `<td>${escapeHtml(r.account ?? "")}</td>` +
      `<td>${escapeHtml(r.niss ?? "")}</td>` +
      `<td>${escapeHtml(r.last_billing_date ?? "")}</td>` +
      `<td>${escapeHtml(r.billing_date ?? "")}</td>` +
      `<td>${escapeHtml(r.id_billing_period ?? "")}</td>` +
      `<td>${escapeHtml(r.id_item_to_bill ?? "")}</td>` +
      `<td>${escapeHtml(r.item_status ?? "")}</td>` +
      `<td>${escapeHtml(r.id_reading ?? "")}</td>` +
      `<td>${escapeHtml(r.read_status ?? "")}</td>` +
      `<td>${escapeHtml(r.reading_type ?? "")}</td>` +
      `<td>${escapeHtml(r.ready_usage ?? "")}</td>` +
      `<td>${escapeHtml(r.reading_prev_date ?? "")}</td>` +
      `<td>${escapeHtml(r.reading_date ?? "")}</td>` +
      `<td>${escapeHtml(r.niss_at_reading ?? "")}</td>` +
      `</tr>`
    );
  });
  tbody.innerHTML = parts.join("") ||
    `<tr><td colspan="${IBP_COL_COUNT}" class="hint-text">No anomalies match the current filters.</td></tr>`;
  tbody.querySelectorAll(".ibp-row-check").forEach((cb) => {
    cb.addEventListener("change", (e) => {
      const id = e.target.closest("tr").dataset.ibpId;
      if (e.target.checked) ibpSelected.add(id); else ibpSelected.delete(id);
      ibpUpdateGenerateEnabled();
      $("#ibp-select-all").checked = visible.length > 0 && visible.every((r) => ibpSelected.has(String(r.id_anomalous)));
    });
  });
  $("#ibp-select-all").checked = visible.length > 0 && visible.every((r) => ibpSelected.has(String(r.id_anomalous)));
  renderFilteredCount("#ibp-filtered-count", visible.length, ibpRows.length);
  if (ibpRows.length) {
    const n = visible.length;
    hxRenderDashboard("ibp-dash", {
      gauges: [
        { label: "Reading already billed", count: visible.filter((r) => ["7000STSRED", "7001STSRED"].includes(r.read_status)).length, total: n, c1: "#6366f1", c2: "#06b6d4" },
        { label: "Item to bill billed", count: visible.filter((r) => r.item_status === "STTOBILL07").length, total: n, c1: "#10b981", c2: "#22c55e" },
      ],
      tiles: [
        { icon: "📆", label: "Open anomalies", value: new Set(visible.map((r) => r.id_anomalous)).size.toLocaleString(), accent: true },
        { icon: "👤", label: "Accounts", value: new Set(visible.map((r) => r.account).filter(Boolean)).size.toLocaleString() },
        { icon: "★", label: "Total ready usage", value: hxSum(visible, "ready_usage").toLocaleString() },
        { icon: "🧩", label: "Offered services", value: new Set(visible.map((r) => r.offered_service_desc)).size.toLocaleString() },
      ],
      split: { title: "Offered service", entries: hxCountBy(visible, (r) => r.offered_service_desc) },
    });
  }
}

$("#ibp-select-all").addEventListener("change", (e) => {
  ibpVisibleRows().forEach((r) => {
    const idStr = String(r.id_anomalous);
    if (e.target.checked) ibpSelected.add(idStr); else ibpSelected.delete(idStr);
  });
  ibpUpdateGenerateEnabled();
  ibpRenderTable();
});
$("#ibp-service-filter").addEventListener("change", ibpRenderTable);
$("#ibp-search").addEventListener("input", ibpRenderTable);
$("#ibp-filter-clear-btn").addEventListener("click", () => {
  $("#ibp-service-filter").value = "";
  $("#ibp-search").value = "";
  ibpRenderTable();
});

async function ibpDetect() {
  const btn = $("#ibp-detect-btn");
  btn.disabled = true;
  $("#ibp-summary").textContent = "Scanning…";
  try {
    const data = await api("/api/incorrect-billing-period/detect", { method: "POST" });
    ibpGroups = data.groups || [];
    ibpRows = ibpFlattenGroups();
    ibpSelected.clear();

    const serviceSel = $("#ibp-service-filter");
    const current = serviceSel.value;
    const descs = [...new Set(ibpGroups.map((g) => g.offered_service_desc || "(none)"))];
    serviceSel.innerHTML = `<option value="">All</option>` +
      descs.map((d) => `<option value="${escapeHtml(d)}">${escapeHtml(d)}</option>`).join("");
    if (descs.includes(current)) serviceSel.value = current;

    $("#ibp-filter-row").hidden = ibpRows.length === 0;
    $("#ibp-summary").textContent = data.possibly_truncated
      ? `${data.anomaly_count} anomaly(ies) found (capped at ${data.limit} - refine or re-run if you expect more).`
      : `${data.anomaly_count} anomaly(ies) found across ${ibpGroups.length} offered service(s).`;
    ibpUpdateGenerateEnabled();
    ibpRenderTable();
  } catch (err) {
    $("#ibp-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}
$("#ibp-detect-btn").addEventListener("click", ibpDetect);

$("#ibp-generate-btn").addEventListener("click", async () => {
  if (!ibpSelected.size) return;
  const program = $("#ibp-program").value.trim();
  if (!program) { showToast("Enter the Jira/Program # this change is for.", true); return; }
  const audit_user = $("#ibp-audit-user").value.trim();
  const clean = $("#ibp-clean-toggle").checked;
  const btn = $("#ibp-generate-btn");
  btn.disabled = true;
  try {
    const result = await api("/api/incorrect-billing-period/generate", {
      method: "POST",
      body: { anomaly_ids: [...ibpSelected], program, audit_user, clean },
    });
    $("#ibp-output").textContent = result.sql_text;
    let msg = `Correction script generated: ${result.anomaly_count} anomaly(ies).`;
    if (result.warnings.length) msg += `  ${result.warnings.length} warning(s) - see comments at the top of the script.`;
    showToast(msg);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = state.role === "viewer";
  }
});

$("#ibp-copy-btn").addEventListener("click", async () => {
  const text = $("#ibp-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Correction script copied to clipboard.");
  } catch (_) {
    showToast("Couldn't copy - select and copy manually.", true);
  }
});

$("#ibp-download-btn").addEventListener("click", () => {
  const text = $("#ibp-output").textContent;
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "incorrect_billing_period_correction.sql";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// ---------------- TNB CYCLE/DISC Analysis ----------------
// RJ, 2026-09-25 - see app/core/tnb_cycle_disc.py for the query. One scan
// (POST /api/tnb-cycle-disc/detect), then billing period / NISS / TNB-side
// filtering and sorting all client-side.
const TCD_TNB_CODE = "8000STSRED";
const TCD_SIDE_COLS = [
  ["id_reading", "ID Reading"],
  ["reading_type", "Reading Type"],
  ["reading_prev_date", "Prev. Date"],
  ["reading_date", "Reading Date"],
  ["prev_value", "Prev. Value"],
  ["value", "Value"],
  ["reading_usage", "Reading Usage"],
  ["corrected_usage", "Corrected Usage"],
  ["ready_usage", "Ready Usage"],
  ["read_status", "Read Status"],
];
const TCD_COLUMNS = [
  { key: "niss", label: "NISS" },
  { key: "billing_period", label: "Billing Period" },
  { key: "usage_name", label: "Usage" },
  { key: "in_contract", label: "In Contract" },
  ...TCD_SIDE_COLS.map(([k, l]) => ({ key: `c_${k}`, label: l, side: "c" })),
  ...TCD_SIDE_COLS.map(([k, l]) => ({ key: `d_${k}`, label: l, side: "d" })),
];
const TCD_DATE_KEYS = new Set(["c_reading_prev_date", "c_reading_date", "d_reading_prev_date", "d_reading_date"]);
let tcdRows = [];
let tcdSortKey = null;
let tcdSortDir = 1;

function tcdFmt(key, v) {
  if (key === "in_contract") return String(v) === "1" ? "Yes" : "No";
  const s = v ?? "";
  return TCD_DATE_KEYS.has(key) && typeof s === "string" ? s.replace(/ 00:00:00$/, "") : s;
}

function tcdVisibleRows() {
  const bp = $("#tcd-filter-bp").value;
  const niss = $("#tcd-filter-niss").value.trim().toLowerCase();
  const tnb = $("#tcd-filter-tnb").value;
  const contract = $("#tcd-filter-contract").value;
  let rows = tcdRows.filter((r) => {
    if (contract === "yes" && String(r.in_contract) !== "1") return false;
    if (contract === "no" && String(r.in_contract) === "1") return false;
    if (bp && r.billing_period !== bp && r.c_billing_period !== bp && r.d_billing_period !== bp) return false;
    if (niss && !String(r.niss ?? "").toLowerCase().includes(niss)) return false;
    const cT = r.c_read_status_code === TCD_TNB_CODE;
    const dT = r.d_read_status_code === TCD_TNB_CODE;
    if (tnb === "cycle" && !cT) return false;
    if (tnb === "disc" && !dT) return false;
    if (tnb === "both" && !(cT && dT)) return false;
    return true;
  });
  if (tcdSortKey) rows = [...rows].sort((a, b) => _hierCompareValues(a[tcdSortKey], b[tcdSortKey], tcdSortDir));
  return rows;
}

function tcdRenderHeader() {
  const tr = document.querySelector("#tcd-table thead tr.tcd-col-row");
  tr.innerHTML = TCD_COLUMNS.map((c) => {
    const arrow = tcdSortKey === c.key ? `<span class="stats-table-sort-arrow">${tcdSortDir === 1 ? "▲" : "▼"}</span>` : "";
    const cls = c.side === "c" ? " tcd-col-cycle" : c.side === "d" ? " tcd-col-disc" : "";
    return `<th class="stats-table-th-sortable${cls}" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  tr.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    tcdSortDir = tcdSortKey === k ? -tcdSortDir : 1;
    tcdSortKey = k;
    tcdRenderTable();
  }));
}

function tcdRenderTable() {
  tcdRenderHeader();
  const rows = tcdVisibleRows();
  const tbody = document.querySelector("#tcd-table tbody");
  tbody.innerHTML = rows.length
    ? rows.map((r) => "<tr>" + TCD_COLUMNS.map((c) => {
        const classes = [];
        if (c.side === "c") classes.push("tcd-cell-cycle");
        if (c.side === "d") classes.push("tcd-cell-disc");
        if (c.key === "c_read_status" && r.c_read_status_code === TCD_TNB_CODE) classes.push("tcd-tnb");
        if (c.key === "d_read_status" && r.d_read_status_code === TCD_TNB_CODE) classes.push("tcd-tnb");
        if (c.key === "in_contract") classes.push(String(r.in_contract) === "1" ? "tcd-in-contract" : "tcd-no-contract");
        return `<td${classes.length ? ` class="${classes.join(" ")}"` : ""}>${escapeHtml(tcdFmt(c.key, r[c.key]))}</td>`;
      }).join("") + "</tr>").join("")
    : `<tr><td colspan="${TCD_COLUMNS.length}" class="hint-text">No pairs match the current filters.</td></tr>`;
  renderFilteredCount("#tcd-filtered-count", rows.length, tcdRows.length);
  if (tcdRows.length) {
    const n = rows.length;
    hxRenderDashboard("tcd-dash", {
      gauges: [
        { label: "Reading date in contract", count: rows.filter((r) => String(r.in_contract) === "1").length, total: n, c1: "#10b981", c2: "#22c55e",
          hint: "A non-cancelled contracted service covers the reading date" },
        { label: "Cycle is TNB", count: rows.filter((r) => r.c_read_status_code === TCD_TNB_CODE).length, total: n, c1: "#6366f1", c2: "#3b82f6" },
        { label: "Disconnection is TNB", count: rows.filter((r) => r.d_read_status_code === TCD_TNB_CODE).length, total: n, c1: "#f59e0b", c2: "#f97316" },
      ],
      tiles: [
        { icon: "★", label: "Ready usage (cycle + disc.)", value: (hxSum(rows, "c_ready_usage") + hxSum(rows, "d_ready_usage")).toLocaleString(), accent: true },
        { icon: "🔌", label: "Pairs", value: n.toLocaleString() },
        { icon: "🏠", label: "Sector supplies", value: new Set(rows.map((r) => r.id_sector_supply)).size.toLocaleString() },
        { icon: "📅", label: "Billing periods", value: new Set(rows.map((r) => r.billing_period).filter(Boolean)).size.toLocaleString() },
        { icon: "⚡", label: "Both TNB", value: rows.filter((r) => r.c_read_status_code === TCD_TNB_CODE && r.d_read_status_code === TCD_TNB_CODE).length.toLocaleString() },
      ],
      split: { title: "Usage type", entries: hxCountBy(rows, (r) => r.usage_name) },
    });
  }
}

async function tcdDetect() {
  const btn = $("#tcd-detect-btn");
  btn.disabled = true;
  $("#tcd-summary").textContent = "Scanning… (~20–30s)";
  try {
    const data = await api("/api/tnb-cycle-disc/detect", { method: "POST" });
    tcdRows = data.rows || [];
    const sel = $("#tcd-filter-bp");
    const current = sel.value;
    // Billing periods newest first (by id), shown in words.
    const periods = new Map();
    tcdRows.forEach((r) => {
      [[r.billing_period, r.id_billing_period], [r.c_billing_period, r.c_id_billing_period], [r.d_billing_period, r.d_id_billing_period]]
        .forEach(([name, id]) => { if (name && !periods.has(name)) periods.set(name, Number(id) || 0); });
    });
    const names = [...periods.entries()].sort((a, b) => b[1] - a[1]).map(([n]) => n);
    sel.innerHTML = `<option value="">All</option>` + names.map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
    if (names.includes(current)) sel.value = current;
    $("#tcd-filter-row").hidden = tcdRows.length === 0;
    $("#tcd-export-btn").disabled = tcdRows.length === 0;
    const outside = tcdRows.filter((r) => String(r.in_contract) !== "1").length;
    $("#tcd-summary").textContent = `${data.count} cycle/disconnection pair(s) across ${data.supply_count} sector supply(ies) — ${outside} with the reading date outside any non-cancelled contracted service.`;
    tcdRenderTable();
  } catch (err) {
    $("#tcd-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}
$("#tcd-detect-btn").addEventListener("click", tcdDetect);
$("#tcd-filter-bp").addEventListener("change", tcdRenderTable);
$("#tcd-filter-tnb").addEventListener("change", tcdRenderTable);
$("#tcd-filter-contract").addEventListener("change", tcdRenderTable);
$("#tcd-filter-niss").addEventListener("input", tcdRenderTable);
$("#tcd-filter-clear-btn").addEventListener("click", () => {
  $("#tcd-filter-contract").value = "";
  $("#tcd-filter-bp").value = "";
  $("#tcd-filter-niss").value = "";
  $("#tcd-filter-tnb").value = "";
  tcdRenderTable();
});
$("#tcd-export-btn").addEventListener("click", () => {
  const rows = tcdVisibleRows();
  if (!rows.length) return;
  const header = TCD_COLUMNS.map((c) => (c.side === "c" ? "Cycle " : c.side === "d" ? "Disc " : "") + c.label);
  const lines = [header.map((h) => `"${h}"`).join(",")];
  rows.forEach((r) => lines.push(TCD_COLUMNS.map((c) => `"${String(tcdFmt(c.key, r[c.key]) ?? "").replace(/"/g, '""')}"`).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "tnb_cycle_disc.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});
tcdRenderHeader();

// ---------------- Wrong Stuck in Hierarchy ITB ----------------
// RJ, 2026-09-25 - see app/core/wrong_stuck_hierarchy.py. One scan with
// no parameters covers every billing period ("initial search should not
// have any param"); billing period / search / main MP / service-190
// status are all filters applied client-side afterwards.
const WSH_COLUMNS = [
  { key: "reference", label: "Account" },
  { key: "niss", label: "NISS" },
  { key: "id_item_to_bill", label: "ID Item To Bill (stuck)" },
  { key: "id_main_mp", label: "ID Main MP" },
  { key: "description", label: "Billing Period" },
  { key: "id_item_to_bill_190", label: "ID Item To Bill (service 190)" },
  { key: "status_190", label: "Status (service 190)" },
];
let wshRows = [];
let wshSortKey = null;
let wshSortDir = 1;
let wshSelected = new Set(); // stuck ID_ITEM_TO_BILL of each checked row

function wshUpdateSelectionUI() {
  const n = wshSelected.size;
  const partners = wshRows.filter((r) => wshSelected.has(String(r.id_item_to_bill)) && r.status_190 === "STTOBILL09").length;
  $("#wsh-selection-hint").textContent = n
    ? `${n} row(s) selected → ${n} stuck item(s)${partners ? ` + up to ${partners} service-190 item(s) in STTOBILL09` : ""}.`
    : "No rows selected.";
  $("#wsh-generate-btn").disabled = n === 0;
}

function wshVisibleRows() {
  const period = $("#wsh-filter-period").value;
  const q = $("#wsh-filter-search").value.trim().toLowerCase();
  const mainMp = $("#wsh-filter-mainmp").value.trim();
  const st = $("#wsh-filter-status190").value;
  let rows = wshRows.filter((r) => {
    if (period && String(r.id_billing_period) !== period) return false;
    if (q && ![r.reference, r.niss, r.id_item_to_bill, r.id_item_to_bill_190].some((v) => String(v ?? "").toLowerCase().includes(q))) return false;
    if (mainMp && !String(r.id_main_mp ?? "").includes(mainMp)) return false;
    if (st === "__none__" && r.status_190) return false;
    if (st && st !== "__none__" && r.status_190 !== st) return false;
    return true;
  });
  if (wshSortKey) rows = [...rows].sort((a, b) => _hierCompareValues(a[wshSortKey], b[wshSortKey], wshSortDir));
  return rows;
}

function wshRenderTable() {
  const head = document.querySelector("#wsh-table thead tr");
  const rowsForHeader = wshVisibleRows();
  const allChecked = rowsForHeader.length > 0 && rowsForHeader.every((r) => wshSelected.has(String(r.id_item_to_bill)));
  head.innerHTML = `<th><input type="checkbox" id="wsh-select-all" title="Select all visible rows"${allChecked ? " checked" : ""} /></th>` +
    WSH_COLUMNS.map((c) => {
    const arrow = wshSortKey === c.key ? `<span class="stats-table-sort-arrow">${wshSortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th class="stats-table-th-sortable" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  $("#wsh-select-all").addEventListener("change", (ev) => {
    wshVisibleRows().forEach((r) => {
      if (ev.target.checked) wshSelected.add(String(r.id_item_to_bill));
      else wshSelected.delete(String(r.id_item_to_bill));
    });
    wshRenderTable();
  });
  head.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    wshSortDir = wshSortKey === k ? -wshSortDir : 1;
    wshSortKey = k;
    wshRenderTable();
  }));
  const rows = wshVisibleRows();
  document.querySelector("#wsh-table tbody").innerHTML = rows.length
    ? rows.map((r) => {
        const id = String(r.id_item_to_bill);
        return `<tr><td><input type="checkbox" class="wsh-row-check" data-id="${escapeHtml(id)}"${wshSelected.has(id) ? " checked" : ""} /></td>` +
          WSH_COLUMNS.map((c) => {
            const v = r[c.key] ?? "";
            const cls = c.key === "status_190" && !v ? ' class="hint-text"' : "";
            return `<td${cls}>${escapeHtml(c.key === "status_190" && !v ? "(none)" : v)}</td>`;
          }).join("") + "</tr>";
      }).join("")
    : `<tr><td colspan="${WSH_COLUMNS.length + 1}" class="hint-text">${wshRows.length ? "No rows match the current filters." : "No stuck secondaries found."}</td></tr>`;
  renderFilteredCount("#wsh-filtered-count", rows.length, wshRows.length);
  wshUpdateSelectionUI();
  if (wshRows.length) {
    const n = rows.length;
    hxRenderDashboard("wsh-dash", {
      gauges: [
        { label: "Service-190 also stuck", count: rows.filter((r) => r.status_190 === "STTOBILL09").length, total: n, c1: "#f97316", c2: "#ef4444",
          hint: "The account's service-190 item for the same period is also STTOBILL09 (goes in the script too)" },
        { label: "No service-190 item", count: rows.filter((r) => !r.status_190).length, total: n, c1: "#94a3b8", c2: "#64748b" },
        { label: "Selected for script", count: rows.filter((r) => wshSelected.has(String(r.id_item_to_bill))).length, total: n, c1: "#6366f1", c2: "#06b6d4" },
      ],
      tiles: [
        { icon: "🪜", label: "Stuck items", value: new Set(rows.map((r) => r.id_item_to_bill)).size.toLocaleString(), accent: true },
        { icon: "👤", label: "Accounts", value: new Set(rows.map((r) => r.reference)).size.toLocaleString() },
        { icon: "🎯", label: "Main MPs", value: new Set(rows.map((r) => r.id_main_mp)).size.toLocaleString() },
        { icon: "📅", label: "Billing periods", value: new Set(rows.map((r) => r.id_billing_period)).size.toLocaleString() },
      ],
      split: { title: "Billing period", entries: hxCountBy(rows, (r) => r.description) },
    });
  }
}

document.querySelector("#wsh-table tbody").addEventListener("change", (ev) => {
  const cb = ev.target.closest(".wsh-row-check");
  if (!cb) return;
  if (cb.checked) wshSelected.add(cb.dataset.id); else wshSelected.delete(cb.dataset.id);
  wshRenderTable(); // refreshes select-all, the selection hint and the "Selected for script" gauge
});

$("#wsh-generate-btn").addEventListener("click", async () => {
  if (!wshSelected.size) return;
  const program = $("#wsh-program").value.trim();
  if (!program) { showToast("Enter the Jira/Program # this change is for.", true); return; }
  const btn = $("#wsh-generate-btn");
  btn.disabled = true;
  try {
    const result = await api("/api/wrong-stuck-hierarchy/generate", {
      method: "POST",
      body: { item_ids: [...wshSelected], program, audit_user: $("#wsh-audit-user").value.trim(), clean: $("#wsh-clean-toggle").checked },
    });
    $("#wsh-output").textContent = result.sql_text;
    let msg = `Script generated: ${result.item_count} item(s) to bill (${result.stuck_count} stuck + ${result.partner_count} service-190).`;
    if (result.not_found) msg += ` ${result.not_found} selected item(s) are no longer stuck and were skipped.`;
    showToast(msg);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = wshSelected.size === 0;
  }
});
$("#wsh-copy-btn").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#wsh-output").textContent); showToast("Script copied to clipboard."); }
  catch (_) { showToast("Couldn't copy - select and copy manually.", true); }
});
$("#wsh-download-btn").addEventListener("click", () => {
  const blob = new Blob([$("#wsh-output").textContent], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "wrong_stuck_hierarchy_itb_update.sql";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

$("#wsh-detect-btn").addEventListener("click", async () => {
  const btn = $("#wsh-detect-btn");
  btn.disabled = true;
  $("#wsh-summary").textContent = "Scanning all billing periods…";
  try {
    const data = await api("/api/wrong-stuck-hierarchy/detect", { method: "POST", body: {} });
    wshRows = data.rows || [];
    // RJ: "for all ITB that is in status STTOBILL09" - every row starts checked.
    wshSelected = new Set(wshRows.map((r) => String(r.id_item_to_bill)));
    // Billing period filter: newest first, labelled in words.
    const periods = new Map();
    wshRows.forEach((r) => { if (r.id_billing_period && !periods.has(r.id_billing_period)) periods.set(r.id_billing_period, r.description || r.id_billing_period); });
    const pSel = $("#wsh-filter-period");
    const pCur = pSel.value;
    const pIds = [...periods.keys()].sort((a, b) => Number(b) - Number(a));
    pSel.innerHTML = `<option value="">All</option>` + pIds.map((id) => {
      const n = wshRows.filter((r) => r.id_billing_period === id).length;
      return `<option value="${escapeHtml(id)}">${escapeHtml(periods.get(id))} (${n})</option>`;
    }).join("");
    if (pIds.includes(pCur)) pSel.value = pCur;
    const statuses = [...new Set(wshRows.map((r) => r.status_190).filter(Boolean))].sort();
    const hasNone = wshRows.some((r) => !r.status_190);
    const sel = $("#wsh-filter-status190");
    sel.innerHTML = `<option value="">All</option>` +
      statuses.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("") +
      (hasNone ? `<option value="__none__">(none)</option>` : "");
    $("#wsh-filter-row").hidden = wshRows.length === 0;
    $("#wsh-export-btn").disabled = wshRows.length === 0;
    $("#wsh-summary").textContent = `${data.count} stuck item(s) across ${data.period_count} billing period(s), ${data.account_count} account(s), ${data.main_mp_count} main MP(s).`;
    wshRenderTable();
  } catch (err) {
    $("#wsh-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

["#wsh-filter-search", "#wsh-filter-mainmp"].forEach((id) => $(id).addEventListener("input", wshRenderTable));
$("#wsh-filter-status190").addEventListener("change", wshRenderTable);
$("#wsh-filter-period").addEventListener("change", wshRenderTable);
$("#wsh-filter-clear-btn").addEventListener("click", () => {
  $("#wsh-filter-period").value = "";
  $("#wsh-filter-search").value = "";
  $("#wsh-filter-mainmp").value = "";
  $("#wsh-filter-status190").value = "";
  wshRenderTable();
});
$("#wsh-export-btn").addEventListener("click", () => {
  const rows = wshVisibleRows();
  if (!rows.length) return;
  const lines = [WSH_COLUMNS.map((c) => `"${c.label}"`).join(",")];
  rows.forEach((r) => lines.push(WSH_COLUMNS.map((c) => `"${String(r[c.key] ?? "").replace(/"/g, '""')}"`).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "wrong_stuck_hierarchy_itb.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// Wrong Stuck in Hierarchy sub-nav (own data attribute, same reason as
// the Bill Issuance sub-nav comment explains).
$$(".da-subnav-btn[data-wsh-sub]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const sub = btn.dataset.wshSub;
    $$(".da-subnav-btn[data-wsh-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    $$(".da-subpage[data-wsh-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.wshSub === sub));
  });
});

// --- Tab 2: Sanitary stuck, Water billed/pending (RJ, 2026-09-27) ---
const WSHS_COLUMNS = [
  { key: "reference", label: "Account" },
  { key: "description", label: "Billing Period" },
  { key: "billing_date", label: "Billing Date", date: true },
  { key: "sanitary_niss", label: "Sanitary NISS" },
  { key: "id_item_to_bill_sanitary", label: "ID Item To Bill (Sanitary)" },
  { key: "status_sanitary", label: "Status (Sanitary)" },
  { key: "water_niss", label: "Water NISS" },
  { key: "id_item_to_bill_water", label: "ID Item To Bill (Water)" },
  { key: "status_water", label: "Status (Water)" },
];
let wshsRows = [];
let wshsSortKey = null;
let wshsSortDir = 1;

function wshsFmt(c, v) {
  const s = v ?? "";
  return c.date && typeof s === "string" ? s.replace(/ 00:00:00(\.0+)?$/, "") : s;
}

function wshsVisibleRows() {
  const period = $("#wshs-filter-period").value;
  const q = $("#wshs-filter-search").value.trim().toLowerCase();
  const water = $("#wshs-filter-water").value;
  let rows = wshsRows.filter((r) => {
    if (period && String(r.id_billing_period) !== period) return false;
    if (water && r.status_water !== water) return false;
    if (q && ![r.reference, r.sanitary_niss, r.water_niss, r.id_item_to_bill_sanitary, r.id_item_to_bill_water]
      .some((v) => String(v ?? "").toLowerCase().includes(q))) return false;
    return true;
  });
  if (wshsSortKey) rows = [...rows].sort((a, b) => _hierCompareValues(a[wshsSortKey], b[wshsSortKey], wshsSortDir));
  return rows;
}

function wshsRenderTable() {
  const head = document.querySelector("#wshs-table thead tr");
  head.innerHTML = WSHS_COLUMNS.map((c) => {
    const arrow = wshsSortKey === c.key ? `<span class="stats-table-sort-arrow">${wshsSortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th class="stats-table-th-sortable" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  head.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    wshsSortDir = wshsSortKey === k ? -wshsSortDir : 1;
    wshsSortKey = k;
    wshsRenderTable();
  }));
  const rows = wshsVisibleRows();
  document.querySelector("#wshs-table tbody").innerHTML = rows.length
    ? rows.map((r) => "<tr>" + WSHS_COLUMNS.map((c) => `<td>${escapeHtml(wshsFmt(c, r[c.key]))}</td>`).join("") + "</tr>").join("")
    : `<tr><td colspan="${WSHS_COLUMNS.length}" class="hint-text">${wshsRows.length ? "No rows match the current filters." : "No sanitary items stuck while water is billed/pending."}</td></tr>`;
  renderFilteredCount("#wshs-filtered-count", rows.length, wshsRows.length);
  const n = rows.length;
  hxRenderDashboard("wshs-dash", {
    gauges: n ? [
      { label: "Water already billed", count: rows.filter((r) => r.status_water === "STTOBILL07").length, total: n, c1: "#ef4444", c2: "#f97316" },
      { label: "Water pending", count: rows.filter((r) => r.status_water === "STTOBILL01").length, total: n, c1: "#f59e0b", c2: "#eab308" },
    ] : [],
    tiles: [
      { icon: "🚿", label: "Stuck sanitary items", value: new Set(rows.map((r) => r.id_item_to_bill_sanitary)).size.toLocaleString(), accent: true },
      { icon: "👤", label: "Accounts", value: new Set(rows.map((r) => r.reference)).size.toLocaleString() },
      { icon: "📅", label: "Billing periods", value: new Set(rows.map((r) => r.id_billing_period)).size.toLocaleString() },
    ],
  });
}

$("#wshs-detect-btn").addEventListener("click", async () => {
  const btn = $("#wshs-detect-btn");
  btn.disabled = true;
  $("#wshs-summary").textContent = "Scanning all billing periods…";
  try {
    const data = await api("/api/wrong-stuck-hierarchy/sanitary/detect", { method: "POST" });
    wshsRows = data.rows || [];
    const periods = new Map();
    wshsRows.forEach((r) => { if (r.id_billing_period && !periods.has(r.id_billing_period)) periods.set(r.id_billing_period, r.description || r.id_billing_period); });
    const pIds = [...periods.keys()].sort((a, b) => Number(b) - Number(a));
    $("#wshs-filter-period").innerHTML = `<option value="">All</option>` +
      pIds.map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(periods.get(id))}</option>`).join("");
    $("#wshs-filter-row").hidden = wshsRows.length === 0;
    $("#wshs-export-btn").disabled = wshsRows.length === 0;
    $("#wshs-summary").textContent = data.count
      ? `${data.count} sanitary item(s) stuck across ${data.account_count} account(s), ${data.period_count} billing period(s).`
      : "None found — every stuck sanitary item's water item is also still in hierarchy (or has no match).";
    wshsRenderTable();
  } catch (err) {
    $("#wshs-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});
["#wshs-filter-period", "#wshs-filter-water"].forEach((id) => $(id).addEventListener("change", wshsRenderTable));
$("#wshs-filter-search").addEventListener("input", wshsRenderTable);
$("#wshs-filter-clear-btn").addEventListener("click", () => {
  ["#wshs-filter-period", "#wshs-filter-water", "#wshs-filter-search"].forEach((id) => { $(id).value = ""; });
  wshsRenderTable();
});
$("#wshs-export-btn").addEventListener("click", () => {
  const rows = wshsVisibleRows();
  if (!rows.length) return;
  const lines = [WSHS_COLUMNS.map((c) => `"${c.label}"`).join(",")];
  rows.forEach((r) => lines.push(WSHS_COLUMNS.map((c) => `"${String(wshsFmt(c, r[c.key]) ?? "").replace(/"/g, '""')}"`).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "sanitary_stuck_water_billed.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// ---------------- Wrong Billed Consumption (RJ 2026-09-27, reworked 09-28) ----------------
// See app/core/wrong_billed_consumption.py + web/wbc_jobs.py. Bill-centric:
// per bill of the chosen (default: current) billing period, three sources of
// usage are compared - the bill's calculation base (CONCSMO003 + CC210), the
// READINGS_ITEMSTOBILL ready usage of its items, and the GCGT_RE_READING
// ready usage of those readings. The period is scanned in ID_BILL chunks as a
// background job (one query over ~1.7M bills can't fit the 120s timeout);
// the page polls for progress. Filters/sort client-side.
function wbcDiffPill(v, title) {
  const d = Number(v);
  if (!v || Math.abs(d) < 0.001) return `<span class="hint-text">0</span>`;
  return `<span class="hx-pill ${d > 0 ? "hx-pill-billed" : "hx-pill-blue"}" title="${escapeHtml(title)}">${d > 0 ? "+" : ""}${escapeHtml(v)}</span>`;
}
const WBC_COLUMNS = [
  { key: "niss", label: "NISS", render: (r) => `<span class="hx-niss">${escapeHtml(r.niss ?? "")}</span>` },
  { key: "id_bill", label: "Bill", mono: true },
  { key: "usage_type", label: "Usage Type", render: (r) => `<span title="${escapeHtml(r.usage_type ?? "")}">${escapeHtml(r.usage_type_desc || r.usage_type || "")}</span>` },
  { key: "calculation_base", label: "★ Calculation Base", num: true, render: (r) => `<strong class="wbc-billed">${escapeHtml(r.calculation_base ?? "")}</strong>` },
  { key: "rit_ready_usage", label: "★ Ready Usage (Reading ITB)", num: true, render: (r) => `<strong>${wbcMarkDiff(r.rit_ready_usage, r.calculation_base)}</strong>` },
  { key: "reading_ready_usage", label: "★ Ready Usage (RE_READING)", num: true, render: (r) => `<strong>${wbcMarkDiff(r.reading_ready_usage, r.calculation_base)}</strong>` },
  { key: "diff_calc_vs_rit", label: "Calc − ITB", num: true, render: (r) => wbcDiffPill(r.diff_calc_vs_rit, "Calculation base − READINGS_ITEMSTOBILL ready usage") },
  { key: "diff_calc_vs_reading", label: "Calc − Reading", num: true, render: (r) => wbcDiffPill(r.diff_calc_vs_reading, "Calculation base − GCGT_RE_READING ready usage") },
  { key: "diff_rit_vs_reading", label: "ITB − Reading", num: true, render: (r) => wbcDiffPill(r.diff_rit_vs_reading, "READINGS_ITEMSTOBILL − GCGT_RE_READING ready usage") },
  { key: "reading_count", label: "Readings #", num: true },
  { key: "id_readings", label: "Readings", mono: true, render: (r) =>
      `<span title="${escapeHtml((r.reading_details || "").split(" ; ").join("\n"))}">${escapeHtml(r.id_readings || "")}</span>` },
  { key: "id_item_to_bill", label: "Item To Bill", mono: true, render: (r) => escapeHtml(r.id_item_to_bill ?? "") +
      (Number(r.item_to_bill_count) > 1 ? ` <span class="hint-text">(+${Number(r.item_to_bill_count) - 1})</span>` : "") },
  // ---- supporting columns ----
  { key: "bill_status", label: "Bill Status", divider: true, render: (r) => `<span class="hx-pill ${r.bill_status === "ESTFAC0005" ? "hx-pill-green" : "hx-pill-blue"}" title="${escapeHtml(r.bill_status ?? "")}">${escapeHtml(r.bill_status_desc || r.bill_status || "")}</span>` },
  { key: "billing_type", label: "Billing Type", render: (r) => `<span title="${escapeHtml(r.billing_type ?? "")}">${escapeHtml(r.billing_type_desc || r.billing_type || "")}</span>` },
  { key: "id_contracted_service", label: "Contracted Service", mono: true },
  { key: "mp_type", label: "MP Type", render: (r) => `<span class="hx-pill ${r.mp_type === "TIPEQM0001" ? "hx-pill-blue" : "hx-pill-amber"}" title="${escapeHtml(r.mp_types || r.mp_type || "")}">${escapeHtml(r.mp_type_desc || r.mp_type || "—")}</span>` },
  { key: "billing_period", label: "Billing Period" },
  { key: "reading_date", label: "Reading Date", date: true },
];
let wbcJobId = null;
let wbcRows = [];
let wbcSortKey = null;
let wbcSortDir = 1;
let wbcDir = "";
let wbcPeriodsLoaded = false;
// Rendering speed (RJ 2026-09-27: "the rendering is a little bit slow"):
// the table shows one page of WBC_PAGE_SIZE rows at a time instead of up
// to 2,000, and the search box waits for a short pause in typing.
const WBC_PAGE_SIZE = 100;
let wbcPage = 0;
let _wbcSearchTimer = null;

// Highlights a billed start/end value that differs from the reading's own.
function wbcMarkDiff(billed, own) {
  const nb = Number(billed), no = Number(own);
  const same = billed !== "" && billed != null && own !== "" && own != null && !isNaN(nb) && !isNaN(no)
    ? Math.abs(nb - no) < 0.001
    : String(billed ?? "") === String(own ?? "");
  return same ? escapeHtml(billed ?? "") : `<span class="wbc-diff" title="Reading has ${escapeHtml(own ?? "")}">${escapeHtml(billed ?? "")}</span>`;
}

async function wbcOnPageShown() {
  if (wbcPeriodsLoaded) return;
  const sel = $("#wbc-period");
  try {
    const data = await api("/api/wrong-billed-consumption/billing-periods");
    const periods = data.periods || [];
    sel.innerHTML = periods.map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.description || p.id)}${p.is_current ? " (current)" : ""}</option>`).join("");
    // "we start always with the current billing period" (RJ 2026-09-28).
    const cur = periods.find((p) => p.is_current) || periods[1] || periods[0];
    if (cur) sel.value = cur.id;
    wbcPeriodsLoaded = true;
  } catch (err) {
    sel.innerHTML = `<option value="">(couldn't load periods)</option>`;
    showToast(err.message, true);
  }
}

function wbcVisibleRows() {
  const q = $("#wbc-filter-search").value.trim().toLowerCase();
  const usage = $("#wbc-filter-usage").value;
  const status = $("#wbc-filter-status").value;
  // MP type checkboxes (default: Normal only). "" = readings with no MP type.
  const mpTypes = new Set($$("#wbc-filter-mptype input:checked").map((c) => c.value));
  let rows = wbcRows.filter((r) => {
    // A grouped row passes when ANY of its readings' MP types is ticked.
    const rowTypes = r.mp_types != null ? String(r.mp_types).split(",") : [r.mp_type || ""];
    if (!rowTypes.some((t) => mpTypes.has(t.trim()))) return false;
    if (usage && r.usage_type !== usage) return false;
    if (status && r.bill_status !== status) return false;
    if (wbcDir === "over" && !(Number(r.diff_calc_vs_rit) > 0)) return false;
    if (wbcDir === "under" && !(Number(r.diff_calc_vs_rit) < 0)) return false;
    if (wbcDir === "itbreading" && !(Math.abs(Number(r.diff_rit_vs_reading)) >= 0.001)) return false;
    if (q && ![r.niss, r.id_bill, r.id_readings, r.id_item_to_bill].some((v) => String(v ?? "").toLowerCase().includes(q))) return false;
    return true;
  });
  if (wbcSortKey) rows = [...rows].sort((a, b) => _hierCompareValues(a[wbcSortKey], b[wbcSortKey], wbcSortDir));
  return rows;
}

function wbcRenderTable() {
  const head = document.querySelector("#wbc-table thead tr");
  head.innerHTML = WBC_COLUMNS.map((c) => {
    const arrow = wbcSortKey === c.key ? `<span class="stats-table-sort-arrow">${wbcSortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th class="stats-table-th-sortable${c.divider ? " wbc-divider" : ""}" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  head.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    wbcSortDir = wbcSortKey === k ? -wbcSortDir : 1;
    wbcSortKey = k;
    wbcPage = 0;
    wbcRenderTable();
  }));
  const all = wbcVisibleRows();
  const pages = Math.max(1, Math.ceil(all.length / WBC_PAGE_SIZE));
  wbcPage = Math.min(wbcPage, pages - 1);
  const rows = all.slice(wbcPage * WBC_PAGE_SIZE, (wbcPage + 1) * WBC_PAGE_SIZE);
  document.querySelector("#wbc-table tbody").innerHTML = rows.length
    ? rows.map((r) => `<tr class="hx-row ${Number(r.diff_calc_vs_rit) > 0 ? "hx-row-anomalous" : ""}">` +
        WBC_COLUMNS.map((c) => `<td class="${c.num ? "hx-num" : ""}${c.mono ? " hx-mono" : ""}${c.divider ? " wbc-divider" : ""}">${c.render ? c.render(r) : (c.date ? hxDate(r[c.key]) : escapeHtml(r[c.key] ?? ""))}</td>`).join("") +
        "</tr>").join("")
    : `<tr><td colspan="${WBC_COLUMNS.length}" class="hint-text">${wbcRows.length ? "No rows match the current filters." : "No mismatches in this billing period."}</td></tr>`;
  const pager = $("#wbc-pager");
  pager.hidden = pages <= 1;
  pager.innerHTML = pages <= 1 ? "" :
    `<button type="button" class="hx-icon-btn" data-wbc-page="prev" ${wbcPage === 0 ? "disabled" : ""} title="Previous page">‹</button>` +
    `<span>Rows ${(wbcPage * WBC_PAGE_SIZE + 1).toLocaleString()}–${Math.min((wbcPage + 1) * WBC_PAGE_SIZE, all.length).toLocaleString()} of ${all.length.toLocaleString()} · page ${wbcPage + 1} / ${pages}</span>` +
    `<button type="button" class="hx-icon-btn" data-wbc-page="next" ${wbcPage >= pages - 1 ? "disabled" : ""} title="Next page">›</button>`;
  renderFilteredCount("#wbc-filtered-count", all.length, wbcRows.length);
  if (wbcRows.length) {
    const n = all.length;
    const nz = (k) => all.filter((r) => Math.abs(Number(r[k])) >= 0.001);
    hxRenderDashboard("wbc-dash", {
      gauges: [
        { label: "Calc ≠ Reading ITB", count: nz("diff_calc_vs_rit").length, total: n, c1: "#ef4444", c2: "#f97316", hint: "Calculation base differs from READINGS_ITEMSTOBILL ready usage" },
        { label: "Calc ≠ RE_READING", count: nz("diff_calc_vs_reading").length, total: n, c1: "#6366f1", c2: "#06b6d4", hint: "Calculation base differs from GCGT_RE_READING ready usage" },
        { label: "ITB ≠ RE_READING", count: nz("diff_rit_vs_reading").length, total: n, c1: "#f59e0b", c2: "#eab308", hint: "READINGS_ITEMSTOBILL differs from GCGT_RE_READING ready usage" },
      ],
      tiles: [
        { icon: "⚖️", label: "Net Calc − ITB", value: hxSum(all, "diff_calc_vs_rit").toLocaleString(), accent: true },
        { icon: "📐", label: "Net Calc − Reading", value: hxSum(all, "diff_calc_vs_reading").toLocaleString() },
        { icon: "🔗", label: "Net ITB − Reading", value: hxSum(all, "diff_rit_vs_reading").toLocaleString() },
        { icon: "🧾", label: "Bills", value: n.toLocaleString() },
        { icon: "🔌", label: "NISS", value: new Set(all.map((r) => r.niss).filter(Boolean)).size.toLocaleString() },
      ],
      split: { title: "Usage type", entries: hxCountBy(all, (r) => r.usage_type_desc || r.usage_type) },
    });
  }
}

function wbcSetProgress(done, total) {
  const bar = $("#wbc-progress");
  if (!bar) return;
  bar.hidden = total === null;
  const pct = total ? Math.round((done / total) * 100) : 0;
  bar.querySelector(".wbc-progress-fill").style.width = `${pct}%`;
}

async function wbcPollJob(jobId, label) {
  for (;;) {
    await new Promise((r) => setTimeout(r, 2000));
    if (wbcJobId !== jobId) return null; // superseded
    const s = await api(`/api/wrong-billed-consumption/jobs/${encodeURIComponent(jobId)}`);
    wbcSetProgress(s.chunks_done, s.chunks_total || 1);
    $("#wbc-summary").textContent = `Scanning ${label}… chunk ${s.chunks_done}/${s.chunks_total || "?"} · ` +
      `${Number(s.bill_count || 0).toLocaleString()} bills in period · ${Number(s.count || 0).toLocaleString()} mismatch(es) so far`;
    if (s.status !== "running") return s;
  }
}

$("#wbc-cancel-btn")?.addEventListener("click", async () => {
  if (!wbcJobId) return;
  try { await api(`/api/wrong-billed-consumption/jobs/${encodeURIComponent(wbcJobId)}/cancel`, { method: "POST" }); } catch (_) { /* ignore */ }
});

$("#wbc-detect-btn").addEventListener("click", async () => {
  const id = $("#wbc-period").value;
  if (!id) { showToast("Pick a billing period first.", true); return; }
  const btn = $("#wbc-detect-btn");
  btn.disabled = true;
  const cancelBtn = $("#wbc-cancel-btn");
  if (cancelBtn) cancelBtn.hidden = false;
  const label = $("#wbc-period").selectedOptions[0]?.textContent || id;
  $("#wbc-summary").textContent = `Starting scan of ${label}…`;
  wbcSetProgress(0, 1);
  try {
    const start = await api("/api/wrong-billed-consumption/detect", { method: "POST", body: { id_billing_period: id } });
    wbcJobId = start.job_id;
    const data = await wbcPollJob(start.job_id, label);
    if (!data) return;
    if (data.status === "failed") throw new Error(data.error || "Scan failed.");
    wbcRows = data.rows || [];
    wbcPage = 0;
    const opts = (key, descKey) => {
      const m = new Map();
      wbcRows.forEach((r) => { if (r[key] && !m.has(r[key])) m.set(r[key], r[descKey] || r[key]); });
      return `<option value="">All</option>` + [...m.entries()].sort((a, b) => String(a[1]).localeCompare(String(b[1])))
        .map(([v, d]) => `<option value="${escapeHtml(v)}">${escapeHtml(d)}</option>`).join("");
    };
    const usageCur = $("#wbc-filter-usage").value, statusCur = $("#wbc-filter-status").value;
    $("#wbc-filter-usage").innerHTML = opts("usage_type", "usage_type_desc");
    $("#wbc-filter-status").innerHTML = opts("bill_status", "bill_status_desc");
    $("#wbc-filter-usage").value = usageCur; $("#wbc-filter-status").value = statusCur;
    $("#wbc-filters").hidden = wbcRows.length === 0;
    $("#wbc-export-btn").disabled = wbcRows.length === 0;
    $("#wbc-dash").hidden = wbcRows.length === 0;
    $("#wbc-summary").textContent = `${label}: ${data.count.toLocaleString()} bill(s) with a mismatch out of ` +
      `${Number(data.bill_count || 0).toLocaleString()} bills, ${Number(data.supply_count || 0).toLocaleString()} NISS` +
      (data.status === "cancelled" ? ` — cancelled after ${data.chunks_done}/${data.chunks_total} chunks.` : ".") +
      (data.truncated ? " (capped at 50,000 rows)" : "");
    wbcRenderTable();
  } catch (err) {
    $("#wbc-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    if (cancelBtn) cancelBtn.hidden = true;
    wbcSetProgress(0, null);
  }
});
["#wbc-filter-usage", "#wbc-filter-status", "#wbc-filter-mptype"].forEach((id) => $(id).addEventListener("change", () => { wbcPage = 0; wbcRenderTable(); }));
$("#wbc-filter-search").addEventListener("input", () => {
  clearTimeout(_wbcSearchTimer);
  _wbcSearchTimer = setTimeout(() => { wbcPage = 0; wbcRenderTable(); }, 250);
});
$("#wbc-pager").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-wbc-page]");
  if (!b || b.disabled) return;
  wbcPage += b.dataset.wbcPage === "next" ? 1 : -1;
  wbcRenderTable();
  $("#wbc-table").scrollIntoView({ block: "start", behavior: "smooth" });
});
$("#wbc-filter-dir").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-value]");
  if (!b) return;
  wbcPage = 0;
  wbcDir = b.dataset.value;
  $$("#wbc-filter-dir button").forEach((x) => x.classList.toggle("is-active", x === b));
  wbcRenderTable();
});
$("#wbc-filter-clear-btn").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  ["#wbc-filter-usage", "#wbc-filter-status", "#wbc-filter-search"].forEach((id) => { $(id).value = ""; });
  $$("#wbc-filter-mptype input").forEach((c) => { c.checked = c.value === "TIPEQM0001"; });
  wbcDir = "";
  $$("#wbc-filter-dir button").forEach((x) => x.classList.toggle("is-active", x.dataset.value === ""));
  wbcRenderTable();
});
$("#wbc-export-btn").addEventListener("click", () => {
  const rows = wbcVisibleRows();
  if (!rows.length) return;
  const keys = ["niss", "id_bill", "usage_type", "calculation_base", "rit_ready_usage", "reading_ready_usage",
    "diff_calc_vs_rit", "diff_calc_vs_reading", "diff_rit_vs_reading", "reading_count", "id_readings", "reading_details",
    "id_item_to_bill", "item_to_bill_count", "link_rows", "bill_status", "bill_status_desc", "billing_type", "billing_type_desc",
    "id_contracted_service", "mp_type", "mp_types", "mp_type_desc", "id_billing_period", "billing_period", "reading_date"];
  const lines = [keys.join(",")];
  rows.forEach((r) => lines.push(keys.map((k) => `"${String(r[k] ?? "").replace(/ 00:00:00(\.0+)?$/, "").replace(/"/g, '""')}"`).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `wrong_billed_consumption_${$("#wbc-period").value}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// ---------------- Wrong Bill CASE 1 - Unusual high Sanitary (RJ 2026-09-30) ----------------
// See app/core/unusual_sanitary.py + web/alerts.py (daily 7 AM e-mail).
const USAN_COLUMNS = [
  { key: "reference", label: "Account", render: (r) => `<span class="hx-niss">${escapeHtml(r.reference ?? "")}</span>` },
  { key: "niss", label: "NISS", mono: true },
  { key: "total_amount", label: "★ Total Amount", num: true, render: (r) => `<strong class="usan-amount">${escapeHtml(hxFmtNum(r.total_amount))}</strong>` },
  { key: "water_total_amount", label: "Water Bill Amount", num: true, render: (r) => escapeHtml(hxFmtNum(r.water_total_amount)) },
  { key: "sanitary_minus_water", label: "Sanitary − Water", num: true, render: (r) => `<span class="usan-amount">${escapeHtml(hxFmtNum(r.sanitary_minus_water))}</span>` },
  { key: "base", label: "Base (÷0.155)", num: true, render: (r) => escapeHtml(hxFmtNum(r.base)) },
  { key: "id_billing_period", label: "Billing Period", mono: true },
  { key: "id_bill", label: "ID Bill", mono: true },
  { key: "bill_number", label: "Bill Number", mono: true },
  { key: "water_id_bill", label: "Water ID Bill", mono: true, render: (r) => escapeHtml(r.water_id_bill ?? "") + (Number(r.water_bill_count) > 1 ? ` <span class="hx-pill hx-pill-ghost" title="Water bills summed">×${escapeHtml(r.water_bill_count)}</span>` : "") },
  { key: "billing_date", label: "Billing Date", date: true },
  { key: "create_date", label: "Created", date: true },
  { key: "billing_status", label: "Status", render: (r) => `<span class="hx-pill hx-pill-amber">${escapeHtml(r.billing_status ?? "")}</span>` },
  { key: "cod_concept", label: "Concept", mono: true },
  { key: "print_description", label: "Description" },
];
let usanRows = [];
let usanSortKey = null;
let usanSortDir = 1;
let usanStatusLoaded = false;
let usanScanned = false;

function hxFmtNum(v) {
  const n = Number(v);
  if (v === null || v === undefined || v === "" || Number.isNaN(n)) return v ?? "";
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function usanVisibleRows() {
  const q = $("#usan-filter-search").value.trim().toLowerCase();
  const bp = $("#usan-filter-bp").value;
  const st = $("#usan-filter-status").value;
  let rows = usanRows.filter((r) => {
    if (bp && String(r.id_billing_period) !== bp) return false;
    if (st && r.billing_status !== st) return false;
    if (q && ![r.reference, r.niss, r.id_bill, r.bill_number, r.water_id_bill].some((v) => String(v ?? "").toLowerCase().includes(q))) return false;
    return true;
  });
  if (usanSortKey) rows = [...rows].sort((a, b) => _hierCompareValues(a[usanSortKey], b[usanSortKey], usanSortDir));
  return rows;
}

function usanRenderTable() {
  const head = document.querySelector("#usan-table thead tr");
  head.innerHTML = USAN_COLUMNS.map((c) => {
    const arrow = usanSortKey === c.key ? `<span class="stats-table-sort-arrow">${usanSortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th class="stats-table-th-sortable" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  head.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    usanSortDir = usanSortKey === k ? -usanSortDir : 1;
    usanSortKey = k;
    usanRenderTable();
  }));
  const rows = usanVisibleRows();
  document.querySelector("#usan-table tbody").innerHTML = rows.length
    ? rows.map((r) => `<tr class="hx-row hx-row-anomalous">` + USAN_COLUMNS.map((c) =>
        `<td class="${c.num ? "hx-num" : ""}${c.mono ? " hx-mono" : ""}">${c.render ? c.render(r) : c.date ? hxDate(r[c.key]) : escapeHtml(r[c.key] ?? "")}</td>`).join("") + "</tr>").join("")
    : `<tr><td colspan="${USAN_COLUMNS.length}" class="hint-text">${usanRows.length ? "No rows match the current filters." : "✅ No unusual high Sanitary bills found."}</td></tr>`;
  renderFilteredCount("#usan-filtered-count", rows.length, usanRows.length);
  if (usanRows.length) {
    hxRenderDashboard("usan-dash", {
      gauges: [],
      tiles: [
        { icon: "🚨", label: "Bills to rebill", value: new Set(rows.map((r) => r.id_bill)).size.toLocaleString(), accent: true },
        { icon: "💰", label: "Total amount", value: hxFmtNum(hxSum(rows, "total_amount")) },
        { icon: "👤", label: "Accounts", value: new Set(rows.map((r) => r.reference).filter(Boolean)).size.toLocaleString() },
        { icon: "🔌", label: "NISS", value: new Set(rows.map((r) => r.niss).filter(Boolean)).size.toLocaleString() },
        { icon: "📅", label: "Billing periods", value: new Set(rows.map((r) => r.id_billing_period).filter(Boolean)).size.toLocaleString() },
      ],
      split: { title: "Bill status", entries: hxCountBy(rows, (r) => r.billing_status) },
    });
  }
}

async function usanScan() {
  const btn = $("#usan-detect-btn");
  usanScanned = true;
  btn.disabled = true;
  $("#usan-summary").textContent = "Scanning…";
  try {
    const data = await api("/api/unusual-sanitary/detect", { method: "POST" });
    usanRows = data.rows || [];
    const keepBp = $("#usan-filter-bp").value, keepSt = $("#usan-filter-status").value;
    $("#usan-filter-bp").innerHTML = `<option value="">All</option>` +
      [...new Set(usanRows.map((r) => String(r.id_billing_period)))].sort().reverse().map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    $("#usan-filter-status").innerHTML = `<option value="">All</option>` +
      [...new Set(usanRows.map((r) => r.billing_status).filter(Boolean))].sort().map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    $("#usan-filter-bp").value = keepBp; $("#usan-filter-status").value = keepSt;
    $("#usan-filters").hidden = usanRows.length === 0;
    $("#usan-dash").hidden = usanRows.length === 0;
    $("#usan-export-btn").disabled = usanRows.length === 0;
    $("#usan-summary").textContent = usanRows.length
      ? `🚨 ${data.bill_count} bill(s) on ${data.account_count} account(s) — review and apply rebilling ASAP.`
      : "✅ No unusual high Sanitary bills found.";
    wbSetTabCount("case1", data.bill_count);
    usanRenderTable();
  } catch (err) {
    wbSetTabCount("case1", null);
    $("#usan-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

const USAN_STATUS_LABELS = { sent: "✅ Sent", no_cases: "— No cases", error: "⚠️ Error", already_sent: "Already sent" };
async function usanLoadAlertStatus() {
  try {
    const s = await api("/api/alerts/unusual-sanitary/status");
    const parts = [];
    if (!s.configured) parts.push("⚠️ E-mail is NOT configured yet — an admin must fill in Settings › Email alerts.");
    else if (!s.enabled) parts.push("⏸ Daily alert is disabled in Settings › Email alerts.");
    else parts.push(`Daily from ${String(s.send_hour).padStart(2, "0")}:00, retrying every ${s.retry_minutes} min until sent.`);
    parts.push(s.sent_today ? "Today's e-mail: ✅ sent." : "Today's e-mail: not sent yet.");
    parts.push(`Recipients: ${(s.recipients || []).join(", ") || "—"}`);
    $("#usan-alert-summary").textContent = parts.join("  ·  ");
    const log = s.log || [];
    document.querySelector("#usan-alert-log tbody").innerHTML = log.length
      ? log.map((l) => `<tr><td class="hx-mono">${escapeHtml(l.attempted_at)}</td><td>${escapeHtml(USAN_STATUS_LABELS[l.status] || l.status)}</td>` +
          `<td class="hx-num">${escapeHtml(l.row_count ?? "")}</td><td>${escapeHtml(l.detail ?? "")}</td></tr>`).join("")
      : `<tr><td colspan="4" class="hint-text">No checks yet.</td></tr>`;
    usanStatusLoaded = true;
  } catch (err) {
    $("#usan-alert-summary").textContent = `Couldn't load alert status: ${err.message}`;
  }
}

// ---- Wrong Bill menu: case tabs with a count badge each (RJ 2026-09-30:
// "WRONG Bill will be the menu and case 1 is a tab inside, will add more
// tabs later ... show how many for each tab"). To add a case: a
// .da-subnav-btn[data-wb-sub=caseN] with a [data-wb-count=caseN] badge, a
// .da-subpage[data-wb-sub=caseN], an entry in WB_TABS, and HX_REFRESH.wrongbill.
const WB_TABS = {
  case1: { scan: () => usanScan(), loaded: () => usanScanned, onShow: () => usanLoadAlertStatus() },
  case2: { scan: () => wbpdScan(), loaded: () => wbpdScanned },
  case3: { scan: () => wbszScan(), loaded: () => wbszScanned, onShow: () => wbszLoadPeriods() },
  case4: { scan: () => wbfrScan(), loaded: () => wbfrScanned, onShow: () => wbfrLoadPeriods() },
};
function wbSetTabCount(sub, n) {
  const el = document.querySelector(`[data-wb-count="${sub}"]`);
  if (!el) return;
  el.textContent = n === null || n === undefined ? "!" : String(n);
  el.classList.toggle("is-alarm", Number(n) > 0);
  el.classList.toggle("is-zero", Number(n) === 0);
}
function wbOnPageShown() {
  // Fill every tab's count on first visit, so all badges show without opening each tab.
  Object.entries(WB_TABS).forEach(([sub, t]) => {
    t.onShow && t.onShow();
    if (!t.loaded()) t.scan();
  });
}
$$(".da-subnav-btn[data-wb-sub]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const sub = btn.dataset.wbSub;
    $$(".da-subnav-btn[data-wb-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    $$(".da-subpage[data-wb-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.wbSub === sub));
    const t = WB_TABS[sub];
    if (t && t.onShow) t.onShow();
  });
});

// ---- Wrong Bill Case 2: % distribution primary with a metered secondary (RJ 2026-10-01)
// See app/core/wrong_bill_perc_dist.py. One row per primary; secondaries
// render only when expanded (984 primaries / ~6k secondaries live).
let wbpdPrimaries = [];
let wbpdScanned = false;
let wbpdSortKey = null;
let wbpdSortDir = 1;
let wbpdPerc = "";
let wbpdPdev = "";
const wbpdOpen = new Set();
const WBPD_COLUMNS = [
  { key: "_toggle", label: "", nosort: true },
  { key: "p_id_mp", label: "Primary MP", mono: true },
  { key: "p_niss", label: "NISS", render: (p) => `<span class="hx-niss">${escapeHtml(p.p_niss ?? "")}</span>` + wbpdCopyBtn(p.p_niss, "Copy main sector supply (NISS)") },
  { key: "p_account", label: "Account", mono: true, render: (p) => escapeHtml(p.p_account ?? "") + wbpdCopyBtn(p.p_account, "Copy account number") },
  { key: "metered_count", label: "★ Metered secondaries", num: true, render: (p) => `<span class="hx-pill hx-pill-billed">${p.metered_count}</span>` },
  { key: "secondary_count", label: "Active secondaries", num: true },
  { key: "metered_types", label: "Metered type", render: (p) => (p.metered_types || []).map((t) => `<span class="hx-pill hx-pill-amber">${escapeHtml(t)}</span>`).join(" ") },
  { key: "perc_dist_sum", label: "Σ % (primary + secondaries)", num: true, render: (p) => {
      const ok = Math.abs(Number(p.perc_dist_sum) - 100) < 0.05;
      return `<span class="${ok ? "" : "usan-amount"}">${escapeHtml(String(p.perc_dist_sum))}</span>`; } },
  { key: "p_perc_dist", label: "Primary %", num: true },
  { key: "p_id_device", label: "Primary device", mono: true },
  { key: "p_mp_type_desc", label: "Primary type" },
  { key: "p_status_desc", label: "Primary status" },
];
const WBPD_SEC_COLUMNS = [
  ["id_mp", "Secondary MP"], ["niss", "NISS"], ["mp_type_desc", "Type"], ["status_desc", "Status"],
  ["perc_dist", "%"], ["ind_dist_ppal", "IND_DIST_PPAL"], ["id_device", "ID Device"], ["serial_num", "Serial"],
  ["installation_date", "Installed"], ["calc_module", "Calc module"],
];

// RJ 2026-10-01: "add a way to copy the main sector supply or account".
function wbpdCopyBtn(value, title) {
  if (!value) return "";
  return ` <button type="button" class="biss2-copy-btn" data-wbpd-copy="${escapeHtml(value)}" title="${escapeHtml(title)}">📋</button>`;
}

function wbpdIsPerc100(p) { return Math.abs(Number(p.perc_dist_sum) - 100) < 0.05; }

function wbpdVisible() {
  const q = $("#wbpd-filter-search").value.trim().toLowerCase();
  const ps = $("#wbpd-filter-pstatus").value;
  const st = $("#wbpd-filter-stype").value;
  let rows = wbpdPrimaries.filter((p) => {
    if (ps && p.p_status_desc !== ps) return false;
    if (st && !(p.metered_types || []).includes(st)) return false;
    if (wbpdPerc === "100" && !wbpdIsPerc100(p)) return false;
    if (wbpdPerc === "not100" && wbpdIsPerc100(p)) return false;
    if (wbpdPdev === "1" && !p.p_id_device) return false;
    if (wbpdPdev === "0" && p.p_id_device) return false;
    if (q) {
      const hay = [p.p_id_mp, p.p_niss, p.p_account, p.p_id_device]
        .concat(...(p.secondaries || []).map((s) => [s.id_mp, s.niss, s.id_device, s.serial_num]));
      if (!hay.some((v) => String(v ?? "").toLowerCase().includes(q))) return false;
    }
    return true;
  });
  if (wbpdSortKey) rows = [...rows].sort((a, b) => _hierCompareValues(
    Array.isArray(a[wbpdSortKey]) ? a[wbpdSortKey].join(",") : a[wbpdSortKey],
    Array.isArray(b[wbpdSortKey]) ? b[wbpdSortKey].join(",") : b[wbpdSortKey], wbpdSortDir));
  return rows;
}

function wbpdDetailHtml(p) {
  const head = WBPD_SEC_COLUMNS.map(([, l]) => `<th>${escapeHtml(l)}</th>`).join("");
  const body = (p.secondaries || []).map((s) => {
    const metered = String(s.has_device) === "1";
    return `<tr class="${metered ? "wbpd-sec-metered" : ""}">` + WBPD_SEC_COLUMNS.map(([k]) => {
      let v = s[k] ?? "";
      if (k === "installation_date") return `<td>${hxDate(v)}</td>`;
      if (k === "id_device" && metered) return `<td class="hx-mono"><strong>${escapeHtml(v)}</strong> <span class="hx-pill hx-pill-billed">device</span></td>`;
      return `<td class="${["id_mp", "id_device", "serial_num"].includes(k) ? "hx-mono" : ""}">${escapeHtml(v)}</td>`;
    }).join("") + "</tr>";
  }).join("");
  return `<tr class="wbpd-detail-row"><td colspan="${WBPD_COLUMNS.length}"><div class="wbpd-detail"><table class="data-grid wbpd-sec-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></td></tr>`;
}

function wbpdRender() {
  const head = document.querySelector("#wbpd-table thead tr");
  head.innerHTML = WBPD_COLUMNS.map((c) => {
    if (c.nosort) return `<th></th>`;
    const arrow = wbpdSortKey === c.key ? `<span class="stats-table-sort-arrow">${wbpdSortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th class="stats-table-th-sortable" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  head.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    wbpdSortDir = wbpdSortKey === k ? -wbpdSortDir : 1;
    wbpdSortKey = k;
    wbpdRender();
  }));
  const rows = wbpdVisible();
  document.querySelector("#wbpd-table tbody").innerHTML = rows.length
    ? rows.map((p) => {
        const open = wbpdOpen.has(String(p.p_id_mp));
        const main = `<tr class="hx-row wbpd-main${open ? " is-open" : ""}" data-wbpd-mp="${escapeHtml(p.p_id_mp)}">` + WBPD_COLUMNS.map((c) => {
          if (c.key === "_toggle") return `<td class="wbpd-toggle">${open ? "▾" : "▸"}</td>`;
          const v = c.render ? c.render(p) : escapeHtml(p[c.key] ?? "");
          return `<td class="${c.num ? "hx-num" : ""}${c.mono ? " hx-mono" : ""}">${v}</td>`;
        }).join("") + "</tr>";
        return open ? main + wbpdDetailHtml(p) : main;
      }).join("")
    : `<tr><td colspan="${WBPD_COLUMNS.length}" class="hint-text">${wbpdPrimaries.length ? "No primaries match the current filters." : "✅ No % distribution primary with a metered secondary."}</td></tr>`;
  renderFilteredCount("#wbpd-filtered-count", rows.length, wbpdPrimaries.length);
  if (wbpdPrimaries.length) {
    const n = rows.length;
    const metered = rows.reduce((a, p) => a + p.metered_count, 0);
    const meteredSecs = rows.flatMap((p) => (p.secondaries || []).filter((s) => String(s.has_device) === "1"));
    const discon = rows.filter((p) => (p.secondaries || []).some((s) => String(s.has_device) === "1" && s.status === "2000STAMPO")).length;
    hxRenderDashboard("wbpd-dash", {
      gauges: [
        { label: "Σ % ≠ 100", count: rows.filter((p) => !wbpdIsPerc100(p)).length, total: n, c1: "#ef4444", c2: "#f97316",
          hint: "Primary + secondaries' PERC_DIST does not add up to 100" },
        { label: "Primary has a device", count: rows.filter((p) => p.p_id_device).length, total: n, c1: "#6366f1", c2: "#06b6d4" },
        { label: "Metered secondary disconnected", count: discon, total: n, c1: "#f59e0b", c2: "#eab308",
          hint: "At least one metered secondary has status Disconnected" },
      ],
      tiles: [
        { icon: "🏢", label: "Primaries", value: n.toLocaleString(), accent: true },
        { icon: "🔌", label: "Metered secondaries", value: metered.toLocaleString() },
        { icon: "🔗", label: "Non-inactive secondaries", value: rows.reduce((a, p) => a + p.secondary_count, 0).toLocaleString() },
        { icon: "👤", label: "Accounts", value: new Set(rows.map((p) => p.p_account).filter(Boolean)).size.toLocaleString() },
      ],
      split: { title: "Metered secondary status", entries: hxCountBy(meteredSecs, (s) => s.status_desc) },
    });
  }
}

async function wbpdScan() {
  const btn = $("#wbpd-detect-btn");
  wbpdScanned = true;
  btn.disabled = true;
  $("#wbpd-summary").textContent = "Scanning… (about 10–15 s)";
  try {
    const data = await api("/api/wrong-bill/perc-dist/detect", { method: "POST" });
    wbpdPrimaries = data.primaries || [];
    const keepPs = $("#wbpd-filter-pstatus").value, keepSt = $("#wbpd-filter-stype").value;
    $("#wbpd-filter-pstatus").innerHTML = `<option value="">All</option>` +
      [...new Set(wbpdPrimaries.map((p) => p.p_status_desc).filter(Boolean))].sort().map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    $("#wbpd-filter-stype").innerHTML = `<option value="">All</option>` +
      [...new Set(wbpdPrimaries.flatMap((p) => p.metered_types || []).filter(Boolean))].sort().map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    $("#wbpd-filter-pstatus").value = keepPs; $("#wbpd-filter-stype").value = keepSt;
    $("#wbpd-filters").hidden = wbpdPrimaries.length === 0;
    $("#wbpd-dash").hidden = wbpdPrimaries.length === 0;
    $("#wbpd-export-btn").disabled = wbpdPrimaries.length === 0;
    $("#wbpd-expand-btn").disabled = wbpdPrimaries.length === 0;
    $("#wbpd-copy-niss-btn").disabled = wbpdPrimaries.length === 0;
    $("#wbpd-copy-acct-btn").disabled = wbpdPrimaries.length === 0;
    $("#wbpd-summary").textContent = wbpdPrimaries.length
      ? `${data.primary_count} primary(ies) with ${data.metered_secondary_count} metered secondary(ies).`
      : "✅ None found.";
    wbSetTabCount("case2", data.primary_count);
    wbpdRender();
  } catch (err) {
    wbSetTabCount("case2", null);
    $("#wbpd-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

$("#wbpd-detect-btn").addEventListener("click", wbpdScan);
document.querySelector("#wbpd-table tbody").addEventListener("click", (ev) => {
  const copyBtn = ev.target.closest("[data-wbpd-copy]");
  if (copyBtn) { ev.stopPropagation(); biss2CopyAccount(copyBtn.dataset.wbpdCopy, copyBtn); return; }
  const tr = ev.target.closest("tr.wbpd-main");
  if (!tr || ev.target.closest("a, button")) return;
  const id = tr.dataset.wbpdMp;
  if (wbpdOpen.has(id)) wbpdOpen.delete(id); else wbpdOpen.add(id);
  wbpdRender();
});
$("#wbpd-expand-btn").addEventListener("click", () => {
  const vis = wbpdVisible();
  const allOpen = vis.length && vis.every((p) => wbpdOpen.has(String(p.p_id_mp)));
  if (allOpen) vis.forEach((p) => wbpdOpen.delete(String(p.p_id_mp)));
  else vis.forEach((p) => wbpdOpen.add(String(p.p_id_mp)));
  $("#wbpd-expand-btn").textContent = allOpen ? "⊞ Expand all" : "⊟ Collapse all";
  wbpdRender();
});
[["#wbpd-copy-niss-btn", "p_niss", "NISS"], ["#wbpd-copy-acct-btn", "p_account", "account(s)"]].forEach(([id, key, what]) => {
  $(id).addEventListener("click", () => {
    const vals = [...new Set(wbpdVisible().map((p) => p[key]).filter(Boolean))];
    if (!vals.length) return;
    biss2CopyAccount(vals.join("\n"), null);
    showToast(`Copied ${vals.length} ${what} (one per line).`);
  });
});
["#wbpd-filter-pstatus", "#wbpd-filter-stype"].forEach((id) => $(id).addEventListener("change", wbpdRender));
$("#wbpd-filter-search").addEventListener("input", wbpdRender);
[["#wbpd-filter-perc", (v) => { wbpdPerc = v; }], ["#wbpd-filter-pdev", (v) => { wbpdPdev = v; }]].forEach(([id, set]) => {
  $(id).addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-value]");
    if (!b) return;
    set(b.dataset.value);
    $$(`${id} button`).forEach((x) => x.classList.toggle("is-active", x === b));
    wbpdRender();
  });
});
$("#wbpd-filter-clear-btn").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  ["#wbpd-filter-pstatus", "#wbpd-filter-stype", "#wbpd-filter-search"].forEach((id) => { $(id).value = ""; });
  wbpdPerc = ""; wbpdPdev = "";
  ["#wbpd-filter-perc", "#wbpd-filter-pdev"].forEach((id) => $$(`${id} button`).forEach((x) => x.classList.toggle("is-active", x.dataset.value === "")));
  wbpdRender();
});
$("#wbpd-export-btn").addEventListener("click", () => {
  const rows = wbpdVisible();
  if (!rows.length) return;
  const pCols = ["p_id_mp", "p_niss", "p_account", "p_mp_type_desc", "p_status_desc", "p_perc_dist", "p_id_device", "metered_count", "secondary_count", "perc_dist_sum"];
  const sCols = WBPD_SEC_COLUMNS.map(([k]) => k).concat("has_device");
  const lines = [pCols.concat(sCols.map((k) => "s_" + k)).map((c) => `"${c.toUpperCase()}"`).join(",")];
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  rows.forEach((p) => (p.secondaries || []).forEach((s) =>
    lines.push(pCols.map((k) => esc(p[k])).concat(sCols.map((k) => esc(s[k]))).join(","))));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `wrong_bill_case2_perc_dist_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// ---- Wrong Bill Case 3: Sanitary 0 while water consumed (RJ 2026-10-01)
// See app/core/wrong_bill_sanitary_zero.py.
let wbszRows = [];
let wbszScanned = false;
let wbszPeriodsLoaded = false;
let wbszSortKey = null;
let wbszSortDir = 1;
let wbszConcept = "";
const WBSZ_COLUMNS = [
  { key: "reference", label: "Account", render: (r) => `<span class="hx-niss">${escapeHtml(r.reference ?? "")}</span>` + wbpdCopyBtn(r.reference, "Copy account number") },
  { key: "niss", label: "NISS", mono: true, render: (r) => escapeHtml(r.niss ?? "") + wbpdCopyBtn(r.niss, "Copy NISS") },
  { key: "water_consumption", label: "★ Water consumption", num: true, render: (r) => `<strong>${escapeHtml(hxFmtNum(r.water_consumption))}</strong>` },
  { key: "sanitary_amount", label: "Sanitary amount", num: true, render: (r) => `<span class="usan-amount">${escapeHtml(hxFmtNum(r.sanitary_amount))}</span>` },
  { key: "sanitary_concept_count", label: "Sanitary concept", render: (r) => Number(r.sanitary_concept_count) > 0
      ? `<span class="hx-pill hx-pill-amber">Present, 0</span>` : `<span class="hx-pill hx-pill-billed">Missing</span>` },
  { key: "billing_period_desc", label: "Billing period" },
  { key: "billing_date", label: "Billing date", date: true },
  { key: "create_date", label: "Created", date: true },
  { key: "sanitary_id_bill", label: "Sanitary ID Bill", mono: true },
  { key: "sanitary_bill_number", label: "Bill number", mono: true },
  { key: "billing_status_desc", label: "Status" },
  { key: "billing_type_desc", label: "Billing type", render: (r) => `${escapeHtml(r.billing_type_desc ?? "")} <span class="hint-text">${escapeHtml(r.billing_type ?? "")}</span>` },
  { key: "fare_name", label: "Tariff (billed)", render: (r) => `${escapeHtml(r.fare_name ?? "")} <span class="hint-text" title="${escapeHtml(r.fare_source ?? "")}">${escapeHtml(r.id_fare ?? "")}</span>` +
      (r.billing_service_fare && String(r.billing_service_fare) !== String(r.id_fare) ? ` <span class="hx-pill hx-pill-amber" title="Billing service fare today">BS ${escapeHtml(r.billing_service_fare)}</span>` : "") },
  { key: "id_billing_service", label: "Billing service", mono: true },
  { key: "sanitary_total_amount", label: "Sanitary bill total", num: true, render: (r) => escapeHtml(hxFmtNum(r.sanitary_total_amount)) },
  { key: "water_id_bill", label: "Water ID Bill", mono: true, render: (r) => escapeHtml(r.water_id_bill ?? "") + (Number(r.water_bill_count) > 1 ? ` <span class="hx-pill hx-pill-ghost">×${escapeHtml(r.water_bill_count)}</span>` : "") },
];

async function wbszLoadPeriods() {
  if (wbszPeriodsLoaded) return;
  try {
    const d = await api("/api/wrong-billed-consumption/billing-periods");
    // RJ 2026-10-01: "the selection of id billing period to be by checkbox"
    $("#wbsz-period").innerHTML = (d.periods || []).map((p) =>
      `<label class="${p.is_current ? "is-current" : ""}"><input type="checkbox" value="${escapeHtml(p.id)}" data-desc="${escapeHtml(p.description)}"${p.is_current ? " checked" : ""} />` +
      `${escapeHtml(p.description)}${p.is_current ? " (current)" : ""}</label>`).join("") || `<span class="hint-text">No periods.</span>`;
    wbszPeriodsLoaded = true;
    wbszScopeHint();
  } catch { /* nothing selected = server uses the current period */ }
}

function wbszSelectedPeriods() {
  return [...document.querySelectorAll("#wbsz-period input[type=checkbox]:checked")].map((o) => o.value).filter(Boolean);
}

function wbszScopeHint() {
  const n = wbszSelectedPeriods().length;
  const f = $("#wbsz-date-from").value, t = $("#wbsz-date-to").value;
  const parts = [];
  if (n) parts.push(`${n} billing period(s)`);
  if (f && t) parts.push(`created ${f} → ${t}`);
  $("#wbsz-scope-hint").textContent = parts.length ? `Scope: ${parts.join(" AND ")}` : "Scope: current billing period";
}

function wbszVisible() {
  const q = $("#wbsz-filter-search").value.trim().toLowerCase();
  const st = $("#wbsz-filter-status").value;
  const bd = $("#wbsz-filter-bdate").value;
  let rows = wbszRows.filter((r) => {
    if (st && r.billing_status_desc !== st) return false;
    if (bd && String(r.billing_date).slice(0, 10) !== bd) return false;
    if (wbszConcept === "zero" && !(Number(r.sanitary_concept_count) > 0)) return false;
    if (wbszConcept === "missing" && Number(r.sanitary_concept_count) > 0) return false;
    if (q && ![r.reference, r.niss, r.sanitary_id_bill, r.sanitary_bill_number, r.water_id_bill, r.id_billing_service]
      .some((v) => String(v ?? "").toLowerCase().includes(q))) return false;
    return true;
  });
  if (wbszSortKey) rows = [...rows].sort((a, b) => _hierCompareValues(a[wbszSortKey], b[wbszSortKey], wbszSortDir));
  return rows;
}

function wbszRender() {
  const head = document.querySelector("#wbsz-table thead tr");
  head.innerHTML = WBSZ_COLUMNS.map((c) => {
    const arrow = wbszSortKey === c.key ? `<span class="stats-table-sort-arrow">${wbszSortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th class="stats-table-th-sortable" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  head.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    wbszSortDir = wbszSortKey === k ? -wbszSortDir : 1;
    wbszSortKey = k;
    wbszRender();
  }));
  const rows = wbszVisible();
  document.querySelector("#wbsz-table tbody").innerHTML = rows.length
    ? rows.map((r) => `<tr class="hx-row hx-row-anomalous">` + WBSZ_COLUMNS.map((c) =>
        `<td class="${c.num ? "hx-num" : ""}${c.mono ? " hx-mono" : ""}">${c.render ? c.render(r) : c.date ? hxDate(r[c.key]) : escapeHtml(r[c.key] ?? "")}</td>`).join("") + "</tr>").join("")
    : `<tr><td colspan="${WBSZ_COLUMNS.length}" class="hint-text">${wbszRows.length ? "No rows match the current filters." : "✅ No sanitary bill at 0 with water consumption."}</td></tr>`;
  renderFilteredCount("#wbsz-filtered-count", rows.length, wbszRows.length);
  if (wbszRows.length) {
    const n = rows.length;
    hxRenderDashboard("wbsz-dash", {
      gauges: [
        { label: "Sanitary concept missing", count: rows.filter((r) => !(Number(r.sanitary_concept_count) > 0)).length, total: n, c1: "#ef4444", c2: "#f97316",
          hint: "No SANITARY concept on the bill at all" },
        { label: "Sanitary concept present, 0", count: rows.filter((r) => Number(r.sanitary_concept_count) > 0).length, total: n, c1: "#f59e0b", c2: "#eab308" },
        { label: "Already invoiced", count: rows.filter((r) => r.billing_status === "ESTFAC0005").length, total: n, c1: "#6366f1", c2: "#06b6d4",
          hint: "Sanitary bill status Invoiced (ESTFAC0005) - needs rebilling" },
      ],
      tiles: [
        { icon: "🚨", label: "Sanitary bills", value: n.toLocaleString(), accent: true },
        { icon: "💧", label: "Water consumption (sum)", value: hxFmtNum(hxSum(rows, "water_consumption")) },
        { icon: "👤", label: "Accounts", value: new Set(rows.map((r) => r.reference).filter(Boolean)).size.toLocaleString() },
        { icon: "📅", label: "Billing dates", value: new Set(rows.map((r) => String(r.billing_date).slice(0, 10))).size.toLocaleString() },
      ],
      split: { title: "Bill status", entries: hxCountBy(rows, (r) => r.billing_status_desc) },
    });
  }
}

async function wbszScan() {
  const btn = $("#wbsz-detect-btn");
  wbszScanned = true;
  btn.disabled = true;
  $("#wbsz-summary").textContent = "Scanning…";
  try {
    await wbszLoadPeriods();
    const f = $("#wbsz-date-from").value, t = $("#wbsz-date-to").value;
    if ((f && !t) || (!f && t)) throw new Error("Give both creation dates (from and to), or clear them.");
    const body = { billing_periods: wbszSelectedPeriods(), date_from: f || null, date_to: t || null };
    const data = await api("/api/wrong-bill/sanitary-zero/detect", { method: "POST", body });
    wbszRows = data.rows || [];
    const keepSt = $("#wbsz-filter-status").value, keepBd = $("#wbsz-filter-bdate").value;
    $("#wbsz-filter-status").innerHTML = `<option value="">All</option>` +
      [...new Set(wbszRows.map((r) => r.billing_status_desc).filter(Boolean))].sort().map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    $("#wbsz-filter-bdate").innerHTML = `<option value="">All</option>` +
      [...new Set(wbszRows.map((r) => String(r.billing_date).slice(0, 10)))].sort().reverse().map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    $("#wbsz-filter-status").value = keepSt; $("#wbsz-filter-bdate").value = keepBd;
    $("#wbsz-filters").hidden = wbszRows.length === 0;
    $("#wbsz-dash").hidden = wbszRows.length === 0;
    $("#wbsz-export-btn").disabled = wbszRows.length === 0;
    $("#wbsz-copy-acct-btn").disabled = wbszRows.length === 0;
    const pNames = (data.scope.billing_periods || []).map((id) =>
      document.querySelector(`#wbsz-period input[value="${id}"]`)?.dataset.desc || id);
    const scopeTxt = [pNames.length ? pNames.join(", ") : "",
      data.scope.date_from ? `created ${data.scope.date_from} → ${data.scope.date_to}` : ""].filter(Boolean).join(" · ");
    $("#wbsz-summary").textContent = wbszRows.length
      ? `🚨 ${data.count} sanitary bill(s) on ${data.account_count} account(s) — ${scopeTxt}.`
      : `✅ None found — ${scopeTxt}.`;
    wbSetTabCount("case3", data.count);
    wbszRender();
  } catch (err) {
    wbSetTabCount("case3", null);
    $("#wbsz-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

$("#wbsz-detect-btn").addEventListener("click", wbszScan);
["#wbsz-period", "#wbsz-date-from", "#wbsz-date-to"].forEach((id) => $(id).addEventListener("change", wbszScopeHint));
$("#wbsz-period").addEventListener("change", (ev) => {
  if (ev.target.matches("input[type=checkbox]") && ev.target.checked && wbszSelectedPeriods().length > 12) {
    ev.target.checked = false;
    showToast("Pick at most 12 billing periods.", true);
    wbszScopeHint();
  }
});
$("#wbsz-period-none-btn").addEventListener("click", () => {
  document.querySelectorAll("#wbsz-period input[type=checkbox]").forEach((c) => { c.checked = false; });
  wbszScopeHint();
});
$("#wbsz-dates-clear-btn").addEventListener("click", () => {
  $("#wbsz-date-from").value = ""; $("#wbsz-date-to").value = "";
  wbszScopeHint();
});
$("#wbsz-filter-concept").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-value]");
  if (!b) return;
  wbszConcept = b.dataset.value;
  $$("#wbsz-filter-concept button").forEach((x) => x.classList.toggle("is-active", x === b));
  wbszRender();
});
["#wbsz-filter-status", "#wbsz-filter-bdate"].forEach((id) => $(id).addEventListener("change", wbszRender));
$("#wbsz-filter-search").addEventListener("input", wbszRender);
$("#wbsz-filter-clear-btn").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  ["#wbsz-filter-status", "#wbsz-filter-bdate", "#wbsz-filter-search"].forEach((id) => { $(id).value = ""; });
  wbszConcept = "";
  $$("#wbsz-filter-concept button").forEach((x) => x.classList.toggle("is-active", x.dataset.value === ""));
  wbszRender();
});
document.querySelector("#wbsz-table tbody").addEventListener("click", (ev) => {
  const copyBtn = ev.target.closest("[data-wbpd-copy]");
  if (copyBtn) { ev.stopPropagation(); biss2CopyAccount(copyBtn.dataset.wbpdCopy, copyBtn); }
});
$("#wbsz-copy-acct-btn").addEventListener("click", () => {
  const vals = [...new Set(wbszVisible().map((r) => r.reference).filter(Boolean))];
  if (!vals.length) return;
  biss2CopyAccount(vals.join("\n"), null);
  showToast(`Copied ${vals.length} account(s) (one per line).`);
});
$("#wbsz-export-btn").addEventListener("click", () => {
  const rows = wbszVisible();
  if (!rows.length) return;
  const cols = ["reference", "niss", "id_billing_period", "billing_period_desc", "billing_date", "create_date", "sanitary_id_bill", "sanitary_bill_number",
    "billing_status", "billing_status_desc", "billing_type", "billing_type_desc", "id_billing_service", "id_fare", "fare_name", "fare_source", "billing_service_fare", "sanitary_amount", "sanitary_concept_count",
    "sanitary_total_amount", "water_id_bill", "water_bill_count", "water_consumption"];
  const lines = [cols.map((c) => `"${c.toUpperCase()}"`).join(",")];
  rows.forEach((r) => lines.push(cols.map((k) => `"${String(r[k] ?? "").replace(/"/g, '""')}"`).join(",")));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `wrong_bill_case3_sanitary_zero_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// ---- Wrong Bill Case 4: First bill regularized (RJ 2026-10-02)
// See app/core/wrong_bill_first_regularized.py. Summary per billing period
// (click a period = drill down) + one row per first bill with its
// regularized bills (ID_REG_BILL) in a collapsed detail.
let wbfrBills = [];
let wbfrPeriods = [];
let wbfrScanned = false;
let wbfrPeriodsLoaded = false;
let wbfrSortKey = null;
let wbfrSortDir = 1;
let wbfrReg = "";
const wbfrOpen = new Set();
const WBFR_COLUMNS = [
  { key: "_toggle", label: "", nosort: true },
  { key: "reference", label: "Account", render: (b) => `<span class="hx-niss">${escapeHtml(b.reference ?? "")}</span>` + wbpdCopyBtn(b.reference, "Copy account number") },
  { key: "niss", label: "NISS", mono: true, render: (b) => escapeHtml(b.niss ?? "") + wbpdCopyBtn(b.niss, "Copy NISS") },
  { key: "offered_service", label: "Service" },
  { key: "id_bill", label: "ID Bill", mono: true, render: (b) => escapeHtml(b.id_bill ?? "") + wbpdCopyBtn(b.id_bill, "Copy ID_BILL") },
  { key: "billing_status_desc", label: "Bill status", render: (b) => `${escapeHtml(b.billing_status_desc ?? "")} <span class="hint-text">${escapeHtml(b.billing_status ?? "")}</span>` },
  { key: "billing_type_desc", label: "Billing type" },
  { key: "billing_period_desc", label: "Billing period" },
  { key: "last_billing_date", label: "Last billing date = CS from", date: true },
  { key: "reg_concepts", label: "★ Regularization concepts", render: (b) => `<span class="usan-amount">${escapeHtml(b.reg_concepts ?? "")}</span>` },
  { key: "reg_amount", label: "Reg. amount", num: true, render: (b) => escapeHtml(hxFmtNum(b.reg_amount)) },
  { key: "regularized_count", label: "Regularized bills", num: true, render: (b) => b.regularized_count
      ? `<span class="hx-pill hx-pill-billed">${b.regularized_count}</span>` + (b.other_cs_count ? ` <span class="hx-pill hx-pill-warn" title="Regularized bill(s) on another contracted service">Other CS ${b.other_cs_count}</span>` : "")
      : `<span class="hx-pill hx-pill-ghost">none</span>` },
  { key: "billing_date", label: "Billing date", date: true },
  { key: "total_amount", label: "Bill total", num: true, render: (b) => escapeHtml(hxFmtNum(b.total_amount)) },
  { key: "id_contracted_service", label: "Contracted service", mono: true },
];
const WBFR_REG_COLUMNS = [
  ["id_bill", "Regularized ID Bill"], ["bill_number", "Bill number"], ["billing_period_desc", "Billing period"], ["billing_date", "Billing date"],
  ["billing_status_desc", "Status"], ["billing_type_desc", "Billing type"], ["total_amount", "Total"], ["id_contracted_service", "Contracted service"], ["same_cs", "Same CS?"],
];

async function wbfrLoadPeriods() {
  if (wbfrPeriodsLoaded) return;
  try {
    const d = await api("/api/wrong-billed-consumption/billing-periods");
    $("#wbfr-period").innerHTML = (d.periods || []).map((p) =>
      `<label class="${p.is_current ? "is-current" : ""}"><input type="checkbox" value="${escapeHtml(p.id)}" data-desc="${escapeHtml(p.description)}"${p.is_current ? " checked" : ""} />` +
      `${escapeHtml(p.description)}${p.is_current ? " (current)" : ""}</label>`).join("") || `<span class="hint-text">No periods.</span>`;
    wbfrPeriodsLoaded = true;
  } catch { /* nothing selected = server uses the current period */ }
}
function wbfrSelectedPeriods() {
  return [...document.querySelectorAll("#wbfr-period input[type=checkbox]:checked")].map((o) => o.value).filter(Boolean);
}
function wbfrPeriodName(id, fallback) {
  return fallback || document.querySelector(`#wbfr-period input[value="${id}"]`)?.dataset.desc || String(id);
}

function wbfrVisible() {
  const q = $("#wbfr-filter-search").value.trim().toLowerCase();
  const per = $("#wbfr-filter-period").value, svc = $("#wbfr-filter-service").value, st = $("#wbfr-filter-status").value;
  let rows = wbfrBills.filter((b) => {
    if (per && String(b.id_billing_period) !== per) return false;
    if (svc && b.offered_service !== svc) return false;
    if (st && b.billing_status_desc !== st) return false;
    if (wbfrReg === "other" && !b.other_cs_count) return false;
    if (wbfrReg === "none" && b.regularized_count) return false;
    if (q) {
      const hay = [b.reference, b.niss, b.id_bill, b.bill_number, b.id_contracted_service].concat(...(b.regularized || []).map((r) => [r.id_bill, r.bill_number]));
      if (!hay.some((v) => String(v ?? "").toLowerCase().includes(q))) return false;
    }
    return true;
  });
  if (wbfrSortKey) rows = [...rows].sort((a, b) => _hierCompareValues(a[wbfrSortKey], b[wbfrSortKey], wbfrSortDir));
  return rows;
}

function wbfrDetailHtml(b) {
  const head = WBFR_REG_COLUMNS.map(([, l]) => `<th>${escapeHtml(l)}</th>`).join("");
  const body = (b.regularized || []).length
    ? b.regularized.map((r) => "<tr>" + WBFR_REG_COLUMNS.map(([k]) => {
        const v = r[k] ?? "";
        if (k === "billing_date") return `<td>${hxDate(v)}</td>`;
        if (k === "total_amount") return `<td class="hx-num">${escapeHtml(hxFmtNum(v))}</td>`;
        if (k === "same_cs") return `<td>${String(v) === "1" ? "Same" : `<span class="hx-pill hx-pill-warn">Other CS</span>`}</td>`;
        if (k === "billing_status_desc") return `<td>${escapeHtml(v)} <span class="hint-text">${escapeHtml(r.billing_status ?? "")}</span></td>`;
        return `<td class="${["id_bill", "bill_number", "id_contracted_service"].includes(k) ? "hx-mono" : ""}">${escapeHtml(v)}${k === "id_bill" ? wbpdCopyBtn(v, "Copy ID_BILL") : ""}</td>`;
      }).join("") + "</tr>").join("")
    : `<tr><td colspan="${WBFR_REG_COLUMNS.length}" class="hint-text">No regularized bill points to this bill (ID_REG_BILL) — or only Cancelled / Rebilled / credit-note ones.</td></tr>`;
  const info = `<div class="hint-text wbfr-detail-info">CS ${escapeHtml(b.id_contracted_service ?? "")} from ${hxDate(b.cs_from_date)} · last billing date ${hxDate(b.last_billing_date)} · concepts: ${escapeHtml(b.reg_concepts ?? "")}</div>`;
  return `<tr class="wbpd-detail-row"><td colspan="${WBFR_COLUMNS.length}"><div class="wbpd-detail">${info}<table class="data-grid wbpd-sec-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></td></tr>`;
}

function wbfrRenderPeriods() {
  $("#wbfr-period-card").hidden = !wbfrPeriods.length;
  const sel = $("#wbfr-filter-period").value;
  const cols = [["billing_period_desc", "Billing period"], ["count", "★ First bills regularized"], ["account_count", "Accounts"], ["electricity", "Electricity"],
    ["water", "Water"], ["sanitary", "Sanitary"], ["regularized_bills", "Regularized bills"], ["no_regularized", "No regularized bill found"], ["reg_amount", "Reg. amount"]];
  document.querySelector("#wbfr-period-table thead tr").innerHTML = cols.map(([, l]) => `<th>${escapeHtml(l)}</th>`).join("");
  document.querySelector("#wbfr-period-table tbody").innerHTML = wbfrPeriods.map((p) =>
    `<tr class="hx-row wbfr-period-row${sel === String(p.id_billing_period) ? " is-selected" : ""}" data-wbfr-period="${escapeHtml(p.id_billing_period)}" title="Click to drill down (click again to show all)">` +
    cols.map(([k]) => {
      if (k === "billing_period_desc") return `<td><strong>${escapeHtml(wbfrPeriodName(p.id_billing_period, p.billing_period_desc))}</strong> <span class="hint-text">${escapeHtml(p.id_billing_period)}</span></td>`;
      if (k === "count") return `<td class="hx-num">${p.count ? `<span class="hx-pill hx-pill-billed">${p.count}</span>` : "0"}</td>`;
      if (k === "reg_amount") return `<td class="hx-num">${escapeHtml(hxFmtNum(p.reg_amount))}</td>`;
      return `<td class="hx-num">${escapeHtml(p[k] ?? 0)}</td>`;
    }).join("") + "</tr>").join("");
}

function wbfrRender() {
  wbfrRenderPeriods();
  const head = document.querySelector("#wbfr-table thead tr");
  head.innerHTML = WBFR_COLUMNS.map((c) => {
    if (c.nosort) return `<th></th>`;
    const arrow = wbfrSortKey === c.key ? `<span class="stats-table-sort-arrow">${wbfrSortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th class="stats-table-th-sortable" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  head.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    wbfrSortDir = wbfrSortKey === k ? -wbfrSortDir : 1;
    wbfrSortKey = k;
    wbfrRender();
  }));
  const rows = wbfrVisible();
  document.querySelector("#wbfr-table tbody").innerHTML = rows.length
    ? rows.map((b) => {
        const open = wbfrOpen.has(String(b.id_bill));
        const main = `<tr class="hx-row hx-row-anomalous wbpd-main${open ? " is-open" : ""}" data-wbfr-bill="${escapeHtml(b.id_bill)}">` + WBFR_COLUMNS.map((c) => {
          if (c.key === "_toggle") return `<td class="wbpd-toggle">${open ? "▾" : "▸"}</td>`;
          const v = c.render ? c.render(b) : c.date ? hxDate(b[c.key]) : escapeHtml(b[c.key] ?? "");
          return `<td class="${c.num ? "hx-num" : ""}${c.mono ? " hx-mono" : ""}">${v}</td>`;
        }).join("") + "</tr>";
        return open ? main + wbfrDetailHtml(b) : main;
      }).join("")
    : `<tr><td colspan="${WBFR_COLUMNS.length}" class="hint-text">${wbfrBills.length ? "No bills match the current filters." : "✅ No first bill carrying a regularization."}</td></tr>`;
  renderFilteredCount("#wbfr-filtered-count", rows.length, wbfrBills.length);
  if (wbfrBills.length) {
    const n = rows.length;
    hxRenderDashboard("wbfr-dash", {
      gauges: [
        { label: "Regularizes another CS", count: rows.filter((b) => b.other_cs_count).length, total: n, c1: "#ef4444", c2: "#f97316",
          hint: "At least one regularized bill is on another contracted service (previous contract)" },
        { label: "No regularized bill found", count: rows.filter((b) => !b.regularized_count).length, total: n, c1: "#f59e0b", c2: "#eab308",
          hint: "No (non-cancelled/rebilled/credit-note) bill has ID_REG_BILL = this bill" },
        { label: "Already invoiced", count: rows.filter((b) => b.billing_status === "ESTFAC0005").length, total: n, c1: "#6366f1", c2: "#06b6d4" },
      ],
      tiles: [
        { icon: "🚨", label: "First bills regularized", value: n.toLocaleString(), accent: true },
        { icon: "👤", label: "Accounts", value: new Set(rows.map((b) => b.reference).filter(Boolean)).size.toLocaleString() },
        { icon: "🧾", label: "Regularized bills", value: rows.reduce((a, b) => a + b.regularized_count, 0).toLocaleString() },
        { icon: "Σ", label: "Reg. amount", value: hxFmtNum(hxSum(rows, "reg_amount")) },
      ],
      split: { title: "Service", entries: hxCountBy(rows, (b) => b.offered_service) },
    });
  }
}

let wbfrSeq = 0;  // latest scan wins - an older, slower response is ignored
async function wbfrScan() {
  const btn = $("#wbfr-detect-btn");
  const seq = ++wbfrSeq;
  wbfrScanned = true;
  btn.disabled = true;
  await wbfrLoadPeriods();
  const periods = wbfrSelectedPeriods();
  const df = $("#wbfr-date-from").value, dt = $("#wbfr-date-to").value;
  $("#wbfr-summary").textContent = df || dt ? "Scanning the creation dates…" : `Scanning ${periods.length || 1} billing period(s)… (~6–20 s each)`;
  try {
    if ((df && !dt) || (!df && dt)) throw new Error("Give both creation dates (from and to), or clear them.");
    const data = await api("/api/wrong-bill/first-regularized/detect", { method: "POST",
      body: { billing_periods: periods, date_from: df || null, date_to: dt || null } });
    if (seq !== wbfrSeq) return;
    wbfrBills = data.bills || [];
    wbfrPeriods = data.periods || [];
    const opts = (id, vals) => {
      const keep = $(id).value;
      $(id).innerHTML = `<option value="">All</option>` + vals.map(([v, l]) => `<option value="${escapeHtml(v)}">${escapeHtml(l)}</option>`).join("");
      $(id).value = vals.some(([v]) => v === keep) ? keep : "";
    };
    opts("#wbfr-filter-period", wbfrPeriods.map((p) => [String(p.id_billing_period), wbfrPeriodName(p.id_billing_period, p.billing_period_desc)]));
    opts("#wbfr-filter-service", [...new Set(wbfrBills.map((b) => b.offered_service).filter(Boolean))].sort().map((s) => [s, s]));
    opts("#wbfr-filter-status", [...new Set(wbfrBills.map((b) => b.billing_status_desc).filter(Boolean))].sort().map((s) => [s, s]));
    const has = wbfrBills.length > 0;
    $("#wbfr-filters").hidden = !has;
    $("#wbfr-dash").hidden = !has;
    ["#wbfr-export-btn", "#wbfr-expand-btn", "#wbfr-copy-acct-btn"].forEach((id) => { $(id).disabled = !has; });
    const names = [(data.scope.billing_periods || []).map((id) => wbfrPeriodName(id)).join(", "),
      data.scope.date_from ? `created ${data.scope.date_from} → ${data.scope.date_to}` : ""].filter(Boolean).join(" · ");
    $("#wbfr-summary").textContent = has
      ? `🚨 ${data.count} first bill(s) on ${data.account_count} account(s), ${data.regularized_count} regularized bill(s) — ${names} (${data.seconds}s).`
      : `✅ None found — ${names} (${data.seconds}s).`;
    wbSetTabCount("case4", data.count);
    wbfrRender();
  } catch (err) {
    if (seq !== wbfrSeq) return;
    wbSetTabCount("case4", null);
    $("#wbfr-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    if (seq === wbfrSeq) btn.disabled = false;
  }
}

$("#wbfr-detect-btn").addEventListener("click", wbfrScan);
$("#wbfr-period").addEventListener("change", (ev) => {
  if (ev.target.matches("input[type=checkbox]") && ev.target.checked && wbfrSelectedPeriods().length > 12) {
    ev.target.checked = false;
    showToast("Pick at most 12 billing periods.", true);
  }
});
$("#wbfr-period-none-btn").addEventListener("click", () => {
  document.querySelectorAll("#wbfr-period input[type=checkbox]").forEach((c) => { c.checked = false; });
  wbfrScopeHint();
});
// RJ 2026-10-02: "I want to also see the cases for the latest 7 days" -
// bill CREATE_DATE range; dates + ticked periods = AND.
function wbfrScopeHint() {
  const n = wbfrSelectedPeriods().length, f = $("#wbfr-date-from").value, t = $("#wbfr-date-to").value;
  const parts = [];
  if (f && t) parts.push(`created ${f} → ${t}`);
  if (n) parts.push(`${n} billing period(s)`);
  $("#wbfr-scope-hint").textContent = parts.length ? `Scope: ${parts.join(" AND ")}` : "Scope: current billing period";
}
const wbfrIso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
$("#wbfr-last7-btn").addEventListener("click", () => {
  const today = new Date();
  $("#wbfr-date-to").value = wbfrIso(today);
  $("#wbfr-date-from").value = wbfrIso(new Date(today.getTime() - 6 * 86400000));
  // Any billing period: untick the periods so only the dates apply.
  document.querySelectorAll("#wbfr-period input[type=checkbox]").forEach((c) => { c.checked = false; });
  wbfrScopeHint();
  wbfrScan();
});
$("#wbfr-dates-clear-btn").addEventListener("click", () => {
  $("#wbfr-date-from").value = ""; $("#wbfr-date-to").value = "";
  wbfrScopeHint();
});
["#wbfr-period", "#wbfr-date-from", "#wbfr-date-to"].forEach((id) => $(id).addEventListener("change", wbfrScopeHint));
document.querySelector("#wbfr-period-table tbody").addEventListener("click", (ev) => {
  const tr = ev.target.closest("tr[data-wbfr-period]");
  if (!tr) return;
  const sel = $("#wbfr-filter-period");
  sel.value = sel.value === tr.dataset.wbfrPeriod ? "" : tr.dataset.wbfrPeriod;
  wbfrRender();
  if (sel.value) document.querySelector("#wbfr-table").scrollIntoView({ behavior: "smooth", block: "start" });
});
document.querySelector("#wbfr-table tbody").addEventListener("click", (ev) => {
  const copyBtn = ev.target.closest("[data-wbpd-copy]");
  if (copyBtn) { ev.stopPropagation(); biss2CopyAccount(copyBtn.dataset.wbpdCopy, copyBtn); return; }
  const tr = ev.target.closest("tr[data-wbfr-bill]");
  if (!tr || ev.target.closest("a, button")) return;
  const id = tr.dataset.wbfrBill;
  if (wbfrOpen.has(id)) wbfrOpen.delete(id); else wbfrOpen.add(id);
  wbfrRender();
});
$("#wbfr-expand-btn").addEventListener("click", () => {
  const vis = wbfrVisible();
  const allOpen = vis.length && vis.every((b) => wbfrOpen.has(String(b.id_bill)));
  if (allOpen) vis.forEach((b) => wbfrOpen.delete(String(b.id_bill)));
  else vis.forEach((b) => wbfrOpen.add(String(b.id_bill)));
  $("#wbfr-expand-btn").textContent = allOpen ? "⊞ Expand all" : "⊟ Collapse all";
  wbfrRender();
});
["#wbfr-filter-period", "#wbfr-filter-service", "#wbfr-filter-status"].forEach((id) => $(id).addEventListener("change", wbfrRender));
$("#wbfr-filter-search").addEventListener("input", wbfrRender);
$("#wbfr-filter-reg").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-value]");
  if (!b) return;
  wbfrReg = b.dataset.value;
  $$("#wbfr-filter-reg button").forEach((x) => x.classList.toggle("is-active", x === b));
  wbfrRender();
});
$("#wbfr-filter-clear-btn").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  ["#wbfr-filter-period", "#wbfr-filter-service", "#wbfr-filter-status", "#wbfr-filter-search"].forEach((id) => { $(id).value = ""; });
  wbfrReg = "";
  $$("#wbfr-filter-reg button").forEach((x) => x.classList.toggle("is-active", x.dataset.value === ""));
  wbfrRender();
});
$("#wbfr-copy-acct-btn").addEventListener("click", () => {
  const vals = [...new Set(wbfrVisible().map((b) => b.reference).filter(Boolean))];
  if (!vals.length) return;
  biss2CopyAccount(vals.join("\n"), null);
  showToast(`Copied ${vals.length} account(s) (one per line).`);
});
$("#wbfr-export-btn").addEventListener("click", () => {
  const rows = wbfrVisible();
  if (!rows.length) return;
  const bCols = ["id_billing_period", "billing_period_desc", "reference", "niss", "offered_service", "id_contracted_service", "id_bill", "bill_number",
    "billing_status", "billing_status_desc", "billing_type_desc", "billing_date", "last_billing_date", "cs_from_date", "total_amount", "reg_concepts", "reg_amount", "regularized_count"];
  const rCols = ["id_bill", "bill_number", "id_billing_period", "billing_period_desc", "billing_date", "billing_status", "billing_status_desc", "billing_type_desc", "total_amount", "id_contracted_service", "same_cs"];
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [bCols.concat(rCols.map((k) => "reg_" + k)).map((c) => `"${c.toUpperCase()}"`).join(",")];
  rows.forEach((b) => {
    const base = bCols.map((k) => esc(b[k]));
    if (!(b.regularized || []).length) lines.push(base.concat(rCols.map(() => '""')).join(","));
    else b.regularized.forEach((r) => lines.push(base.concat(rCols.map((k) => esc(r[k]))).join(",")));
  });
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `wrong_bill_case4_first_bill_regularized_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

$("#usan-detect-btn").addEventListener("click", usanScan);
["#usan-filter-bp", "#usan-filter-status"].forEach((id) => $(id).addEventListener("change", usanRenderTable));
$("#usan-filter-search").addEventListener("input", usanRenderTable);
$("#usan-filter-clear-btn").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  ["#usan-filter-bp", "#usan-filter-status", "#usan-filter-search"].forEach((id) => { $(id).value = ""; });
  usanRenderTable();
});
$("#usan-alert-refresh-btn").addEventListener("click", usanLoadAlertStatus);
$("#usan-send-now-btn").addEventListener("click", async () => {
  if (!confirm("Run the check now and e-mail the detected cases to the configured recipients (even if today's e-mail was already sent)?")) return;
  const btn = $("#usan-send-now-btn");
  btn.disabled = true;
  try {
    const r = await api("/api/alerts/unusual-sanitary/send-now", { method: "POST" });
    if (r.status === "sent") showToast(`E-mail sent (${r.row_count} row(s)).`);
    else if (r.status === "no_cases") showToast("No cases found — nothing to send.");
    else showToast(r.detail || r.status, true);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    usanLoadAlertStatus();
  }
});
$("#usan-export-btn").addEventListener("click", () => {
  const rows = usanVisibleRows();
  if (!rows.length) return;
  const cols = ["reference", "niss", "id_billing_period", "id_bill", "bill_number", "billing_date", "create_date", "billing_status", "cod_concept", "print_description", "total_amount", "base",
    "water_id_bill", "water_bill_count", "water_total_amount", "sanitary_minus_water"];
  const lines = [cols.map((c) => `"${c.toUpperCase()}"`).join(",")];
  rows.forEach((r) => lines.push(cols.map((k) => `"${String(r[k] ?? "").replace(/"/g, '""')}"`).join(",")));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `unusual_high_sanitary_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// ---------------- DOUBLE ITB (RJ 2026-09-27) ----------------
// See app/core/double_itb.py. One scan; filters/sort client-side; built
// with the shared hx dashboard + filter panel from the start.
const DITB_COLUMNS = [
  { key: "niss", label: "NISS", render: (r) => `<span class="hx-niss">${escapeHtml(r.niss ?? "")}</span>` },
  { key: "billing_period", label: "Billing Period" },
  { key: "needs_rebilling", label: "Rebilling", render: (r) => String(r.needs_rebilling) === "1"
      ? `<span class="hx-pill hx-pill-billed">Needs rebilling</span>` : `<span class="hx-pill hx-pill-ghost">No</span>` },
  { key: "anom_ready_usage", label: "★ Ready Usage (anomalous)", num: true, render: (r) => `<strong>${escapeHtml(r.anom_ready_usage ?? "")}</strong>` },
  { key: "anom_reading_count", label: "Readings", num: true },
  { key: "anom_id_item_to_bill", label: "Anomalous ITB", mono: true },
  { key: "anom_status", label: "Status", render: (r) => `<span class="hx-pill hx-pill-amber" title="${escapeHtml(r.anom_status ?? "")}">Anomalous</span>` },
  { key: "anom_billing_date", label: "Billing Date", date: true },
  { key: "billed_id_item_to_bill", label: "Billed ITB", mono: true },
  { key: "billed_status", label: "Status", render: (r) => `<span class="hx-pill hx-pill-green" title="${escapeHtml(r.billed_status ?? "")}">Billed</span>` },
  { key: "billed_id_bill", label: "ID Bill", mono: true },
  { key: "ini_date", label: "INI Date", date: true },
  { key: "end_date", label: "END Date", date: true },
  { key: "id_billing_service", label: "Billing Service", mono: true },
  { key: "account", label: "Account", mono: true },
  { key: "offered_service", label: "Service" },
];
let ditbRows = [];
let ditbSortKey = null;
let ditbSortDir = 1;
let ditbRebill = "";

function ditbVisibleRows() {
  const q = $("#ditb-filter-search").value.trim().toLowerCase();
  const bp = $("#ditb-filter-bp").value;
  const svc = $("#ditb-filter-service").value;
  let rows = ditbRows.filter((r) => {
    if (bp && r.billing_period !== bp) return false;
    if (svc && r.offered_service !== svc) return false;
    if (ditbRebill && String(r.needs_rebilling) !== ditbRebill) return false;
    if (q && ![r.niss, r.account, r.anom_id_item_to_bill, r.billed_id_item_to_bill, r.id_billing_service]
      .some((v) => String(v ?? "").toLowerCase().includes(q))) return false;
    return true;
  });
  if (ditbSortKey) rows = [...rows].sort((a, b) => _hierCompareValues(a[ditbSortKey], b[ditbSortKey], ditbSortDir));
  return rows;
}

function ditbCell(c, r) {
  if (c.render) return c.render(r);
  if (c.date) return hxDate(r[c.key]);
  return escapeHtml(r[c.key] ?? "");
}

function ditbRenderTable() {
  const head = document.querySelector("#ditb-table thead tr");
  head.innerHTML = DITB_COLUMNS.map((c) => {
    const arrow = ditbSortKey === c.key ? `<span class="stats-table-sort-arrow">${ditbSortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th class="stats-table-th-sortable" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  head.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    ditbSortDir = ditbSortKey === k ? -ditbSortDir : 1;
    ditbSortKey = k;
    ditbRenderTable();
  }));
  const rows = ditbVisibleRows();
  document.querySelector("#ditb-table tbody").innerHTML = rows.length
    ? rows.map((r) => `<tr class="${String(r.needs_rebilling) === "1" ? "hx-row hx-row-anomalous" : "hx-row"}">` +
        DITB_COLUMNS.map((c) => `<td class="${c.num ? "hx-num" : ""}${c.mono ? " hx-mono" : ""}">${ditbCell(c, r)}</td>`).join("") + "</tr>").join("")
    : `<tr><td colspan="${DITB_COLUMNS.length}" class="hint-text">${ditbRows.length ? "No rows match the current filters." : "No double items to bill found."}</td></tr>`;
  renderFilteredCount("#ditb-filtered-count", rows.length, ditbRows.length);
  if (ditbRows.length) {
    const n = rows.length;
    const rebill = rows.filter((r) => String(r.needs_rebilling) === "1");
    hxRenderDashboard("ditb-dash", {
      gauges: [
        { label: "Needs rebilling", count: rebill.length, total: n, c1: "#ef4444", c2: "#f97316",
          hint: "Anomalous item's ready usage is not zero" },
        { label: "Zero ready usage", count: n - rebill.length, total: n, c1: "#10b981", c2: "#22c55e" },
        { label: "Twin billed in another period", count: rows.filter((r) => r.billed_id_billing_period && r.billed_id_billing_period !== r.id_billing_period).length, total: n, c1: "#6366f1", c2: "#06b6d4",
          hint: "The billed twin's billing period differs from the anomalous item's" },
      ],
      tiles: [
        { icon: "★", label: "Ready usage to rebill", value: hxSum(rebill, "anom_ready_usage").toLocaleString(), accent: true },
        { icon: "👯", label: "Anomalous items", value: new Set(rows.map((r) => r.anom_id_item_to_bill)).size.toLocaleString() },
        { icon: "🔌", label: "NISS", value: new Set(rows.map((r) => r.niss).filter(Boolean)).size.toLocaleString() },
        { icon: "👤", label: "Accounts", value: new Set(rows.map((r) => r.account).filter(Boolean)).size.toLocaleString() },
        { icon: "📅", label: "Billing periods", value: new Set(rows.map((r) => r.billing_period).filter(Boolean)).size.toLocaleString() },
      ],
      split: { title: "Offered service", entries: hxCountBy(rows, (r) => r.offered_service) },
    });
  }
}

$("#ditb-detect-btn").addEventListener("click", async () => {
  const btn = $("#ditb-detect-btn");
  btn.disabled = true;
  $("#ditb-summary").textContent = "Scanning…";
  try {
    const data = await api("/api/double-itb/detect", { method: "POST" });
    ditbRows = data.rows || [];
    const periods = new Map();
    ditbRows.forEach((r) => { if (r.billing_period && !periods.has(r.billing_period)) periods.set(r.billing_period, Number(r.id_billing_period) || 0); });
    $("#ditb-filter-bp").innerHTML = `<option value="">All</option>` +
      [...periods.entries()].sort((a, b) => b[1] - a[1]).map(([n]) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
    $("#ditb-filter-service").innerHTML = `<option value="">All</option>` +
      [...new Set(ditbRows.map((r) => r.offered_service).filter(Boolean))].sort().map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    $("#ditb-filters").hidden = ditbRows.length === 0;
    $("#ditb-export-btn").disabled = ditbRows.length === 0;
    $("#ditb-summary").textContent = `${data.anomalous_item_count} anomalous item(s) with a billed twin — ${data.needs_rebilling_count} need rebilling.`;
    ditbRenderTable();
  } catch (err) {
    $("#ditb-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});
["#ditb-filter-bp", "#ditb-filter-service"].forEach((id) => $(id).addEventListener("change", ditbRenderTable));
$("#ditb-filter-search").addEventListener("input", ditbRenderTable);
$("#ditb-filter-rebill").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-value]");
  if (!b) return;
  ditbRebill = b.dataset.value;
  $$("#ditb-filter-rebill button").forEach((x) => x.classList.toggle("is-active", x === b));
  ditbRenderTable();
});
$("#ditb-filter-clear-btn").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  ["#ditb-filter-bp", "#ditb-filter-service", "#ditb-filter-search"].forEach((id) => { $(id).value = ""; });
  ditbRebill = "";
  $$("#ditb-filter-rebill button").forEach((x) => x.classList.toggle("is-active", x.dataset.value === ""));
  ditbRenderTable();
});
$("#ditb-export-btn").addEventListener("click", () => {
  const rows = ditbVisibleRows();
  if (!rows.length) return;
  const cols = DITB_COLUMNS.map((c) => c.key);
  const labels = ["NISS", "Billing Period", "Needs Rebilling", "Ready Usage (anomalous)", "Readings", "Anomalous ITB", "Anomalous Status",
    "Anomalous Billing Date", "Billed ITB", "Billed Status", "ID Bill", "INI Date", "END Date", "Billing Service", "Account", "Service"];
  const lines = [labels.map((l) => `"${l}"`).join(",")];
  rows.forEach((r) => lines.push(cols.map((k) => {
    let v = r[k] ?? "";
    if (k === "needs_rebilling") v = String(v) === "1" ? "Yes" : "No";
    if (["anom_billing_date", "ini_date", "end_date"].includes(k)) v = String(v).replace(/ 00:00:00(\.0+)?$/, "");
    return `"${String(v).replace(/"/g, '""')}"`;
  }).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "double_itb.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// ---------------- Disconnection TNB ----------------
// RJ, 2026-09-27 - see app/core/disconnection_tnb.py. One scan, no
// parameters; billing period / NISS / contract status / estimated
// filters are client-side.
const DTNB_COLUMNS = [
  { key: "niss", label: "NISS" },
  { key: "billing_period", label: "Billing Period" },
  { key: "id_reading", label: "ID Reading" },
  { key: "reading_prev_date", label: "Prev. Date", date: true },
  { key: "reading_date", label: "Reading Date", date: true },
  { key: "prev_value", label: "Prev. Value" },
  { key: "value", label: "Value" },
  { key: "reading_usage", label: "Reading Usage" },
  { key: "corrected_usage", label: "Corrected Usage" },
  { key: "ready_usage", label: "Ready Usage" },
  { key: "read_status", label: "Read Status" },
  { key: "ind_estimate", label: "Estimated" },
  { key: "id_contracted_service", label: "Nearest Contracted Service", contract: true },
  { key: "contract_status", label: "Contract Status", contract: true },
  { key: "contract_from_date", label: "Contract From", date: true, contract: true },
  { key: "contract_end_date", label: "Contract End Date", date: true, contract: true },
  { key: "days_from_end", label: "Days After End", contract: true },
];
let dtnbRows = [];
let dtnbSortKey = null;
let dtnbSortDir = 1;

function dtnbFmt(c, v) {
  const s = v ?? "";
  if (c.date && typeof s === "string") return s.replace(/ 00:00:00(\.0+)?$/, "");
  if (c.key === "ind_estimate") return String(s) === "1" ? "Yes" : String(s) === "0" ? "No" : s;
  return s;
}

function dtnbVisibleRows() {
  const bp = $("#dtnb-filter-bp").value;
  const niss = $("#dtnb-filter-niss").value.trim().toLowerCase();
  const cs = $("#dtnb-filter-contract").value;
  const est = $("#dtnb-filter-est").value;
  let rows = dtnbRows.filter((r) => {
    if (bp && r.billing_period !== bp) return false;
    if (niss && !String(r.niss ?? "").toLowerCase().includes(niss)) return false;
    if (cs === "__none__" && r.id_contracted_service) return false;
    if (cs && cs !== "__none__" && r.contract_status !== cs) return false;
    if (est && String(r.ind_estimate) !== est) return false;
    return true;
  });
  if (dtnbSortKey) rows = [...rows].sort((a, b) => _hierCompareValues(a[dtnbSortKey], b[dtnbSortKey], dtnbSortDir));
  return rows;
}

function dtnbRenderTable() {
  const head = document.querySelector("#dtnb-table thead tr");
  head.innerHTML = DTNB_COLUMNS.map((c) => {
    const arrow = dtnbSortKey === c.key ? `<span class="stats-table-sort-arrow">${dtnbSortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th class="stats-table-th-sortable${c.contract ? " dtnb-col-contract" : ""}" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  head.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    dtnbSortDir = dtnbSortKey === k ? -dtnbSortDir : 1;
    dtnbSortKey = k;
    dtnbRenderTable();
  }));
  const rows = dtnbVisibleRows();
  document.querySelector("#dtnb-table tbody").innerHTML = rows.length
    ? rows.map((r) => "<tr>" + DTNB_COLUMNS.map((c) => {
        const cls = [];
        if (c.contract) cls.push("dtnb-cell-contract");
        if (c.key === "ready_usage") cls.push("dtnb-ready");
        if (c.key === "days_from_end" && r.days_from_end !== "" && Number(r.days_from_end) > 0) cls.push("dtnb-after-end");
        return `<td${cls.length ? ` class="${cls.join(" ")}"` : ""}>${escapeHtml(dtnbFmt(c, r[c.key]))}</td>`;
      }).join("") + "</tr>").join("")
    : `<tr><td colspan="${DTNB_COLUMNS.length}" class="hint-text">${dtnbRows.length ? "No rows match the current filters." : "No TNB disconnections with ready usage found."}</td></tr>`;
  renderFilteredCount("#dtnb-filtered-count", rows.length, dtnbRows.length);
  if (dtnbRows.length) {
    const n = rows.length;
    hxRenderDashboard("dtnb-dash", {
      gauges: [
        { label: "Read after contract end", count: rows.filter((r) => r.days_from_end !== "" && Number(r.days_from_end) > 0).length, total: n, c1: "#ef4444", c2: "#f97316",
          hint: "Reading date is later than the nearest contracted service's end date" },
        { label: "Estimated readings", count: rows.filter((r) => String(r.ind_estimate) === "1").length, total: n, c1: "#f59e0b", c2: "#eab308" },
        { label: "No contract found", count: rows.filter((r) => !r.id_contracted_service).length, total: n, c1: "#94a3b8", c2: "#64748b" },
      ],
      tiles: [
        { icon: "★", label: "Total ready usage", value: hxSum(rows, "ready_usage").toLocaleString(), accent: true },
        { icon: "⛔", label: "TNB disconnections", value: n.toLocaleString() },
        { icon: "🏠", label: "Sector supplies", value: new Set(rows.map((r) => r.id_sector_supply)).size.toLocaleString() },
        { icon: "📅", label: "Billing periods", value: new Set(rows.map((r) => r.billing_period).filter(Boolean)).size.toLocaleString() },
      ],
      split: { title: "Nearest contract status", entries: hxCountBy(rows, (r) => r.contract_status, "(no contract)") },
    });
  }
}

$("#dtnb-detect-btn").addEventListener("click", async () => {
  const btn = $("#dtnb-detect-btn");
  btn.disabled = true;
  $("#dtnb-summary").textContent = "Scanning…";
  try {
    const data = await api("/api/disconnection-tnb/detect", { method: "POST" });
    dtnbRows = data.rows || [];
    const periods = new Map();
    dtnbRows.forEach((r) => { if (r.billing_period && !periods.has(r.billing_period)) periods.set(r.billing_period, Number(r.id_billing_period) || 0); });
    const bpSel = $("#dtnb-filter-bp");
    const bpCur = bpSel.value;
    const bpNames = [...periods.entries()].sort((a, b) => b[1] - a[1]).map(([n]) => n);
    bpSel.innerHTML = `<option value="">All</option>` + bpNames.map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
    if (bpNames.includes(bpCur)) bpSel.value = bpCur;
    const statuses = [...new Set(dtnbRows.map((r) => r.contract_status).filter(Boolean))].sort();
    $("#dtnb-filter-contract").innerHTML = `<option value="">All</option>` +
      statuses.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("") +
      (data.no_contract_count ? `<option value="__none__">(no contract)</option>` : "");
    $("#dtnb-filter-row").hidden = dtnbRows.length === 0;
    $("#dtnb-export-btn").disabled = dtnbRows.length === 0;
    $("#dtnb-summary").textContent = `${data.count} TNB disconnection reading(s) with ready usage across ${data.supply_count} sector supply(ies)` +
      (data.no_contract_count ? ` — ${data.no_contract_count} with no contracted service found.` : ".");
    dtnbRenderTable();
  } catch (err) {
    $("#dtnb-summary").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});
["#dtnb-filter-bp", "#dtnb-filter-contract", "#dtnb-filter-est"].forEach((id) => $(id).addEventListener("change", dtnbRenderTable));
$("#dtnb-filter-niss").addEventListener("input", dtnbRenderTable);
$("#dtnb-filter-clear-btn").addEventListener("click", () => {
  ["#dtnb-filter-bp", "#dtnb-filter-contract", "#dtnb-filter-est", "#dtnb-filter-niss"].forEach((id) => { $(id).value = ""; });
  dtnbRenderTable();
});
$("#dtnb-export-btn").addEventListener("click", () => {
  const rows = dtnbVisibleRows();
  if (!rows.length) return;
  const lines = [DTNB_COLUMNS.map((c) => `"${c.label}"`).join(",")];
  rows.forEach((r) => lines.push(DTNB_COLUMNS.map((c) => `"${String(dtnbFmt(c, r[c.key]) ?? "").replace(/"/g, '""')}"`).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "disconnection_tnb.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});
dtnbRenderTable();

// ---------------- Reading Validation/Modif ----------------
// RJ, 2026-09-23, own SQL - see app/core/reading_validation.py's module
// docstring for the full query provenance. Phase 1 scope only (RJ: "this
// is the main query for now"): display, filter, sort - no edit/modif
// capability yet despite the menu's own name.
let rvRows = [];              // raw rows from the last detect call (already display-formatted server-side)
let rvSortKey = null;
let rvSortDir = 1;
let rvSupplies = [];          // from search-account: [{id_sector_supply, niss, offered_service_desc, contract_status}]
let rvActiveSupply = null;    // the id_sector_supply currently loaded (string)
let rvDetailLevel = "medium"; // "important" | "medium" | "full" - RJ, 2026-09-23: "we will have
                               // 3 option for the detail, the full option, medium option (what we
                               // display now by defualt) and important detail" - medium stays the
                               // default, same as before this 3-tier round.
let rvSelectedIdReading = null;  // id_reading of the last-clicked row (chain-highlight anchor)
let rvRowByIdReading = new Map(); // id_reading (string) -> row object, rebuilt on every load
let rvReferenceMode = "id";      // "id" (default) | "order" - RJ, 2026-09-23 (4th round): "add a
                                  // new referencing mode ... by row order" - see rvFindChainRows.

// Inline editing (RJ, 2026-09-25: "I want to be able to update the
// following columns reading prev date, reading date, reading_type
// (dropdown, id not description), prev_value, value, reading_usage,
// corrected_usage, ready usage. Only this columns."). Mirrors app/core/
// reading_validation.py's EDITABLE_COLUMNS / CASCADING_COLUMNS - the
// server is the authority for which DB column each key maps to and
// which kind it coerces to, this is just the client-side column set
// used to decide which cells render as inputs and which edits cascade.
const RV_EDITABLE_COLUMNS = new Set([
  "reading_prev_date", "reading_date", "reading_type",
  "prev_value", "reading", "metered_usage", "corrected_usage", "bill_ready_usage",
]);
const RV_CASCADE_COLUMNS = new Set(["reading_prev_date", "reading_date", "prev_value", "reading"]);
let rvOriginalByIdReading = new Map(); // id_reading -> {col: original display string}, snapshot at load
let rvDirtyIds = new Set();            // id_reading of every row with at least one pending edit
let rvReadingTypeOptions = [];         // [{code, description}], fetched once and cached
let rvReadingTypeOptionsLoaded = false;

// RJ, 2026-09-25 (follow-up round): "the dates, remove the :0000" - every
// reading_prev_date/reading_date in this app's real data is always a
// midnight timestamp (the time-of-day portion never carries real
// information), so displaying " 00:00:00" on every single row is just
// noise. Stripped once here, at row-ingestion time (rvSetRows), rather
// than only at render time, so the STRIPPED string is what ends up in
// rvOriginalByIdReading's snapshot too - keeps the original-vs-edited
// diffing, the cascade, and what's ultimately sent as old_value/new_value
// to /api/reading-validation/generate-script all consistent with what's
// on screen. A row with a genuine non-midnight time (none seen in this
// app's own data, but just in case) is left completely alone - the
// regex only matches an exact " 00:00:00" suffix.
function rvStripMidnightTime(v) {
  if (typeof v !== "string") return v;
  return v.replace(/ 00:00:00$/, "");
}

// RJ, 2026-09-25 (follow-up round): "the corrected usage, reading usage
// and ready usage should automatically be updated as well if you change
// the value. for reading usage, it is equal to the value - prev_value,
// for [corrected usage and ready usage] it is equal to the value - prev
// value, multiplied by the multiplier column". Recomputes all three
// usage columns on ONE row from its current reading/prev_value/
// usage_multiplier - called after any direct edit to reading/prev_value
// AND after rvCascadeEdit propagates a new reading/prev_value onto a
// linked row, so the usage columns stay correct however the value
// actually changed. usage_multiplier comes straight from build_readings_
// query's own GCGT_ME_USAGE_TYPE_METER join (see reading_validation.py);
// missing/non-numeric multiplier falls back to 1 (no scaling) rather than
// blanking the usage columns out.
function rvFormatUsageNumber(n) {
  if (!Number.isFinite(n)) return "";
  const rounded = Math.round(n * 1e6) / 1e6; // guard against float noise (0.1+0.2 style)
  return String(rounded);
}

function rvRecalcUsage(row) {
  const val = parseFloat(row.reading);
  const prev = parseFloat(row.prev_value);
  if (Number.isNaN(val) || Number.isNaN(prev)) return;
  const diff = val - prev;
  const multiplierRaw = parseFloat(row.usage_multiplier);
  const multiplier = Number.isFinite(multiplierRaw) ? multiplierRaw : 1;
  row.metered_usage = rvFormatUsageNumber(diff);
  row.corrected_usage = rvFormatUsageNumber(diff * multiplier);
  row.bill_ready_usage = rvFormatUsageNumber(diff * multiplier);
}

function rvVisibleIndices() {
  const billingPeriod = $("#rv-filter-billing-period").value;
  const dateFrom = $("#rv-filter-date-from").value;
  const dateTo = $("#rv-filter-date-to").value;
  const readingType = $("#rv-filter-reading-type").value;
  const readStatus = $("#rv-filter-read-status").value;
  const usageType = $("#rv-filter-usage-type").value;
  const search = $("#rv-search-box").value.trim().toLowerCase();
  return rvRows
    .map((r, i) => [r, i])
    .filter(([r]) => {
      if (billingPeriod && String(r.billing_period ?? "") !== billingPeriod) return false;
      if (readingType && (r.reading_type ?? "") !== readingType) return false;
      if (readStatus && (r.read_status ?? "") !== readStatus) return false;
      if (usageType && (r.usage_name ?? "") !== usageType) return false;
      if (dateFrom || dateTo) {
        // reading_date arrives already display-formatted (diff_engine.
        // cell_display), which for a date/datetime column is "YYYY-MM-DD"
        // or "YYYY-MM-DD HH:MM:SS" - a plain string compare against an
        // ISO <input type=date> value works fine either way since both
        // start with YYYY-MM-DD.
        const rd = String(r.reading_date ?? "");
        if (!rd) return false;
        if (dateFrom && rd < dateFrom) return false;
        if (dateTo && rd.slice(0, 10) > dateTo) return false;
      }
      if (search) {
        const hay = Object.values(r).join(" ").toLowerCase();
        if (!hay.includes(search)) return false;
      }
      return true;
    })
    .map(([, i]) => i);
}

// Column definitions, in display order, each tagged with the smallest
// detail tier it appears in - a column shows at its own tier AND every
// wider one ("important" < "medium" < "full"). RJ, 2026-09-23 (2nd
// round): "we will have 3 option for the detail, the full option,
// medium option (what we display now by defualt) and important detail
// which will be: [his own explicit, smaller column list]". So "medium"
// is exactly the column set this page already defaulted to before this
// round (still the default), "important" is RJ's new, smaller list
// (verbatim order), and "full" is everything, including the columns
// that were already "all details"-only. id_last_reading is deliberately
// NOT a visible column at any tier - it exists purely so the click-to-
// highlight chain logic below can look it up per row.
const RV_COLUMN_DEFS = [
  { key: "niss", label: "NISS", tier: "important" },
  { key: "id_reading", label: "ID Reading", tier: "important" },
  { key: "company_meter_num", label: "Meter #", tier: "important" },
  { key: "reading_type", label: "Reading Type", tier: "important", editable: "dropdown" },
  { key: "reading_prev_date", label: "Prev. Date", tier: "important", editable: "text" },
  { key: "reading_date", label: "Reading Date", tier: "important", editable: "text" },
  { key: "usage_name", label: "Consumption Type", tier: "important" },
  { key: "read_status", label: "Read Status", tier: "important" },
  { key: "prev_value", label: "Prev. Value", tier: "important", editable: "text" },
  { key: "reading", label: "Reading", tier: "important", editable: "text" },
  { key: "metered_usage", label: "Metered Usage", tier: "important", editable: "text" },
  { key: "corrected_usage", label: "Corrected Usage", tier: "important", editable: "text" },
  { key: "bill_ready_usage", label: "Bill Ready Usage", tier: "important", editable: "text" },
  { key: "ind_estimate", label: "Estimated?", tier: "important" },
  { key: "billing_period", label: "Billing Period", tier: "important" },
  { key: "id_sector_supply", label: "Sector Supply", tier: "medium" },
  { key: "id_measuring_point", label: "Measuring Point", tier: "medium" },
  { key: "id_device", label: "ID Device", tier: "medium" },
  { key: "model_name", label: "Model", tier: "medium" },
  { key: "usage_code", label: "Usage Code", tier: "medium" },
  { key: "reading_unit", label: "Unit", tier: "medium" },
  { key: "corrected_unit", label: "Corrected Unit", tier: "medium" },
  { key: "bill_unit", label: "Bill Unit", tier: "medium" },
  { key: "ind_negative_usage", label: "Negative?", tier: "medium" },
  { key: "reading_time_ts", label: "Reading Time", tier: "full" },
  { key: "update_user", label: "Update User", tier: "full" },
  { key: "power_factor", label: "Power Factor", tier: "full" },
  { key: "ind_modreb", label: "Modif. Reb.?", tier: "full" },
  { key: "reader_name", label: "Reader", tier: "full" },
  { key: "reading_origin", label: "Origin", tier: "full" },
  { key: "digitizer", label: "Digitizer", tier: "full" },
];
const RV_TIER_RANK = { important: 0, medium: 1, full: 2 };

function rvColumnDefs() {
  const rank = RV_TIER_RANK[rvDetailLevel] ?? RV_TIER_RANK.medium;
  return RV_COLUMN_DEFS.filter((c) => RV_TIER_RANK[c.tier] <= rank);
}

// Header is rebuilt on every render (detail-level toggle changes the
// column set) - same "rebuild + rewire each time" approach bcState's
// dynamic-column tables already use (see bcWireSortableHeaders above),
// rather than the wire-once hierWireSortableHeaders pattern the old
// static header used.
function rvRenderTableHeader() {
  const headRow = document.querySelector("#rv-table thead tr");
  headRow.innerHTML = rvColumnDefs().map((c) => {
    const arrow = rvSortKey === c.key ? `<span class="stats-table-sort-arrow">${rvSortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th class="stats-table-th-sortable" data-sort="${c.key}">${escapeHtml(c.label)}${arrow}</th>`;
  }).join("");
  headRow.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      rvSortDir = rvSortKey === key ? -rvSortDir : 1;
      rvSortKey = key;
      rvRenderTable();
    });
  });
}

// Reading-chain highlight (RJ, 2026-09-23: "each reading is being
// referenced by id_reading and id_last_reading, so the id_reading of a
// row, is the id_last reading of the next row ... highlight the
// reference if a row is clicked, same with the reading date ... and
// value reference from a prev_value of the previous row"). Clicking a
// row highlights: the row it chains FROM (its own id_last_reading ==
// that row's id_reading) and the row that chains FROM it (whose
// id_last_reading == this row's id_reading) - plus the specific
// date/value cells that carry that relationship on both rows.
// RJ, 2026-09-23 (2nd round): "change the row color for the referencing
// rows, and a different color for readings and dates" - so the linked
// ROW gets its own color (rv-row-linked, distinct from rv-row-selected),
// and the two kinds of linked CELL get their own separate colors too:
// rv-cell-linked-date for the reading_date/reading_prev_date pair,
// rv-cell-linked-value for the reading/prev_value pair (see styles.css).
//
// RJ, 2026-09-23 (4th round): "add a new referencing mode ... by row
// order, the idea is the first row is referencing to its lower row that
// it is following, for example row 1 reading prev date and prev value,
// references row 2 reading date and value". So there are now two ways
// to find the "prev"/"next" linked row for a click, chosen by
// rvReferenceMode ("id", the default, or "order"):
//   - "id": via the real ID_READING/ID_LAST_READING FK chain, same as
//     before - independent of whatever order the table happens to be
//     sorted/filtered into.
//   - "order": purely positional - whichever row is physically the next
//     <tr> below the clicked one in the CURRENTLY RENDERED table (so it
//     respects the active sort/filter) is treated as the row the
//     clicked one's prev_date/prev_value refer to, and the row directly
//     above is treated as the row that refers to the clicked one. This
//     is a plain DOM-sibling lookup, not a data lookup, precisely
//     because "row order" means screen position, not any DB relationship.
// Once prevTr/nextTr are found, the actual cell-highlighting is
// identical for both modes.
//
// Takes the id_reading straight off `selRow` itself (data-id-reading)
// rather than the globally-selected/clicked row, so this same function
// can also drive edit cascading (rvCascadeEdit) for ANY row - not only
// whichever one the user last clicked to highlight.
function rvFindChainRows(selRow) {
  const idReading = selRow.dataset.idReading;
  if (rvReferenceMode === "order") {
    return { prevTr: selRow.nextElementSibling?.hasAttribute("data-id-reading") ? selRow.nextElementSibling : null,
             nextTr: selRow.previousElementSibling?.hasAttribute("data-id-reading") ? selRow.previousElementSibling : null };
  }
  const tbody = document.querySelector("#rv-table tbody");
  const cur = rvRowByIdReading.get(idReading);
  let prevTr = null;
  if (cur) {
    const prevKey = cur.id_last_reading !== null && cur.id_last_reading !== undefined ? String(cur.id_last_reading) : null;
    if (prevKey && rvRowByIdReading.has(prevKey)) {
      prevTr = tbody.querySelector(`tr[data-id-reading="${CSS.escape(prevKey)}"]`);
    }
  }
  const nextTr = tbody.querySelector(`tr[data-id-last-reading="${CSS.escape(idReading)}"]`);
  return { prevTr, nextTr };
}

function rvApplyChainHighlight() {
  const tbody = document.querySelector("#rv-table tbody");
  tbody.querySelectorAll("tr[data-id-reading]").forEach((tr) => {
    tr.classList.remove("rv-row-selected", "rv-row-linked");
    tr.querySelectorAll("td[data-col]").forEach((td) => td.classList.remove("rv-cell-linked-date", "rv-cell-linked-value"));
  });
  if (rvSelectedIdReading === null) return;
  const selRow = tbody.querySelector(`tr[data-id-reading="${CSS.escape(rvSelectedIdReading)}"]`);
  if (!selRow) return;
  selRow.classList.add("rv-row-selected");

  const { prevTr, nextTr } = rvFindChainRows(selRow);

  // The row this one refers FROM (its own prev_date/prev_value == that
  // row's reading_date/reading).
  if (prevTr) {
    prevTr.classList.add("rv-row-linked");
    selRow.querySelector('td[data-col="reading_prev_date"]')?.classList.add("rv-cell-linked-date");
    prevTr.querySelector('td[data-col="reading_date"]')?.classList.add("rv-cell-linked-date");
    selRow.querySelector('td[data-col="prev_value"]')?.classList.add("rv-cell-linked-value");
    prevTr.querySelector('td[data-col="reading"]')?.classList.add("rv-cell-linked-value");
  }
  // The row that refers TO this one (its reading_date/reading == this
  // row's prev_date/prev_value).
  if (nextTr) {
    nextTr.classList.add("rv-row-linked");
    nextTr.querySelector('td[data-col="reading_prev_date"]')?.classList.add("rv-cell-linked-date");
    selRow.querySelector('td[data-col="reading_date"]')?.classList.add("rv-cell-linked-date");
    nextTr.querySelector('td[data-col="prev_value"]')?.classList.add("rv-cell-linked-value");
    selRow.querySelector('td[data-col="reading"]')?.classList.add("rv-cell-linked-value");
  }
}

// Renders one <td>'s contents for a given column def + row - a plain
// escaped text node for a display-only column, or an input/select for
// an editable one (RJ, 2026-09-25). The dropdown's selected value is
// the row's RAW reading_type_code (never the translated reading_type
// description it sits next to in the column - see EDITABLE_COLUMNS'
// own comment in reading_validation.py for why).
// RJ, 2026-09-25 (follow-up round): "make the values that are updated to
// be know, maybe make it bold and change the color to mark it as
// modified" - `dirty` marks THIS specific cell as changed from its
// original snapshot value (not just "this row has some edit somewhere",
// which rv-row-dirty already covers) - see rv-edit-dirty in styles.css
// for the actual bold+color treatment.
function rvRenderCell(c, r, rowIdx, dirty) {
  const dirtyClass = dirty ? " rv-edit-dirty" : "";
  if (c.editable === "dropdown") {
    const currentCode = r.reading_type_code ?? "";
    const options = rvReadingTypeOptions.length
      ? rvReadingTypeOptions
      : (currentCode ? [{ code: currentCode, description: r[c.key] ?? currentCode }] : []);
    const optionsHtml = options.map((o) => {
      const sel = String(o.code) === String(currentCode) ? " selected" : "";
      return `<option value="${escapeHtml(o.code)}"${sel}>${escapeHtml(o.description)}</option>`;
    }).join("");
    return `<select class="rv-edit-select${dirtyClass}" data-col="${c.key}" data-row-idx="${rowIdx}">${optionsHtml}</select>`;
  }
  if (c.editable === "text") {
    return `<input type="text" class="rv-edit-input${dirtyClass}" data-col="${c.key}" data-row-idx="${rowIdx}" value="${escapeHtml(r[c.key] ?? "")}" />`;
  }
  return escapeHtml(r[c.key] ?? "");
}

function rvRenderTable() {
  rvRenderTableHeader();
  const cols = rvColumnDefs();
  let indices = rvVisibleIndices();
  if (rvSortKey) {
    indices = [...indices].sort((a, b) => _hierCompareValues(rvRows[a][rvSortKey], rvRows[b][rvSortKey], rvSortDir));
  }
  const tbody = document.querySelector("#rv-table tbody");
  tbody.innerHTML = indices.length
    ? indices.map((i) => {
        const r = rvRows[i];
        const idReading = r.id_reading !== null && r.id_reading !== undefined ? String(r.id_reading) : "";
        const idLastReading = r.id_last_reading !== null && r.id_last_reading !== undefined ? String(r.id_last_reading) : "";
        const rowDirty = rvDirtyIds.has(idReading);
        const dirtyClass = rowDirty ? " rv-row-dirty" : "";
        const original = rowDirty ? rvOriginalByIdReading.get(idReading) : null;
        return `<tr class="${dirtyClass.trim()}" data-row-idx="${i}" data-id-reading="${escapeHtml(idReading)}" data-id-last-reading="${escapeHtml(idLastReading)}">` +
          cols.map((c) => {
            const cellDirty = c.editable && original
              ? String(rvEditableFieldValue(r, c.key)) !== String(original[c.key] ?? "")
              : false;
            return `<td data-col="${c.key}"${c.editable ? ' class="rv-cell-editable"' : ""}>${rvRenderCell(c, r, i, cellDirty)}</td>`;
          }).join("") +
          `</tr>`;
      }).join("")
    : `<tr><td colspan="${cols.length}" class="hint-text">No readings match the current filters.</td></tr>`;
  renderFilteredCount("#rv-filtered-count", indices.length, rvRows.length);
  rvApplyChainHighlight();
  rvUpdateDirtyUI();
  if (rvRows.length) {
    const vis = indices.map((i) => rvRows[i]);
    const n = vis.length;
    const status = (r) => String(r.read_status || "").toLowerCase();
    hxRenderDashboard("rv-dash", {
      gauges: [
        { label: "Billed readings", count: vis.filter((r) => status(r).includes("billed") && !status(r).includes("not billed")).length, total: n, c1: "#10b981", c2: "#22c55e" },
        { label: "Estimated", count: vis.filter((r) => String(r.ind_estimate) === "1").length, total: n, c1: "#f59e0b", c2: "#eab308" },
        { label: "Terminated not billed", count: vis.filter((r) => status(r).includes("terminated")).length, total: n, c1: "#ef4444", c2: "#f97316" },
      ],
      tiles: [
        { icon: "★", label: "Total bill-ready usage", value: hxSum(vis, "bill_ready_usage").toLocaleString(), accent: true },
        { icon: "📏", label: "Readings", value: n.toLocaleString() },
        { icon: "📅", label: "Billing periods", value: new Set(vis.map((r) => r.billing_period).filter(Boolean)).size.toLocaleString() },
        { icon: "🔢", label: "Meters", value: new Set(vis.map((r) => r.company_meter_num).filter(Boolean)).size.toLocaleString() },
        { icon: "✏️", label: "Pending edits", value: rvDirtyIds.size.toLocaleString() },
      ],
      split: { title: "Reading type", entries: hxCountBy(vis, (r) => r.reading_type) },
    });
  }
}

document.querySelector("#rv-table tbody").addEventListener("click", (ev) => {
  if (ev.target.closest(".rv-edit-input, .rv-edit-select")) return; // editing, not selecting
  const tr = ev.target.closest("tr[data-id-reading]");
  if (!tr) return;
  const idReading = tr.dataset.idReading;
  if (!idReading) return;
  rvSelectedIdReading = rvSelectedIdReading === idReading ? null : idReading;
  rvApplyChainHighlight();
});

// --- Inline editing (RJ, 2026-09-25) ----------------------------------

function rvEditableFieldValue(row, col) {
  return col === "reading_type" ? (row.reading_type_code ?? "") : (row[col] ?? "");
}

function rvMarkDirty(row) {
  const idReading = row.id_reading !== null && row.id_reading !== undefined ? String(row.id_reading) : "";
  if (!idReading) return;
  const original = rvOriginalByIdReading.get(idReading);
  if (!original) return;
  const stillDirty = [...RV_EDITABLE_COLUMNS].some((col) => String(rvEditableFieldValue(row, col)) !== String(original[col] ?? ""));
  if (stillDirty) rvDirtyIds.add(idReading);
  else rvDirtyIds.delete(idReading);
}

function rvUpdateDirtyUI() {
  const scriptCard = $("#rv-script-card");
  if (!scriptCard) return;
  scriptCard.hidden = rvRows.length === 0;
  $("#rv-dirty-count").textContent = rvDirtyIds.size
    ? `${rvDirtyIds.size} row(s) with pending edits.`
    : "No pending edits.";
  $("#rv-generate-btn").disabled = rvDirtyIds.size === 0;
}

// Cascades an edited date/value cell to the linked row (RJ, 2026-09-25:
// "Any update in the dates and reading values ... should reflect on the
// referenced value"). Single hop only - reuses rvFindChainRows (now
// keyed off the EDITED row, not necessarily the clicked/selected one),
// so it automatically follows whichever reference mode (ID chain or row
// order) is currently active, matching both of RJ's own examples.
function rvCascadeEdit(row, col, newVal) {
  if (!RV_CASCADE_COLUMNS.has(col)) return;
  const idReading = row.id_reading !== null && row.id_reading !== undefined ? String(row.id_reading) : "";
  const tbody = document.querySelector("#rv-table tbody");
  const selRow = tbody.querySelector(`tr[data-id-reading="${CSS.escape(idReading)}"]`);
  if (!selRow) return;
  const { prevTr, nextTr } = rvFindChainRows(selRow);
  const prevRow = prevTr ? rvRows[Number(prevTr.dataset.rowIdx)] : null;
  const nextRow = nextTr ? rvRows[Number(nextTr.dataset.rowIdx)] : null;

  if (col === "reading_prev_date" && prevRow) { prevRow.reading_date = newVal; rvMarkDirty(prevRow); }
  if (col === "prev_value" && prevRow) { prevRow.reading = newVal; rvRecalcUsage(prevRow); rvMarkDirty(prevRow); }
  if (col === "reading_date" && nextRow) { nextRow.reading_prev_date = newVal; rvMarkDirty(nextRow); }
  if (col === "reading" && nextRow) { nextRow.prev_value = newVal; rvRecalcUsage(nextRow); rvMarkDirty(nextRow); }
}

document.querySelector("#rv-table tbody").addEventListener("change", (ev) => {
  const el = ev.target;
  const isInput = el.classList.contains("rv-edit-input");
  const isSelect = el.classList.contains("rv-edit-select");
  if (!isInput && !isSelect) return;
  const rowIdx = Number(el.dataset.rowIdx);
  const col = el.dataset.col;
  const row = rvRows[rowIdx];
  if (!row) return;

  if (col === "reading_type") {
    const opt = rvReadingTypeOptions.find((o) => String(o.code) === String(el.value));
    row.reading_type_code = el.value;
    row.reading_type = opt ? opt.description : el.value;
  } else {
    row[col] = el.value;
  }
  if (col === "reading" || col === "prev_value") rvRecalcUsage(row);
  rvMarkDirty(row);
  rvCascadeEdit(row, col, el.value);
  rvRenderTable();
});

async function rvLoadReadingTypeOptionsOnce() {
  if (rvReadingTypeOptionsLoaded) return;
  try {
    const data = await api("/api/reading-validation/reading-types");
    rvReadingTypeOptions = data.reading_types || [];
    rvReadingTypeOptionsLoaded = true;
  } catch (err) {
    showToast("Couldn't load Reading Type options: " + err.message, true);
  }
}

// Snapshots the current (just-loaded) value of every editable column per
// row, keyed by id_reading - the ORIGINAL values rvMarkDirty compares
// against and, ultimately, what the generated script's WHERE clause
// guards on (RJ: "in the where clause, we need to put the original
// values").
function rvSnapshotOriginals() {
  rvOriginalByIdReading = new Map();
  rvDirtyIds = new Set();
  for (const row of rvRows) {
    const idReading = row.id_reading !== null && row.id_reading !== undefined ? String(row.id_reading) : "";
    if (!idReading) continue;
    const snapshot = {};
    for (const col of RV_EDITABLE_COLUMNS) snapshot[col] = rvEditableFieldValue(row, col);
    rvOriginalByIdReading.set(idReading, snapshot);
  }
}

function rvResetEdits() {
  for (const row of rvRows) {
    const idReading = row.id_reading !== null && row.id_reading !== undefined ? String(row.id_reading) : "";
    const original = rvOriginalByIdReading.get(idReading);
    if (!original) continue;
    for (const col of RV_EDITABLE_COLUMNS) {
      if (col === "reading_type") {
        row.reading_type_code = original.reading_type;
        const opt = rvReadingTypeOptions.find((o) => String(o.code) === String(original.reading_type));
        row.reading_type = opt ? opt.description : row.reading_type;
      } else {
        row[col] = original[col];
      }
    }
  }
  rvDirtyIds = new Set();
  rvRenderTable();
}
$("#rv-reset-edits-btn").addEventListener("click", rvResetEdits);

$("#rv-generate-btn").addEventListener("click", async () => {
  if (!rvDirtyIds.size) return;
  const program = $("#rv-program").value.trim();
  if (!program) { showToast("Enter the Jira/Program # this change is for.", true); return; }
  const auditUser = $("#rv-audit-user").value.trim();
  const rowsPayload = [];
  for (const idReading of rvDirtyIds) {
    const row = rvRows.find((r) => String(r.id_reading) === idReading);
    const original = rvOriginalByIdReading.get(idReading);
    if (!row || !original) continue;
    const edits = [];
    for (const col of RV_EDITABLE_COLUMNS) {
      const oldVal = original[col] ?? "";
      const newVal = rvEditableFieldValue(row, col);
      if (String(oldVal) !== String(newVal)) edits.push({ column: col, old_value: String(oldVal), new_value: String(newVal) });
    }
    if (edits.length) rowsPayload.push({ id_reading: idReading, edits });
  }
  if (!rowsPayload.length) { showToast("No changes to generate a script for.", true); return; }

  const btn = $("#rv-generate-btn");
  btn.disabled = true;
  try {
    const result = await api("/api/reading-validation/generate-script", {
      method: "POST",
      body: { rows: rowsPayload, program, audit_user: auditUser, kind: "update" },
    });
    $("#rv-script-output").textContent = result.sql_text;
    let msg = `Update script generated: ${result.statement_count} statement(s).`;
    if (result.warning_count) msg += `  ${result.warning_count} warning(s) - see comments at the top of the script.`;
    showToast(msg);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = rvDirtyIds.size === 0;
  }
});

$("#rv-script-copy-btn").addEventListener("click", async () => {
  const text = $("#rv-script-output").textContent;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Update script copied to clipboard.");
  } catch (_) {
    showToast("Couldn't copy - select and copy manually.", true);
  }
});

$("#rv-script-download-btn").addEventListener("click", () => {
  const text = $("#rv-script-output").textContent;
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "reading_validation_update.sql";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

document.querySelectorAll('input[name="rv-detail-level"]').forEach((radio) => {
  radio.addEventListener("change", (ev) => {
    if (!ev.target.checked) return;
    rvDetailLevel = ev.target.value;
    rvRenderTable();
  });
});

document.querySelectorAll('input[name="rv-reference-mode"]').forEach((radio) => {
  radio.addEventListener("change", (ev) => {
    if (!ev.target.checked) return;
    rvReferenceMode = ev.target.value;
    rvApplyChainHighlight();
  });
});

// Populates a <select> filter with the distinct values found in rvRows
// for one column, preserving the current selection when it's still a
// valid option - same convention as ibpDetect's own offered-service
// filter dropdown.
function rvPopulateFilter(selectId, column) {
  const sel = $(selectId);
  const current = sel.value;
  const values = [...new Set(rvRows.map((r) => r[column]).filter((v) => v !== null && v !== undefined && v !== ""))]
    .sort((a, b) => String(a).localeCompare(String(b)));
  sel.innerHTML = `<option value="">All</option>` +
    values.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  if (values.includes(current)) sel.value = current;
}

function rvPopulateAllFilters() {
  rvPopulateFilter("#rv-filter-billing-period", "billing_period");
  rvPopulateFilter("#rv-filter-reading-type", "reading_type");
  rvPopulateFilter("#rv-filter-read-status", "read_status");
  rvPopulateFilter("#rv-filter-usage-type", "usage_name");
}

["#rv-filter-billing-period", "#rv-filter-reading-type", "#rv-filter-read-status", "#rv-filter-usage-type"].forEach((id) => {
  $(id).addEventListener("change", rvRenderTable);
});
$("#rv-filter-date-from").addEventListener("change", rvRenderTable);
$("#rv-filter-date-to").addEventListener("change", rvRenderTable);
$("#rv-search-box").addEventListener("input", rvRenderTable);
$("#rv-filter-clear-btn").addEventListener("click", () => {
  $("#rv-filter-billing-period").value = "";
  $("#rv-filter-reading-type").value = "";
  $("#rv-filter-read-status").value = "";
  $("#rv-filter-usage-type").value = "";
  $("#rv-filter-date-from").value = "";
  $("#rv-filter-date-to").value = "";
  $("#rv-search-box").value = "";
  rvRenderTable();
});

// Common "new result set just arrived" bookkeeping - rebuilds the
// id_reading lookup map the chain-highlight feature needs and clears
// any highlight/sort left over from the previous supply, since row
// indices and id_reading values from a different supply are meaningless
// once you've switched supplies.
function rvSetRows(rows) {
  rvRows = rows || [];
  for (const row of rvRows) {
    row.reading_prev_date = rvStripMidnightTime(row.reading_prev_date);
    row.reading_date = rvStripMidnightTime(row.reading_date);
  }
  rvRowByIdReading = new Map(
    rvRows
      .filter((r) => r.id_reading !== null && r.id_reading !== undefined)
      .map((r) => [String(r.id_reading), r])
  );
  rvSelectedIdReading = null;
  rvSortKey = null;
  rvSortDir = 1;
  rvSnapshotOriginals();
}

async function rvLoadSupply(idSectorSupply, nissLabel) {
  $("#rv-search-status").textContent = "Loading readings…";
  await rvLoadReadingTypeOptionsOnce();
  try {
    const data = await api("/api/reading-validation/detect", {
      method: "POST",
      body: { id_sector_supply: String(idSectorSupply) },
    });
    rvActiveSupply = String(idSectorSupply);
    rvSetRows(data.rows);
    rvPopulateAllFilters();
    $("#rv-results-card").hidden = false;
    $("#rv-results-niss").textContent = nissLabel ? `— ${nissLabel} (sector supply ${idSectorSupply})` : `— sector supply ${idSectorSupply}`;
    $("#rv-search-status").textContent = `${rvRows.length} reading(s) found.`;
    rvRenderTable();
  } catch (err) {
    $("#rv-search-status").textContent = "";
    showToast(err.message, true);
  }
}

function rvRenderSupplyTabs() {
  const nav = $("#rv-supply-tabs");
  if (rvSupplies.length < 1) { nav.hidden = true; nav.innerHTML = ""; return; }
  nav.hidden = false;
  nav.innerHTML = rvSupplies.map((s) => {
    const label = `${s.niss || s.id_sector_supply}${s.offered_service_desc ? " — " + s.offered_service_desc : ""}`;
    const active = String(s.id_sector_supply) === rvActiveSupply ? "is-active" : "";
    return `<button type="button" class="da-subnav-btn ${active}" data-rv-supply="${escapeHtml(String(s.id_sector_supply))}">${escapeHtml(label)}</button>`;
  }).join("");
  nav.querySelectorAll("[data-rv-supply]").forEach((btn) => {
    btn.addEventListener("click", () => {
      nav.querySelectorAll(".da-subnav-btn").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      const supply = rvSupplies.find((s) => String(s.id_sector_supply) === btn.dataset.rvSupply);
      rvLoadSupply(btn.dataset.rvSupply, supply?.niss);
    });
  });
}

$("#rv-supply-search-btn").addEventListener("click", async () => {
  const raw = $("#rv-supply-input").value.trim();
  if (!raw) { showToast("Enter a sector supply id or NISS.", true); return; }
  rvSupplies = [];
  rvRenderSupplyTabs();
  const body = /^\d+$/.test(raw) ? { id_sector_supply: raw } : { niss: raw };
  const btn = $("#rv-supply-search-btn");
  btn.disabled = true;
  await rvLoadReadingTypeOptionsOnce();
  try {
    const data = await api("/api/reading-validation/detect", { method: "POST", body });
    rvActiveSupply = String(data.id_sector_supply);
    rvSetRows(data.rows);
    rvPopulateAllFilters();
    $("#rv-results-card").hidden = false;
    $("#rv-results-niss").textContent = `— sector supply ${data.id_sector_supply}`;
    $("#rv-search-status").textContent = `${rvRows.length} reading(s) found.`;
    rvRenderTable();
  } catch (err) {
    $("#rv-search-status").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

$("#rv-account-search-btn").addEventListener("click", async () => {
  const account = $("#rv-account-input").value.trim();
  if (!account) { showToast("Enter an account number.", true); return; }
  const btn = $("#rv-account-search-btn");
  btn.disabled = true;
  $("#rv-search-status").textContent = "Searching…";
  try {
    const data = await api("/api/reading-validation/search-account", { method: "POST", body: { account_number: account } });
    rvSupplies = data.supplies || [];
    if (!rvSupplies.length) {
      $("#rv-search-status").textContent = `No sector supply found for account ${account}.`;
      rvRenderSupplyTabs();
      return;
    }
    rvActiveSupply = String(rvSupplies[0].id_sector_supply);
    rvRenderSupplyTabs();
    $("#rv-search-status").textContent = `${rvSupplies.length} supply(ies) found for account ${account}.`;
    await rvLoadSupply(rvSupplies[0].id_sector_supply, rvSupplies[0].niss);
  } catch (err) {
    $("#rv-search-status").textContent = "";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

$("#rv-export-csv-btn").addEventListener("click", () => {
  const indices = rvVisibleIndices();
  if (!indices.length) return;
  const csvKeys = rvColumnDefs().map((c) => c.key);
  const lines = [csvKeys.join(",")];
  indices.forEach((i) => {
    const r = rvRows[i];
    lines.push(csvKeys.map((k) => `"${String(r[k] ?? "").replace(/"/g, '""')}"`).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "reading_validation.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// ---------------- Bulk Checker ----------------
// Ported from the standalone EWA Bulk Checker project (2026-09-12, RJ's
// request - see app/core/bulk_checker.py's module docstring for the full
// provenance note). Finds bulk accounts with no generated file yet for a
// billing cycle and drills into one account's bills. This first round
// covers search/filter/drill-down/notes/export - saved searches, Trend
// history, and multi-account bulk export already have working backend
// routes (see web/server.py) but no frontend UI yet; a follow-up round
// can wire those in once this core is live-verified.
const bcState = {
  columns: [], rows: [],          // raw values from the last search (rows[i][j] aligns with columns[j])
  statusFilter: "all",
  search: "",
  amountMin: "", amountMax: "",   // pending_amount range filter, applied client-side (RJ, 2026-09-13)
  billingPeriod: "",
  notes: {},                      // account_number -> {status, note, updated_by, updated_at_utc}
  noteModalAccount: null,

  detailColumns: [], detailRows: [],
  detailAccount: null,
  detailFilter: "all",
  detailSearch: "",

  billingPeriodsLoaded: false,
};

// Column-name-keyed sort state for the Results and Bills-detail tables
// (RJ, 2026-09-13: "sortable table" from the feature roadmap). Reuses
// _hierCompareValues (generic, not actually hierarchy-specific) since
// both tables store rows as plain arrays aligned to a columns array,
// same shape that comparator already expects.
let bcSortKey = null, bcSortDir = 1;
let bcDetailSortKey = null, bcDetailSortDir = 1;

// Same idea as hierWireSortableHeaders, but Bulk Checker's <thead> is
// rebuilt from scratch (dynamic SQL columns) on every render, so the
// click listeners AND the current sort arrow both need to be reapplied
// each time rather than wired once at page load.
function bcWireSortableHeaders(tableSelector, getKey, setKey, getDir, setDir, rerender) {
  document.querySelectorAll(`${tableSelector} thead th[data-sort]`).forEach((th) => {
    if (th.dataset.sort === getKey()) {
      th.insertAdjacentHTML("beforeend", `<span class="stats-table-sort-arrow">${getDir() === 1 ? "▲" : "▼"}</span>`);
    }
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      setDir(getKey() === key ? -getDir() : 1);
      setKey(key);
      rerender();
    });
  });
}

const BC_NOTE_STATUS_LABELS = { open: "Open", being_handled: "Being handled", resolved: "Resolved" };
function bcNoteIcon(status) {
  if (status === "being_handled") return "🛠";
  if (status === "resolved") return "✅";
  return "📝";
}

// Case-insensitive column lookup - same reasoning as the app's existing
// _da_col helper (app/ui/main_window.py, web/server.py): SQL Server
// column names come back however the driver/query defines them.
function bcColIdx(columns, name) {
  return columns.findIndex((c) => c.toLowerCase() === name.toLowerCase());
}
function bcCell(columns, row, name) {
  const idx = bcColIdx(columns, name);
  return idx === -1 ? "" : row[idx];
}

const BC_LABEL_OVERRIDES = {
  ACCOUNT_NUMBER: "Bulk Account", NEXT_GROUP_DATE: "Next Group Date", IND_GROUP_BILLS: "Group Bills",
  send_date: "Lot Send Date", total_reg: "Total Reg", total_amount: "Total Amount",
  pending_amount: "Pending Amount", process_date: "Process Date", file_number: "File Number",
  num_account: "# Accounts in Lot", is_pending: "Status", BULK_ACCOUNT: "Bulk Account",
  SUB_ACCOUNT: "Sub Account", FILE_NUMBER: "File Number", send_date_RV: "Lot Send Date",
  process_date_RV: "Lot Process Date", niss: "NISS", id_bill: "ID Bill", num_services: "# Services",
};
function bcPrettifyLabel(name) {
  if (BC_LABEL_OVERRIDES[name]) return BC_LABEL_OVERRIDES[name];
  return name.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2")
    .split(" ").filter(Boolean).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

const BC_ISO_DATETIME_RE = /^\d{4}-\d{2}-\d{2}T/;
function bcFormatCell(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string" && BC_ISO_DATETIME_RE.test(value)) return value.slice(0, 10);
  return String(value);
}

// Columns hidden from the main results grid - shown instead as the row's
// highlight color (is_pending -> red "not billed" row) or a small badge
// (has_missing_bill / has_bill_in_invoicing), same idea as EWA's own
// PENDING_HIDDEN_COLUMNS.
const BC_RESULTS_HIDDEN_COLUMNS = new Set(["ID_PAYMENT_FORM_BUNCHER", "has_missing_bill", "has_bill_in_invoicing", "is_pending"]);

// Billing cycles always start on the 1st (confirmed live: every
// GCCOM_BILLING_PERIOD row's INITIAL_DATE/END_DATE spans exactly one
// calendar month) - so the date pickers only need a month, not a full
// date. bcMonthToDate turns the <input type="month"> value ("YYYY-MM")
// into the actual "YYYY-MM-01" ISO date the backend expects; the reverse
// (bcDateToMonth) is used when auto-filling from the billing-period
// picker below.
function bcMonthToDate(monthStr) {
  return monthStr ? `${monthStr}-01` : "";
}
function bcDateToMonth(dateStr) {
  return dateStr ? dateStr.slice(0, 7) : "";
}
// One calendar month after the given "YYYY-MM" string.
function bcNextMonth(monthStr) {
  const [y, m] = monthStr.split("-").map(Number);
  const d = new Date(y, m, 1); // m is already 1-indexed-next since Date's month arg is 0-indexed
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function bcOnPageShown() {
  if (!bcState.billingPeriodsLoaded) {
    bcLoadBillingPeriods();
    bcState.billingPeriodsLoaded = true;
  }
  if (!$("#bc-date-from").value && !$("#bc-date-to").value) {
    const now = new Date();
    const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
    $("#bc-date-from").value = thisMonth;
    $("#bc-date-to").value = bcNextMonth(thisMonth);
  }
}

// account_number -> billing period info ({initialDate}), keyed for the
// auto-fill-dates-on-click behavior below.
const bcPeriodsById = {};

async function bcLoadBillingPeriods() {
  try {
    const data = await api("/api/bulk-checker/billing-periods");
    if (!data.available || !data.rows.length) return;
    const idIdx = bcColIdx(data.columns, "ID_BILLING_PERIOD");
    // Prefer DESCRIPTION; PERIOD_NAME is the same live human label
    // (e.g. "9-September 2026") as a fallback if DESCRIPTION is blank.
    const descIdx = bcColIdx(data.columns, "DESCRIPTION");
    const nameIdx = bcColIdx(data.columns, "PERIOD_NAME");
    const initialDateIdx = bcColIdx(data.columns, "INITIAL_DATE");
    if (idIdx === -1) return;
    const menu = $("#bc-period-menu");
    menu.innerHTML = "";
    data.rows.forEach((row) => {
      const id = String(row[idIdx]);
      const description = (descIdx !== -1 && row[descIdx]) || (nameIdx !== -1 && row[nameIdx]) || "";
      if (initialDateIdx !== -1 && row[initialDateIdx]) {
        bcPeriodsById[id] = { initialDate: String(row[initialDateIdx]) };
      }
      const item = document.createElement("div");
      item.className = "dropdown-item";
      item.textContent = description ? `${id} — ${description}` : id;
      item.addEventListener("click", () => {
        $("#bc-billing-period").value = id;
        const info = bcPeriodsById[id];
        if (info) {
          const fromMonth = bcDateToMonth(info.initialDate);
          $("#bc-date-from").value = fromMonth;
          $("#bc-date-to").value = bcNextMonth(fromMonth);
        }
        menu.hidden = true;
      });
      menu.appendChild(item);
    });
    $("#bc-period-btn").hidden = false;
  } catch (_) {
    // Best-effort convenience only - typing the ID by hand always works.
  }
}

$("#bc-period-btn").addEventListener("click", () => {
  $("#bc-period-menu").hidden = !$("#bc-period-menu").hidden;
});
document.addEventListener("click", (e) => {
  if (!$("#bc-period-dropdown").contains(e.target)) $("#bc-period-menu").hidden = true;
});

// ---------------- Search ----------------
$("#bc-search-btn").addEventListener("click", bcRunSearch);
$("#bc-status-filter").addEventListener("change", () => {
  bcState.statusFilter = $("#bc-status-filter").value;
  if (bcState.columns.length) bcRunSearch();
});
$("#bc-search-box").addEventListener("input", () => {
  bcState.search = $("#bc-search-box").value;
  bcRenderResultsTable();
});
$("#bc-amount-min").addEventListener("input", () => {
  bcState.amountMin = $("#bc-amount-min").value;
  bcRenderResultsTable();
});
$("#bc-amount-max").addEventListener("input", () => {
  bcState.amountMax = $("#bc-amount-max").value;
  bcRenderResultsTable();
});

async function bcRunSearch() {
  const date_from = bcMonthToDate($("#bc-date-from").value);
  const date_to = bcMonthToDate($("#bc-date-to").value);
  const billing_period = $("#bc-billing-period").value.trim();
  if (!date_from || !date_to || !billing_period) {
    showToast("Fill in both dates and the billing period.", true);
    return;
  }
  const btn = $("#bc-search-btn");
  btn.disabled = true;
  $("#bc-search-status").textContent = "Searching…";
  try {
    const result = await api("/api/bulk-checker/search", {
      method: "POST",
      body: { date_from, date_to, billing_period, status_filter: bcState.statusFilter },
    });
    bcState.columns = result.columns;
    bcState.rows = result.display_rows;
    bcState.billingPeriod = billing_period;
    $("#bc-search-status").textContent =
      `${result.row_count} row(s) in ${result.elapsed_ms.toFixed(0)} ms — ${result.pending_count} pending, ${result.missing_bill_count} missing bill, ${result.in_invoicing_count} in invoicing.`;
    $("#bc-results-card").hidden = false;
    $("#bc-detail-card").hidden = true;
    bcRenderKpiRow(result);
    await bcLoadNotesForPeriod(billing_period);
    bcRenderResultsTable();
    if (bcState.statusFilter === "all") {
      bcLoadTrendContext(billing_period);
    } else {
      $("#bc-trend-hint").hidden = true;
    }
  } catch (err) {
    $("#bc-search-status").textContent = "Search failed.";
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ---------------- Trend context (RJ, 2026-09-13: "context and trends,
// not just totals") ----------------
// /api/bulk-checker/trend returns one row per billing period ever fully
// ("all"-filter) searched (bulk_checker_db._SEARCH_HISTORY_TABLE keys on
// billing_period, so it's always that period's LATEST full search, not a
// log of every search run). There's no due-date/aging concept anywhere
// in the analyst-supplied queries this page is built on, so rather than
// invent one, "trend" here means: compare this billing period's numbers
// to the nearest lower billing period that's ever been fully searched -
// labeled by period, not by calendar assumption, since periods aren't
// guaranteed to have been searched back-to-back.
async function bcLoadTrendContext(currentBillingPeriod) {
  try {
    const data = await api("/api/bulk-checker/trend");
    const entries = data.entries || [];
    const idx = entries.findIndex((e) => String(e.billing_period) === String(currentBillingPeriod));
    if (idx <= 0) { $("#bc-trend-hint").hidden = true; return; }
    bcRenderTrendContext(entries[idx], entries[idx - 1]);
  } catch (_) {
    $("#bc-trend-hint").hidden = true;
  }
}

function bcTrendDelta(curr, prev) {
  const diff = curr - prev;
  if (prev === 0) return diff === 0 ? "" : " (new)";
  const pctVal = (diff / Math.abs(prev)) * 100;
  const arrow = diff > 0 ? "▲" : diff < 0 ? "▼" : "→";
  return ` ${arrow}${Math.abs(pctVal).toFixed(1)}%`;
}

function bcRenderTrendContext(current, previous) {
  const money = (v) => Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
  $("#bc-trend-hint").hidden = false;
  $("#bc-trend-hint").textContent =
    `📈 vs billing period ${previous.billing_period} (last searched ${bcFormatCell(previous.searched_at_utc)}): ` +
    `Total ${previous.total} → ${current.total}${bcTrendDelta(current.total, previous.total)}, ` +
    `Outstanding ${money(previous.outstanding_amount)} → ${money(current.outstanding_amount)}${bcTrendDelta(current.outstanding_amount, previous.outstanding_amount)}`;
}

// ---------------- Analyze One Account (RJ, 2026-09-13) - a standalone
// lookup straight into the Bills drill-down for one account, without
// needing to run the bulk search above first. Reuses the same cycle
// dates + billing period fields since build_bill_detail_sql still needs
// them to scope the query. ----------------
function bcRunSingleAccountLookup() {
  const accountNumber = $("#bc-single-account").value.trim();
  const billing_period = $("#bc-billing-period").value.trim();
  if (!accountNumber) {
    showToast("Enter an account number.", true);
    return;
  }
  if (!$("#bc-date-from").value || !$("#bc-date-to").value || !billing_period) {
    showToast("Fill in the cycle dates and billing period above first.", true);
    return;
  }
  bcState.billingPeriod = billing_period;
  bcOpenDetail(accountNumber);
}
$("#bc-single-account-btn").addEventListener("click", bcRunSingleAccountLookup);
$("#bc-single-account").addEventListener("keydown", (e) => {
  if (e.key === "Enter") bcRunSingleAccountLookup();
});

// ---------------- Saved Searches (backend already existed from round 1;
// wired to the UI now - RJ, 2026-09-13) ----------------
async function bcLoadSavedSearches() {
  try {
    const data = await api("/api/bulk-checker/saved-searches");
    const menu = $("#bc-saved-menu");
    menu.innerHTML = "";
    if (!data.searches.length) {
      menu.innerHTML = `<div class="dropdown-item hint-text">No saved searches yet.</div>`;
    }
    data.searches.forEach((s) => {
      const item = document.createElement("div");
      item.className = "dropdown-item";
      item.style.display = "flex";
      item.style.justifyContent = "space-between";
      item.style.alignItems = "center";
      item.style.gap = "8px";
      const label = document.createElement("span");
      label.textContent = `${s.name} — period ${s.billing_period}`;
      label.style.cursor = "pointer";
      label.title = `${s.date_from} to ${s.date_to}`;
      label.addEventListener("click", () => {
        $("#bc-date-from").value = bcDateToMonth(s.date_from);
        $("#bc-date-to").value = bcDateToMonth(s.date_to);
        $("#bc-billing-period").value = s.billing_period;
        $("#bc-saved-menu").hidden = true;
        bcRunSearch();
      });
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn btn-pill-sm";
      delBtn.textContent = "✕";
      delBtn.title = "Delete this saved search";
      delBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          await api(`/api/bulk-checker/saved-searches/${s.id}`, { method: "DELETE" });
          bcLoadSavedSearches();
        } catch (err) {
          showToast(err.message || "Could not delete saved search.", true);
        }
      });
      item.appendChild(label);
      item.appendChild(delBtn);
      menu.appendChild(item);
    });
  } catch (_) {
    // Best-effort - the search/detail flows work fine without this.
  }
}

$("#bc-saved-btn").addEventListener("click", () => {
  const menu = $("#bc-saved-menu");
  if (menu.hidden) bcLoadSavedSearches();
  menu.hidden = !menu.hidden;
});
document.addEventListener("click", (e) => {
  if (!$("#bc-saved-dropdown").contains(e.target)) $("#bc-saved-menu").hidden = true;
});

$("#bc-save-search-btn").addEventListener("click", async () => {
  const date_from = bcMonthToDate($("#bc-date-from").value);
  const date_to = bcMonthToDate($("#bc-date-to").value);
  const billing_period = $("#bc-billing-period").value.trim();
  if (!date_from || !date_to || !billing_period) {
    showToast("Fill in both dates and the billing period first.", true);
    return;
  }
  const name = prompt("Name this search (e.g. \"This month's outstanding\"):");
  if (!name || !name.trim()) return;
  try {
    await api("/api/bulk-checker/saved-searches", {
      method: "POST",
      body: { name: name.trim(), date_from, date_to, billing_period },
    });
    showToast("Search saved.");
  } catch (err) {
    showToast(err.message || "Could not save search.", true);
  }
});

// Cards whose value maps directly onto a STATUS_FILTERS value can act as
// a filter toggle (RJ, 2026-09-13: "make every component actionable") -
// clicking one sets that status filter and reruns the search; clicking
// the already-active one clears back to "all". "Total" and "Outstanding"
// have no matching single-status filter, so they stay plain.
function bcRenderKpiRow(result) {
  const cards = [
    ["rows", "📄", "Total", result.row_count, null],
    ["pending", "🕓", "Pending (no file)", result.pending_count, "pending"],
    ["missing", "⚠️", "Missing bill", result.missing_bill_count, "missing_bill"],
    ["invoicing", "🧾", "In invoicing", result.in_invoicing_count, "in_invoicing"],
    ["outstanding", "💰", "Outstanding", result.outstanding_amount.toLocaleString(undefined, { maximumFractionDigits: 2 }), null],
  ];
  hxGaugesAbove("#bc-kpi-row", [
    { label: "Pending (no file)", count: result.pending_count, total: result.row_count, c1: "#f59e0b", c2: "#f97316" },
    { label: "Missing bill", count: result.missing_bill_count, total: result.row_count, c1: "#ef4444", c2: "#ec4899" },
    { label: "In invoicing", count: result.in_invoicing_count, total: result.row_count, c1: "#6366f1", c2: "#06b6d4" },
  ]);
  $("#bc-kpi-row").innerHTML = cards.map(([kpi, icon, label, value, filterValue]) => {
    const clickable = filterValue !== null;
    const active = clickable && bcState.statusFilter === filterValue;
    return `<div class="kpi-card${clickable ? " kpi-card-clickable" : ""}${active ? " is-active" : ""}" data-kpi="${kpi}"` +
      `${clickable ? ` data-bc-status-filter="${filterValue}" title="Click to filter to this status - click again to clear"` : ""}>` +
      `<div class="kpi-value">${escapeHtml(String(value))}</div>` +
      `<div class="kpi-label"><span class="kpi-card-icon">${icon}</span>${label}</div></div>`;
  }).join("");
}

$("#bc-kpi-row").addEventListener("click", (ev) => {
  const card = ev.target.closest("[data-bc-status-filter]");
  if (!card) return;
  const filterValue = card.dataset.bcStatusFilter;
  bcState.statusFilter = bcState.statusFilter === filterValue ? "all" : filterValue;
  $("#bc-status-filter").value = bcState.statusFilter;
  bcRunSearch();
});

function bcVisibleColumnIdx() {
  return bcState.columns.map((c, i) => i).filter((i) => !BC_RESULTS_HIDDEN_COLUMNS.has(bcState.columns[i]));
}

function bcVisibleResultRows(ignoreFilters = false) {
  let rows = bcState.rows;
  if (!ignoreFilters) {
    const search = bcState.search.trim().toLowerCase();
    const visibleIdx = bcVisibleColumnIdx();
    if (search) rows = rows.filter((row) => visibleIdx.some((i) => String(row[i]).toLowerCase().includes(search)));

    // Amount-range filter (RJ, 2026-09-13) - client-side over the already-
    // loaded result set, same pattern as every other Results filter here.
    const amountIdx = bcColIdx(bcState.columns, "pending_amount");
    if (amountIdx !== -1) {
      const min = parseFloat(bcState.amountMin);
      const max = parseFloat(bcState.amountMax);
      if (!Number.isNaN(min)) rows = rows.filter((row) => { const v = parseFloat(row[amountIdx]); return !Number.isNaN(v) && v >= min; });
      if (!Number.isNaN(max)) rows = rows.filter((row) => { const v = parseFloat(row[amountIdx]); return !Number.isNaN(v) && v <= max; });
    }
  }

  if (bcSortKey) {
    const idx = bcColIdx(bcState.columns, bcSortKey);
    if (idx !== -1) rows = [...rows].sort((a, b) => _hierCompareValues(a[idx], b[idx], bcSortDir));
  }
  return rows;
}

function bcRenderResultsTable() {
  const visibleIdx = bcVisibleColumnIdx();
  const thead = $("#bc-results-table thead tr");
  thead.innerHTML = visibleIdx.map((i) =>
    `<th class="stats-table-th-sortable" data-sort="${escapeHtml(bcState.columns[i])}">${escapeHtml(bcPrettifyLabel(bcState.columns[i]))}</th>`
  ).join("") + `<th>Missing/Invoicing</th><th>Notes</th><th></th>`;
  bcWireSortableHeaders("#bc-results-table", () => bcSortKey, (k) => { bcSortKey = k; }, () => bcSortDir, (d) => { bcSortDir = d; }, bcRenderResultsTable);

  const accountIdx = bcColIdx(bcState.columns, "ACCOUNT_NUMBER");
  const isPendingIdx = bcColIdx(bcState.columns, "is_pending");
  const missingIdx = bcColIdx(bcState.columns, "has_missing_bill");
  const invoicingIdx = bcColIdx(bcState.columns, "has_bill_in_invoicing");

  const tbody = $("#bc-results-table tbody");
  tbody.innerHTML = "";
  const visibleRows = bcVisibleResultRows();
  $("#bc-results-hint").nextSibling; // no-op, keeps diff minimal if hint gains an id later
  visibleRows.forEach((row) => {
    const tr = document.createElement("tr");
    const isPending = isPendingIdx !== -1 && row[isPendingIdx] === "Yes";
    tr.className = isPending ? "row-not-billed" : "";
    tr.innerHTML = visibleIdx.map((i) => `<td>${escapeHtml(bcFormatCell(row[i]))}</td>`).join("");

    const flagsTd = document.createElement("td");
    const flags = [];
    if (missingIdx !== -1 && (row[missingIdx] === "1" || row[missingIdx] === "Yes" || row[missingIdx] === "true"))
      flags.push('<span class="hint-text">⚠️ missing bill</span>');
    if (invoicingIdx !== -1 && (row[invoicingIdx] === "1" || row[invoicingIdx] === "Yes" || row[invoicingIdx] === "true"))
      flags.push('<span class="hint-text">🧾 in invoicing</span>');
    flagsTd.innerHTML = flags.join("<br/>");
    tr.appendChild(flagsTd);

    const accountNumber = accountIdx !== -1 ? String(row[accountIdx]) : "";
    const noteTd = document.createElement("td");
    const note = bcState.notes[accountNumber];
    const noteBtn = document.createElement("button");
    noteBtn.type = "button";
    noteBtn.className = "btn btn-pill-sm";
    noteBtn.title = note ? `${BC_NOTE_STATUS_LABELS[note.status] || note.status}${note.note ? ": " + note.note : ""}` : "Add a note";
    noteBtn.textContent = note ? bcNoteIcon(note.status) : "📝";
    noteBtn.addEventListener("click", (e) => { e.stopPropagation(); bcOpenNoteModal(accountNumber); });
    noteTd.appendChild(noteBtn);
    tr.appendChild(noteTd);

    const billsTd = document.createElement("td");
    const billsBtn = document.createElement("button");
    billsBtn.type = "button";
    billsBtn.className = "btn btn-pill-sm";
    billsBtn.textContent = "Bills →";
    billsBtn.addEventListener("click", (e) => { e.stopPropagation(); bcOpenDetail(accountNumber); });
    billsTd.appendChild(billsBtn);
    tr.appendChild(billsTd);

    tr.addEventListener("click", () => { if (accountNumber) bcOpenDetail(accountNumber); });
    tbody.appendChild(tr);
  });
}

// ---------------- Notes ----------------
async function bcLoadNotesForPeriod(billingPeriod) {
  try {
    const data = await api(`/api/bulk-checker/notes?billing_period=${encodeURIComponent(billingPeriod)}`);
    bcState.notes = data.notes || {};
  } catch (_) {
    bcState.notes = {};
  }
}

function bcOpenNoteModal(accountNumber) {
  bcState.noteModalAccount = accountNumber;
  const existing = bcState.notes[accountNumber];
  $("#bc-note-modal-account").textContent = accountNumber;
  $("#bc-note-modal-status").value = existing ? existing.status : "open";
  $("#bc-note-modal-text").value = existing ? existing.note : "";
  $("#bc-note-modal-status-msg").textContent = "";
  $("#bc-note-modal-overlay").hidden = false;
}

function bcCloseNoteModal() { $("#bc-note-modal-overlay").hidden = true; }
$("#bc-note-modal-close-btn").addEventListener("click", bcCloseNoteModal);
$("#bc-note-modal-overlay").addEventListener("click", (e) => { if (e.target.id === "bc-note-modal-overlay") bcCloseNoteModal(); });

$("#bc-note-modal-save-btn").addEventListener("click", async () => {
  const accountNumber = bcState.noteModalAccount;
  if (!accountNumber) return;
  try {
    await api(`/api/bulk-checker/notes/${encodeURIComponent(accountNumber)}`, {
      method: "PUT",
      body: { billing_period: bcState.billingPeriod, status: $("#bc-note-modal-status").value, note: $("#bc-note-modal-text").value },
    });
    await bcLoadNotesForPeriod(bcState.billingPeriod);
    bcRenderResultsTable();
    showToast("Note saved.");
    bcCloseNoteModal();
  } catch (err) {
    $("#bc-note-modal-status-msg").textContent = err.message;
  }
});

$("#bc-note-modal-clear-btn").addEventListener("click", async () => {
  const accountNumber = bcState.noteModalAccount;
  if (!accountNumber) return;
  try {
    await api(`/api/bulk-checker/notes/${encodeURIComponent(accountNumber)}?billing_period=${encodeURIComponent(bcState.billingPeriod)}`, { method: "DELETE" });
    await bcLoadNotesForPeriod(bcState.billingPeriod);
    bcRenderResultsTable();
    showToast("Note cleared.");
    bcCloseNoteModal();
  } catch (err) {
    $("#bc-note-modal-status-msg").textContent = err.message;
  }
});

// ---------------- Detail drill-down ----------------
// ID_SECTOR_SUPPLY is fetched (see app/core/bulk_checker.py) only so the
// Readings button below can look up that supply's reading history - it's
// not a column an analyst needs to see or export, so it's hidden the same
// way BC_RESULTS_HIDDEN_COLUMNS hides internal-only columns on the main
// results grid.
const BC_DETAIL_HIDDEN_COLUMNS = new Set(["ID_SECTOR_SUPPLY"]);

async function bcOpenDetail(accountNumber) {
  const date_from = bcMonthToDate($("#bc-date-from").value);
  const date_to = bcMonthToDate($("#bc-date-to").value);
  const billing_period = bcState.billingPeriod || $("#bc-billing-period").value.trim();
  bcState.detailAccount = accountNumber;
  bcState.detailFilter = "all";
  $("#bc-detail-account-name").textContent = accountNumber;
  $("#bc-detail-card").hidden = false;
  $("#bc-detail-status").textContent = "Loading…";
  $("#bc-detail-card").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    // Always fetch the full, unfiltered bill list - the done/pending/
    // missing summary cards and their click-to-filter behavior (RJ's
    // request, 2026-09-12) are computed and applied entirely client-side
    // so switching filters is instant and the counts always match what's
    // in the table.
    const result = await api("/api/bulk-checker/detail", {
      method: "POST",
      body: { date_from, date_to, billing_period, account_number: accountNumber, bill_filter: "all" },
    });
    bcState.detailColumns = result.columns;
    bcState.detailRows = result.display_rows;
    $("#bc-detail-status").textContent = `${result.row_count} bill(s) in ${result.elapsed_ms.toFixed(0)} ms.`;
    bcRenderDetailKpiRow();
    bcRenderDetailTable();
  } catch (err) {
    $("#bc-detail-status").textContent = "Failed to load bills.";
    showToast(err.message, true);
  }
}

$("#bc-detail-close-btn").addEventListener("click", () => { $("#bc-detail-card").hidden = true; });
$("#bc-detail-search-box").addEventListener("input", () => {
  bcState.detailSearch = $("#bc-detail-search-box").value;
  bcRenderDetailTable();
});

// A bill row is "done" once it has both an id_bill AND a file_number
// (invoiced/sent to the lot), "pending" once it has an id_bill but no
// file_number yet, and "missing" when it has no id_bill at all - same
// three-way split BILL_FILTERS already uses server-side for "pending"/
// "missing"; "done" is just their complement, computed client-side since
// there's no separate server round-trip for it.
function bcClassifyDetailRow(row) {
  const idBillIdx = bcColIdx(bcState.detailColumns, "id_bill");
  const fileNumberIdx = bcColIdx(bcState.detailColumns, "file_number");
  const hasBill = idBillIdx !== -1 && row[idBillIdx] !== null && row[idBillIdx] !== undefined && row[idBillIdx] !== "";
  if (!hasBill) return "missing";
  const hasFileNumber = fileNumberIdx !== -1 && row[fileNumberIdx] !== null && row[fileNumberIdx] !== undefined && row[fileNumberIdx] !== "";
  return hasFileNumber ? "done" : "pending";
}

function bcRenderDetailKpiRow() {
  const rows = bcState.detailRows;
  let done = 0, pending = 0, missing = 0;
  rows.forEach((row) => {
    const status = bcClassifyDetailRow(row);
    if (status === "done") done++;
    else if (status === "pending") pending++;
    else missing++;
  });
  const cards = [
    ["all", "Total bills", rows.length],
    ["done", "Done", done],
    ["pending", "Pending (billed, not invoiced)", pending],
    ["missing", "Still no bill", missing],
  ];
  $("#bc-detail-kpi-row").innerHTML = cards.map(([key, label, value]) => {
    const active = bcState.detailFilter === key;
    return `<div class="kpi-card kpi-card-clickable${active ? " is-active" : ""}" data-bc-kpi-filter="${key}" title="Click to filter the table to this status - click again to clear">` +
      `<div class="kpi-value">${value}</div><div class="kpi-label">${escapeHtml(label)}</div></div>`;
  }).join("");
}

$("#bc-detail-kpi-row").addEventListener("click", (ev) => {
  const card = ev.target.closest("[data-bc-kpi-filter]");
  if (!card) return;
  const key = card.dataset.bcKpiFilter;
  bcState.detailFilter = bcState.detailFilter === key ? "all" : key;
  bcRenderDetailKpiRow();
  bcRenderDetailTable();
});

function bcVisibleDetailColumnIdx() {
  return bcState.detailColumns.map((c, i) => i).filter((i) => !BC_DETAIL_HIDDEN_COLUMNS.has(bcState.detailColumns[i]));
}

function bcVisibleDetailRows(ignoreFilters = false) {
  let rows = bcState.detailRows;
  if (!ignoreFilters) {
    const search = bcState.detailSearch.trim().toLowerCase();
    if (bcState.detailFilter !== "all") {
      rows = rows.filter((row) => bcClassifyDetailRow(row) === bcState.detailFilter);
    }
    if (search) rows = rows.filter((row) => row.some((v) => String(v).toLowerCase().includes(search)));
  }
  if (bcDetailSortKey) {
    const idx = bcColIdx(bcState.detailColumns, bcDetailSortKey);
    if (idx !== -1) rows = [...rows].sort((a, b) => _hierCompareValues(a[idx], b[idx], bcDetailSortDir));
  }
  return rows;
}

function bcRenderDetailTable() {
  const visibleIdx = bcVisibleDetailColumnIdx();
  const sectorSupplyIdx = bcColIdx(bcState.detailColumns, "ID_SECTOR_SUPPLY");
  const thead = $("#bc-detail-table thead tr");
  thead.innerHTML = visibleIdx.map((i) =>
    `<th class="stats-table-th-sortable" data-sort="${escapeHtml(bcState.detailColumns[i])}">${escapeHtml(bcPrettifyLabel(bcState.detailColumns[i]))}</th>`
  ).join("") + `<th>Readings</th>`;
  bcWireSortableHeaders("#bc-detail-table", () => bcDetailSortKey, (k) => { bcDetailSortKey = k; }, () => bcDetailSortDir, (d) => { bcDetailSortDir = d; }, bcRenderDetailTable);
  const idBillIdx = bcColIdx(bcState.detailColumns, "id_bill");

  const tbody = $("#bc-detail-table tbody");
  tbody.innerHTML = "";
  bcVisibleDetailRows().forEach((row) => {
    const tr = document.createElement("tr");
    const missing = idBillIdx !== -1 && !row[idBillIdx];
    tr.className = missing ? "row-not-billed" : "";
    tr.innerHTML = visibleIdx.map((i) => `<td>${escapeHtml(bcFormatCell(row[i]))}</td>`).join("");

    // Same reading-history popup Hierarchy Analysis uses (RJ: "add a
    // link again to open readings same as the hierarchy checker") -
    // reused directly via the existing /api/hierarchy-analysis/reading-
    // history route and hierRenderReadingModal, not duplicated here.
    const sectorSupply = sectorSupplyIdx !== -1 ? row[sectorSupplyIdx] : null;
    const readingsTd = document.createElement("td");
    if (sectorSupply) {
      readingsTd.innerHTML = `<button type="button" class="btn btn-pill-sm bc-detail-reading-btn" ` +
        `data-sector-supply="${escapeHtml(sectorSupply)}" data-billing-period="${escapeHtml(bcState.billingPeriod ?? "")}">📖 Readings</button>`;
    }
    tr.appendChild(readingsTd);
    tbody.appendChild(tr);
  });
}

$("#bc-detail-table tbody").addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".bc-detail-reading-btn");
  if (!btn) return;
  const sectorSupply = btn.dataset.sectorSupply;
  const billingPeriod = btn.dataset.billingPeriod;
  if (!sectorSupply) return;
  btn.disabled = true;
  try {
    const data = await api("/api/hierarchy-analysis/reading-history", {
      method: "POST",
      body: { id_sector_supply: sectorSupply },
    });
    hierRenderReadingModal(data.rows, billingPeriod);
    $("#hier-reading-modal-title").textContent =
      `Supply ${sectorSupply} · ${data.rows.length} reading(s)` +
      (billingPeriod ? ` · checking period ${billingPeriod}` : "");
    hxOpenModal("hier-reading-modal-overlay");
  } catch (err) {
    showToast(err.message || "Could not load reading history.", true);
  } finally {
    btn.disabled = false;
  }
});

// ---------------- Export (CSV client-side, Excel round-trips the server -
// same pattern as Hierarchy Analysis / Detect All's own export buttons) ----
function bcCsvBlobFor(columns, rows) {
  const escapeCsv = (v) => {
    const s = bcFormatCell(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [columns.map(escapeCsv).join(",")];
  rows.forEach((row) => lines.push(row.map(escapeCsv).join(",")));
  return new Blob([lines.join("\n")], { type: "text/csv" });
}

function bcDownloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

$("#bc-export-csv-btn").addEventListener("click", () => {
  const exportAll = !!$("#bc-export-all")?.checked;
  const rows = bcVisibleResultRows(exportAll);
  if (!rows.length) { showToast("No rows to export.", true); return; }
  bcDownloadBlob(bcCsvBlobFor(bcState.columns, rows), `bulk_checker_${bcState.billingPeriod || "search"}${exportAll ? "_all" : ""}.csv`);
});

$("#bc-detail-export-csv-btn").addEventListener("click", () => {
  const exportAll = !!$("#bc-detail-export-all")?.checked;
  const rows = bcVisibleDetailRows(exportAll);
  if (!rows.length) { showToast("No rows to export.", true); return; }
  bcDownloadBlob(bcCsvBlobFor(bcState.detailColumns, rows), `bulk_checker_bills_${bcState.detailAccount || "account"}${exportAll ? "_all" : ""}.csv`);
});

async function bcExportXlsx(btn, headers, rows, filename) {
  if (!rows.length) { showToast("No rows to export.", true); return; }
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Exporting…";
  try {
    const resp = await fetch("/api/bulk-checker/export-xlsx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, headers, rows }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Export failed (HTTP ${resp.status})`);
    }
    bcDownloadBlob(await resp.blob(), filename);
  } catch (err) {
    showToast(err.message || "Excel export failed.", true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

$("#bc-export-xlsx-btn").addEventListener("click", () => {
  const exportAll = !!$("#bc-export-all")?.checked;
  bcExportXlsx(
    $("#bc-export-xlsx-btn"),
    bcState.columns.map(bcPrettifyLabel),
    bcVisibleResultRows(exportAll).map((row) => row.map(bcFormatCell)),
    `bulk_checker_${bcState.billingPeriod || "search"}${exportAll ? "_all" : ""}.xlsx`
  );
});

$("#bc-detail-export-xlsx-btn").addEventListener("click", () => {
  const exportAll = !!$("#bc-detail-export-all")?.checked;
  bcExportXlsx(
    $("#bc-detail-export-xlsx-btn"),
    bcState.detailColumns.map(bcPrettifyLabel),
    bcVisibleDetailRows(exportAll).map((row) => row.map(bcFormatCell)),
    `bulk_checker_bills_${bcState.detailAccount || "account"}${exportAll ? "_all" : ""}.xlsx`
  );
});

// ---------------- ⟳ Refresh on every menu (RJ 2026-09-27) ----------------
// "add a refresh in all menu, this will refresh the data using the same
// filters". One shared button per page header. It:
//   1. snapshots the page's filter controls (every input/select/textarea
//      with an id, plus hx segmented controls) - table cells, modals and
//      select-all boxes are skipped,
//   2. re-runs that page's own scan/load (HX_REFRESH below - for pages
//      with sub-tabs, the ACTIVE sub-tab's scan),
//   3. waits until the scan's button is re-enabled and no api() call is
//      in flight,
//   4. puts back every control whose value the scan reset, firing that
//      control's own input/change handlers so the page re-filters exactly
//      as it does when you change a filter by hand.
// Pages whose data comes from a person's typed search (Workspace query,
// Reading Validation NISS, DIFF DATES single NISS, Bulk Checker) simply
// re-run that same search.
function hxActiveSub(attr) {
  const b = document.querySelector(`.da-subnav-btn.is-active[${attr}]`);
  return b ? b.getAttribute(attr) : null;
}

const HX_REFRESH = {
  // (Overview keeps its own existing 🔄 Refresh button, so it's not here.)
  workspace: () => ({ btn: "#run-btn" }),
  history: () => ({ fn: loadHistory }),
  dashboard: () => ({ fn: loadDashboard }),
  ai: () => ({ fn: loadAIPage }),
  tools: () => ({ fn: loadToolsPage }),
  settings: () => ({ fn: loadSettingsPage }),
  dateanomaly: () => {
    const sub = hxActiveSub("data-da-sub");
    if (sub === "single") return { btn: "#da-detect-btn" };
    if (sub === "detectall") return { btn: "#da-cleanup-detect-btn" };
    if (sub === "history") return { fn: daHistoryRefresh };
    return { fn: daBatchRefreshRecentRuns };
  },
  hierarchy: () => ({ btn: "#hier-detect-btn" }),
  readingvalidation: () => (rvActiveSupply
    ? { fn: () => rvLoadSupply(rvActiveSupply) }
    : { btn: "#rv-supply-search-btn" }),
  tnbcycledisc: () => ({ btn: "#tcd-detect-btn" }),
  wrongstuckhierarchy: () => ({ btn: hxActiveSub("data-wsh-sub") === "sanitary" ? "#wshs-detect-btn" : "#wsh-detect-btn" }),
  doubleitb: () => ({ btn: "#ditb-detect-btn" }),
  // Wrong Bill: one entry per case tab (add more as cases are added).
  wrongbill: () => ({ btn: ({ case1: "#usan-detect-btn", case2: "#wbpd-detect-btn", case3: "#wbsz-detect-btn", case4: "#wbfr-detect-btn" })[hxActiveSub("data-wb-sub") || "case1"] || "#usan-detect-btn" }),
  wrongbilledconsumption: () => ({ btn: "#wbc-detect-btn" }),
  disconnectiontnb: () => ({ btn: "#dtnb-detect-btn" }),
  billissuance: () => {
    const sub = hxActiveSub("data-biss-sub") || "case1";
    return { btn: sub === "case1" ? "#billiss-detect-btn" : `#biss${sub.replace("case", "")}-detect-btn` };
  },
  incorrectbillingperiod: () => ({ btn: "#ibp-detect-btn" }),
  bulkchecker: () => ({ btn: "#bc-search-btn" }),
};

// ---- Auto-load on open (RJ 2026-10-04): "from overview, when you click an
// item, it should load the data as it opens, also same with menus". The
// first time a menu / case tab is opened, its Scan button runs by itself
// (reusing HX_REFRESH's button map). Not repeated on later visits - use
// Refresh for that. Pages that need an input first (Workspace query,
// Reading Validation NISS, DIFF DATES single NISS / batch, Bulk Checker)
// are left alone; Wrong Bill and Anomalies Statistics already auto-load.
const HX_AUTO_PAGES = new Set(["hierarchy", "tnbcycledisc", "wrongstuckhierarchy", "doubleitb",
  "wrongbilledconsumption", "disconnectiontnb", "billissuance", "incorrectbillingperiod", "dateanomaly"]);
const hxAutoLoaded = new Set();
let hxAutoTimer = null;

function hxAutoKey(page) {
  const sub = { dateanomaly: "data-da-sub", wrongstuckhierarchy: "data-wsh-sub", billissuance: "data-biss-sub" }[page];
  return sub ? `${page}:${hxActiveSub(sub) || ""}` : page;
}

function hxAutoTarget(page) {
  if (!HX_AUTO_PAGES.has(page) || !HX_REFRESH[page]) return null;
  if (page === "dateanomaly" && hxActiveSub("data-da-sub") !== "detectall") return null;
  const t = HX_REFRESH[page]();
  return t && t.btn ? document.querySelector(t.btn) : null;
}

async function hxAutoRun() {
  const page = document.querySelector(".page.is-active")?.id?.replace("page-", "");
  if (!page) return;
  const key = hxAutoKey(page);
  if (hxAutoLoaded.has(key)) return;
  const btn = hxAutoTarget(page);
  if (!btn) return;
  hxAutoLoaded.add(key);
  // Wrong Billed Consumption needs its billing-period list first.
  if (page === "wrongbilledconsumption") {
    for (let i = 0; i < 50 && !$("#wbc-period")?.value; i++) await new Promise((r) => setTimeout(r, 100));
  }
  if (!btn.disabled) btn.click();
}

function hxAutoSchedule() {
  clearTimeout(hxAutoTimer);
  // short delay so Overview's "open page + pick tab" only loads the tab it lands on
  hxAutoTimer = setTimeout(hxAutoRun, 80);
}

document.addEventListener("click", (e) => {
  if (e.target.closest(".nav-item[data-page], .da-subnav-btn")) { hxAutoSchedule(); return; }
  // a manual Scan also counts as "loaded" for that menu / tab
  const page = document.querySelector(".page.is-active")?.id?.replace("page-", "");
  const target = page && hxAutoTarget(page);
  if (target && e.target.closest("button") === target) hxAutoLoaded.add(hxAutoKey(page));
});

// "✕ Clear filters" (RJ 2026-09-30, Bill Issuance cases): resets every
// input/select/checkbox in the button's own filter row to its HTML default
// and fires input+change so the page re-renders with no filters.
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-hx-clear-row]");
  if (!btn) return;
  const row = btn.closest(".key-panel-row, .hx-filter-grid, .hx-filters");
  if (!row) return;
  row.querySelectorAll("input, select").forEach((el) => {
    if (el.type === "checkbox" || el.type === "radio") el.checked = el.defaultChecked;
    else if (el.tagName === "SELECT") {
      const def = [...el.options].find((o) => o.defaultSelected) || el.options[0];
      el.value = def ? def.value : "";
    } else el.value = el.defaultValue;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
});

function hxSnapshotFilters(page) {
  const skip = (el) => !el.id || el.closest("table, .hier-reading-modal, .hx-modal") ||
    /select-all|password|program|audit-user|jira/i.test(el.id) || ["file", "password", "button", "submit"].includes(el.type);
  const controls = [...page.querySelectorAll("input, select, textarea")].filter((el) => !skip(el))
    .map((el) => ({ id: el.id, check: el.type === "checkbox" || el.type === "radio", value: el.type === "checkbox" || el.type === "radio" ? el.checked : el.value }));
  const segs = [...page.querySelectorAll(".hx-seg[id]")]
    .map((s) => ({ id: s.id, value: s.querySelector("button.is-active")?.dataset.value ?? "" }));
  return { controls, segs };
}

function hxRestoreFilters(snap) {
  snap.controls.forEach((c) => {
    const el = document.getElementById(c.id);
    if (!el) return;
    if (c.check) {
      if (el.checked === c.value) return;
      el.checked = c.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    if (el.value === c.value) return;
    if (el.tagName === "SELECT" && ![...el.options].some((o) => o.value === c.value)) return; // value gone after refresh
    el.value = c.value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
  snap.segs.forEach((s) => {
    const seg = document.getElementById(s.id);
    const active = seg?.querySelector("button.is-active")?.dataset.value ?? "";
    if (!seg || active === s.value) return;
    seg.querySelector(`button[data-value="${CSS.escape(s.value)}"]`)?.click();
  });
}

const hxSleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function hxWaitIdle(btn, timeoutMs = 10 * 60 * 1000) {
  const start = Date.now();
  await hxSleep(150); // let the click handler start its request
  while (Date.now() - start < timeoutMs) {
    if (_apiInflight === 0 && !(btn && btn.disabled)) return;
    await hxSleep(200);
  }
}

async function hxRefreshPage(pageId, refreshBtn) {
  const page = document.getElementById(`page-${pageId}`);
  const spec = HX_REFRESH[pageId]?.();
  if (!page || !spec) return;
  const snap = hxSnapshotFilters(page);
  refreshBtn.disabled = true;
  refreshBtn.classList.add("is-spinning");
  try {
    if (spec.fn) {
      await spec.fn();
      await hxWaitIdle(null);
    } else {
      const btn = $(spec.btn);
      if (!btn) return;
      if (btn.disabled) { showToast("A scan is already running on this page.", true); return; }
      btn.click();
      await hxWaitIdle(btn);
    }
    hxRestoreFilters(snap);
    const t = new Date().toLocaleTimeString();
    refreshBtn.title = `Refresh data (keeps your filters) — last refreshed ${t}`;
    showToast(`Refreshed at ${t}.`);
  } catch (err) {
    showToast(err.message || "Refresh failed.", true);
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.classList.remove("is-spinning");
  }
}

function hxInstallRefreshButtons() {
  Object.keys(HX_REFRESH).forEach((pageId) => {
    const header = document.querySelector(`#page-${pageId} > .page-header`);
    if (!header || header.querySelector(".hx-refresh-btn")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "hx-refresh-btn";
    btn.title = "Refresh data (keeps your filters)";
    btn.innerHTML = `<span class="hx-refresh-icon">⟳</span><span>Refresh</span>`;
    btn.addEventListener("click", () => hxRefreshPage(pageId, btn));
    header.classList.add("hx-has-refresh");
    header.appendChild(btn);
  });
}

// ---------------- Bill Issuance Validator: Case 2 / Case 3 release script
// for COMPLETE accounts (RJ 2026-10-04) ----------------
// Uses RJ's own UPDATE GCCOM_NOTICE_TMP template (5000NOTEMP -> 1000NOTEMP,
// bill ESTFAC0012). The server re-runs the case's detect with the last scan's
// scope and only keeps accounts that are still complete.
function bissRelPickedIds(kind) {
  if (kind === "case2") {
    // Checked complete accounts if any, else every complete account (server default).
    const picked = [...biss2Selected].map((i) => biss2Accounts[i]).filter((a) => a && a.complete);
    return [...new Set(picked.map((a) => String(a.id_payment_form)))];
  }
  // Case 3: a search narrows to the visible accounts; no search = all.
  if (!($("#biss3-search")?.value || "").trim()) return [];
  return [...new Set(biss3VisibleIndices().map((i) => String(biss3Rows[i].id_payment_form)))];
}

document.querySelectorAll(".biss-rel-card").forEach((card) => {
  const kind = card.dataset.bissRel;
  const q = (sel) => card.querySelector(sel);
  const out = q("[data-rel-output]");
  q("[data-rel-generate]").addEventListener("click", async () => {
    if (!bissRelScan[kind]) { showToast(kind === "case2" ? "Scan Terminated Accounts first." : "Scan Bills Complete first.", true); return; }
    const ids = bissRelPickedIds(kind);
    if (kind === "case3" && ($("#biss3-search")?.value || "").trim() && !ids.length) { showToast("No accounts match the search.", true); return; }
    const btn = q("[data-rel-generate]");
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Re-checking…";
    try {
      const res = await api("/api/bill-issuance/release-complete", {
        method: "POST",
        body: {
          case: kind, id_payment_forms: ids, ...bissRelScan[kind],
          program: q("[data-rel-program]").value.trim(),
          audit_user: q("[data-rel-user]").value.trim(),
          clean: q("[data-rel-clean]").checked,
        },
      });
      out.textContent = res.sql_text || "-- No complete accounts to release.";
      let msg = `${res.account_count} complete account(s) in the release script${ids.length ? " (selection)" : ""}.`;
      if (res.warnings && res.warnings.length) msg += " " + res.warnings.join(" ");
      q("[data-rel-summary]").textContent = msg;
      showToast(`Release script generated: ${res.account_count} account(s).`);
    } catch (err) {
      showToast(err.message || "Generate failed.", true);
    } finally {
      btn.disabled = state.role === "viewer";
      btn.textContent = original;
    }
  });
  q("[data-rel-copy]").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(out.textContent); showToast("Release script copied to clipboard."); }
    catch (_) { showToast("Couldn't copy - select and copy manually.", true); }
  });
  q("[data-rel-download]").addEventListener("click", () => {
    const blob = new Blob([out.textContent], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `bill_issuance_${kind}_release_complete.sql`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  });
});

// ---------------- Boot ----------------
(async function init() {
  try { hxCollapseInfo(); } catch (e) { console.error("hxCollapseInfo", e); }
  try { hxInstallRefreshButtons(); } catch (e) { console.error("hxInstallRefreshButtons", e); }
  loadServerInfo();
  const loggedIn = await checkSession();
  if (loggedIn) showApp();
  else showLogin();
})();
