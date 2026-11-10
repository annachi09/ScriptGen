/* =====================================================================
   ScriptGen - Bill Issuance Validator: Case 5 "Temp Stop PCAL" (RJ 2026-11-08).
   RJ: "create a case 5 and analyze what is wrong with this cases: ... COD_STATUS = '5002NOTEMP'".
   Every account with a 5002NOTEMP notice on an Invoicing bill, with data checks per account and
   one "main issue". Read only. Data: POST /api/bill-issuance/case5/detect.
   Loaded after app.js (uses escapeHtml, api, showToast).
   ===================================================================== */
(function () {
  const PAGE = 300;
  const S = { data: null, rows: [], view: [], page: 0, loaded: false, issue: "", kpi: "" };
  // RJ 2026-11-08: "selecting cards does not filter" - each KPI card is a filter (click again = off)
  const KPI_FILTERS = {
    all: () => true,
    ready: (r) => String(r.release_ready) === "1",
    clean: (r) => String(r.release_ready) === "1" && r.main_issue === S.data.no_issue_label,
    notready: (r) => String(r.release_ready) !== "1",
    commercial: (r) => r.segment === "Commercial",
    rateonly: (r) => num(r.rate_only) > 0,
    noissue: (r) => r.main_issue === S.data.no_issue_label,
  };
  // RJ 2026-11-08: "make main issue colorful ... same as its card" - one fixed colour per issue
  const ISSUE_COLORS = {
    also_5000: "#ef4444", cs_not_active: "#dc2626", rate_only: "#8b5cf6", zero_neg: "#e11d48",
    amount_jump: "#f97316", no_prev: "#0ea5e9", dup_active_cs: "#14b8a6", pcal_cs_change: "#6366f1",
    estimated: "#f59e0b",
  };
  const NO_ISSUE_COLOR = "#10b981";
  function issueColor(label) {
    const d = S.data; if (!d) return "#94a3b8";
    if (label === d.no_issue_label) return NO_ISSUE_COLOR;
    const key = Object.keys(d.check_labels).find((k) => d.check_labels[k] === label);
    return ISSUE_COLORS[key] || "#64748b";
  }
  const esc = (s) => escapeHtml(String(s ?? ""));
  const num = (v) => { const n = Number(String(v ?? "").replace(/,/g, "")); return Number.isFinite(n) ? n : 0; };
  const fmt = (n) => Number(n || 0).toLocaleString();
  const money = (n) => Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const el = (id) => document.getElementById(id);
  const COLORS = ["#ef4444", "#f97316", "#8b5cf6", "#e11d48", "#f59e0b", "#0ea5e9", "#14b8a6", "#6366f1", "#84cc16", "#64748b"];
  const FLAG_ICONS = { also_5000: "⚠", cs_not_active: "⛔", rate_only: "🅡", zero_neg: "0️⃣", amount_jump: "📈", no_prev: "🆕", dup_active_cs: "👯", pcal_cs_change: "🔁", estimated: "≈" };

  function applyFilters() {
    const q = (el("bis5-search").value || "").trim();
    const seg = el("bis5-seg").value, chk = el("bis5-check").value, rel = el("bis5-ready").value;
    S.view = S.rows.filter((r) =>
      (!q || String(r.reference || "").includes(q)) &&
      // RJ 2026-11-08: release readiness filter
      (!rel || (rel === "ready" && String(r.release_ready) === "1") || (rel === "not" && String(r.release_ready) !== "1") ||
        (rel === "clean" && String(r.release_ready) === "1" && r.main_issue === S.data.no_issue_label)) &&
      (!S.issue || r.main_issue === S.issue) &&
      (!S.kpi || KPI_FILTERS[S.kpi](r)) &&
      (!seg || r.segment === seg) &&
      (!chk || num(r[chk]) > 0));
    S.page = 0;
    renderKpis();
    renderIssues();
    renderTable();
  }

  function renderIssues() {
    const d = S.data; if (!d) return;
    const order = Object.values(d.check_labels).concat([d.no_issue_label]);
    const entries = order.map((l) => [l, d.issues[l] || 0]).filter((e) => e[1] > 0);
    const max = Math.max(1, ...entries.map((e) => e[1]));
    el("bis5-issues").innerHTML = `<div class="hint-text" style="margin-bottom:6px;">Main issue per account (first matching check, in this order) - click to filter</div>` +
      entries.map(([l, n]) => `<div class="bis5-bar${S.issue === l ? " is-on" : ""}${S.issue && S.issue !== l ? " is-dim" : ""}" data-bis5-issue="${esc(l)}" style="--c:${issueColor(l)}">
        <span class="bis5-bar-name">${esc(l)}</span><span class="bis5-bar-track"><span style="width:${(100 * n / max).toFixed(1)}%"></span></span>
        <span class="bis5-bar-val">${fmt(n)} <small>${(100 * n / Math.max(1, d.account_count)).toFixed(1)}%</small></span></div>`).join("");
  }

  function renderKpis() {
    const d = S.data; if (!d) return;
    const amt = S.rows.reduce((s, r) => s + num(r.amount), 0);
    // clickable cards (data-bis5-kpi); the active one is highlighted, the others dimmed
    const card = (key, icon, label, val, tip, color) => `<div class="kpi-card bis5-kpi${key ? " kpi-card-clickable" : ""}${S.kpi && S.kpi === key ? " is-active" : ""}${S.kpi && key && S.kpi !== key ? " is-dim" : ""}"` +
      `${key ? ` data-bis5-kpi="${key}"` : ""} style="--k:${color}" title="${esc(tip)}${key ? (S.kpi === key ? " - click to clear" : " - click to filter") : ""}">` +
      `<div class="kpi-value">${val}</div><div class="kpi-label"><span class="kpi-card-icon">${icon}</span>${esc(label)}</div></div>`;
    const seg = (k) => fmt(d.segments[k] || 0);
    el("bis5-kpi-row").innerHTML =
      card("all", "👥", "Accounts held", fmt(d.account_count), "Accounts with a 5002NOTEMP notice on an Invoicing bill", "#6366f1") +
      card("", "⏸", "Notices", fmt(d.notice_count), "5002NOTEMP Temp Stop PCAL notices", "#0ea5e9") +
      card("", "💰", "Bill amount", money(amt), "Total of the held Invoicing bills", "#64748b") +
      card("ready", "🟢", "Can be released", fmt(d.ready_count), "Every billable service of the account has its bill for the period (Invoicing or Invoiced)", "#16a34a") +
      card("clean", "✨", "Ready + no data issue", fmt(d.ready_clean_count), "Services complete and no check failed", NO_ISSUE_COLOR) +
      card("notready", "🔴", "Services incomplete", fmt(d.account_count - d.ready_count), "At least one billable service has no Invoicing / Invoiced bill for the period", "#dc2626") +
      card("commercial", "🏢", "Commercial", seg("Commercial"), "Accounts with a commercial tariff (E-C* / W-C*)", "#f59e0b") +
      card("rateonly", "🅡", "Rate only", fmt(d.checks.rate_only), "Accounts whose only contracted service is the Rate (176)", ISSUE_COLORS.rate_only) +
      card("noissue", "✅", "No data issue", fmt(d.issues[d.no_issue_label] || 0), d.no_issue_label, NO_ISSUE_COLOR);
  }

  function flags(r) {
    const d = S.data;
    return Object.keys(FLAG_ICONS).filter((k) => num(r[k]) > 0)
      .map((k) => `<span class="bis5-flag" style="--c:${ISSUE_COLORS[k] || "#64748b"}" title="${esc(d.check_labels[k] || k)}">${FLAG_ICONS[k]}</span>`).join("");
  }

  function renderTable() {
    const tb = document.querySelector("#bis5-table tbody");
    const start = S.page * PAGE, rows = S.view.slice(start, start + PAGE);
    tb.innerHTML = rows.map((r) => `<tr style="box-shadow: inset 4px 0 0 ${issueColor(r.main_issue)};">
      <td><b>${esc(r.reference)}</b></td>
      <td>${String(r.release_ready) === "1" ? `<span style="color:#16a34a;font-weight:700;">✅ Yes</span>` : `<span style="color:#dc2626;font-weight:700;" title="${esc(r.missing_services || "")}">❌ No</span>`}</td>
      <td>${fmt(num(r.services_billed))} / ${fmt(num(r.services))}${r.missing_services ? `<br><small class="hint-text">Missing: ${esc(r.missing_services)}</small>` : ""}</td>
      <td>${esc(r.segment)}</td><td><span class="bis5-issue-pill" style="--c:${issueColor(r.main_issue)}">${esc(r.main_issue)}</span></td><td>${flags(r)}</td>
      <td>${esc(r.fares)}</td><td>${fmt(num(r.bills))}</td><td>${money(num(r.amount))}</td><td>${esc(r.billing_period)}</td>
      <td>${esc(r.first_notice)}</td></tr>`).join("") ||
      `<tr><td colspan="11" class="hint-text">${S.loaded ? "No accounts for these filters." : "Click Scan."}</td></tr>`;
    const relBtn = el("bis5-release-btn");
    if (relBtn) {
      const n = S.view.filter((r) => String(r.release_ready) === "1").length;
      relBtn.hidden = !S.loaded;
      relBtn.textContent = `🔓 Generate release script (${fmt(n)} ready account(s) in view)`;
      relBtn.disabled = !n;
    }
    el("bis5-filtered-count").textContent = S.loaded ? `${fmt(S.view.length)} of ${fmt(S.rows.length)} account(s)` : "";
    const pages = Math.max(1, Math.ceil(S.view.length / PAGE));
    el("bis5-page").textContent = S.view.length ? `${fmt(start + 1)}-${fmt(start + rows.length)} of ${fmt(S.view.length)} (page ${S.page + 1}/${pages})` : "";
    el("bis5-prev").disabled = S.page <= 0;
    el("bis5-next").disabled = S.page >= pages - 1;
  }

  async function scan() {
    const btn = el("bis5-detect-btn");
    if (btn.disabled) return;   // a scan is already running
    btn.disabled = true; el("bis5-summary").textContent = "Scanning… (about 30 s)";
    try {
      const d = await api("/api/bill-issuance/case5/detect", { method: "POST" });
      S.data = d; S.rows = d.rows || []; S.loaded = true;
      el("bis5-summary").textContent = `${fmt(d.account_count)} account(s), ${fmt(d.notice_count)} notice(s) · ${d.seconds}s · read only`;
      const segSel = el("bis5-seg");
      segSel.innerHTML = `<option value="">All segments</option>` + Object.keys(d.segments).sort().map((s) => `<option>${esc(s)}</option>`).join("");
      el("bis5-check").innerHTML = `<option value="">Any check</option>` + Object.entries(d.check_labels).map(([k, l]) => `<option value="${esc(k)}">${esc(l)} (${fmt(d.checks[k])})</option>`).join("");
      renderKpis();
      el("bis5-export-btn").hidden = false;
      applyFilters();
    } catch (err) {
      el("bis5-summary").textContent = `⚠ ${err.message}`;
      showToast(err.message, true);
    } finally { btn.disabled = false; }
  }

  function exportCsv() {
    const qv = (v) => { v = String(v ?? ""); return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
    const keys = ["reference", "release_ready", "services", "services_billed", "missing_services", "segment", "main_issue", "fares", "bills", "notices", "amount", "billing_period", "rate_only", "zero_neg", "amount_jump", "no_prev",
      "cs_not_active", "dup_active_cs", "pcal_cs_change", "estimated", "also_5000", "first_notice", "last_notice"];
    const lines = S.view.map((r) => keys.map((k) => qv(r[k])).join(","));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + [keys.map((k) => k.toUpperCase()).join(",")].concat(lines).join("\r\n")], { type: "text/csv;charset=utf-8" }));
    a.download = "case5_temp_stop_pcal.csv"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // RJ 2026-11-08: release script for the READY accounts in the current view (server re-checks them live)
  async function generateRelease() {
    const ids = S.view.filter((r) => String(r.release_ready) === "1").map((r) => String(r.id_payment_form));
    if (!ids.length) return;
    const btn = el("bis5-release-btn"); btn.disabled = true;
    el("bis5-release-out").textContent = "Re-checking the accounts live and generating… (about 30 s)";
    el("bis5-release-wrap").hidden = false;
    try {
      const d = await api("/api/bill-issuance/release-complete", { method: "POST", body: { case: "case5", id_payment_forms: ids } });
      el("bis5-release-out").textContent = d.sql_text;
      el("bis5-release-info").textContent = `${fmt(d.account_count)} account(s) in the script` +
        (d.warnings && d.warnings.length ? ` · ⚠ ${d.warnings.join(" · ")}` : "");
    } catch (err) {
      el("bis5-release-out").textContent = `⚠ ${err.message}`; showToast(err.message, true);
    } finally { btn.disabled = false; }
  }

  function bind() {
    if (!el("bis5-detect-btn")) return;
    el("bis5-ready").addEventListener("change", applyFilters);
    el("bis5-release-btn").addEventListener("click", generateRelease);
    el("bis5-copy-btn").addEventListener("click", () => { navigator.clipboard.writeText(el("bis5-release-out").textContent || ""); showToast("Script copied"); });
    el("bis5-download-btn").addEventListener("click", () => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([el("bis5-release-out").textContent || ""], { type: "text/plain" }));
      a.download = "case5_release_temp_stop_pcal.sql"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    });
    el("bis5-detect-btn").addEventListener("click", scan);
    el("bis5-export-btn").addEventListener("click", exportCsv);
    el("bis5-search").addEventListener("input", applyFilters);
    ["bis5-seg", "bis5-check"].forEach((id) => el(id).addEventListener("change", applyFilters));
    el("bis5-issues").addEventListener("click", (e) => {
      const b = e.target.closest("[data-bis5-issue]"); if (!b) return;
      S.issue = S.issue === b.dataset.bis5Issue ? "" : b.dataset.bis5Issue; applyFilters();
    });
    el("bis5-kpi-row").addEventListener("click", (e) => {
      const c = e.target.closest("[data-bis5-kpi]"); if (!c || !S.data) return;
      const k = c.dataset.bis5Kpi;
      S.kpi = (S.kpi === k || k === "all") ? "" : k;   // "Accounts held" = show all
      applyFilters();
    });
    el("bis5-clear").addEventListener("click", () => { S.issue = ""; S.kpi = ""; el("bis5-search").value = ""; el("bis5-seg").value = ""; el("bis5-check").value = ""; el("bis5-ready").value = ""; applyFilters(); });
    el("bis5-prev").addEventListener("click", () => { if (S.page > 0) { S.page--; renderTable(); } });
    el("bis5-next").addEventListener("click", () => { S.page++; renderTable(); });
    // first open of the tab: the app's auto-load clicks #bis5-detect-btn (HX_REFRESH billissuance);
    // fallback only if that did not start a scan
    document.querySelector('.da-subnav-btn[data-biss-sub="case5"]')?.addEventListener("click", () => {
      setTimeout(() => { if (!S.loaded && !el("bis5-detect-btn").disabled) scan(); }, 400);
    });
    renderTable();
  }
  bind();
  window.bissCase5 = { scan, state: S };
})();
