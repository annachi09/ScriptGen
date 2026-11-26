/* =====================================================================
   ScriptGen - Rebilling Monitor tab 4 "Rebilling with anomaly in bill date (LACKS DATA)" (RJ 2026-11-26).
   Disputed bills (ESTFAC0010) whose rebilling line is Anomalous (EREALB0008) 'NOSUFDATFO - Lacks Data'; their
   readings via item to bill -> reading item to bill -> GCGT_RE_READING. Fix: LAST_BILLING_DATE := READING_PREV_DATE
   (earliest when more than one reading). POST /api/rebilling-monitor/lacks-data/detect | /generate
   (app/core/rebilling_lacks_data.py). UI standard: docs/UI_STANDARD.md.
   ===================================================================== */
(function () {
  const el = (id) => document.getElementById(id);
  if (!el("rld-detect-btn")) return;
  const esc = (s) => escapeHtml(String(s ?? ""));
  const fmt = (n) => Number(n || 0).toLocaleString();
  const day = (s) => String(s || "").slice(0, 10);
  const money = (n) => Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const S = { rows: [], sel: new Set(), filter: null, group: "", loaded: false, searched: [] };
  const key = (r) => String(r.id_bill);
  const READ_STATUS = { "1000STSRED": "Available", "4000STSRED": "Calculated usage", "6000STSRED": "Sent to bill", "7000STSRED": "Billed", "7001STSRED": "UAU - Billed", "8000STSRED": "Terminated not billed" };

  const PRED = {
    change: (r) => r.needs_change,
    ok: (r) => r.reading_count > 0 && !r.needs_change,
    none: (r) => r.reading_count === 0,
    big: (r) => r.needs_change && Math.abs(Number(r.diff_days)) > 1,
    oneday: (r) => r.needs_change && Math.abs(Number(r.diff_days)) === 1,
    anomalous: (r) => r.line_status === "EREALB0008",
    disputedline: (r) => r.line_status === "EREALB0001",
    many: (r) => r.reading_group === "many",
    selected: (r) => S.sel.has(key(r)),
  };

  // RJ 2026-11-26 (2): related RCCS + rebilling activity, with their statuses
  const NO_RCCS = "(no RCCS)";
  const rccsStatus = (r) => (r.rccs ? (r.rccs_status_en || r.rccs_status) : NO_RCCS);
  const rebStatus = (r) => r.reb_status_en || r.reb_activity_status || "?";
  const REB_TONE = { ESTREF0001: "warn", ESTREF0002: "info", ESTREF0003: "neutral", ESTREF0004: "ok" };
  const RCCS_TONE = { COMPLST008: "ok", COMPLST054: "ok", COMPLST121: "warn" };

  function fillSelect(id, values) {
    const sel = el(id), keep = sel.value, first = sel.options[0].outerHTML;
    sel.innerHTML = first + values.map(([v, n]) => `<option value="${esc(v)}">${esc(v)} (${fmt(n)})</option>`).join("");
    sel.value = values.some(([v]) => v === keep) ? keep : "";
  }
  function counts(fn) {
    const m = new Map(); S.rows.forEach((r) => m.set(fn(r), (m.get(fn(r)) || 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }

  function visible() {
    const terms = sgStd.terms(el("rld-search"));   // RJ 2026-11-26: paste-many filter
    const rs = el("rld-rccs-status").value, bs = el("rld-reb-status").value;
    return S.rows.filter((r) => (!S.group || r.reading_group === S.group) && (!S.filter || PRED[S.filter](r))
      && (!rs || rccsStatus(r) === rs) && (!bs || rebStatus(r) === bs)
      && sgStd.matches(terms, [r.account, r.rccs, r.id_rebilling_activity, r.id_bill, r.bill_number,
        r.reading_list.map((x) => x.id_reading), r.reading_list.map((x) => x.id_measuring_point)]));
  }

  // RJ 2026-11-26 (3): "focus on the billing date and last billing date are the same ... show it better" -
  // every row is a zero-day bill (LAST_BILLING_DATE = BILLING_DATE); the fix opens the period at READING_PREV_DATE.
  function nowCell(r) {
    return `<div class="rld-now"><span class="rld-date is-off">${esc(day(r.billing_date))}</span>
      <span class="sg-pill" data-tone="bad" title="LAST_BILLING_DATE = BILLING_DATE">0-day period</span>
      <small>last billing = billing date</small></div>`;
  }

  function readsCell(r) {
    if (!r.reading_list.length) return `<span class="sg-pill" data-tone="warn">No reading linked</span>`;
    const src = r.proposed_last_billing_date;
    let marked = false;
    return `<div class="rld-reads">${r.reading_list.map((x) => {
      const isSrc = !marked && x.reading_prev_date === src && (marked = true);
      return `<div class="rld-read${isSrc ? " is-src" : ""}" title="${isSrc ? "Its READING_PREV_DATE becomes the bill's LAST_BILLING_DATE" : ""}">
        <span class="rld-range">${esc(day(x.reading_prev_date))}<i>→</i>${esc(day(x.reading_date))}</span>
        <small>#${esc(x.id_reading)} · ${esc(READ_STATUS[x.read_status] || x.read_status)} · MP ${esc(x.id_measuring_point)}</small></div>`;
    }).join("")}</div>`;
  }

  function afterCell(r) {
    if (!r.reading_count) return `<span class="hint-text">No reading - nothing to propose</span>`;
    if (!r.needs_change) return `<span class="sg-pill" data-tone="ok">Already matches</span>`;
    const p = Number(r.period_days_after);
    const tone = Math.abs(Number(r.diff_days)) > 1 ? "warn" : "ok";
    return `<div class="rld-tl" title="LAST_BILLING_DATE ${esc(day(r.last_billing_date))} → ${esc(day(r.proposed_last_billing_date))}">
      <div class="rld-tl-ends"><b>${esc(day(r.proposed_last_billing_date))}</b><b>${esc(day(r.billing_date))}</b></div>
      <div class="rld-tl-bar"><i></i></div>
      <div class="rld-tl-foot"><span class="sg-pill" data-tone="${tone}">${fmt(p)}-day period</span>
        <small>last billing <s>${esc(day(r.last_billing_date))}</s> → <b>${esc(day(r.proposed_last_billing_date))}</b></small></div></div>`;
  }

  function statusCell(r) {
    const lineAnom = r.line_status === "EREALB0008";
    return `<div class="rld-st">
      <div><small>Line</small><span class="sg-pill" data-tone="${lineAnom ? "bad" : "warn"}" title="${esc(r.line_status)}${r.reason ? " · " + esc(r.reason) : ""}">${lineAnom ? "Anomalous · Lacks Data" : "Disputed · not run yet"}</span></div>
      <div><small>Rebilling</small><b>${esc(r.id_rebilling_activity)}</b>
        <span class="sg-pill" data-tone="${REB_TONE[r.reb_activity_status] || "neutral"}" title="${esc(r.reb_activity_status)} · ${esc(r.reb_type_en || r.reb_activity_type)}">${esc(rebStatus(r))}</span></div>
      <div><small>RCCS</small>${r.rccs ? `<b>${esc(r.rccs)}</b>
        <span class="sg-pill" data-tone="${RCCS_TONE[r.rccs_status] || "info"}" title="${esc(r.rccs_status)} · opened ${esc(day(r.rccs_created))}">${esc(r.rccs_status_en || r.rccs_status)}</span>`
        : `<span class="hint-text" title="The rebilling activity has no ID_RECLAMATION">none linked</span>`}</div></div>`;
  }

  function render() {
    const c = (p) => S.rows.filter(p).length;
    const cards = [
      { key: null, label: "Same-day bills", value: S.rows.length, tone: S.rows.length ? "bad" : "ok",
        sub: `${fmt(new Set(S.rows.map((r) => r.account)).size)} accounts · ${money(S.rows.reduce((s, r) => s + (Number(r.total_amount) || 0), 0))}` },
      // RJ 2026-11-26 (4): "include disputed in the checks" - line already failed vs not processed yet
      { key: "anomalous", label: "Line Anomalous · Lacks Data", value: c(PRED.anomalous), tone: "bad", sub: "rebilling already failed" },
      { key: "disputedline", label: "Line Disputed · not run yet", value: c(PRED.disputedline), tone: "warn", sub: "will fail on Lacks Data - fix first" },
      { key: "oneday", label: "Reading starts 1 day before", value: c(PRED.oneday), tone: "warn", sub: "typical case - safe to fix" },
      { key: "big", label: "Reading starts earlier", value: c(PRED.big), tone: "bad", sub: "more than 1 day - check first" },
      { key: "many", label: "More than 1 reading", value: c(PRED.many), tone: "info", sub: "earliest prev date used" },
    ];
    if (c(PRED.ok)) cards.push({ key: "ok", label: "Already matches", value: c(PRED.ok), tone: "ok", sub: "nothing to change" });
    if (c(PRED.none)) cards.push({ key: "none", label: "No reading linked", value: c(PRED.none), tone: "neutral", sub: "nothing to propose" });
    cards.push({ key: "selected", label: "Ticked", value: S.sel.size, tone: "neutral" });
    sgStd.kpis(el("rld-kpis"), cards, { active: S.filter, onPick: (k) => { S.filter = S.filter === k ? null : k; render(); } });

    el("rld-seg-all").textContent = fmt(S.rows.length);
    el("rld-seg-one").textContent = fmt(S.rows.filter((r) => r.reading_group === "one").length);
    el("rld-seg-many").textContent = fmt(S.rows.filter((r) => r.reading_group === "many").length);
    el("rld-seg").querySelectorAll("button").forEach((b) => b.classList.toggle("is-active", b.dataset.g === S.group));

    const rows = visible();
    el("rld-shown").textContent = `${fmt(rows.length)} of ${fmt(S.rows.length)} bills shown`;
    const fixable = rows.filter(PRED.change);
    el("rld-check-all").checked = fixable.length > 0 && fixable.every((r) => S.sel.has(key(r)));
    el("rld-table").querySelector("tbody").innerHTML = rows.map((r) => `<tr class="${S.sel.has(key(r)) ? "is-ticked" : ""}">
      <td><input type="checkbox" data-rld-id="${esc(key(r))}" ${S.sel.has(key(r)) ? "checked" : ""} ${r.needs_change ? "" : "disabled title=\"Nothing to change\""} /></td>
      <td><b>${esc(r.bill_number)}</b><div class="hint-text">account ${esc(r.account)} · ID ${esc(r.id_bill)}</div>
        <div class="hint-text">${esc(r.billing_period || r.id_billing_period)} · ${money(r.total_amount)} · <span class="sg-pill" data-tone="bad" title="ESTFAC0010">Disputed</span></div></td>
      <td>${nowCell(r)}</td>
      <td>${readsCell(r)}</td>
      <td>${afterCell(r)}</td>
      <td>${statusCell(r)}</td>
    </tr>`).join("") || `<tr><td colspan="6" class="hint-text">${S.loaded ? "No bill matches the current filters." : "Loading…"}</td></tr>`;
    el("rld-gen-btn").disabled = S.sel.size === 0;
    el("rld-gen-btn").textContent = S.sel.size ? `🛠 Generate script (${S.sel.size})` : "🛠 Generate script";
    el("rld-export-btn").disabled = !S.rows.length;
  }

  async function scan(inputs = "") {
    const btn = el("rld-detect-btn"), sbtn = el("rld-find-btn");
    btn.disabled = sbtn.disabled = true; el("rld-meta").textContent = inputs ? "Searching…" : "Scanning…";
    try {
      const d = await api("/api/rebilling-monitor/lacks-data/detect", { method: "POST", body: { inputs } });
      S.searched = d.searched || [];
      if (S.searched.length) { S.filter = null; S.group = ""; }   // a search shows every bill found
      S.rows = (d.rows || []).map((r) => ({ ...r, reading_count: Number(r.reading_count) || 0 }));
      S.loaded = true;
      const ok = new Set(S.rows.filter(PRED.change).map(key));
      [...S.sel].forEach((k) => { if (!ok.has(k)) S.sel.delete(k); });
      el("rld-toolbar").hidden = false;
      fillSelect("rld-rccs-status", counts(rccsStatus));
      fillSelect("rld-reb-status", counts(rebStatus));
      sgStd.scanMeta(el("rld-meta"), { rows: S.rows.length, elapsedMs: d.elapsed_ms,
        note: `${sgStd.scopeNote(S.searched)} · ${fmt(d.count)} same-day bill${d.count === 1 ? "" : "s"} · ${fmt(d.fixable)} to fix` });
      render();
    } catch (e) { el("rld-meta").textContent = ""; showToast(e.message, true); }
    finally { btn.disabled = sbtn.disabled = false; }
  }

  async function generate() {
    const btn = el("rld-gen-btn"); btn.disabled = true;
    try {
      const d = await api("/api/rebilling-monitor/lacks-data/generate", { method: "POST", body: {
        bill_ids: [...S.sel], user: el("rld-user").value.trim(), program: el("rld-program").value.trim(), clean: el("rld-clean").checked } });
      panel.show(d.sql, d.rollback_sql);
      el("rld-out-wrap").hidden = false;
      el("rld-out-info").textContent = `${d.bills} bill(s): ${d.one} with 1 reading, ${d.many} with more than 1. Saved in History.` +
        (d.skipped?.length ? ` Skipped ${d.skipped.length} that no longer need it (re-read live): ${d.skipped.join(", ")}.` : "");
    } catch (e) { showToast(e.message, true); }
    finally { btn.disabled = S.sel.size === 0; }
  }

  function exportCsv() {
    const list = S.sel.size ? S.rows.filter(PRED.selected) : visible();
    const head = ["ID_BILL", "BILL_NUMBER", "ACCOUNT", "BILLING_PERIOD", "TOTAL_AMOUNT", "LAST_BILLING_DATE", "BILLING_DATE",
      "READING_COUNT", "ID_READING", "READING_PREV_DATE", "READING_DATE", "READ_STATUS", "ID_MEASURING_POINT",
      "PROPOSED_LAST_BILLING_DATE", "DIFF_DAYS", "ID_REBILLING_ACTIVITY", "REB_STATUS", "REB_TYPE", "RCCS", "RCCS_STATUS"];
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [head.join(",")];
    list.forEach((r) => (r.reading_list.length ? r.reading_list : [{}]).forEach((x) => lines.push([r.id_bill, r.bill_number, r.account,
      r.billing_period, r.total_amount, day(r.last_billing_date), day(r.billing_date), r.reading_count, x.id_reading, day(x.reading_prev_date),
      day(x.reading_date), x.read_status, x.id_measuring_point, day(r.proposed_last_billing_date), r.diff_days, r.id_rebilling_activity, rebStatus(r), r.reb_type_en, r.rccs, r.rccs ? rccsStatus(r) : ""].map(q).join(","))));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\r\n")], { type: "text/csv" }));
    a.download = `rebilling_lacks_data_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
  }

  const panel = sgStd.scriptPanel({ out: "rld-out", rollback: "rld-rb-out", copy: "rld-copy-btn", download: "rld-download-btn",
    rbCopy: "rld-rb-copy-btn", rbDownload: "rld-rb-download-btn", name: () => `REB_LACKS_DATA_BILL_DATE_${new Date().toISOString().slice(0, 10)}` });

  el("rld-table").addEventListener("change", (e) => {
    if (e.target.id === "rld-check-all") {
      visible().filter(PRED.change).forEach((r) => (e.target.checked ? S.sel.add(key(r)) : S.sel.delete(key(r))));
    } else {
      const cb = e.target.closest("[data-rld-id]"); if (!cb) return;
      cb.checked ? S.sel.add(cb.dataset.rldId) : S.sel.delete(cb.dataset.rldId);
    }
    render();
  });
  el("rld-seg").addEventListener("click", (e) => { const b = e.target.closest("button[data-g]"); if (!b) return; S.group = b.dataset.g; render(); });
  el("rld-detect-btn").addEventListener("click", () => scan(""));
  // RJ 2026-11-26: Search screen (Search | Detect all)
  sgStd.find(document.querySelector('[data-sg-find="rld"]'), { onSearch: (t) => scan(t) });
  el("rld-gen-btn").addEventListener("click", generate);
  el("rld-export-btn").addEventListener("click", exportCsv);
  sgStd.bindFilter(el("rld-search"), el("rld-search-count"), render);
  el("rld-rccs-status").addEventListener("change", render);
  el("rld-reb-status").addEventListener("change", render);
  el("rld-clear").addEventListener("click", () => { el("rld-search").value = ""; el("rld-search-count").textContent = ""; el("rld-rccs-status").value = ""; el("rld-reb-status").value = ""; S.group = ""; S.filter = null; render(); });
  document.querySelector('.da-subnav-btn[data-rbm-sub="lacksdata"]')?.addEventListener("click", () => { if (!S.loaded) scan(""); });

  window.rbmLacksData = { scan, state: S };
})();
