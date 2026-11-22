/* =====================================================================
   ScriptGen - SR Quick FIX tab 2 "Sanitary Anomaly - H2O Pending" (RJ 2026-11-17).
   "i will provide with SR CODE, you will list the anomalies (GCCOM_ANOMALOUS) that is linked to the SR
   which is not resolved. The update script is to resolve the anomaly and get the reading of the anomaly,
   then using the reading get the ITB that is in status 00 anomalous, update it to status 01, here we
   will expect both for water and sanitary. next tab is to detect all cases" (anomaly type to come).
   Data: POST /api/sr-quick-fix/san-h2o/detect and /generate. Loaded after app.js.
   ===================================================================== */
(function () {
  const S = { data: null, rows: [], sel: new Set(), codes: "" };
  const SVC_COLOR = { "1": "#f59e0b", "19": "#0ea5e9", "190": "#14b8a6" };
  const ITB_LABEL = { STTOBILL00: "Anomalous", STTOBILL01: "Pending", STTOBILL04: "Cancelled", STTOBILL07: "Billed" };
  const RS_LABEL = { "6000STSRED": "Sent to bill", "7000STSRED": "Billed", "7001STSRED": "UAU - Billed", "5000STSRED": "Anomalous" };
  const esc = (s) => escapeHtml(String(s ?? ""));
  const fmt = (n) => Number(n || 0).toLocaleString();
  const el = (id) => document.getElementById(id);
  const day = (s) => String(s || "").slice(0, 10);

  // inner sub-nav (By SR code / Detect all)
  document.querySelectorAll(".da-subnav-btn[data-sh2o-sub]").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".da-subnav-btn[data-sh2o-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    document.querySelectorAll(".da-subpage[data-sh2o-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.sh2oSub === btn.dataset.sh2oSub));
  }));

  function renderKpis() {
    const d = S.data; if (!d) { el("sh2o-kpi-row").innerHTML = ""; return; }
    const card = (icon, label, val, tip, color) => `<div class="kpi-card" style="--k:${color}" title="${esc(tip)}">` +
      `<div class="kpi-value">${val}</div><div class="kpi-label"><span class="kpi-card-icon">${icon}</span>${esc(label)}</div></div>`;
    el("sh2o-kpi-row").innerHTML =
      card("🎫", "SRs found", fmt(d.sr_count), "SR codes with at least one unresolved anomaly", "#6366f1") +
      card("⚠", "Unresolved anomalies", fmt(d.count), "GCCOM_ANOMALOUS linked to the SR, not resolved", "#ef4444") +
      card("👥", "Accounts", fmt(d.account_count), "Accounts of those anomalies", "#0ea5e9") +
      card("➡", "ITBs 00 → 01", fmt(d.itb_to_move), "Item-to-bills of the anomalies' readings still Anomalous (water + sanitary)", "#14b8a6") +
      card("☑", "Selected", fmt(S.sel.size), "Anomalies that go in the script", "#16a34a");
  }

  function readingsCell(r) {
    const seen = new Map();
    (r.links || []).forEach((l) => { if (!seen.has(String(l.id_reading))) seen.set(String(l.id_reading), l); });
    return [...seen.values()].map((l) => `<div>${esc(l.id_reading)} <small class="hint-text">${esc(RS_LABEL[l.read_status] || l.read_status)} · ${esc(day(l.reading_date))}</small></div>`).join("")
      || `<span class="hint-text">no reading linked</span>`;
  }

  function itbCell(r) {
    const seen = new Map();
    (r.links || []).forEach((l) => { if (!seen.has(String(l.id_item_to_bill))) seen.set(String(l.id_item_to_bill), l); });
    return [...seen.values()].map((l) => {
      const move = l.itb_status === "STTOBILL00";
      return `<div style="border-left:3px solid ${SVC_COLOR[String(l.id_offered_service)] || "#64748b"};padding-left:6px;margin:2px 0;">` +
        `<b>${esc(l.service_desc || l.id_offered_service)}</b> ${esc(l.id_item_to_bill)} · ` +
        (move ? `<span style="color:#ef4444;font-weight:700;">Anomalous → Pending</span>` : esc(ITB_LABEL[l.itb_status] || l.itb_status)) + `</div>`;
    }).join("") || `<span class="hint-text">no item-to-bill</span>`;
  }

  function renderTable() {
    const tb = document.querySelector("#sh2o-table tbody");
    tb.innerHTML = S.rows.map((r) => {
      const id = String(r.id_anomalous);
      return `<tr style="box-shadow: inset 4px 0 0 ${r.itb_to_move && r.itb_to_move.length ? "#ef4444" : "#f59e0b"};">
        <td><input type="checkbox" data-sh2o-id="${esc(id)}" ${S.sel.has(id) ? "checked" : ""} /></td>
        <td><b>${esc(r.sr_code || "")}</b>${r.sr_code ? "" : `<small class="hint-text">ID_SERVREQ ${esc(r.id_servreq)} (no SR row)</small>`}</td>
        <td>${esc((r.servreq_type_desc || "").trim() || r.servreq_type)}<br><small style="color:#f59e0b;font-weight:700;">${esc(r.servreq_status_desc || r.servreq_status)}</small></td>
        <td>${esc(r.account)}</td><td>${esc(id)}<br><small class="hint-text">ITB ${esc(r.anom_itb)}</small></td>
        <td>${esc(r.anom_desc || r.anom_code)}</td>
        <td>${esc(r.anom_status_desc || r.anomalous_status)}<br><small class="hint-text">${esc(r.anomalous_status)}</small></td>
        <td><span style="color:${SVC_COLOR[String(r.id_offered_service)] || "inherit"};font-weight:700;">${esc(r.service_desc || r.id_offered_service)}</span></td>
        <td>${readingsCell(r)}</td><td>${itbCell(r)}</td></tr>`;
    }).join("") || `<tr><td colspan="10" class="hint-text">${S.data ? (S.mode === "all" ? "No unresolved WATERNOTBI anomaly with an SR." : "No unresolved anomaly linked to these SR(s).") : "Enter an SR code and click Scan, or use Detect all cases."}</td></tr>`;
    el("sh2o-gen-btn").disabled = !S.sel.size;
    el("sh2o-gen-btn").textContent = `🛠 Generate script (${fmt(S.sel.size)} anomaly(ies))`;
    el("sh2o-check-all").checked = S.rows.length > 0 && S.rows.every((r) => S.sel.has(String(r.id_anomalous)));
    renderKpis();
  }

  // RJ 2026-11-18: mode "bysr" (SR codes) or "all" (every unresolved WATERNOTBI anomaly with an ID_SERVREQ)
  async function scan(mode) {
    mode = mode === "all" ? "all" : "bysr";
    const codes = mode === "all" ? "" : ((el("sh2o-codes").value || "").trim() || (S.mode === "bysr" ? S.codes : ""));
    if (mode === "bysr" && !codes) { showToast("Enter an SR code.", true); return; }
    const btn = el(mode === "all" ? "sh2o-all-btn" : "sh2o-detect-btn"); if (btn.disabled) return;
    btn.disabled = true; el("sh2o-summary").textContent = "Scanning…"; el("sh2o-out-wrap").hidden = true;
    try {
      const d = await api("/api/sr-quick-fix/san-h2o/detect", { method: "POST", body: { sr_codes: codes, mode } });
      S.data = d; S.rows = d.rows || []; S.codes = codes; S.mode = mode;
      S.sel = new Set(S.rows.map((r) => String(r.id_anomalous)));
      el("sh2o-results-title").textContent = mode === "all" ? "All WATERNOTBI cases (unresolved, with SR)" : "Anomalies of the SR";
      el("sh2o-summary").innerHTML = (mode === "all" ? `All cases: <b>${fmt(d.count)}</b> unresolved WATERNOTBI anomal(y/ies) with an SR in ${fmt(d.sr_count)} SR(s)`
        : `SR ${esc(d.sr_codes.join(", "))}: <b>${fmt(d.count)}</b> unresolved anomal(y/ies)`) +
        `, ${fmt(d.itb_to_move)} item-to-bill(s) to move 00 → 01 · ${(d.elapsed_ms / 1000).toFixed(1)}s · read only` +
        (mode === "bysr" && d.missing_sr && d.missing_sr.length ? ` · <span style="color:#f59e0b;">no unresolved anomaly for ${esc(d.missing_sr.join(", "))}</span>` : "");
      renderTable();
    } catch (err) {
      el("sh2o-summary").textContent = `⚠ ${err.message}`; showToast(err.message, true);
    } finally { btn.disabled = false; }
  }

  async function generate() {   // eslint-disable-line no-unused-vars
    if (!S.sel.size) return;
    const btn = el("sh2o-gen-btn"); btn.disabled = true;
    el("sh2o-out-wrap").hidden = false; el("sh2o-out").textContent = "Re-checking live and generating…"; el("sh2o-rb-out").textContent = "";
    try {
      const d = await api("/api/sr-quick-fix/san-h2o/generate", { method: "POST", body: {
        sr_codes: S.codes, mode: S.mode || "bysr", anomaly_ids: [...S.sel], program: el("sh2o-program").value.trim() || "SAN_H2O_PEND",
        audit_user: el("sh2o-user").value.trim() || "RMA", clean: el("sh2o-clean").checked } });
      el("sh2o-out").textContent = d.sql; el("sh2o-rb-out").textContent = d.rollback_sql;
      el("sh2o-out-info").textContent = `${fmt(d.anomaly_count)} anomal(y/ies) resolved, ${fmt(d.itb_count)} item-to-bill(s) 00 → 01` +
        (d.skipped ? ` · ⚠ ${fmt(d.skipped)} selected anomal(y/ies) no longer unresolved - left out` : "") + " · saved in History";
    } catch (err) {
      el("sh2o-out").textContent = `⚠ ${err.message}`; showToast(err.message, true);
    } finally { btn.disabled = !S.sel.size; }
  }

  function download(text, name) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text || ""], { type: "text/plain" }));
    a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function bind() {
    if (!el("sh2o-detect-btn")) return;
    el("sh2o-detect-btn").addEventListener("click", () => scan("bysr"));
    el("sh2o-all-btn")?.addEventListener("click", () => scan("all"));
    el("sh2o-codes").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); scan("bysr"); } });
    el("sh2o-table").addEventListener("change", (e) => {
      const cb = e.target.closest("[data-sh2o-id]"); if (!cb) return;
      if (cb.checked) S.sel.add(cb.dataset.sh2oId); else S.sel.delete(cb.dataset.sh2oId);
      renderTable();
    });
    el("sh2o-check-all").addEventListener("change", (e) => {
      S.rows.forEach((r) => { const id = String(r.id_anomalous); if (e.target.checked) S.sel.add(id); else S.sel.delete(id); });
      renderTable();
    });
    el("sh2o-gen-btn").addEventListener("click", generate);
    const prog = () => (el("sh2o-program").value.trim() || "SAN_H2O_PEND");
    el("sh2o-copy-btn").addEventListener("click", () => { navigator.clipboard.writeText(el("sh2o-out").textContent || ""); showToast("Script copied"); });
    el("sh2o-rb-copy-btn").addEventListener("click", () => { navigator.clipboard.writeText(el("sh2o-rb-out").textContent || ""); showToast("Rollback copied"); });
    el("sh2o-download-btn").addEventListener("click", () => download(el("sh2o-out").textContent, `${prog()}_sanitary_anomaly_h2o_pending.sql`));
    el("sh2o-rb-download-btn").addEventListener("click", () => download(el("sh2o-rb-out").textContent, `${prog()}_sanitary_anomaly_h2o_pending_ROLLBACK.sql`));
    renderTable();
  }
  bind();
  window.srqSanH2o = { scan, state: S };
})();
