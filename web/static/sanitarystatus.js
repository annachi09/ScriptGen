/* =====================================================================
   ScriptGen - Sanitary Status dashboard (RJ 2026-11-24): "create a new menu for new dashboard, sanitary status,
   first case is to find all sanitary where the premise is disconnected but the tariff is sanitary billing not
   subsidized or zero tarif". Read only (Monitor). Tab 1 "Disconnected, still charged":
   POST /api/sanitary-status/disc-charged/detect (app/core/sanitary_status.py). UI standard: docs/UI_STANDARD.md.
   ===================================================================== */
(function () {
  const S = { rows: [], sel: new Set(), filter: null };
  const el = (id) => document.getElementById(id);
  const esc = (s) => escapeHtml(String(s ?? ""));
  const fmt = (n) => Number(n || 0).toLocaleString();
  const money = (n) => Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const day = (s) => String(s || "").slice(0, 10);
  if (!el("sst1-detect-btn")) return;

  document.querySelectorAll(".da-subnav-btn[data-sst-sub]").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".da-subnav-btn[data-sst-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    document.querySelectorAll(".da-subpage[data-sst-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.sstSub === btn.dataset.sstSub));
  }));

  function visible() {
    const term = (el("sst1-search").value || "").trim().toLowerCase();
    return S.rows.filter((r) => {
      if (S.filter === "selected" && !S.sel.has(r.id_contracted_service)) return false;
      if (S.filter && S.filter.startsWith("prem:") && r.id_premise !== S.filter.slice(5)) return false;
      if (!term) return true;
      return [r.reference, r.niss, r.id_premise, r.id_payment_form, r.id_contracted_service].some((v) => String(v ?? "").toLowerCase().includes(term));
    });
  }

  function render() {
    const premises = new Set(S.rows.map((r) => r.id_premise));
    const billed = S.rows.reduce((s, r) => s + (Number(r.amount_since) || 0), 0);
    const bills = S.rows.reduce((s, r) => s + (Number(r.bills_since) || 0), 0);
    sgStd.kpis(el("sst1-kpis"), [
      { key: null, label: "Sanitary services", value: S.rows.length, tone: S.rows.length ? "bad" : "ok", sub: "disconnected premise, W-SAN01" },
      { key: null, label: "Premises", value: premises.size, tone: "info" },
      { key: null, label: "Bills since disconnection", value: bills, tone: "warn" },
      { key: null, label: "Billed since disconnection", value: money(billed), tone: "warn", sub: "sum of those bills' concepts" },
      { key: "selected", label: "Ticked", value: S.sel.size, tone: "neutral" },
    ], { active: S.filter === "selected" ? "selected" : null, onPick: (k) => { S.filter = S.filter === k ? null : k; render(); } });

    const rows = visible();
    el("sst1-shown").textContent = `${fmt(rows.length)} of ${fmt(S.rows.length)} rows shown`;
    el("sst1-clear").hidden = !(S.filter || el("sst1-search").value);
    el("sst1-table").querySelector("tbody").innerHTML = rows.map((r) => `<tr>
      <td><input type="checkbox" data-sst1-id="${esc(r.id_contracted_service)}" ${S.sel.has(r.id_contracted_service) ? "checked" : ""} /></td>
      <td><b>${esc(r.reference)}</b><br><small class="hint-text">ID_PAYMENT_FORM ${esc(r.id_payment_form)}</small></td>
      <td>${esc(r.niss)}</td>
      <td><a href="#" data-sst1-prem="${esc(r.id_premise)}" title="Show only this premise">${esc(r.id_premise)}</a></td>
      <td><span class="sg-pill" data-tone="bad">Disconnected</span><br><small class="hint-text">${esc(day(r.disconnected_date))}</small></td>
      <td>${esc(r.id_contracted_service)}<br><small class="hint-text">${esc(r.cs_status_name || r.cs_status)}</small></td>
      <td><span class="sg-pill" data-tone="warn" title="ID_FARE ${esc(r.id_fare)}">${esc(r.rate_code)} ${esc(r.fare_name)}</span><br><small class="hint-text">billing service ${esc(r.id_billing_service)} · changed ${esc(day(r.bs_update_date))}</small></td>
      <td style="text-align:right;">${fmt(r.bills_since)}</td>
      <td style="text-align:right;">${money(r.amount_since)}</td>
      <td>${esc(day(r.last_bill_date)) || "-"}<br><small class="hint-text">${esc(r.last_id_bill)}</small></td></tr>`).join("")
      || `<tr><td colspan="10" class="hint-text">${S.rows.length ? "No row matches the filter." : "No disconnected premise is on the Sanitary Charges tariff."}</td></tr>`;
    el("sst1-check-all").checked = rows.length > 0 && rows.every((r) => S.sel.has(r.id_contracted_service));
    el("sst1-export-btn").disabled = !S.rows.length;
    el("sst1-export-btn").textContent = S.sel.size ? `⬇ Export CSV (${S.sel.size} ticked)` : "⬇ Export CSV";
  }

  async function scan() {
    const btn = el("sst1-detect-btn"); if (btn.disabled) return;
    btn.disabled = true; el("sst1-meta").textContent = "Scanning…";
    try {
      const d = await api("/api/sanitary-status/disc-charged/detect", { method: "POST" });
      S.rows = d.rows || []; S.sel = new Set(); S.filter = null;
      sgStd.scanMeta(el("sst1-meta"), { rows: d.count, elapsedMs: d.elapsed_ms, note: "read only" });
      render();
    } catch (err) { el("sst1-meta").textContent = `⚠ ${err.message}`; showToast(err.message, true); }
    finally { btn.disabled = false; }
  }

  function exportCsv() {
    const list = S.sel.size ? S.rows.filter((r) => S.sel.has(r.id_contracted_service)) : visible();
    const cols = ["reference", "id_payment_form", "niss", "id_premise", "disconnected_date", "id_contracted_service", "cs_status",
      "id_billing_service", "id_fare", "rate_code", "fare_name", "bs_update_date", "bills_since", "amount_since", "last_bill_date", "last_id_bill"];
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = [cols.map((c) => c.toUpperCase()).join(","), ...list.map((r) => cols.map((c) => q(r[c])).join(","))].join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `sanitary_disconnected_still_charged_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  el("sst1-detect-btn").addEventListener("click", scan);
  el("sst1-export-btn").addEventListener("click", exportCsv);
  el("sst1-search").addEventListener("input", render);
  el("sst1-clear").addEventListener("click", () => { S.filter = null; el("sst1-search").value = ""; render(); });
  el("sst1-table").addEventListener("change", (e) => {
    const cb = e.target.closest("[data-sst1-id]"); if (!cb) return;
    if (cb.checked) S.sel.add(cb.dataset.sst1Id); else S.sel.delete(cb.dataset.sst1Id);
    render();
  });
  el("sst1-table").addEventListener("click", (e) => {
    const a = e.target.closest("[data-sst1-prem]"); if (!a) return;
    e.preventDefault(); const k = `prem:${a.dataset.sst1Prem}`; S.filter = S.filter === k ? null : k; render();
  });
  el("sst1-check-all").addEventListener("change", (e) => {
    visible().forEach((r) => { if (e.target.checked) S.sel.add(r.id_contracted_service); else S.sel.delete(r.id_contracted_service); });
    render();
  });
  render();
  window.sanitaryStatus = { scan, state: S };
})();
