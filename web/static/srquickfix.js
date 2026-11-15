/* =====================================================================
   ScriptGen - SR Quick FIX (RJ 2026-11-09).
   Tab 1 "SR Incorrect Billing Period": RJ gives the account, we list its sent-to-bill (6000STSRED)
   electricity / water readings; the script = RJ's 3 UPDATEs (reading -> billed with
   VALUE = PREV_VALUE, anomalies resolved, ITBs cancelled - water reading also covers sanitary),
   UPDATE_PROGRAM = INC_BP. Shown only, never executed.
   RJ 2026-11-12: only readings with an Incorrect billing period anomaly on an UNRESOLVED SR are
   shown ("we only need the one with SR, the rest remove"); one row per account with its SR
   ("I am expecting 1 SR only for each account") and the sector supplies + readings as sub rows
   ("if there are multiple sector supply involve, add it as a sub data"). More than 1 SR on an
   account is flagged.
   Data: POST /api/sr-quick-fix/incorrect-bp/detect and /generate. Loaded after app.js.
   ===================================================================== */
(function () {
  const S = { data: null, groups: [], view: [], sel: new Set(), open: new Set(), scope: { accounts: "", floor: null } };
  const ITB_LABEL = { STTOBILL00: "Anomalous", STTOBILL01: "Pending", STTOBILL04: "Cancelled" };
  const SVC_COLOR = { "1": "#f59e0b", "19": "#0ea5e9", "190": "#14b8a6" };
  const COLS = 12;
  const esc = (s) => escapeHtml(String(s ?? ""));
  const num = (v) => { const n = Number(String(v ?? "").replace(/,/g, "")); return Number.isFinite(n) ? n : 0; };
  const fmt = (n) => Number(n || 0).toLocaleString();
  const el = (id) => document.getElementById(id);
  const day = (s) => String(s || "").slice(0, 10);
  const readingsOf = (g) => g.supplies.flatMap((s) => s.readings);

  // own sub-nav listener (same reason as the other pages' data-*-sub navs)
  document.querySelectorAll(".da-subnav-btn[data-srq-sub]").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".da-subnav-btn[data-srq-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    document.querySelectorAll(".da-subpage[data-srq-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.srqSub === btn.dataset.srqSub));
  }));

  function applyFilters() {
    const q = (el("srq-search").value || "").trim();
    S.view = S.groups.filter((g) => !q || String(g.account || "").includes(q) ||
      g.srs.some((s) => String(s.sr_code || "").includes(q)) ||
      g.supplies.some((s) => String(s.niss || "").includes(q) || s.readings.some((r) => String(r.id_reading).includes(q))));
    renderTable();
  }

  function renderKpis() {
    const d = S.data; if (!d) { el("srq-kpi-row").innerHTML = ""; return; }
    const card = (icon, label, val, tip, color) => `<div class="kpi-card" style="--k:${color}" title="${esc(tip)}">` +
      `<div class="kpi-value">${val}</div><div class="kpi-label"><span class="kpi-card-icon">${icon}</span>${esc(label)}</div></div>`;
    el("srq-kpi-row").innerHTML =
      card("👥", "Accounts with SR", fmt(d.account_count), "Accounts with an Incorrect billing period anomaly on an unresolved SR", "#ef4444") +
      card("🎫", "SRs", fmt(d.sr_count), "Distinct unresolved SRs", "#6366f1") +
      card("🏠", "Sector supplies", fmt(d.supply_count), "NISS involved", "#0ea5e9") +
      card("📖", "Readings", fmt(d.count), "Sent-to-bill readings with the SR anomaly", "#8b5cf6") +
      card("📉", "Lower than previous", fmt(d.candidate_count), "VALUE < PREV_VALUE - pre-selected", "#f97316") +
      card("🚿", "With sanitary", fmt(d.sanitary_count), "Water readings also linked to a sanitary item-to-bill", "#14b8a6") +
      card("⚠", "Accounts with >1 SR", fmt((d.multi_sr_accounts || []).length), "Expected 1 SR per account - check these", "#f59e0b") +
      card("☑", "Selected", fmt(S.sel.size), "Readings that go in the script", "#16a34a");
  }

  function itemsCell(r) {
    return (r.items || []).map((i) => {
      const c = SVC_COLOR[String(i.id_offered_service)] || "#64748b";
      const shared = num(i.shared_readings) > 1 ? ` <small title="This item-to-bill holds ${fmt(num(i.shared_readings))} readings - cancelling it affects all of them">· shared ×${fmt(num(i.shared_readings))}</small>` : "";
      return `<div style="border-left:3px solid ${c};padding-left:6px;margin:2px 0;" title="${esc(i.anomalies || "no anomalies")}">` +
        `<b>${esc(i.service_desc || i.id_offered_service)}</b> ${esc(i.id_item_to_bill)} · ${esc(ITB_LABEL[i.item_status] || i.item_status)}` +
        ` · ${num(i.open_anomalies) ? `<span style="color:#ef4444;font-weight:700;">${fmt(num(i.open_anomalies))} open</span>` : "0 open"}${shared}</div>`;
    }).join("") || `<span class="hint-text">no item-to-bill</span>`;
  }

  // RJ 2026-11-12: SR, SR type, SR status on the account row (one line per SR; >1 = flagged)
  function srCols(g) {
    const col = (f) => `<td>${g.srs.map((s) => `<div style="margin:2px 0;">${f(s)}</div>`).join("")}</td>`;
    return col((s) => `<b title="ID_SERVREQ ${esc(s.id_servreq)} · created ${esc(day(s.creation_sr_date))}">${esc(s.sr_code || s.id_servreq)}</b>`) +
      col((s) => `${esc((s.servreq_type_desc || "").trim() || s.servreq_type)} <small class="hint-text">${esc(s.servreq_type)}</small>`) +
      col((s) => `<span style="color:#f59e0b;font-weight:700;">${esc(s.servreq_status_desc || s.servreq_status)}</span> <small class="hint-text">${esc(s.servreq_status)}</small>`);
  }

  function renderTable() {
    const tb = document.querySelector("#srq-table tbody");
    const html = [];
    S.view.forEach((g) => {
      const acc = String(g.account), rs = readingsOf(g);
      const nSel = rs.filter((r) => S.sel.has(String(r.id_reading))).length;
      const open = S.open.has(acc);
      html.push(`<tr class="srq-acc" style="background:rgba(99,102,241,.08);box-shadow: inset 4px 0 0 ${g.multi_sr ? "#f59e0b" : "#ef4444"};">
        <td><input type="checkbox" data-srq-acc="${esc(acc)}" ${nSel && nSel === rs.length ? "checked" : ""} title="Select all readings of the account" /></td>
        <td><button type="button" class="btn btn-pill-sm" data-srq-toggle="${esc(acc)}" style="padding:0 6px;">${open ? "▾" : "▸"}</button> <b>${esc(acc)}</b>
          ${g.multi_sr ? `<br><small style="color:#f59e0b;font-weight:700;">⚠ ${g.srs.length} SRs - expected 1</small>` : ""}</td>
        ${srCols(g)}
        <td colspan="${COLS - 5}">${fmt(g.supplies.length)} sector suppl${g.supplies.length === 1 ? "y" : "ies"} ·
          ${fmt(rs.length)} reading(s) · ${fmt(nSel)} selected ·
          ${g.supplies.map((s) => `<span style="color:${SVC_COLOR[String(s.id_offered_service)] || "inherit"};font-weight:700;">${esc(s.service_desc)}</span> ${esc(s.niss)}`).join(", ")}</td></tr>`);
      if (!open) return;
      g.supplies.forEach((s) => {
        html.push(`<tr><td></td><td colspan="${COLS - 1}" style="padding-left:28px;">↳ <b>NISS ${esc(s.niss)}</b> ·
          <span style="color:${SVC_COLOR[String(s.id_offered_service)] || "inherit"};font-weight:700;">${esc(s.service_desc || s.id_offered_service)}</span>
          · ${fmt(s.readings.length)} reading(s)</td></tr>`);
        s.readings.forEach((r) => {
          const id = String(r.id_reading);
          const color = r.lower_than_prev ? "#ef4444" : "transparent";
          html.push(`<tr style="box-shadow: inset 4px 0 0 ${color};">
            <td style="padding-left:20px;"><input type="checkbox" data-srq-id="${esc(id)}" ${S.sel.has(id) ? "checked" : ""} /></td>
            <td style="padding-left:44px;">Reading <b>${esc(id)}</b>${r.has_sanitary ? " <small>+ sanitary</small>" : ""}</td>
            <td>${esc(r.id_billing_period)}</td>
            <td>${esc(day(r.reading_prev_date))} → ${esc(day(r.reading_date))}</td>
            <td>${esc(r.prev_value)}</td>
            <td style="${r.lower_than_prev ? "color:#ef4444;font-weight:700;" : ""}">${esc(r.value)}</td>
            <td>${esc(r.reading_usage)}</td><td>${esc(r.ready_usage)}</td>
            <td colspan="${COLS - 8}">${itemsCell(r)}</td></tr>`);
        });
      });
    });
    tb.innerHTML = html.join("") || `<tr><td colspan="${COLS}" class="hint-text">${S.data
      ? "No sent-to-bill reading with an Incorrect billing period anomaly on an unresolved SR." : "Enter an account and click Scan."}</td></tr>`;
    const nReadings = S.view.reduce((n, g) => n + readingsOf(g).length, 0);
    el("srq-filtered-count").textContent = S.data ? `${fmt(S.view.length)} of ${fmt(S.groups.length)} account(s) · ${fmt(nReadings)} reading(s) · ${fmt(S.sel.size)} selected` : "";
    el("srq-gen-btn").disabled = !S.sel.size;
    el("srq-gen-btn").textContent = `🛠 Generate script (${fmt(S.sel.size)} reading(s))`;
    const all = S.view.flatMap(readingsOf);
    el("srq-check-all").checked = all.length > 0 && all.every((r) => S.sel.has(String(r.id_reading)));
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
      S.data = d; S.groups = d.groups || []; S.scope = { accounts, floor };
      S.sel = new Set((d.rows || []).filter((r) => r.candidate).map((r) => String(r.id_reading)));
      // single account scan: open it; system-wide: collapsed
      S.open = new Set(S.groups.length <= 3 ? S.groups.map((g) => String(g.account)) : []);
      el("srq-summary").innerHTML = `${all ? "System-wide" : `Account(s) ${esc(d.accounts.join(", "))}`}: <b>${fmt(d.account_count)}</b> account(s) with an unresolved SR, ` +
        `${fmt(d.count)} reading(s) (of ${fmt(d.scanned_count)} sent to bill scanned) · ${(d.elapsed_ms / 1000).toFixed(1)}s · read only` +
        (d.missing_accounts && d.missing_accounts.length ? ` · <span style="color:#f59e0b;">no reading with an unresolved SR for ${esc(d.missing_accounts.join(", "))}</span>` : "") +
        ((d.multi_sr_accounts || []).length ? ` · <span style="color:#f59e0b;font-weight:700;">⚠ more than 1 SR: ${esc(d.multi_sr_accounts.join(", "))}</span>` : "");
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
        (d.skipped ? ` · ⚠ ${fmt(d.skipped)} selected reading(s) no longer qualify - left out` : "") + " · saved in History";
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
    el("srq-search").addEventListener("input", applyFilters);
    el("srq-table").addEventListener("click", (e) => {
      const t = e.target.closest("[data-srq-toggle]"); if (!t) return;
      const a = t.dataset.srqToggle; if (S.open.has(a)) S.open.delete(a); else S.open.add(a);
      renderTable();
    });
    el("srq-table").addEventListener("change", (e) => {
      const cb = e.target.closest("[data-srq-id]");
      if (cb) { if (cb.checked) S.sel.add(cb.dataset.srqId); else S.sel.delete(cb.dataset.srqId); renderTable(); return; }
      const ab = e.target.closest("[data-srq-acc]"); if (!ab) return;
      const g = S.groups.find((x) => String(x.account) === ab.dataset.srqAcc); if (!g) return;
      readingsOf(g).forEach((r) => { const id = String(r.id_reading); if (ab.checked) S.sel.add(id); else S.sel.delete(id); });
      renderTable();
    });
    el("srq-check-all").addEventListener("change", (e) => {
      S.view.flatMap(readingsOf).forEach((r) => { const id = String(r.id_reading); if (e.target.checked) S.sel.add(id); else S.sel.delete(id); });
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
