/* =====================================================================
   ScriptGen - TNB Dashboard (RJ 2026-11-25): "create a new menu for TNB Dashboard, 1st tab is TNB billed but
   transferred transaction is not done". Read only (Monitor). Tab 1 "Billed, debt not transferred":
   POST /api/tnb-dashboard/not-transferred/detect (app/core/tnb_dashboard.py). UI standard: docs/UI_STANDARD.md.
   ===================================================================== */
(function () {
  const S = { rows: [], sel: new Set(), filter: null };
  const el = (id) => document.getElementById(id);
  const esc = (s) => escapeHtml(String(s ?? ""));
  const fmt = (n) => Number(n || 0).toLocaleString();
  const money = (n) => Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const day = (s) => String(s || "").slice(0, 10);
  const key = (r) => String(r.id_bill);
  const sum = (list) => list.reduce((s, r) => s + (Number(r.total_amount) || 0), 0);
  if (!el("tnb1-detect-btn")) return;

  document.querySelectorAll(".da-subnav-btn[data-tnbd-sub]").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".da-subnav-btn[data-tnbd-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    document.querySelectorAll(".da-subpage[data-tnbd-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.tnbdSub === btn.dataset.tnbdSub));
  }));

  const PRED = {
    pending: (r) => r.settlement === "pending",
    paid_source: (r) => r.settlement === "paid_source",
    half: (r) => r.half_done,
    selected: (r) => S.sel.has(key(r)),
  };

  function visible() {
    const term = (el("tnb1-search").value || "").trim().toLowerCase();
    return S.rows.filter((r) => (!S.filter || PRED[S.filter](r)) && (!term ||
      [r.cod_ref_complaint, r.src_reference, r.dest_reference, r.bill_number, r.id_bill].some((v) => String(v ?? "").toLowerCase().includes(term))));
  }

  function settlementPill(r) {
    if (r.half_done) return `<span class="sg-pill" data-tone="bad" title="The destination has the transfer's Off-cycle bill but this source bill was never credited">Destination billed, source not credited</span>`;
    if (r.settlement === "pending") return `<span class="sg-pill" data-tone="warn">Still pending on source</span>`;
    if (r.settlement === "paid_source") return `<span class="sg-pill" data-tone="info">Paid on source - ${esc(r.payment_modes_en)}</span>`;
    return `<span class="sg-pill" data-tone="neutral">No collection</span>`;
  }

  function render() {
    const pend = S.rows.filter(PRED.pending), paid = S.rows.filter(PRED.paid_source), half = S.rows.filter(PRED.half);
    sgStd.kpis(el("tnb1-kpis"), [
      { key: null, label: "TNB bills not transferred", value: S.rows.length, tone: S.rows.length ? "bad" : "ok",
        sub: `${fmt(new Set(S.rows.map((r) => r.cod_ref_complaint)).size)} RCCS · ${money(sum(S.rows))}` },
      { key: "pending", label: "Still pending on source", value: pend.length, tone: "warn", sub: money(sum(pend)) },
      { key: "paid_source", label: "Paid on source by customer", value: paid.length, tone: "info", sub: money(sum(paid)) },
      { key: "half", label: "Destination billed, source not credited", value: half.length, tone: "bad", sub: money(sum(half)),
        tip: "The transfer's Off-cycle bill exists on the destination but the source TNB bill was never credited - charged twice" },
      { key: "selected", label: "Ticked", value: S.sel.size, tone: "neutral" },
    ], { active: S.filter, onPick: (k) => { S.filter = S.filter === k ? null : k; render(); } });

    const rows = visible();
    el("tnb1-shown").textContent = `${fmt(rows.length)} of ${fmt(S.rows.length)} rows shown`;
    el("tnb1-clear").hidden = !(S.filter || el("tnb1-search").value);
    el("tnb1-table").querySelector("tbody").innerHTML = rows.map((r) => `<tr>
      <td><input type="checkbox" data-tnb1-id="${esc(key(r))}" ${S.sel.has(key(r)) ? "checked" : ""} /></td>
      <td><b>${esc(r.cod_ref_complaint)}</b><br><small class="hint-text">solved ${esc(day(r.rccs_update_date))} · opened ${esc(day(r.rccs_date))}</small></td>
      <td>${esc(r.src_reference)}<br><small class="hint-text">ID_PAYMENT_FORM ${esc(r.src_id_payment_form)}</small></td>
      <td>${esc(r.dest_reference)}<br><small class="hint-text">ID_PAYMENT_FORM ${esc(r.dest_id_payment_form) || "?"}</small></td>
      <td>${esc(r.bill_number)}<br><small class="hint-text">ID_BILL ${esc(r.id_bill)} · billed ${esc(day(r.bill_create_date))}</small></td>
      <td style="text-align:right;">${money(r.total_amount)}<br><small class="hint-text">pending ${money(r.pending_amount)}</small></td>
      <td>${settlementPill(r)}${r.last_payment_date ? `<br><small class="hint-text">paid ${esc(day(r.last_payment_date))}</small>` : ""}</td>
      <td><span class="sg-pill" data-tone="${r.dt_action_status === "ACTSTA0002" ? "warn" : "neutral"}" title="${esc(r.dt_action_status)}">${esc(r.dt_action_status_en || "-")}</span><br><small class="hint-text">${esc(day(r.dt_action_date))}</small></td>
    </tr>`).join("")
      || `<tr><td colspan="8" class="hint-text">${S.rows.length ? "No row matches the filter." : "Every solved TNB RCCS has its debt transferred."}</td></tr>`;
    el("tnb1-check-all").checked = rows.length > 0 && rows.every((r) => S.sel.has(key(r)));
    el("tnb1-export-btn").disabled = !S.rows.length;
    el("tnb1-export-btn").textContent = S.sel.size ? `⬇ Export CSV (${S.sel.size} ticked)` : "⬇ Export CSV";
  }

  async function scan() {
    const btn = el("tnb1-detect-btn"); if (btn.disabled) return;
    btn.disabled = true; el("tnb1-meta").textContent = "Scanning…";
    try {
      const d = await api("/api/tnb-dashboard/not-transferred/detect", { method: "POST" });
      S.rows = d.rows || []; S.sel = new Set(); S.filter = null;
      sgStd.scanMeta(el("tnb1-meta"), { rows: d.count, elapsedMs: d.elapsed_ms, note: "read only" });
      render();
    } catch (err) { el("tnb1-meta").textContent = `⚠ ${err.message}`; showToast(err.message, true); }
    finally { btn.disabled = false; }
  }

  function exportCsv() {
    const list = S.sel.size ? S.rows.filter((r) => S.sel.has(key(r))) : visible();
    const cols = ["cod_ref_complaint", "id_complaint", "rccs_date", "rccs_update_date", "src_reference", "src_id_payment_form",
      "dest_reference", "dest_id_payment_form", "id_bill", "bill_number", "billing_date", "bill_create_date", "total_amount",
      "pending_amount", "settlement", "payment_modes_en", "last_payment_date", "half_done", "dt_action_status_en", "dt_action_date"];
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = [cols.map((c) => c.toUpperCase()).join(","), ...list.map((r) => cols.map((c) => q(r[c])).join(","))].join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `tnb_debt_not_transferred_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  el("tnb1-detect-btn").addEventListener("click", scan);
  el("tnb1-export-btn").addEventListener("click", exportCsv);
  el("tnb1-search").addEventListener("input", render);
  el("tnb1-clear").addEventListener("click", () => { S.filter = null; el("tnb1-search").value = ""; render(); });
  el("tnb1-table").addEventListener("change", (e) => {
    const cb = e.target.closest("[data-tnb1-id]"); if (!cb) return;
    if (cb.checked) S.sel.add(cb.dataset.tnb1Id); else S.sel.delete(cb.dataset.tnb1Id);
    render();
  });
  el("tnb1-check-all").addEventListener("change", (e) => {
    visible().forEach((r) => { if (e.target.checked) S.sel.add(key(r)); else S.sel.delete(key(r)); });
    render();
  });
  render();
  window.tnbDashboard = { scan, state: S };
})();
