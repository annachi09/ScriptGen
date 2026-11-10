/* =====================================================================
   ScriptGen - SR Quick FIX (RJ 2026-11-09).
   Tab 1 "SR Incorrect Billing Period": RJ gives the account, we list its sent-to-bill (6000STSRED)
   electricity / water readings; VALUE < PREV_VALUE ones are pre-selected; the script = RJ's 3
   UPDATEs (reading -> billed with VALUE = PREV_VALUE, anomalies resolved, ITBs cancelled - water
   reading also covers sanitary), UPDATE_PROGRAM = INC_BP. Shown only, never executed.
   Data: POST /api/sr-quick-fix/incorrect-bp/detect and /generate. Loaded after app.js.
   ===================================================================== */
(function () {
  const S = { data: null, rows: [], view: [], sel: new Set(), scope: { accounts: "", floor: null } };
  const ITB_LABEL = { STTOBILL00: "Anomalous", STTOBILL01: "Pending", STTOBILL04: "Cancelled" };
  const SVC_COLOR = { "1": "#f59e0b", "19": "#0ea5e9", "190": "#14b8a6" };
  const esc = (s) => escapeHtml(String(s ?? ""));
  const num = (v) => { const n = Number(String(v ?? "").replace(/,/g, "")); return Number.isFinite(n) ? n : 0; };
  const fmt = (n) => Number(n || 0).toLocaleString();
  const el = (id) => document.getElementById(id);
  const day = (s) => String(s || "").slice(0, 10);

  // own sub-nav listener (same reason as the other pages' data-*-sub navs)
  document.querySelectorAll(".da-subnav-btn[data-srq-sub]").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".da-subnav-btn[data-srq-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    document.querySelectorAll(".da-subpage[data-srq-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.srqSub === btn.dataset.srqSub));
  }));

  function applyFilters() {
    const q = (el("srq-search").value || "").trim();
    const lower = el("srq-only-lower").checked;
    S.view = S.rows.filter((r) => (!lower || r.lower_than_prev) &&
      (!q || [r.account, r.niss, r.id_reading].some((v) => String(v || "").includes(q))));
    renderTable();
  }

  function renderKpis() {
    const d = S.data; if (!d) { el("srq-kpi-row").innerHTML = ""; return; }
    const card = (icon, label, val, tip, color) => `<div class="kpi-card" style="--k:${color}" title="${esc(tip)}">` +
      `<div class="kpi-value">${val}</div><div class="kpi-label"><span class="kpi-card-icon">${icon}</span>${esc(label)}</div></div>`;
    el("srq-kpi-row").innerHTML =
      card("📖", "Sent-to-bill readings", fmt(d.count), "Electricity / water readings in 6000STSRED", "#6366f1") +
      card("📉", "Lower than previous", fmt(d.candidate_count), "VALUE < PREV_VALUE with a negative usage - pre-selected", "#ef4444") +
      card("👥", "Accounts", fmt(d.account_count), "Accounts with at least one candidate reading", "#0ea5e9") +
      card("🚿", "With sanitary", fmt(d.sanitary_count), "Candidate water readings also linked to a sanitary item-to-bill", "#14b8a6") +
      card("⚠", "Rollover?", fmt(d.rollover_count), "Lower value but positive usage - meter rollover, not pre-selected", "#f59e0b") +
      card("☑", "Selected", fmt(S.sel.size), "Readings that go in the script", "#16a34a");
  }

  function itemsCell(r) {
    return (r.items || []).map((i) => {
      const c = SVC_COLOR[String(i.id_offered_service)] || "#64748b";
      const shared = num(i.shared_readings) > 1 ? ` <small title="This item-to-bill holds ${fmt(num(i.shared_readings))} readings - cancelling it affects all of them">· shared ×${fmt(num(i.shared_readings))}</small>` : "";
      return `<div style="border-left:3px solid ${c};padding-left:6px;margin:2px 0;" title="${esc(i.anomalies || "no anomalies")}">` +
        `<b>${esc(i.service_desc || i.id_offered_service)}</b> ${esc(i.id_item_to_bill)} · ${esc(ITB_LABEL[i.item_status] || i.item_status)}` +
        ` · ${num(i.open_anomalies) ? `<span style="color:#ef4444;font-weight:700;">${fmt(num(i.open_anomalies))} open</span>` : "0 open"}${shared}` +
        // anomalies in another status (e.g. ESTAN00009) are NOT touched by RJ's template (ESTAN00001 only)
        (!num(i.open_anomalies) && i.anomalies ? `<br><small class="hint-text">anomaly: ${esc(i.anomalies)} - not changed by the script</small>` : "") + `</div>`;
    }).join("") || `<span class="hint-text">no item-to-bill</span>`;
  }

  function renderTable() {
    const tb = document.querySelector("#srq-table tbody");
    tb.innerHTML = S.view.map((r) => {
      const id = String(r.id_reading);
      const color = r.rollover ? "#f59e0b" : r.candidate ? "#ef4444" : "transparent";
      return `<tr style="box-shadow: inset 4px 0 0 ${color};">
        <td><input type="checkbox" data-srq-id="${esc(id)}" ${S.sel.has(id) ? "checked" : ""} /></td>
        <td><b>${esc(r.account)}</b></td><td>${esc(r.niss)}</td>
        <td><span style="color:${SVC_COLOR[String(r.id_offered_service)] || "inherit"};font-weight:700;">${esc(r.service_desc || r.id_offered_service)}</span>${r.has_sanitary ? " <small>+ sanitary</small>" : ""}</td>
        <td>${esc(id)}</td><td>${esc(r.id_billing_period)}</td>
        <td>${esc(day(r.reading_prev_date))} → ${esc(day(r.reading_date))}</td>
        <td>${esc(r.prev_value)}</td>
        <td style="${r.lower_than_prev ? "color:#ef4444;font-weight:700;" : ""}">${esc(r.value)}${r.rollover ? ` <span title="Lower value but positive usage - meter rollover?">⚠</span>` : ""}</td>
        <td>${esc(r.reading_usage)}</td><td>${esc(r.ready_usage)}</td><td>${itemsCell(r)}</td></tr>`;
    }).join("") || `<tr><td colspan="12" class="hint-text">${S.data ? "No readings for these filters." : "Enter an account and click Scan."}</td></tr>`;
    el("srq-filtered-count").textContent = S.data ? `${fmt(S.view.length)} of ${fmt(S.rows.length)} reading(s) · ${fmt(S.sel.size)} selected` : "";
    el("srq-gen-btn").disabled = !S.sel.size;
    el("srq-gen-btn").textContent = `🛠 Generate script (${fmt(S.sel.size)} reading(s))`;
    el("srq-check-all").checked = S.view.length > 0 && S.view.every((r) => S.sel.has(String(r.id_reading)));
    renderKpis();
  }

  async function scan(all) {
    const accounts = all ? "" : (el("srq-accounts").value || "").trim();
    if (!all && !accounts) {
      // Refresh with nothing entered: re-run the last scan, else ask for an account
      if (S.data) return scan(S.scope.accounts === "");
      showToast("Enter an account (or click All candidates).", true); return;
    }
    const floor = el("srq-floor").value ? Number(el("srq-floor").value) : null;
    const btns = [el("srq-detect-btn"), el("srq-all-btn")];
    if (btns.some((b) => b.disabled)) return;
    btns.forEach((b) => { b.disabled = true; });
    el("srq-summary").textContent = all ? "Scanning all candidates… (a few seconds)" : "Scanning…";
    el("srq-out-wrap").hidden = true;
    try {
      const d = await api("/api/sr-quick-fix/incorrect-bp/detect", { method: "POST", body: { accounts, period_floor: floor } });
      S.data = d; S.rows = d.rows || []; S.scope = { accounts, floor };
      S.sel = new Set(S.rows.filter((r) => r.candidate).map((r) => String(r.id_reading)));
      el("srq-summary").innerHTML = `${all ? "System-wide candidates" : `Account(s) ${esc(d.accounts.join(", "))}`}: ${fmt(d.count)} sent-to-bill reading(s), ` +
        `<b>${fmt(d.candidate_count)}</b> lower than previous · ${(d.elapsed_ms / 1000).toFixed(1)}s · read only` +
        (d.missing_accounts && d.missing_accounts.length ? ` · <span style="color:#f59e0b;">no sent-to-bill reading for ${esc(d.missing_accounts.join(", "))}</span>` : "");
      if (all) el("srq-only-lower").checked = true;
      applyFilters();
    } catch (err) {
      el("srq-summary").textContent = `⚠ ${err.message}`; showToast(err.message, true);
    } finally { btns.forEach((b) => { b.disabled = false; }); }
  }

  async function generate() {
    if (!S.sel.size) return;
    const btn = el("srq-gen-btn"); btn.disabled = true;
    el("srq-out-wrap").hidden = false; el("srq-out").textContent = "Re-checking the readings live and generating…"; el("srq-rb-out").textContent = "";
    try {
      const d = await api("/api/sr-quick-fix/incorrect-bp/generate", { method: "POST", body: {
        accounts: S.scope.accounts, period_floor: S.scope.floor, reading_ids: [...S.sel],
        program: el("srq-program").value.trim() || "INC_BP", audit_user: el("srq-user").value.trim() || "RMA",
        clean: el("srq-clean").checked } });
      el("srq-out").textContent = d.sql; el("srq-rb-out").textContent = d.rollback_sql;
      el("srq-out-info").textContent = `${fmt(d.reading_count)} reading(s), ${fmt(d.item_count)} item-to-bill(s)` +
        (d.sanitary_items ? ` incl. ${fmt(d.sanitary_items)} sanitary` : "") +
        (d.skipped ? ` · ⚠ ${fmt(d.skipped)} selected reading(s) are no longer Sent to bill - left out` : "") + " · saved in History";
    } catch (err) {
      el("srq-out").textContent = `⚠ ${err.message}`; showToast(err.message, true);
    } finally { btn.disabled = !S.sel.size; }
  }

  function download(text, name) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text || ""], { type: "text/plain" }));
    a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function bind() {
    if (!el("srq-detect-btn")) return;
    el("srq-detect-btn").addEventListener("click", () => scan(false));
    el("srq-all-btn").addEventListener("click", () => scan(true));
    el("srq-accounts").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || !e.shiftKey)) { e.preventDefault(); scan(false); } });
    el("srq-only-lower").addEventListener("change", applyFilters);
    el("srq-search").addEventListener("input", applyFilters);
    el("srq-table").addEventListener("change", (e) => {
      const cb = e.target.closest("[data-srq-id]"); if (!cb) return;
      if (cb.checked) S.sel.add(cb.dataset.srqId); else S.sel.delete(cb.dataset.srqId);
      renderTable();
    });
    el("srq-check-all").addEventListener("change", (e) => {
      S.view.forEach((r) => { const id = String(r.id_reading); if (e.target.checked) S.sel.add(id); else S.sel.delete(id); });
      renderTable();
    });
    el("srq-gen-btn").addEventListener("click", generate);
    const prog = () => (el("srq-program").value.trim() || "INC_BP");
    el("srq-copy-btn").addEventListener("click", () => { navigator.clipboard.writeText(el("srq-out").textContent || ""); showToast("Script copied"); });
    el("srq-rb-copy-btn").addEventListener("click", () => { navigator.clipboard.writeText(el("srq-rb-out").textContent || ""); showToast("Rollback copied"); });
    el("srq-download-btn").addEventListener("click", () => download(el("srq-out").textContent, `${prog()}_sr_incorrect_billing_period.sql`));
    el("srq-rb-download-btn").addEventListener("click", () => download(el("srq-rb-out").textContent, `${prog()}_sr_incorrect_billing_period_ROLLBACK.sql`));
    renderTable();
  }
  bind();
  window.srQuickFix = { scan, state: S };
})();
