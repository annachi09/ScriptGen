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

async function api(path, options = {}) {
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
  $("#account-username-text").textContent = state.username;
  $("#account-role-badge").textContent = state.role;
  applyRolePermissionsToUI();
  refreshConnectionStatus();
  refreshSidebarConnectionName();
  loadRecentQueries();
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

$("#sign-out-btn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  state.username = null;
  state.role = null;
  state.allowedMenus = null;
  showLogin();
});

// ---------------- Nav ----------------
$$(".nav-item[data-page]").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".nav-item[data-page]").forEach((b) => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    const page = btn.dataset.page;
    $$(".page").forEach((p) => p.classList.remove("is-active"));
    $(`#page-${page}`).classList.add("is-active");
    if (page === "history") loadHistory();
    if (page === "dashboard") loadDashboard();
    if (page === "ai") loadAIPage();
    if (page === "tools") loadToolsPage();
    if (page === "settings") loadSettingsPage();
    if (page === "dateanomaly") { daBatchRefreshRecentRuns(); daHistoryRefresh(); }
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
    tasks.push(refreshConnectionsTable(), refreshAIConfigForm(), refreshUsersTable(), refreshMenuAccessTable());
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
      tr.innerHTML = `<td>${escapeHtml(r.id_reading)}</td><td>${escapeHtml(r.id_billing_period)}</td><td>${escapeHtml(r.reading_date)}</td><td>${escapeHtml(r.read_status)}</td>`;
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

async function daBatchRenderJob(job) {
  daBatchLastJobId = job.job_id;
  $("#da-batch-explain-ai-btn").disabled = job.results.length === 0;
  daBatchRenderResultsTable(job.results);
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
function daCleanupVisibleIndices() {
  return daCleanupRows
    .map((_, idx) => idx)
    .filter((idx) => {
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
}

// Exports whatever's currently VISIBLE (i.e. filtered/searched), not the
// full scan - matches the export button living right next to the filter
// bar, and matches the #dashboard-export-csv-btn convention elsewhere on
// this page (build lines, Blob, temp <a download>, revoke).
$("#da-cleanup-export-csv-btn").addEventListener("click", () => {
  const visible = daCleanupVisibleIndices();
  if (!visible.length) return;
  const header = ["id_item_to_bill", "account", "supply", "offered_service", "contract_status", "anomalous_type", "anomalous_status", "item_status", "needs_status_advance", "all_cycle", "billing_period_count"];
  const lines = [header.join(",")];
  visible.forEach((idx) => {
    const r = daCleanupRows[idx];
    const row = [
      r.id_item_to_bill, r.account, r.supply, r.offered_service, r.contract_status,
      daCleanupTypeText(r), r.anomalous_status_description || r.anomalous_status || "",
      r.item_status, r.needs_status_advance ? "Yes" : "",
      r.all_cycle === true ? "Yes" : r.all_cycle === false ? "No" : "",
      r.billing_period_count ?? "",
    ].map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`);
    lines.push(row.join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "detect_all_anomalies.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

// Excel counterpart to the CSV export above - same "whatever's currently
// visible" rows, same columns, but a browser can't write a real .xlsx
// binary on its own, so this one round-trips through the server
// (POST /api/date-anomaly/detect-all/export-xlsx, openpyxl-built) instead
// of building a Blob client-side like the CSV button does.
$("#da-cleanup-export-xlsx-btn").addEventListener("click", async () => {
  const visible = daCleanupVisibleIndices();
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
    a.href = url; a.download = "detect_all_anomalies.xlsx";
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
  $("#da-cleanup-kpi-row").innerHTML = [
    ["Shown", total, false],
    ["Needs status advance", needsAdvance, true],
    ["Distinct offered services", distinctServices, false],
    ["Distinct accounts", distinctAccounts, false],
  ].map(([label, value, clickable]) =>
    `<div class="kpi-card${clickable ? " kpi-card-clickable" : ""}${clickable && advanceActive ? " is-active" : ""}"${clickable ? ' id="da-cleanup-kpi-advance" title="Click to toggle the Needs status advance filter"' : ""}><div class="kpi-value">${value}</div><div class="kpi-label">${label}</div></div>`
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
let hierFilters = { search: "", status: "", type: "", mpStatus: "", billingPeriod: "", primaryOnly: false };
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

function hierVisibleIndices() {
  const search = hierFilters.search.trim().toLowerCase();
  const filtered = hierRows
    .map((r, i) => [r, i])
    .filter(([r]) => {
      if (hierFilters.status && r.read_status !== hierFilters.status) return false;
      if (hierFilters.type && r.reading_type !== hierFilters.type) return false;
      if (hierFilters.mpStatus && r.mp_status !== hierFilters.mpStatus) return false;
      if (hierFilters.billingPeriod && String(r.id_billing_period ?? "") !== hierFilters.billingPeriod) return false;
      if (hierFilters.primaryOnly && Number(r.secondaries_not_sent_count ?? 0) !== 0) return false;
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
  $("#hier-filter-row").hidden = hierRows.length === 0;
  $("#hier-export-csv-btn").hidden = hierRows.length === 0;
  $("#hier-export-xlsx-btn").hidden = hierRows.length === 0;
  $("#hier-dashboard-card").hidden = hierRows.length === 0;
}

function hierRenderKpiRow(visibleRows) {
  const distinctNiss = new Set(visibleRows.map((r) => r.niss).filter(Boolean)).size;
  const anomalousCount = visibleRows.filter((r) => r.read_status === "5000STSRED").length;
  const periods = visibleRows.map((r) => Number(r.id_billing_period)).filter((n) => !Number.isNaN(n));
  const oldestPeriod = periods.length ? Math.min(...periods) : null;
  const primaryOnlyCount = visibleRows.filter((r) => Number(r.secondaries_not_sent_count ?? NaN) === 0).length;
  const cards = [
    ["rows", "📄", "Pending primaries", visibleRows.length],
    ["niss", "🔌", "Distinct NISS", distinctNiss],
    ["anomalous", "⚠️", "Anomalous reads", anomalousCount],
    ["oldest", "📅", "Oldest billing period", oldestPeriod ?? "—"],
    ["primaryonly", "🟢", "Primary-only fixes", primaryOnlyCount],
  ];
  $("#hier-kpi-row").innerHTML = cards.map(([kpi, icon, label, value]) =>
    `<div class="kpi-card" data-kpi="${kpi}">
      <div class="kpi-value">${value}</div>
      <div class="kpi-label"><span class="kpi-card-icon">${icon}</span>${label}</div>
    </div>`
  ).join("");
}

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
  const distinctPeriods = new Set(visibleRows.map((r) => r.id_billing_period).filter(Boolean)).size;
  const cards = [
    ["readyusage", "★", "Total ready usage", totalReadyUsage.toLocaleString()],
    ["primaries", "🗂️", "Pending primaries", visibleRows.length],
    ["secondaries", "🔗", "Total secondaries", totalSecondaries],
    ["avg", "📊", "Avg secondaries / primary", avgSecondaries],
    ["periods", "📅", "Billing periods in view", distinctPeriods],
  ];
  $("#hier-dashboard-kpi-row").innerHTML = cards.map(([kpi, icon, label, value]) =>
    `<div class="kpi-card" data-kpi="${kpi}">
      <div class="kpi-value">${value}</div>
      <div class="kpi-label"><span class="kpi-card-icon">${icon}</span>${label}</div>
    </div>`
  ).join("");

  // Calculation-module type breakdown - counts how many currently-visible
  // primaries fall under each GCCOM_CALCULATION_MODULE label. Unlabeled
  // rows (no calc_module_type resolved) are grouped as "Unclassified"
  // rather than dropped, so the counts still add up to the total above.
  const typeCounts = new Map();
  visibleRows.forEach((r) => {
    const label = r.calc_module_type || "Unclassified";
    typeCounts.set(label, (typeCounts.get(label) || 0) + 1);
  });
  const typeEntries = [...typeCounts.entries()].sort((a, b) => b[1] - a[1]);
  $("#hier-dashboard-type-row").innerHTML = typeEntries.length
    ? typeEntries.map(([label, count]) =>
        `<div class="kpi-card" data-kpi="type" title="${escapeHtml(label)}">
          <div class="kpi-value">${count}</div>
          <div class="kpi-label"><span class="kpi-card-icon">🏷️</span>${escapeHtml(label)}</div>
        </div>`
      ).join("")
    : "";
}

function hierRenderTable() {
  const visible = hierVisibleIndices();
  const tbody = $("#hier-table tbody");
  tbody.innerHTML = "";
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
    const isPrimaryOnly = Number(r.secondaries_not_sent_count ?? NaN) === 0;
    tr.className = isPrimaryOnly ? "row-primary-only" : (r.read_status === "5000STSRED" ? "row-multi-period" : "");
    tr.innerHTML = (
      `<td>${escapeHtml(r.id_measuring_point ?? "")}</td>` +
      `<td>${escapeHtml(r.id_main_mp ?? "")}</td>` +
      `<td>${escapeHtml(r.niss ?? "")}</td>` +
      `<td title="${escapeHtml(r.mp_type_desc ?? "")}">${escapeHtml(r.mp_type ?? "")}${r.mp_type_desc ? " (" + escapeHtml(r.mp_type_desc) + ")" : ""}</td>` +
      `<td title="${escapeHtml(r.mp_status_desc ?? "")}">${escapeHtml(r.mp_status ?? "")}${r.mp_status_desc ? " (" + escapeHtml(r.mp_status_desc) + ")" : ""}</td>` +
      `<td>${escapeHtml(r.secondary_count ?? "0")}</td>` +
      `<td>${escapeHtml(r.secondaries_not_sent_count ?? "0")}</td>` +
      `<td title="ID_CALCULATION_MODULE ${escapeHtml(r.id_calculation_module ?? "")}">${escapeHtml(r.calc_module_type ?? "")}</td>` +
      `<td title="${escapeHtml(r.billing_period_desc ?? "")}">${escapeHtml(r.id_billing_period ?? "")}${r.billing_period_desc ? " (" + escapeHtml(r.billing_period_desc) + ")" : ""}</td>` +
      `<td>${escapeHtml(r.id_reading ?? "")}</td>` +
      `<td>${escapeHtml(r.reading_date ?? "")}</td>` +
      `<td title="${escapeHtml(r.reading_type_desc ?? "")}">${escapeHtml(r.reading_type ?? "")}${r.reading_type_desc ? " (" + escapeHtml(r.reading_type_desc) + ")" : ""}</td>` +
      `<td>${escapeHtml(r.read_status ?? "")}</td>` +
      `<td><strong>${escapeHtml(r.ready_usage ?? "")}</strong></td>` +
      `<td>${escapeHtml(r.reading_usage ?? "")}</td>` +
      `<td><button type="button" class="btn btn-pill-sm hier-drill-btn" data-idx="${idx}" title="View this hierarchy's full member list">🔗</button></td>`
    );
    tbody.appendChild(tr);
  });
  const visibleRows = visible.map((idx) => hierRows[idx]);
  hierRenderKpiRow(visibleRows);
  hierRenderDashboard(visibleRows);
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
    hierFilters = { search: "", status: "", type: "", mpStatus: "", billingPeriod: "", primaryOnly: false };
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

$("#hier-filter-clear-btn").addEventListener("click", () => {
  hierFilters = { search: "", status: "", type: "", mpStatus: "", billingPeriod: "", primaryOnly: false };
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
    tr.innerHTML = (
      `<td>${escapeHtml(r.id_measuring_point ?? "")}</td>` +
      `<td>${escapeHtml(r.id_main_mp ?? "")}</td>` +
      `<td>${escapeHtml(r.niss ?? "")}</td>` +
      // "Primary?" now follows the corrected definition (MP_TYPE IN
      // Principal/Principal acoplado), not the IND_DIST_PPAL flag this
      // drill-down query still happens to return - see server-side
      // app/core/hierarchy_analysis.py's module docstring for why.
      `<td>${["TIPEQM0003", "TIPEQM0005"].includes(r.mp_type) ? "Yes" : ""}</td>` +
      `<td>${escapeHtml(r.perc_dist ?? "")}</td>` +
      `<td>${escapeHtml(r.mp_type ?? "")}</td>` +
      `<td>${escapeHtml(r.status ?? "")}</td>` +
      `<td>${escapeHtml(r.id_billing_period ?? "")}${r.billing_period_desc ? " (" + escapeHtml(r.billing_period_desc) + ")" : ""}</td>` +
      `<td>${escapeHtml(r.id_reading ?? "")}</td>` +
      `<td>${escapeHtml(r.reading_date ?? "")}</td>` +
      `<td>${escapeHtml(r.reading_type ?? "")}${r.reading_type_desc ? " (" + escapeHtml(r.reading_type_desc) + ")" : ""}</td>` +
      `<td>${escapeHtml(r.read_status ?? "")}</td>` +
      `<td><strong>${escapeHtml(r.ready_usage ?? "")}</strong></td>` +
      `<td>${escapeHtml(r.value ?? "")}</td>` +
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
      `<td>${escapeHtml(r.prev_date ?? "")}</td>` +
      `<td>${escapeHtml(r.reading_date ?? "")}</td>` +
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
      `— supply ${sectorSupply} (${data.rows.length} reading(s))` +
      (billingPeriod ? `, checking billing period ${billingPeriod}` : "");
    $("#hier-reading-modal-overlay").hidden = false;
  } catch (err) {
    showToast(err.message || "Could not load reading history.", true);
  } finally {
    btn.disabled = false;
  }
});

$("#hier-reading-modal-close-btn").addEventListener("click", () => {
  $("#hier-reading-modal-overlay").hidden = true;
});
$("#hier-reading-modal-overlay").addEventListener("click", (ev) => {
  if (ev.target.id === "hier-reading-modal-overlay") $("#hier-reading-modal-overlay").hidden = true;
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
    const periodNote = row.id_billing_period
      ? ` — scoped to billing period ${row.id_billing_period}${row.billing_period_desc ? " (" + row.billing_period_desc + ")" : ""}`
      : "";
    const notBilledNote = notBilledCount ? `, ${notBilledCount} not billed` : "";
    $("#hier-detail-title").textContent = `— measuring point ${row.id_measuring_point} (${data.rows.length} member row(s)${notBilledNote})${periodNote}`;
    $("#hier-detail-card").hidden = false;
    $("#hier-detail-card").scrollIntoView({ behavior: "smooth", block: "start" });
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

$("#hier-detail-close-btn").addEventListener("click", () => { $("#hier-detail-card").hidden = true; });

$("#hier-export-csv-btn").addEventListener("click", () => {
  const visible = hierVisibleIndices();
  if (!visible.length) return;
  const header = ["id_measuring_point", "id_main_mp", "niss", "mp_type", "mp_status", "secondary_count", "secondaries_not_sent_count", "id_calculation_module", "calc_module_type", "id_billing_period", "billing_period_desc", "id_reading", "reading_date", "reading_type", "reading_type_desc", "read_status", "ready_usage", "reading_usage"];
  const lines = [header.join(",")];
  visible.forEach((idx) => {
    const r = hierRows[idx];
    lines.push(header.map((k) => `"${String(r[k] ?? "").replace(/"/g, '""')}"`).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "hierarchy_analysis.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

$("#hier-export-xlsx-btn").addEventListener("click", async () => {
  const visible = hierVisibleIndices();
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
    a.href = url; a.download = "hierarchy_analysis.xlsx";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showToast(err.message || "Excel export failed.", true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
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

// ---------------- Boot ----------------
(async function init() {
  loadServerInfo();
  const loggedIn = await checkSession();
  if (loggedIn) showApp();
  else showLogin();
})();
