/* =====================================================================
   ScriptGen - TNB Dashboard (RJ 2026-11-25). Tabs: Summary | Case 1 Debt not transferred | Case 2 TNB on
   cancelled contract | Case 3 Cycle vs Disconnection (was its own menu, logic in app.js tcd*) | Case 4 TNB
   disconnections (was its own menu, app.js dtnb*). Original notes:
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
    selected: (r) => S.sel.has(key(r)),
  };

  function visible() {
    const terms = sgStd.terms(el("tnb1-search"));   // RJ 2026-11-26: paste-many filter
    return S.rows.filter((r) => (!S.filter || PRED[S.filter](r))
      && sgStd.matches(terms, [r.src_reference, r.dest_reference, r.cod_ref_complaint, r.bill_number, r.id_bill]));
  }

  function settlementPill(r) {
    if (r.settlement === "pending") return `<span class="sg-pill" data-tone="warn">Still pending on source</span>`;
    if (r.settlement === "paid_source") return `<span class="sg-pill" data-tone="info">Paid on source - ${esc(r.payment_modes_en)}</span>`;
    return `<span class="sg-pill" data-tone="neutral">No collection</span>`;
  }

  function render() {
    const pend = S.rows.filter(PRED.pending), paid = S.rows.filter(PRED.paid_source);
    sgStd.kpis(el("tnb1-kpis"), [
      { key: null, label: "TNB bills not transferred", value: S.rows.length, tone: S.rows.length ? "bad" : "ok",
        sub: `${fmt(new Set(S.rows.map((r) => r.cod_ref_complaint)).size)} RCCS · ${money(sum(S.rows))}` },
      { key: "pending", label: "Still pending on source", value: pend.length, tone: "warn", sub: money(sum(pend)) },
      { key: "paid_source", label: "Paid on source by customer", value: paid.length, tone: "info", sub: money(sum(paid)) },
      { key: "selected", label: "Ticked", value: S.sel.size, tone: "neutral" },
    ], { active: S.filter, onPick: (k) => { S.filter = S.filter === k ? null : k; render(); } });

    const rows = visible();
    el("tnb1-shown").textContent = `${fmt(rows.length)} of ${fmt(S.rows.length)} rows shown`;
    el("tnb1-clear").hidden = !(S.filter || el("tnb1-search").value);
    el("tnb1-table").querySelector("tbody").innerHTML = rows.map((r) => `<tr>
      <td><input type="checkbox" data-tnb1-id="${esc(key(r))}" ${S.sel.has(key(r)) ? "checked" : ""} /></td>
      <td><b>${esc(r.cod_ref_complaint)}</b> <span class="sg-pill" data-tone="${r.rccs_type === "5163COMTYP" ? "info" : "warn"}" title="${esc(r.rccs_type)}">${esc(r.rccs_type_en)}</span><br><small class="hint-text">solved ${esc(day(r.rccs_update_date))} · opened ${esc(day(r.rccs_date))}</small></td>
      <td>${esc(r.src_reference)}<br><small class="hint-text">ID_PAYMENT_FORM ${esc(r.src_id_payment_form)}</small></td>
      <td>${esc(r.dest_reference)}<br><small class="hint-text">ID_PAYMENT_FORM ${esc(r.dest_id_payment_form) || "?"}</small></td>
      <td>${r.proposed_dest ? `${esc(r.proposed_dest)}${r.proposed_dest !== r.dest_reference ? ` <span class="sg-pill" data-tone="warn" title="Differs from the selected destination">differs</span>` : ""}` : `<span class="hint-text" title="RJ 2026-11-26: the query for the system's proposed account comes later">pending query</span>`}</td>
      <td>${esc(r.bill_number)}<br><small class="hint-text">ID_BILL ${esc(r.id_bill)} · billed ${esc(day(r.bill_create_date))}</small></td>
      <td style="text-align:right;">${money(r.total_amount)}<br><small class="hint-text">pending ${money(r.pending_amount)}</small></td>
      <td>${settlementPill(r)}${r.last_payment_date ? `<br><small class="hint-text">paid ${esc(day(r.last_payment_date))}</small>` : ""}</td>
      <td><span class="sg-pill" data-tone="${r.dt_action_status === "ACTSTA0002" ? "warn" : "neutral"}" title="${esc(r.dt_action_status)}">${esc(r.dt_action_status_en || "-")}</span><br><small class="hint-text">${esc(day(r.dt_action_date))}</small></td>
    </tr>`).join("")
      || `<tr><td colspan="9" class="hint-text">${S.rows.length ? "No row matches the filter." : "Every solved TNB RCCS has its debt transferred."}</td></tr>`;
    el("tnb1-check-all").checked = rows.length > 0 && rows.every((r) => S.sel.has(key(r)));
    el("tnb1-export-btn").disabled = !S.rows.length;
    el("tnb1-export-btn").textContent = S.sel.size ? `⬇ Export CSV (${S.sel.size} ticked)` : "⬇ Export CSV";
  }

  async function scan(inputs = "") {
    const btn = el("tnb1-detect-btn"), sbtn = el("tnb1-find-btn"); if (btn.disabled) return;
    btn.disabled = sbtn.disabled = true; el("tnb1-meta").textContent = inputs ? "Searching…" : "Scanning…";
    try {
      const d = await api("/api/tnb-dashboard/not-transferred/detect", { method: "POST", body: { inputs } });
      S.rows = d.rows || []; S.sel = new Set(); S.filter = null; S.searched = d.searched || [];
      sgStd.scanMeta(el("tnb1-meta"), { rows: d.count, elapsedMs: d.elapsed_ms, note: `${sgStd.scopeNote(S.searched)} · read only` });
      render(); window.tnbDashboard?.renderSummary?.();
    } catch (err) { el("tnb1-meta").textContent = `⚠ ${err.message}`; showToast(err.message, true); }
    finally { btn.disabled = sbtn.disabled = false; }
  }

  function exportCsv() {
    const list = S.sel.size ? S.rows.filter((r) => S.sel.has(key(r))) : visible();
    const cols = ["cod_ref_complaint", "rccs_type_en", "id_complaint", "rccs_date", "rccs_update_date", "src_reference", "src_id_payment_form",
      "dest_reference", "dest_id_payment_form", "proposed_dest", "id_bill", "bill_number", "billing_date", "bill_create_date", "total_amount",
      "pending_amount", "settlement", "payment_modes_en", "last_payment_date", "proposed_dest", "dt_action_status_en", "dt_action_date"];
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = [cols.map((c) => c.toUpperCase()).join(","), ...list.map((r) => cols.map((c) => q(r[c])).join(","))].join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `tnb_debt_not_transferred_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  el("tnb1-detect-btn").addEventListener("click", () => scan(""));
  sgStd.find(document.querySelector('[data-sg-find="tnb1"]'), { onSearch: (t) => scan(t) });   // RJ 2026-11-26 Search screen
  el("tnb1-export-btn").addEventListener("click", exportCsv);
  sgStd.bindFilter(el("tnb1-search"), el("tnb1-search-count"), render);
  el("tnb1-clear").addEventListener("click", () => { S.filter = null; el("tnb1-search").value = ""; el("tnb1-search-count").textContent = ""; render(); });
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

  // =====================================================================================================
  // Case 2 "Transferred to wrong account" (RJ 2026-11-26): "TNB billed and Transferred but not on the selected
  // account ... the account where it debt transferred is not correct selected destination account (the one in
  // RCCS ATT)". POST /api/tnb-dashboard/wrong-destination/detect - one row per TNB bill.
  // =====================================================================================================
  const W = { rows: [], sel: new Set(), filter: null };
  const wkey = (r) => String(r.id_bill);
  const WPRED = {
    other_rccs: (r) => r.kind === "other_rccs", unselected: (r) => r.kind === "unselected",
    after: (r) => !r.selected_after_transfer, selected: (r) => W.sel.has(wkey(r)),
  };
  function wVisible() {
    const terms = sgStd.terms(el("tnb5-search"));   // RJ 2026-11-26: paste-many filter
    return W.rows.filter((r) => (!W.filter || WPRED[W.filter](r))
      && sgStd.matches(terms, [r.src_reference, r.dest_reference, r.actual_references, r.cod_ref_complaint, r.actual_via_rccs, r.bill_number, r.id_bill]));
  }
  function wRender() {
    const amt = (l) => l.reduce((s, r) => s + (Number(r.transferred_amount) || 0), 0);
    sgStd.kpis(el("tnb5-kpis"), [
      { key: null, label: "Bills sent to the wrong account", value: W.rows.length, tone: W.rows.length ? "bad" : "ok",
        sub: `${fmt(new Set(W.rows.map((r) => r.cod_ref_complaint)).size)} RCCS · ${money(amt(W.rows))}` },
      { key: "after", label: "Sent after the destination was selected", value: W.rows.filter(WPRED.after).length, tone: "bad", sub: "clearest cases" },
      { key: "other_rccs", label: "Went to another RCCS's destination", value: W.rows.filter(WPRED.other_rccs).length, tone: "warn", sub: "same account, other TNB RCCS" },
      { key: "unselected", label: "Went to an account no RCCS selected", value: W.rows.filter(WPRED.unselected).length, tone: "warn" },
      { key: "selected", label: "Ticked", value: W.sel.size, tone: "neutral" },
    ], { active: W.filter, onPick: (k) => { W.filter = W.filter === k ? null : k; wRender(); } });
    const rows = wVisible();
    el("tnb5-shown").textContent = `${fmt(rows.length)} of ${fmt(W.rows.length)} rows shown`;
    el("tnb5-clear").hidden = !(W.filter || el("tnb5-search").value);
    el("tnb5-table").querySelector("tbody").innerHTML = rows.map((r) => `<tr style="box-shadow: inset 4px 0 0 ${r.selected_after_transfer ? "#f59e0b" : "#ef4444"};">
      <td><input type="checkbox" data-tnb5-id="${esc(wkey(r))}" ${W.sel.has(wkey(r)) ? "checked" : ""} /></td>
      <td><b>${esc(r.cod_ref_complaint)}</b> <span class="sg-pill" data-tone="${r.rccs_type === "5163COMTYP" ? "info" : "warn"}">${esc(r.rccs_type_en)}</span><br><small class="hint-text">solved ${esc(day(r.rccs_update_date))}</small></td>
      <td>${esc(r.src_reference)}<br><small class="hint-text">ID_PAYMENT_FORM ${esc(r.src_id_payment_form)}</small></td>
      <td>${esc(r.dest_reference)}<br><small class="hint-text">selected ${esc(day(r.dest_selected_at)) || "-"}</small></td>
      <td><b style="color:#ef4444;">${r.actual_references.map(esc).join(", ")}</b><br><small class="hint-text">${r.actual_via_rccs ? `= destination of ${esc(r.actual_via_rccs)}` : "not a destination of any TNB RCCS"}</small></td>
      <td>${esc(r.bill_number)}<br><small class="hint-text">${money(r.total_amount)} · pending ${money(r.pending_amount)}</small></td>
      <td>${esc(day(r.transfer_at))} <span class="sg-pill" data-tone="${r.selected_after_transfer ? "warn" : "bad"}">${r.selected_after_transfer ? "before selection" : "after selection"}</span><br>
        <small class="hint-text">${r.pair_list.map((p) => `${money(p.amount)}: ${esc(p.credit_bill)} → ${esc(p.to_bill)}`).join(" · ")}</small></td>
    </tr>`).join("") || `<tr><td colspan="7" class="hint-text">${W.rows.length ? "No row matches the filter." : "Every transfer went to the selected destination."}</td></tr>`;
    el("tnb5-check-all").checked = rows.length > 0 && rows.every((r) => W.sel.has(wkey(r)));
    el("tnb5-export-btn").disabled = !W.rows.length;
    el("tnb5-export-btn").textContent = W.sel.size ? `⬇ Export CSV (${W.sel.size} ticked)` : "⬇ Export CSV";
  }
  async function wScan(inputs = "") {
    const btn = el("tnb5-detect-btn"), sbtn = el("tnb5-find-btn"); if (btn.disabled) return;
    btn.disabled = sbtn.disabled = true; el("tnb5-meta").textContent = inputs ? "Searching…" : "Scanning…";
    try {
      const d = await api("/api/tnb-dashboard/wrong-destination/detect", { method: "POST", body: { inputs } });
      W.rows = d.rows || []; W.sel = new Set(); W.filter = null; W.searched = d.searched || [];
      sgStd.scanMeta(el("tnb5-meta"), { rows: d.count, elapsedMs: d.elapsed_ms, note: `${sgStd.scopeNote(W.searched)} · read only` });
      wRender(); window.tnbDashboard?.renderSummary?.();
    } catch (err) { el("tnb5-meta").textContent = `⚠ ${err.message}`; showToast(err.message, true); }
    finally { btn.disabled = sbtn.disabled = false; }
  }
  function wExport() {
    const list = W.sel.size ? W.rows.filter((r) => W.sel.has(wkey(r))) : wVisible();
    const head = ["RCCS", "TYPE", "SOURCE_REFERENCE", "SOURCE_ID_PAYMENT_FORM", "SELECTED_DESTINATION", "DESTINATION_SELECTED_AT", "ACTUALLY_TRANSFERRED_TO",
      "ACTUAL_IS_DESTINATION_OF_RCCS", "TNB_BILL_NUMBER", "ID_BILL", "TNB_AMOUNT", "TNB_PENDING", "TRANSFER_AT", "SELECTED_AFTER_TRANSFER", "TRANSFER_PAIRS"];
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [head.join(","), ...list.map((r) => [r.cod_ref_complaint, r.rccs_type_en, r.src_reference, r.src_id_payment_form, r.dest_reference, r.dest_selected_at,
      r.actual_references.join(" "), r.actual_via_rccs, r.bill_number, r.id_bill, r.total_amount, r.pending_amount, r.transfer_at, r.selected_after_transfer ? "Y" : "N",
      r.pair_list.map((p) => `${p.credit_bill}(-${p.amount})->${p.to_bill}@${p.to_reference}`).join(" ")].map(q).join(","))];
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\r\n")], { type: "text/csv" }));
    a.download = `tnb_transferred_to_wrong_account_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  el("tnb5-detect-btn").addEventListener("click", () => wScan(""));
  sgStd.find(document.querySelector('[data-sg-find="tnb5"]'), { onSearch: (t) => wScan(t) });   // RJ 2026-11-26 Search screen
  el("tnb5-export-btn").addEventListener("click", wExport);
  sgStd.bindFilter(el("tnb5-search"), el("tnb5-search-count"), wRender);
  el("tnb5-clear").addEventListener("click", () => { W.filter = null; el("tnb5-search").value = ""; el("tnb5-search-count").textContent = ""; wRender(); });
  el("tnb5-table").addEventListener("change", (e) => {
    const cb = e.target.closest("[data-tnb5-id]"); if (!cb) return;
    if (cb.checked) W.sel.add(cb.dataset.tnb5Id); else W.sel.delete(cb.dataset.tnb5Id);
    wRender();
  });
  el("tnb5-check-all").addEventListener("change", (e) => {
    wVisible().forEach((r) => { if (e.target.checked) W.sel.add(wkey(r)); else W.sel.delete(wkey(r)); });
    wRender();
  });
  wRender();

  // =====================================================================================================
  // Tab 2 "TNB on cancelled contract" (RJ 2026-11-25): "check the RCCS id payment form and id offered service,
  // ... show if there is an RCCS TNB where its linked to a cancelled contract. show the status of the TNB".
  // POST /api/tnb-dashboard/cancelled-contract/detect. Headline = RCCS whose ONLY contracts on that service are
  // Cancelled; "also active/terminated" = a cancelled contract next to another one (TNB usually on the other).
  // =====================================================================================================
  const C = { rows: [], sel: new Set(), filter: "only", status: "" };
  const ckey = (r) => String(r.id_complaint);
  const usage = (m) => { const x = String(m || "").match(/Total Usage\s*([\d.,]+)\s*(\S+)/i); return x ? `${Number(x[1].replace(/,/g, "")).toLocaleString()} ${x[2]}` : ""; };
  const CPRED = {
    only: (r) => r.only_cancelled, mixed: (r) => !r.only_cancelled,
    open: (r) => r.only_cancelled && r.status_group === "open", billed: (r) => r.tnb_on_cancelled,
    selected: (r) => C.sel.has(ckey(r)),
  };
  function cVisible() {
    const term = (el("tnb2-search").value || "").trim().toLowerCase();
    return C.rows.filter((r) => (!C.filter || CPRED[C.filter](r)) && (!C.status || r.cod_status_complaint === C.status) && (!term ||
      [r.cod_ref_complaint, r.reference, r.id_payment_form, r.tnb_bill_number, ...r.cancelled_list.map((c) => c.id)].some((v) => String(v ?? "").toLowerCase().includes(term))));
  }
  function statusPill(r) {
    const tone = r.status_group === "solved" ? "ok" : r.status_group === "closed" ? "neutral" : "warn";
    return `<span class="sg-pill" data-tone="${tone}" title="${esc(r.cod_status_complaint)}">${esc(r.status_en)}</span>`;
  }
  function cRender() {
    const only = C.rows.filter(CPRED.only);
    sgStd.kpis(el("tnb2-kpis"), [
      { key: "only", label: "Cancelled contract only", value: only.length, tone: only.length ? "bad" : "ok", sub: "every contract on that service is Cancelled" },
      { key: "open", label: "…of which still open", value: only.filter((r) => r.status_group === "open").length, tone: "warn", sub: "not Solved / Canceled / Rejected" },
      { key: "billed", label: "TNB billed on a cancelled contract", value: C.rows.filter(CPRED.billed).length, tone: "bad" },
      { key: "mixed", label: "Also has an Active / Terminated contract", value: C.rows.length - only.length, tone: "info", sub: "TNB usually on the other contract" },
      { key: "selected", label: "Ticked", value: C.sel.size, tone: "neutral" },
    ], { active: C.filter, onPick: (k) => { C.filter = C.filter === k ? null : k; cRender(); } });
    const stSel = el("tnb2-status");
    const counts = {}; C.rows.forEach((r) => { counts[r.cod_status_complaint] = counts[r.cod_status_complaint] || { n: 0, en: r.status_en }; counts[r.cod_status_complaint].n++; });
    stSel.innerHTML = `<option value="">All TNB statuses</option>` + Object.entries(counts).sort((a, b) => b[1].n - a[1].n)
      .map(([k, v]) => `<option value="${esc(k)}" ${C.status === k ? "selected" : ""}>${esc(v.en)} (${fmt(v.n)})</option>`).join("");
    const rows = cVisible();
    el("tnb2-shown").textContent = `${fmt(rows.length)} of ${fmt(C.rows.length)} rows shown`;
    el("tnb2-clear").hidden = !(C.filter || C.status || el("tnb2-search").value);
    el("tnb2-table").querySelector("tbody").innerHTML = rows.map((r) => `<tr style="box-shadow: inset 4px 0 0 ${r.only_cancelled ? "#ef4444" : "#94a3b8"};">
      <td><input type="checkbox" data-tnb2-id="${esc(ckey(r))}" ${C.sel.has(ckey(r)) ? "checked" : ""} /></td>
      <td><b>${esc(r.cod_ref_complaint)}</b> <span class="sg-pill" data-tone="${r.rccs_type === "5163COMTYP" ? "info" : "warn"}">${esc(r.rccs_type_en)}</span><br><small class="hint-text">opened ${esc(day(r.rccs_date))} · updated ${esc(day(r.rccs_update_date))}</small></td>
      <td>${statusPill(r)}${usage(r.motive) ? `<br><small class="hint-text" title="${esc(r.motive)}">TNB usage ${esc(usage(r.motive))}</small>` : ""}</td>
      <td>${esc(r.reference)}<br><small class="hint-text">ID_PAYMENT_FORM ${esc(r.id_payment_form)} · service ${esc(r.id_offered_service)}</small></td>
      <td>${r.cancelled_list.map((c) => `<div><span class="sg-pill" data-tone="bad">Cancelled</span> ${esc(c.id)}<br><small class="hint-text">from ${esc(c.from) || "-"} · cancelled ${esc(c.drop || c.updated) || "-"}${c.reason ? ` · ${esc(c.reason)}` : ""}</small></div>`).join("")}</td>
      <td>${r.other_list.length ? r.other_list.map((c) => `<div>${esc(c.id)} <small class="hint-text">${esc(c.status_en)}</small></div>`).join("") : `<span class="hint-text">none</span>`}</td>
      <td>${r.tnb_id_bill ? `${esc(r.tnb_bill_number)}<br><small class="hint-text">${money(r.tnb_amount)} · pending ${money(r.tnb_pending)} · on ${esc(r.tnb_bill_cs)} </small>${r.tnb_on_cancelled ? `<span class="sg-pill" data-tone="bad">cancelled contract</span>` : `<small class="hint-text">(${esc(r.tnb_bill_cs_status_en)})</small>`}` : `<span class="hint-text">not billed</span>`}</td>
    </tr>`).join("") || `<tr><td colspan="7" class="hint-text">${C.rows.length ? "No row matches the filter." : "No TNB RCCS is linked to a cancelled contract."}</td></tr>`;
    el("tnb2-check-all").checked = rows.length > 0 && rows.every((r) => C.sel.has(ckey(r)));
    el("tnb2-export-btn").disabled = !C.rows.length;
    el("tnb2-export-btn").textContent = C.sel.size ? `⬇ Export CSV (${C.sel.size} ticked)` : "⬇ Export CSV";
  }
  async function cScan() {
    const btn = el("tnb2-detect-btn"); if (btn.disabled) return;
    btn.disabled = true; el("tnb2-meta").textContent = "Scanning…";
    try {
      const d = await api("/api/tnb-dashboard/cancelled-contract/detect", { method: "POST" });
      C.rows = d.rows || []; C.sel = new Set(); C.filter = "only"; C.status = "";
      sgStd.scanMeta(el("tnb2-meta"), { rows: d.count, elapsedMs: d.elapsed_ms, note: "read only" });
      cRender(); window.tnbDashboard?.renderSummary?.();
    } catch (err) { el("tnb2-meta").textContent = `⚠ ${err.message}`; showToast(err.message, true); }
    finally { btn.disabled = false; }
  }
  function cExport() {
    const list = C.sel.size ? C.rows.filter((r) => C.sel.has(ckey(r))) : cVisible();
    const head = ["RCCS", "TYPE", "TNB_STATUS", "STATUS_CODE", "RCCS_DATE", "RCCS_UPDATE_DATE", "TNB_USAGE", "REFERENCE", "ID_PAYMENT_FORM",
      "ID_OFFERED_SERVICE", "CANCELLED_CONTRACTS", "OTHER_CONTRACTS", "ONLY_CANCELLED", "TNB_BILL_NUMBER", "TNB_AMOUNT", "TNB_PENDING", "TNB_BILL_CONTRACT", "TNB_BILL_CONTRACT_STATUS"];
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [head.join(","), ...list.map((r) => [r.cod_ref_complaint, r.rccs_type_en, r.status_en, r.cod_status_complaint, r.rccs_date, r.rccs_update_date,
      usage(r.motive), r.reference, r.id_payment_form, r.id_offered_service, r.cancelled_list.map((c) => c.id).join(" "),
      r.other_list.map((c) => `${c.id} ${c.status_en}`).join("; "), r.only_cancelled ? "Y" : "N", r.tnb_bill_number, r.tnb_amount, r.tnb_pending,
      r.tnb_bill_cs, r.tnb_bill_cs_status_en].map(q).join(","))];
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\r\n")], { type: "text/csv" }));
    a.download = `tnb_on_cancelled_contract_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  el("tnb2-detect-btn").addEventListener("click", cScan);
  el("tnb2-export-btn").addEventListener("click", cExport);
  el("tnb2-search").addEventListener("input", cRender);
  el("tnb2-status").addEventListener("change", (e) => { C.status = e.target.value; cRender(); });
  el("tnb2-clear").addEventListener("click", () => { C.filter = null; C.status = ""; el("tnb2-search").value = ""; cRender(); });
  el("tnb2-table").addEventListener("change", (e) => {
    const cb = e.target.closest("[data-tnb2-id]"); if (!cb) return;
    if (cb.checked) C.sel.add(cb.dataset.tnb2Id); else C.sel.delete(cb.dataset.tnb2Id);
    cRender();
  });
  el("tnb2-check-all").addEventListener("change", (e) => {
    cVisible().forEach((r) => { if (e.target.checked) C.sel.add(ckey(r)); else C.sel.delete(ckey(r)); });
    cRender();
  });
  cRender();

  // =====================================================================================================
  // Summary (RJ 2026-11-25): "add all tnb related menu as a case in TNB dashboard menu, make it look more
  // better and advance, add a main summary page first on the top. make better KPIS and show the data more
  // clean and straight to the point without loosing the information" + "once added, remove the old menus".
  // One "Scan all" runs the 4 cases' own scans in parallel (each case renders its own tab from that same
  // result - nothing is scanned twice), then the summary reads their data:
  //   Case 1 tnbDashboard.state.rows  (Debt not transferred)      Case 2 cState.rows (Cancelled contract)
  //   Case 3 tcdRows  (app.js, Cycle vs Disconnection)            Case 4 dtnbRows   (app.js, TNB disconnections)
  // =====================================================================================================
  const X = { running: false, done: false, at: null, ms: 0, status: {} };
  const CASES = [
    { id: "nottransferred", n: 1, btn: "tnb1-detect-btn", title: "Debt not transferred", sub: "Billed on the old account, never moved to the new one" },
    { id: "wrongdest", n: 2, btn: "tnb5-detect-btn", title: "Transferred to wrong account", sub: "Debt moved, but not to the RCCS's selected destination" },
    { id: "cancelled", n: 3, btn: "tnb2-detect-btn", title: "TNB on cancelled contract", sub: "Account's only contract for that service is cancelled" },
    { id: "cycledisc", n: 4, btn: "tcd-detect-btn", title: "Cycle vs Disconnection", sub: "Cycle + disconnection reading on the same date, one is TNB" },
    { id: "disctnb", n: 5, btn: "dtnb-detect-btn", title: "TNB disconnections", sub: "Disconnection reading in TNB with ready usage" },
  ];
  const num = (v) => Number(String(v ?? "").replace(/,/g, "")) || 0;
  const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);

  function waitIdle(btn, maxMs = 180000) {
    return new Promise((resolve) => {
      const t0 = Date.now();
      const tick = () => (!btn.disabled || Date.now() - t0 > maxMs) ? resolve() : setTimeout(tick, 250);
      setTimeout(tick, 300);
    });
  }
  async function runCase(c) {
    const btn = el(c.btn); if (!btn) return;
    X.status[c.id] = "running"; renderSummary();
    if (btn.disabled) await waitIdle(btn);   // a scan of that case already running - just wait for it
    else { btn.click(); await waitIdle(btn); }
    X.status[c.id] = "done"; renderSummary();
  }
  async function scanAll() {
    if (X.running) return;
    X.running = true; const t0 = Date.now();
    el("tnbx-scan-btn").disabled = true; el("tnbx-scan-btn").textContent = "Scanning…";
    el("tnbx-meta").textContent = `Scanning the ${CASES.length} cases…`;
    try { await Promise.all(CASES.map(runCase)); }
    finally {
      X.running = false; X.done = true; X.at = new Date(); X.ms = Date.now() - t0;
      el("tnbx-scan-btn").disabled = false; el("tnbx-scan-btn").textContent = "↻ Scan all";
      el("tnbx-meta").textContent = `Last scan ${X.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ${(X.ms / 1000).toFixed(1)} s · read only`;
      renderSummary();
    }
  }

  function data() {
    const r1 = S.rows, r2 = C.rows, r3 = typeof tcdRows !== "undefined" ? tcdRows : [], r4 = typeof dtnbRows !== "undefined" ? dtnbRows : [];   // app.js top-level lets
    const r5 = W.rows, w5after = r5.filter((r) => !r.selected_after_transfer);
    const pend = r1.filter((r) => r.settlement === "pending"), paid = r1.filter((r) => r.settlement === "paid_source");
    const only = r2.filter((r) => r.only_cancelled), onlyOpen = only.filter((r) => r.status_group === "open");
    const tnbC = r3.filter((r) => r.c_read_status_code === "8000STSRED"), tnbD = r3.filter((r) => r.d_read_status_code === "8000STSRED");
    const outC = r3.filter((r) => String(r.in_contract) !== "1");
    const after = r4.filter((r) => r.days_from_end !== "" && r.days_from_end != null && Number(r.days_from_end) > 0);
    const noCs = r4.filter((r) => !r.id_contracted_service);
    return { r1, r2, r3, r4, r5, w5after, pend, paid, only, onlyOpen, tnbC, tnbD, outC, after, noCs,
      amt1: sum(r1), amt5: r5.reduce((s, r) => s + (Number(r.transferred_amount) || 0), 0), amtPend: sum(pend), amtPaid: sum(paid),
      rccs1: new Set(r1.map((r) => r.cod_ref_complaint)).size,
      ready4: r4.reduce((s, r) => s + num(r.ready_usage), 0), ready3: r3.reduce((s, r) => s + num(r.c_ready_usage) + num(r.d_ready_usage), 0),
      sup3: new Set(r3.map((r) => r.id_sector_supply)).size, sup4: new Set(r4.map((r) => r.id_sector_supply)).size };
  }

  const bar = (parts) => {   // parts: [{v, tone, label}]
    const total = parts.reduce((s, p) => s + p.v, 0) || 1;
    return `<div class="tnbx-bar">${parts.filter((p) => p.v > 0).map((p) => `<span data-tone="${p.tone}" style="width:${(p.v / total) * 100}%" title="${esc(p.label)}: ${fmt(p.v)}"></span>`).join("")}</div>` +
      `<div class="tnbx-legend">${parts.map((p) => `<span><i data-tone="${p.tone}"></i>${esc(p.label)} <b>${fmt(p.v)}</b></span>`).join("")}</div>`;
  };
  const stat = (label, value, tone = "") => `<div class="tnbx-stat"${tone ? ` data-tone="${tone}"` : ""}><span>${esc(label)}</span><b>${value}</b></div>`;
  const loading = (id) => X.status[id] === "running";

  function caseCard(c, d) {
    const busy = loading(c.id);
    const n = { nottransferred: d.r1.length, wrongdest: d.r5.length, cancelled: d.only.length, cycledisc: d.r3.length, disctnb: d.r4.length }[c.id];
    const unit = { nottransferred: "bills", wrongdest: "bills", cancelled: "RCCS", cycledisc: "pairs", disctnb: "readings" }[c.id];
    let body = "";
    if (c.id === "nottransferred") body =
      stat("Amount not transferred", money(d.amt1), "warn") + stat("RCCS", fmt(d.rccs1)) + stat("Still pending on old account", `${fmt(d.pend.length)} · ${money(d.amtPend)}`, d.pend.length ? "warn" : "") +
      bar([{ v: d.pend.length, tone: "warn", label: "Pending on old account" }, { v: d.paid.length, tone: "info", label: "Paid on old account" }]);
    if (c.id === "wrongdest") body =
      stat("Amount sent elsewhere", money(d.amt5), d.r5.length ? "bad" : "") + stat("RCCS", fmt(new Set(d.r5.map((r) => r.cod_ref_complaint)).size)) +
      stat("Sent after destination selected", fmt(d.w5after.length), d.w5after.length ? "bad" : "") +
      bar([{ v: d.r5.filter((r) => r.kind === "other_rccs").length, tone: "warn", label: "Another RCCS's destination" }, { v: d.r5.filter((r) => r.kind === "unselected").length, tone: "bad", label: "Account no RCCS selected" }]);
    if (c.id === "cancelled") body =
      stat("Still open", fmt(d.onlyOpen.length), d.onlyOpen.length ? "warn" : "") + stat("Billed on cancelled contract", fmt(d.r2.filter((r) => r.tnb_on_cancelled).length), "bad") +
      stat("Also active / terminated contract", fmt(d.r2.length - d.only.length)) +
      bar([{ v: d.only.filter((r) => r.status_group === "open").length, tone: "warn", label: "Open" }, { v: d.only.filter((r) => r.status_group === "solved").length, tone: "ok", label: "Solved" },
        { v: d.only.filter((r) => r.status_group === "closed").length, tone: "neutral", label: "Canceled / rejected" }]);
    if (c.id === "cycledisc") body =
      stat("Sector supplies", fmt(d.sup3)) + stat("Outside any contract", fmt(d.outC.length), d.outC.length ? "warn" : "") +
      // RJ 2026-11-25: no usage total here - meter-rollover readings make the sum meaningless (it stays on the case tab)
      stat("Both readings TNB", fmt(d.r3.filter((r) => r.c_read_status_code === "8000STSRED" && r.d_read_status_code === "8000STSRED").length)) +
      bar([{ v: d.tnbC.length, tone: "info", label: "Cycle is TNB" }, { v: d.tnbD.length, tone: "warn", label: "Disconnection is TNB" }]);
    if (c.id === "disctnb") body =
      stat("Sector supplies", fmt(d.sup4)) + stat("Read after contract end", fmt(d.after.length), d.after.length ? "bad" : "") +
      stat("Estimated readings", fmt(d.r4.filter((r) => String(r.ind_estimate) === "1").length)) +
      bar([{ v: d.after.length, tone: "bad", label: "After contract end" }, { v: d.noCs.length, tone: "neutral", label: "No contract" },
        { v: Math.max(0, d.r4.length - d.after.length - d.noCs.length), tone: "info", label: "Within contract" }]);
    const tone = busy ? "neutral" : n ? "bad" : "ok";
    return `<article class="tnbx-case" data-tone="${tone}">
      <header><span class="tnbx-case-n">Case ${c.n}</span><span class="tnbx-chip" data-tone="${tone}">${busy ? "Scanning…" : n ? "Needs action" : "Clear"}</span></header>
      <h3>${esc(c.title)}</h3><p>${esc(c.sub)}</p>
      <div class="tnbx-case-big">${busy && !X.done ? "…" : fmt(n)} <small>${unit}</small></div>
      <div class="tnbx-case-body"${busy && !X.done ? " hidden" : ""}>${body}</div>
      <button type="button" class="btn btn-pill-sm tnbx-open" data-tnbx-open="${c.id}">Open case ${c.n} →</button>
    </article>`;
  }

  function priorities(d) {
    const out = [];
    d.r5.forEach((r) => out.push({ w: (r.selected_after_transfer ? 3e9 : 4e9) + num(r.transferred_amount), c: 2, id: "wrongdest", ref: r.cod_ref_complaint, acc: r.src_reference,
      what: money(r.transferred_amount), why: `Sent to ${r.actual_references.join(", ")} instead of ${r.dest_reference}${r.selected_after_transfer ? " (before it was selected)" : ""}`, tone: "bad" }));
    d.pend.forEach((r) => out.push({ w: 2e9 + num(r.total_amount), c: 1, id: "nottransferred", ref: r.cod_ref_complaint, acc: r.src_reference, what: money(r.total_amount),
      why: `Still pending on the old account, not moved to ${r.dest_reference}`, tone: "warn" }));
    d.onlyOpen.forEach((r) => { const u = String(r.motive || "").match(/Total Usage\s*([\d.,]+)\s*(\S+)/i);
      out.push({ w: 1e9 + (u ? num(u[1]) : 0), c: 3, id: "cancelled", ref: r.cod_ref_complaint, acc: r.reference, what: u ? `${fmt(num(u[1]))} ${u[2]}` : "-",
        why: `${r.status_en} - only contract is cancelled`, tone: "warn" }); });
    return out.sort((a, b) => b.w - a.w).slice(0, 10);
  }

  function renderSummary() {
    const root = el("tnbx-summary"); if (!root) return;
    const d = data();
    const busyAll = X.running && !X.done;
    const v = (x) => (busyAll ? "…" : x);
    // RJ 2026-11-26: a case showing a Search result feeds the Summary with that subset - say so
    const searched = [[1, S.searched], [2, W.searched]].filter(([, l]) => l && l.length).map(([n]) => `Case ${n}`);
    if (X.done && !X.running) el("tnbx-meta").textContent = searched.length
      ? `${searched.join(" & ")} show${searched.length === 1 ? "s" : ""} a search - ↻ Scan all to reload every case`
      : `Last scan ${X.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ${(X.ms / 1000).toFixed(1)} s · read only`;
    // headline KPIs - the four questions the team asks first
    el("tnbx-hero").innerHTML = [
      { label: "Money not transferred", value: v(money(d.amt1)), sub: `${fmt(d.r1.length)} bills · ${fmt(d.rccs1)} RCCS`, tone: d.r1.length ? "warn" : "ok", go: "nottransferred" },
      { label: "Transferred to the wrong account", value: v(fmt(d.r5.length)), sub: `${money(d.amt5)} · ${fmt(d.w5after.length)} after the destination was selected`, tone: d.r5.length ? "bad" : "ok", go: "wrongdest" },
      { label: "TNB stuck on cancelled contract", value: v(fmt(d.onlyOpen.length)), sub: `open of ${fmt(d.only.length)} cancelled-only RCCS`, tone: d.onlyOpen.length ? "warn" : "ok", go: "cancelled" },
      { label: "TNB readings to review", value: v(fmt(d.r3.length + d.r4.length)), sub: `${fmt(d.r3.length)} cycle/disc pairs · ${fmt(d.r4.length)} disconnections`, tone: d.r3.length + d.r4.length ? "info" : "ok", go: "cycledisc" },
    ].map((k) => `<button type="button" class="tnbx-kpi" data-tone="${k.tone}" data-tnbx-open="${k.go}"${k.f ? ` data-tnbx-filter="${k.f}"` : ""}>
        <span class="tnbx-kpi-label">${esc(k.label)}</span><span class="tnbx-kpi-value">${k.value}</span><span class="tnbx-kpi-sub">${esc(k.sub)}</span></button>`).join("");
    el("tnbx-cases").innerHTML = CASES.map((c) => caseCard(c, d)).join("");
    // Electricity vs Water (cases 1 and 2)
    const split = (list, f) => ({ e: list.filter((r) => r.rccs_type === "5164COMTYP").length, w: list.filter((r) => r.rccs_type === "5163COMTYP").length, f });
    const rowsEW = [["Debt not transferred (bills)", split(d.r1)], ["Transferred to wrong account", split(d.r5)], ["Cancelled contract only (RCCS)", split(d.only)], ["…of which open", split(d.onlyOpen)]];
    el("tnbx-split").innerHTML = `<table class="tnbx-mini"><thead><tr><th></th><th>⚡ Electricity</th><th>💧 Water</th></tr></thead><tbody>` +
      rowsEW.map(([l, s]) => `<tr><td>${esc(l)}</td><td>${busyAll ? "…" : fmt(s.e)}</td><td>${busyAll ? "…" : fmt(s.w)}</td></tr>`).join("") + `</tbody></table>`;
    const pr = priorities(d);
    el("tnbx-priority").innerHTML = pr.length ? `<table class="tnbx-mini tnbx-prio"><thead><tr><th>#</th><th>Case</th><th>RCCS</th><th>Account</th><th>Amount / usage</th><th>Why</th></tr></thead><tbody>` +
      pr.map((p, i) => `<tr><td>${i + 1}</td><td><span class="tnbx-chip" data-tone="${p.tone}">Case ${p.c}</span></td><td><a href="#" data-tnbx-find="${esc(p.id)}|${esc(p.ref)}">${esc(p.ref)}</a></td><td>${esc(p.acc)}</td><td style="text-align:right;">${esc(p.what)}</td><td>${esc(p.why)}</td></tr>`).join("") +
      `</tbody></table>` : `<div class="hint-text">${busyAll ? "Scanning…" : "Nothing to act on."}</div>`;
  }

  function openCase(id, filter, find) {
    const b = document.querySelector(`.da-subnav-btn[data-tnbd-sub="${id}"]`); if (b) b.click();
    if (id === "nottransferred" && filter) { S.filter = filter; render(); }
    if (find) { const inp = el({ nottransferred: "tnb1-search", wrongdest: "tnb5-search", cancelled: "tnb2-search" }[id]); if (inp) { if (id === "cancelled") C.filter = null; inp.value = find; inp.dispatchEvent(new Event("input")); } }
    document.getElementById("page-tnbdashboard")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  el("tnbx-summary").addEventListener("click", (e) => {
    const f = e.target.closest("[data-tnbx-find]");
    if (f) { e.preventDefault(); const [id, ref] = f.dataset.tnbxFind.split("|"); openCase(id, null, ref); return; }
    const o = e.target.closest("[data-tnbx-open]"); if (o) openCase(o.dataset.tnbxOpen, o.dataset.tnbxFilter || null);
  });
  el("tnbx-scan-btn").addEventListener("click", () => scanAll());
  // first time the page is shown -> scan all (replaces the per-tab auto-load; the page can be reached from the
  // menu, an Overview card or the search, so watch the page itself)
  const page = document.getElementById("page-tnbdashboard");
  new MutationObserver(() => { if (page.classList.contains("is-active") && !X.done && !X.running) scanAll(); })
    .observe(page, { attributes: true, attributeFilter: ["class"] });
  renderSummary();
  window.tnbDashboard = { scan, state: S, cScan, cState: C, wScan, wState: W, scanAll, renderSummary, summaryState: X };
})();
