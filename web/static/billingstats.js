/* =====================================================================
   ScriptGen - Billing Statistics dashboard (RJ, 2026-11-04).
   "create a new Dashboard, Billing Statistics. Focused on accounts. counting how many by Billing
   Status, add filter and KPI. lets have November 2026 (use the current date to determine which
   billing period is displayed), also have a quick card for Subsidized, normal customer, and ones
   enrolled in fixed billing using table ewa_gccom_fixed_billing. Will be good to have also some
   category per tariff and per signal in GCCOM_PAYMENT_FORM. By default lets focus on Service
   bills, but there will be filter of other bill type." + "make very good dashboard good color modern".

   Data: POST /api/billing-stats/detect {billing_period, bill_types} -> rows = one per combination
   of account attributes (status set, tariff set, signal, subsidized, fixed billing) with DISTINCT
   accounts (see app/core/billing_stats.py). Every count below is a sum of those rows, so an
   account is counted once in totals; per-status / per-tariff bars count the accounts HAVING that
   status / tariff (an account with two statuses appears in both bars).
   Loaded after app.js (uses $, $$, escapeHtml, api, showToast).
   ===================================================================== */
(function () {
  const esc = (s) => escapeHtml(String(s ?? ""));
  const num = (v) => { const n = Number(String(v ?? "").replace(/,/g, "")); return Number.isFinite(n) ? n : 0; };
  const fmt = (n) => Number(n || 0).toLocaleString();
  const money = (n) => Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const pct = (a, b) => (b ? (100 * a / b) : 0);
  const pctTxt = (a, b) => `${pct(a, b).toFixed(b && a / b < 0.01 && a ? 2 : 1)}%`;
  const PALETTE = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#f97316", "#84cc16", "#06b6d4", "#e11d48", "#64748b"];
  // fixed colours for the usual bill statuses (live OUC_ADMIN.GCCOM_BILL_STATUS codes)
  const STATUS_COLORS = {
    ESTFAC0005: "#10b981",   // Invoiced
    ESTFAC0012: "#3b82f6",   // Invoicing
    ESTFAC0008: "#6366f1",   // Generated
    ESTFAC0007: "#ef4444",   // Cancelled
    ESTFAC0015: "#f59e0b",   // Waiting for other Services
    ESTFAC0016: "#fb923c",   // Waiting For Final Bill
    ESTFAC0042: "#8b5cf6",   // Rebilled
    ESTFAC0031: "#f97316",   // Pending Approval
    ESTFAC8801: "#14b8a6",   // Paid
    ESTFAC0010: "#e11d48",   // Disputed
  };
  // RJ 2026-11-04 (later): "i want the status filter at the top, and by default filtering Invoiced only"
  const DEFAULT_STATUS = "ESTFAC0005";   // Invoiced
  // RJ 2026-11-04: quick-view tabs (customer segment x fixed billing)
  const PRESETS = [
    { id: "all", label: "All accounts", ico: "👥", seg: "all", fixed: "all", c: "#4f46e5" },
    { id: "fixed", label: "Fixed billing", ico: "📌", seg: "all", fixed: "yes", c: "#f97316" },
    { id: "nofixed", label: "Not on fixed billing", ico: "➖", seg: "all", fixed: "no", c: "#64748b" },
    { id: "sub", label: "Subsidized", ico: "🇧🇭", seg: "sub", fixed: "all", c: "#10b981" },
    { id: "subfixed", label: "Subsidized + fixed", ico: "🟢", seg: "sub", fixed: "yes", c: "#059669" },
    { id: "subnofix", label: "Subsidized, not fixed", ico: "🟩", seg: "sub", fixed: "no", c: "#34d399" },
    { id: "normal", label: "Normal", ico: "👤", seg: "normal", fixed: "all", c: "#6366f1" },
    { id: "normalfixed", label: "Normal + fixed", ico: "🔷", seg: "normal", fixed: "yes", c: "#0ea5e9" },
  ];
  const defaultFilters = () => ({ statuses: new Set([DEFAULT_STATUS]), tariff: "", signal: "", seg: "all", fixed: "all" });
  const S = { shown: false, loading: false, data: null, rows: [], period: null, billTypes: ["TFGEN00001"], f: defaultFilters() };
  const root = () => $("#bst-root");
  const stName = (c) => (S.data && S.data.statuses[c]) || c;
  const btName = (c) => (S.data && S.data.bill_types[c]) || c;
  const stColor = (c, i) => STATUS_COLORS[c] || PALETTE[i % PALETTE.length];
  const split = (s) => String(s || "").split("|").filter(Boolean);

  function prep(rows) {
    return rows.map((r) => ({
      st: split(r.statuses), tf: split(r.tariffs), sig: String(r.signal || "(none)"),
      sub: num(r.subsidized) === 1, fix: num(r.fixed_billing) === 1,
      acc: num(r.accounts), bills: num(r.bills), amt: num(r.amount), pend: num(r.pending),
    }));
  }
  function match(r, skip) {
    const f = S.f;
    if (skip !== "status" && f.statuses.size && !r.st.some((s) => f.statuses.has(s))) return false;
    if (skip !== "tariff" && f.tariff && !r.tf.includes(f.tariff)) return false;
    if (skip !== "signal" && f.signal && r.sig !== f.signal) return false;
    if (skip !== "seg" && f.seg === "sub" && !r.sub) return false;
    if (skip !== "seg" && f.seg === "normal" && r.sub) return false;
    if (skip !== "fixed" && f.fixed === "yes" && !r.fix) return false;
    if (skip !== "fixed" && f.fixed === "no" && r.fix) return false;
    return true;
  }
  const sum = (rows, k) => rows.reduce((s, r) => s + r[k], 0);
  function countBy(rows, keysOf) {
    const m = new Map();
    rows.forEach((r) => keysOf(r).forEach((k) => m.set(k, (m.get(k) || 0) + r.acc)));
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }

  async function load(force) {
    if (S.loading) return;
    S.loading = true;
    if (!S.data || force) root().innerHTML = `<div class="bst-loading">Counting accounts by bill status…</div>`;
    else { const u = $("#bst-updated"); if (u) u.textContent = "Loading…"; root().style.opacity = ".55"; }
    try {
      // RJ 2026-11-04 (speed): statuses filtered server-side ([] = all); force = skip the 10-min cache
      const data = await api("/api/billing-stats/detect", { method: "POST", body: { billing_period: S.period, bill_types: S.billTypes, statuses: [...S.f.statuses], force: !!force } });
      S.data = data; S.rows = prep(data.rows || []); S.period = data.billing_period;
      $("#bst-period-badge").innerHTML = `${esc(data.billing_period_name)}<small>${data.current_period && data.current_period.id === data.billing_period ? "current period (today's date)" : "selected period"} · ${esc(data.billing_period)}</small>`;
      $("#bst-updated").textContent = `Updated ${data.generated_at} · ${data.seconds}s`;
      render();
    } catch (err) {
      root().innerHTML = `<div class="bst-empty">⚠ ${esc(err.message)}</div>`;
      showToast(err.message, true);
    } finally {
      S.loading = false; root().style.opacity = "";
    }
  }

  function barsHtml(entries, total, attr, active, colorOf, nameOf, limit = 14) {
    const max = Math.max(1, ...entries.map((e) => e[1]));
    return `<div class="bst-bars">${entries.slice(0, limit).map(([k, v], i) => {
      const on = active instanceof Set ? active.has(k) : active === k;
      const dim = (active instanceof Set ? active.size : active) && !on;
      return `<div class="bst-bar${on ? " is-on" : ""}${dim ? " is-dim" : ""}" style="--c:${colorOf(k, i)}" ${attr}="${esc(k)}" title="${esc(nameOf(k))}: ${fmt(v)} account(s) - click to filter">` +
        `<span class="bst-bar-name"><span class="bst-dot"></span>${esc(nameOf(k))}</span>` +
        `<span class="bst-bar-track"><span style="width:${(100 * v / max).toFixed(1)}%"></span></span>` +
        `<span class="bst-bar-val">${fmt(v)}<small>${pctTxt(v, total)}</small></span></div>`;
    }).join("")}${entries.length > limit ? `<div class="bst-note" style="margin:6px 0 0;">+ ${entries.length - limit} more (use the filter above)</div>` : ""}</div>`;
  }
  function donutHtml(entries, total, colorOf, nameOf) {
    const R = 62, C = 2 * Math.PI * R; let off = 0;
    const top = entries.slice(0, 7); const rest = entries.slice(7).reduce((s, e) => s + e[1], 0);
    const segs = top.concat(rest ? [["__other", rest]] : []);
    const arcs = segs.map(([k, v], i) => {
      const len = total ? C * v / total : 0;
      const a = `<circle r="${R}" cx="80" cy="80" fill="none" stroke="${k === "__other" ? "#94a3b8" : colorOf(k, i)}" stroke-width="22" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 80 80)"><title>${esc(k === "__other" ? "Other" : nameOf(k))}: ${fmt(v)}</title></circle>`;
      off += len; return a;
    }).join("");
    return `<div class="bst-donut-wrap"><svg width="160" height="160" viewBox="0 0 160 160" role="img" aria-label="Accounts by status">${arcs}` +
      `<text x="80" y="80" text-anchor="middle" class="bst-donut-center">${fmt(total)}</text><text x="80" y="98" text-anchor="middle" class="bst-donut-cap">accounts</text></svg>` +
      `<div class="bst-legend">${segs.map(([k, v], i) => `<div><span style="display:flex;gap:8px;align-items:center;"><span class="bst-dot" style="--c:${k === "__other" ? "#94a3b8" : colorOf(k, i)}"></span>${esc(k === "__other" ? "Other statuses" : nameOf(k))}</span><b>${pctTxt(v, total)}</b></div>`).join("")}</div></div>`;
  }

  function render() {
    if (!S.data) return;
    const d = S.data, f = S.f;
    const rows = S.rows.filter((r) => match(r));
    const acc = sum(rows, "acc"), bills = sum(rows, "bills"), amt = sum(rows, "amt"), pend = sum(rows, "pend");
    // segment cards ignore their own filter so all 3 stay visible
    const segBase = S.rows.filter((r) => match(r, "seg") && match(r, "fixed"));
    const segTot = sum(segBase, "acc");
    const subRows = segBase.filter((r) => r.sub), norRows = segBase.filter((r) => !r.sub), fixRows = segBase.filter((r) => r.fix);
    const statusMini = (rs) => countBy(rs, (r) => r.st).slice(0, 3).map(([k, v]) => `<span>${esc(stName(k))} ${fmt(v)}</span>`).join("");
    const segCard = (key, ico, label, rs, grad, sh, on) => {
      const n = sum(rs, "acc");
      return `<button type="button" class="bst-seg${on ? " is-on" : ""}" style="--g:${grad};--sh:${sh}" data-bst-seg="${key}">` +
        `<div class="bst-seg-top"><span>${label}</span><span class="bst-seg-ico">${ico}</span></div>` +
        `<div class="bst-seg-n">${fmt(n)}</div><div class="bst-seg-sub">${pctTxt(n, segTot)} of accounts · ${fmt(sum(rs, "bills"))} bills · ${money(sum(rs, "amt"))}</div>` +
        `<div class="bst-seg-bar"><span style="width:${pct(n, segTot).toFixed(1)}%"></span></div><div class="bst-seg-mini">${statusMini(rs)}</div></button>`;
    };
    const stEntries = countBy(S.rows.filter((r) => match(r, "status")), (r) => r.st);
    const stTotal = sum(S.rows.filter((r) => match(r, "status")), "acc");
    const tfEntries = countBy(S.rows.filter((r) => match(r, "tariff")), (r) => r.tf);
    const sgEntries = countBy(S.rows.filter((r) => match(r, "signal")), (r) => [r.sig]);
    const tfTot = sum(S.rows.filter((r) => match(r, "tariff")), "acc"), sgTot = sum(S.rows.filter((r) => match(r, "signal")), "acc");
    const allTariffs = countBy(S.rows, (r) => r.tf).map((e) => e[0]).sort();
    const allSignals = countBy(S.rows, (r) => [r.sig]).map((e) => e[0]).sort();
    const btCounts = (d.bill_type_counts || []).map((r) => [String(r.bill_type), num(r.bills), num(r.bills)]);
    // RJ 2026-11-04: status chips = every status of the period (bills, selected bill types) - the
    // rows only hold the selected statuses now (filtered in SQL)
    const stChips = (d.status_counts || []).map((r) => [String(r.status), num(r.bills)]);
    // RJ 2026-11-04: "easy filtering (maybe pre defined tab, like fix billing, normal, etc...)"
    const presetBase = S.rows.filter((r) => match(r, "seg") && match(r, "fixed"));
    const presetN = (p) => sum(presetBase.filter((r) => (p.seg === "all" || (p.seg === "sub" ? r.sub : !r.sub)) && (p.fixed === "all" || (p.fixed === "yes" ? r.fix : !r.fix))), "acc");
    const presetsHtml = PRESETS.map((p) => `<button type="button" class="bst-preset${f.seg === p.seg && f.fixed === p.fixed ? " is-on" : ""}" data-bst-preset="${p.id}" style="--c:${p.c}"><span>${p.ico} ${esc(p.label)}</span><b>${fmt(presetN(p))}</b></button>`).join("");
    const btSel = new Set(S.billTypes);
    const allBt = btSel.has("ALL");
    const stOrder = (d.statuses && Object.keys(d.statuses)) || [];
    const stIdx = (c) => { const i = stEntries.findIndex((e) => e[0] === c); return i < 0 ? stOrder.indexOf(c) : i; };
    const colorSt = (c) => stColor(c, stIdx(c));
    // matrix: status x segment
    const mRows = stEntries.map(([c]) => {
      const rs = rows.filter((r) => r.st.includes(c));
      return [c, sum(rs.filter((r) => r.sub), "acc"), sum(rs.filter((r) => !r.sub), "acc"), sum(rs.filter((r) => r.fix), "acc"), sum(rs, "acc"), sum(rs, "bills"), sum(rs, "amt"), sum(rs, "pend")];
    }).filter((m) => m[4] > 0);
    const mMax = Math.max(1, ...mRows.map((m) => m[4]));
    const isDefaultSt = f.statuses.size === 1 && f.statuses.has(DEFAULT_STATUS);
    const filtersOn = !isDefaultSt || f.tariff || f.signal || f.seg !== "all" || f.fixed !== "all";

    root().innerHTML = `
      <div class="bst-filters">
        <div class="bst-presets" role="tablist" aria-label="Quick views">
          <label class="bst-f bst-period-pick">📅 Billing period<select id="bst-period">${(d.periods || []).map((p) => `<option value="${p.id}"${p.id === d.billing_period ? " selected" : ""}>${esc(p.name)}${d.current_period && p.id === d.current_period.id ? " (current)" : ""}</option>`).join("")}</select></label>
          ${presetsHtml}</div>
        <div class="bst-status-row">
          <span class="bst-kpi-label" style="margin:0;">Bill status</span>
          <div class="bst-chips">${stChips.map(([c, v]) => `<button type="button" class="bst-chip${f.statuses.has(c) ? " is-on" : ""}" data-bst-st="${esc(c)}" title="${fmt(v)} bill(s) in the period" style="${f.statuses.has(c) ? "" : `border-left:4px solid ${colorSt(c)};`}">${esc(stName(c))} <small>${fmt(v)}</small></button>`).join("")}
            <button type="button" class="bst-chip${f.statuses.size ? "" : " is-on"}" data-bst-st-all="1" title="Every status (slower - reads every bill of the period)">All statuses</button></div>
        </div>
        <label class="bst-f">Customer<select id="bst-seg"><option value="all">All customers</option><option value="sub"${f.seg === "sub" ? " selected" : ""}>Subsidized</option><option value="normal"${f.seg === "normal" ? " selected" : ""}>Normal</option></select></label>
        <label class="bst-f">Fixed billing<select id="bst-fixed"><option value="all">All</option><option value="yes"${f.fixed === "yes" ? " selected" : ""}>Enrolled</option><option value="no"${f.fixed === "no" ? " selected" : ""}>Not enrolled</option></select></label>
        <label class="bst-f">Tariff<select id="bst-tariff"><option value="">All tariffs</option>${allTariffs.map((t) => `<option${f.tariff === t ? " selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
        <label class="bst-f">Signal<select id="bst-signal"><option value="">All signals</option>${allSignals.map((t) => `<option${f.signal === t ? " selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
        <div class="bst-active">${filtersOn ? `<button type="button" class="bst-btn bst-btn-ghost" id="bst-clear">✖ Clear filters</button>` : ""}<button type="button" class="bst-btn bst-btn-ghost" id="bst-export" title="Grouped combinations; the filtered accounts are downloaded from the Accounts table at the bottom">⬇ Export summary CSV</button></div>
        <div style="flex-basis:100%;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
          <span class="bst-kpi-label" style="margin:0;">Bill type</span>
          <div class="bst-chips">${btCounts.map(([c, a, b]) => `<button type="button" class="bst-chip${allBt || btSel.has(c) ? " is-on" : ""}" data-bst-bt="${esc(c)}" title="${fmt(b)} bill(s) in the period">${esc(btName(c))} <small>${fmt(a)}</small></button>`).join("")}
            <button type="button" class="bst-chip${allBt ? " is-on" : ""}" data-bst-bt="ALL">All types</button></div>
        </div>
      </div>

      <div class="bst-kpis">
        <div class="bst-kpi" style="--k:#4f46e5"><div class="bst-kpi-label"><span class="bst-kpi-ico">👥</span>Accounts</div><div class="bst-kpi-value">${fmt(acc)}</div><div class="bst-kpi-sub">with a ${allBt ? "" : esc([...btSel].map(btName).join(" / ")) + " "}bill in ${esc(d.billing_period_name)}</div></div>
        <div class="bst-kpi" style="--k:#0ea5e9"><div class="bst-kpi-label"><span class="bst-kpi-ico">🧾</span>Bills</div><div class="bst-kpi-value">${fmt(bills)}</div><div class="bst-kpi-sub">${acc ? (bills / acc).toFixed(2) : 0} bills per account</div></div>
        <div class="bst-kpi" style="--k:#10b981"><div class="bst-kpi-label"><span class="bst-kpi-ico">💰</span>Billed amount</div><div class="bst-kpi-value">${money(amt)}</div><div class="bst-kpi-sub">avg ${money(acc ? amt / acc : 0)} per account</div></div>
        <div class="bst-kpi" style="--k:#f59e0b"><div class="bst-kpi-label"><span class="bst-kpi-ico">⏳</span>Pending amount</div><div class="bst-kpi-value">${money(pend)}</div><div class="bst-kpi-sub">${pctTxt(pend, amt)} of the billed amount</div></div>
        <div class="bst-kpi" style="--k:#ec4899"><div class="bst-kpi-label"><span class="bst-kpi-ico">🏷</span>Statuses</div><div class="bst-kpi-value">${fmt(stEntries.length)}</div><div class="bst-kpi-sub">top: ${esc(stEntries[0] ? stName(stEntries[0][0]) : "-")}</div></div>
      </div>

      <div class="bst-segs">
        ${segCard("sub", "🇧🇭", "Subsidized", subRows, "linear-gradient(135deg,#059669,#10b981 55%,#34d399)", "rgba(16,185,129,.55)", f.seg === "sub")}
        ${segCard("normal", "👤", "Normal customers", norRows, "linear-gradient(135deg,#4338ca,#6366f1 55%,#818cf8)", "rgba(99,102,241,.55)", f.seg === "normal")}
        ${segCard("fixed", "📌", "Fixed billing enrolled", fixRows, "linear-gradient(135deg,#c2410c,#f97316 55%,#fbbf24)", "rgba(249,115,22,.55)", f.fixed === "yes")}
      </div>
      <p class="bst-note">Subsidized = active service on a Bahraini First Account tariff (E-D2 / W-D2 / E-D2-NM). Fixed billing = OUC_ADMIN.EWA_GCCOM_FIXED_BILLING with IND_ACTIVE = 1 (can overlap the other two). Click a card to filter.</p>

      <div class="bst-grid">
        <section class="card bst-panel"><h3>Accounts by bill status</h3><div class="bst-panel-sub">Distinct accounts having at least one bill in that status · click to filter (several allowed)</div>
          ${stEntries.length ? barsHtml(stEntries, stTotal, "data-bst-st", f.statuses, colorSt, stName, 20) : `<div class="bst-empty">No bills for these filters.</div>`}</section>
        <section class="card bst-panel"><h3>Status mix</h3><div class="bst-panel-sub">Share of accounts per status</div>${donutHtml(stEntries, stTotal, colorSt, stName)}</section>
      </div>

      <div class="bst-grid-2">
        <section class="card bst-panel"><h3>By tariff</h3><div class="bst-panel-sub">Accounts per tariff description (GCCOM_FARE.NAME_TYPE of the billed services)</div>
          ${barsHtml(tfEntries, tfTot, "data-bst-tf", f.tariff, (k, i) => PALETTE[(i + 1) % PALETTE.length], (k) => k, 15)}</section>
        <section class="card bst-panel"><h3>By signal</h3><div class="bst-panel-sub">Accounts per signal (GCCOM_PAYMENT_FORM_TYPE description)</div>
          ${barsHtml(sgEntries, sgTot, "data-bst-sg", f.signal, (k, i) => PALETTE[(i + 4) % PALETTE.length], (k) => k, 15)}</section>
      </div>

      <section class="card bst-panel"><h3>Status × customer segment</h3><div class="bst-panel-sub">Accounts per status and segment for the current filters (heat = share of the largest status)</div>
        <div class="grid-wrap"><table class="bst-matrix" id="bst-matrix"><thead><tr><th>Bill status</th><th>Subsidized</th><th>Normal</th><th>Fixed billing</th><th>Accounts</th><th>Bills</th><th>Billed amount</th><th>Pending amount</th></tr></thead>
        <tbody>${mRows.map(([c, s, n, x, t, b, a, p]) => `<tr data-bst-st="${esc(c)}" style="cursor:pointer;--c:${colorSt(c)}"><td><span style="display:inline-flex;gap:8px;align-items:center;"><span class="bst-dot"></span>${esc(stName(c))} <small class="hint-text">${esc(c)}</small></span></td>` +
          `<td>${fmt(s)}</td><td>${fmt(n)}</td><td>${fmt(x)}</td><td class="bst-heat" style="--h:${Math.round(40 * t / mMax)}"><b>${fmt(t)}</b></td><td>${fmt(b)}</td><td>${money(a)}</td><td>${money(p)}</td></tr>`).join("") || `<tr><td colspan="8" class="hint-text">No data.</td></tr>`}</tbody>
        <tfoot><tr><td>All (distinct accounts)</td><td>${fmt(sum(rows.filter((r) => r.sub), "acc"))}</td><td>${fmt(sum(rows.filter((r) => !r.sub), "acc"))}</td><td>${fmt(sum(rows.filter((r) => r.fix), "acc"))}</td><td>${fmt(acc)}</td><td>${fmt(bills)}</td><td>${money(amt)}</td><td>${money(pend)}</td></tr></tfoot></table></div>
      </section>

      <section class="card bst-panel" id="bst-acc-card" style="margin-top:16px;"><h3>Accounts</h3>
        <div class="bst-panel-sub">The accounts behind the current filters (period, bill type, status, quick view, tariff, signal) - the download has only these accounts</div>
        <div class="bst-acc-bar"><input type="search" id="bst-acc-search" placeholder="Search account…" value="${esc(A.search)}">
          <span id="bst-acc-count" class="hint-text"></span>
          <button type="button" class="bst-btn bst-btn-ghost" id="bst-acc-csv">⬇ Download filtered accounts</button></div>
        <div id="bst-acc-body"><div class="bst-loading">Listing accounts…</div></div>
      </section>`;
    loadAccounts();
  }

  // ---- RJ 2026-11-04: "add a last table to list the account and I want to download only the filter
  // account" + "base on the filters only" - POST /api/billing-stats/accounts filters server-side,
  // pages of 200; the CSV holds every filtered account.
  const A = { offset: 0, search: "", timer: null, seq: 0 };
  const accBody = () => ({ billing_period: S.period, bill_types: S.billTypes, statuses: [...S.f.statuses],
    tariff: S.f.tariff, signal: S.f.signal, seg: S.f.seg, fixed: S.f.fixed, search: A.search });
  const accKey = () => JSON.stringify(accBody());
  let accLastKey = "";
  function loadAccounts(keepOffset) {
    clearTimeout(A.timer);
    const k = accKey();
    if (!keepOffset && k !== accLastKey) A.offset = 0;
    A.timer = setTimeout(async () => {
      const seq = ++A.seq;
      try {
        const d = await api("/api/billing-stats/accounts", { method: "POST", body: { ...accBody(), offset: A.offset, limit: 200 } });
        if (seq !== A.seq) return;
        accLastKey = k;
        renderAccounts(d);
      } catch (err) {
        const b = $("#bst-acc-body"); if (b) b.innerHTML = `<div class="bst-empty">⚠ ${esc(err.message)}</div>`;
      }
    }, 250);
  }
  function renderAccounts(d) {
    const b = $("#bst-acc-body"); if (!b) return;
    const cnt = $("#bst-acc-count");
    if (cnt) cnt.textContent = `${fmt(d.total)} of ${fmt(d.all)} account(s)`;
    const from = d.total ? d.offset + 1 : 0, to = d.offset + d.rows.length;
    b.innerHTML = `<div class="grid-wrap"><table class="bst-matrix" id="bst-acc-table"><thead><tr><th>Account</th><th>Bill status</th><th>Tariff</th><th>Signal</th><th>Subsidized</th><th>Fixed billing</th><th>Bills</th><th>Billed amount</th><th>Pending amount</th></tr></thead>
      <tbody>${d.rows.map((r) => `<tr><td><b>${esc(r.account)}</b></td><td style="text-align:left">${esc(split(r.statuses).map(stName).join(", "))}</td><td style="text-align:left">${esc(split(r.tariffs).join(", "))}</td><td style="text-align:left">${esc(r.signal)}</td>` +
        `<td>${String(r.subsidized) === "1" ? "✔" : ""}</td><td>${String(r.fixed_billing) === "1" ? "✔" : ""}</td><td>${fmt(num(r.bills))}</td><td>${money(num(r.amount))}</td><td>${money(num(r.pending))}</td></tr>`).join("") || `<tr><td colspan="9" class="hint-text">No accounts for these filters.</td></tr>`}</tbody></table></div>
      <div class="bst-acc-pager"><button type="button" class="bst-btn bst-btn-ghost" data-bst-acc-page="-1"${d.offset ? "" : " disabled"}>◀ Prev</button>
        <span class="hint-text">${fmt(from)}-${fmt(to)} of ${fmt(d.total)}</span>
        <button type="button" class="bst-btn bst-btn-ghost" data-bst-acc-page="1"${to < d.total ? "" : " disabled"}>Next ▶</button></div>`;
  }
  async function downloadAccounts() {
    const btn = $("#bst-acc-csv"); if (btn) { btn.disabled = true; btn.textContent = "Preparing…"; }
    try {
      const resp = await fetch("/api/billing-stats/accounts", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...accBody(), csv: true }) });
      if (!resp.ok) { let m = `Download failed (${resp.status})`; try { m = (await resp.json()).detail || m; } catch (_) {} throw new Error(m); }
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `billing_statistics_accounts_${S.period}.csv`;
      a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (err) { showToast(err.message, true); }
    finally { if (btn) { btn.disabled = false; btn.textContent = "⬇ Download filtered accounts"; } }
  }

  function exportCsv() {
    if (!S.data) return;
    const q = (v) => { v = String(v ?? ""); return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
    const head = ["STATUSES", "STATUS_NAMES", "TARIFFS", "SIGNAL", "SUBSIDIZED", "FIXED_BILLING", "ACCOUNTS", "BILLS", "AMOUNT", "PENDING"];
    const lines = S.rows.filter((r) => match(r)).map((r) => [r.st.join("|"), r.st.map(stName).join("|"), r.tf.join("|"), r.sig, r.sub ? "YES" : "NO", r.fix ? "YES" : "NO", r.acc, r.bills, r.amt, r.pend].map(q).join(","));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + [head.join(",")].concat(lines).join("\r\n")], { type: "text/csv;charset=utf-8" }));
    a.download = `billing_statistics_${S.data.billing_period}.csv`;
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function bind() {
    const r = root();
    if (!r) return;
    // RJ 2026-11-04: accounts table (search, pager, filtered download)
    r.addEventListener("input", (e) => {
      if (e.target.id === "bst-acc-search") { A.search = e.target.value.trim(); loadAccounts(); }
    });
    r.addEventListener("click", (e) => {
      const pg = e.target.closest("[data-bst-acc-page]");
      if (pg) { A.offset = Math.max(0, A.offset + 200 * Number(pg.dataset.bstAccPage)); loadAccounts(true); return; }
      if (e.target.closest("#bst-acc-csv")) { downloadAccounts(); return; }
      // RJ 2026-11-04 (speed): status change re-queries (server filters + caches 10 min)
      if (e.target.closest("[data-bst-st-all]")) { if (S.f.statuses.size) { S.f.statuses.clear(); load(); } return; }
      const pr = e.target.closest("[data-bst-preset]");
      if (pr) { const p = PRESETS.find((x) => x.id === pr.dataset.bstPreset); if (p) { S.f.seg = p.seg; S.f.fixed = p.fixed; render(); } return; }
      const st = e.target.closest("[data-bst-st]");
      if (st) {
        const c = st.getAttribute("data-bst-st");
        S.f.statuses.has(c) ? S.f.statuses.delete(c) : S.f.statuses.add(c);
        if (!S.f.statuses.size) S.f.statuses.add(DEFAULT_STATUS);
        load(); return;
      }
      const tf = e.target.closest("[data-bst-tf]");
      if (tf) { const v = tf.getAttribute("data-bst-tf"); S.f.tariff = S.f.tariff === v ? "" : v; render(); return; }
      const sg = e.target.closest("[data-bst-sg]");
      if (sg) { const v = sg.getAttribute("data-bst-sg"); S.f.signal = S.f.signal === v ? "" : v; render(); return; }
      const seg = e.target.closest("[data-bst-seg]");
      if (seg) {
        const k = seg.dataset.bstSeg;
        if (k === "fixed") S.f.fixed = S.f.fixed === "yes" ? "all" : "yes";
        else S.f.seg = S.f.seg === k ? "all" : k;
        render(); return;
      }
      const bt = e.target.closest("[data-bst-bt]");
      if (bt) {
        const c = bt.dataset.bstBt;
        if (c === "ALL") S.billTypes = S.billTypes.includes("ALL") ? ["TFGEN00001"] : ["ALL"];
        else {
          const set = new Set(S.billTypes.filter((x) => x !== "ALL"));
          set.has(c) ? set.delete(c) : set.add(c);
          S.billTypes = set.size ? [...set] : ["TFGEN00001"];
        }
        S.f.statuses = new Set([DEFAULT_STATUS]);
        load(); return;
      }
      if (e.target.closest("#bst-clear")) {   // back to the default (Invoiced)
        const reload = !(S.f.statuses.size === 1 && S.f.statuses.has(DEFAULT_STATUS));
        S.f = defaultFilters(); reload ? load() : render(); return;
      }
      if (e.target.closest("#bst-export")) exportCsv();
    });
    r.addEventListener("change", (e) => {
      const id = e.target.id;
      if (id === "bst-period") { S.period = Number(e.target.value); S.f.statuses = new Set([DEFAULT_STATUS]); load(); return; }
      if (id === "bst-seg") S.f.seg = e.target.value;
      else if (id === "bst-fixed") S.f.fixed = e.target.value;
      else if (id === "bst-tariff") S.f.tariff = e.target.value;
      else if (id === "bst-signal") S.f.signal = e.target.value;
      else return;
      render();
    });
    $("#bst-refresh-btn")?.addEventListener("click", () => {
      // RJ 2026-11-04: Refresh reloads the active tab
      if (document.querySelector('.da-subnav-btn[data-bst-sub="ops"]')?.classList.contains("is-active")) window.billingStats.loadOps();
      else load(true);
    });
    document.querySelector('.nav-item[data-page="billingstats"]')?.addEventListener("click", () => {
      if (!S.shown) { S.shown = true; load(); } else render();
    });
  }

  // ---- RJ 2026-11-04: tab 2 "Operations" ------------------------------------------------------
  // Daily bills invoiced / notices sent / e-mail + SMS letters generated & sent / payments received &
  // processed (POST /api/billing-stats/operations - RJ's query, optimised in billing_stats.py).
  const O = { loaded: false, loading: false, data: null, from: "", to: "", period: null };
  // RJ 2026-11-04 (later): bills invoiced / notices sent removed on RJ's request
  const OPS_KEYS = ["date", "email_generated", "email_sent", "sms_generated", "sms_sent", "payment_received", "payment_processed"];
  const OPS = [
    { k: "email_generated", label: "E-mails generated", ico: "✉️", c: "#8b5cf6", sent: "email_sent" },
    { k: "sms_generated", label: "SMS generated", ico: "💬", c: "#ec4899", sent: "sms_sent" },
    { k: "payment_received", label: "Payments received", ico: "💳", c: "#10b981", sent: "payment_processed", sentLabel: "processed" },
  ];
  const oroot = () => $("#bso-root");
  async function loadOps() {
    if (O.loading) return;
    O.loading = true;
    oroot().innerHTML = `<div class="bst-loading">Counting daily operations… (about a minute)</div>`;
    try {
      const d = await api("/api/billing-stats/operations", { method: "POST", body: { date_from: O.from || null, date_to: O.to || null, billing_period: O.period } });
      O.data = d; O.from = d.date_from; O.to = d.date_to; O.period = d.billing_period; O.loaded = true;
      renderOps();
    } catch (err) {
      oroot().innerHTML = `<div class="bst-empty">⚠ ${esc(err.message)}</div>`;
      showToast(err.message, true);
    } finally { O.loading = false; }
  }
  function renderOps() {
    const d = O.data; if (!d) return;
    const rows = (d.rows || []).map((r) => { const o = { date: r.date }; Object.keys(r).forEach((k) => { if (k !== "date") o[k] = num(r[k]); }); return o; });
    const tot = (k) => rows.reduce((s, r) => s + (r[k] || 0), 0);
    const maxOf = (k) => Math.max(1, ...rows.map((r) => r[k] || 0));
    const kpis = OPS.map((o) => {
      const t = tot(o.k), s = o.sent ? tot(o.sent) : null;
      return `<div class="bst-kpi" style="--k:${o.c}"><div class="bst-kpi-label"><span class="bst-kpi-ico">${o.ico}</span>${esc(o.label)}</div>` +
        `<div class="bst-kpi-value">${fmt(t)}</div><div class="bst-kpi-sub">${s == null ? `${rows.length} day(s) · avg ${fmt(Math.round(t / Math.max(1, rows.length)))}/day` : `${fmt(s)} ${o.sentLabel || "sent"} · ${pctTxt(s, t)}`}</div>` +
        (s == null ? "" : `<div class="bst-seg-bar" style="background:color-mix(in srgb, ${o.c} 15%, transparent);"><span style="width:${pct(s, t).toFixed(1)}%;background:${o.c}"></span></div>`) + `</div>`;
    }).join("");
    const chart = (o) => `<section class="card bst-panel"><h3>${o.ico} ${esc(o.label)} per day</h3><div class="bst-panel-sub">${o.sent ? `bar = generated / received, dark part = ${o.sentLabel || "sent"}` : "per day"}</div>` +
      `<div class="bst-bars">${rows.map((r) => { const v = r[o.k] || 0, s = o.sent ? (r[o.sent] || 0) : 0, m = maxOf(o.k);
        return `<div class="bst-bar" style="--c:${o.c};cursor:default;"><span class="bst-bar-name"><span class="bst-dot"></span>${esc(r.date)}</span>` +
          `<span class="bst-bar-track" style="position:relative;"><span style="width:${(100 * v / m).toFixed(1)}%;opacity:${o.sent ? .45 : 1}"></span>${o.sent ? `<span style="position:absolute;left:0;top:0;width:${(100 * s / m).toFixed(1)}%"></span>` : ""}</span>` +
          `<span class="bst-bar-val">${fmt(v)}${o.sent ? `<small>${fmt(s)} ${o.sentLabel || "sent"} · ${pctTxt(s, v)}</small>` : ""}</span></div>`; }).join("") || `<div class="bst-empty">No data.</div>`}</div></section>`;
    oroot().innerHTML = `
      <div class="bst-filters">
        <label class="bst-f bst-period-pick">📅 Billing period<select id="bso-period">${(d.periods || []).map((p) => `<option value="${p.id}"${p.id === d.billing_period ? " selected" : ""}>${esc(p.name)}${d.current_period && p.id === d.current_period.id ? " (current)" : ""}</option>`).join("")}</select></label>
        <label class="bst-f">From<input type="date" id="bso-from" value="${esc(O.from)}"></label>
        <label class="bst-f">To<input type="date" id="bso-to" value="${esc(O.to)}"></label>
        <button type="button" class="bst-btn bst-btn-ghost" id="bso-load">🔍 Load</button>
        <div class="bst-active"><span>Updated ${esc(d.generated_at)} · ${d.seconds}s</span><button type="button" class="bst-btn bst-btn-ghost" id="bso-export">⬇ Export CSV</button></div>
      </div>
      <div class="bst-kpis">${kpis}</div>
      <div class="bst-grid-2">${chart(OPS[0])}${chart(OPS[1])}</div>
      <div class="bst-grid-2">${chart(OPS[2])}<section class="card bst-panel"><h3>Daily operations</h3><div class="bst-panel-sub">${esc(d.date_from)} → ${esc(d.date_to)}</div>
        <div class="grid-wrap"><table class="bst-matrix" id="bso-table"><thead><tr><th>Date</th><th>E-mail gen.</th><th>E-mail sent</th><th>SMS gen.</th><th>SMS sent</th><th>Payments rec.</th><th>Payments proc.</th></tr></thead>
        <tbody>${rows.map((r) => `<tr>${OPS_KEYS.map((k) => `<td>${k === "date" ? esc(r.date) : fmt(r[k])}</td>`).join("")}</tr>`).join("")}</tbody>
        <tfoot><tr><td>Total</td>${OPS_KEYS.slice(1).map((k) => `<td>${fmt(tot(k))}</td>`).join("")}</tr></tfoot></table></div></section></div>`;
  }
  function bindOps() {
    const r = oroot(); if (!r) return;
    // RJ 2026-11-04: period picker - From/To follow the period's dates (To capped at today)
    r.addEventListener("change", (e) => {
      if (e.target.id === "bso-period") { O.period = Number(e.target.value); O.from = ""; O.to = ""; loadOps(); }
    });
    r.addEventListener("click", (e) => {
      if (e.target.closest("#bso-load")) { O.from = $("#bso-from").value; O.to = $("#bso-to").value; loadOps(); return; }
      if (e.target.closest("#bso-export") && O.data) {
        const keys = OPS_KEYS;
        const csv = [keys.map((k) => k.toUpperCase()).join(",")].concat((O.data.rows || []).map((x) => keys.map((k) => String(x[k] ?? "")).join(","))).join("\r\n");
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
        a.download = `billing_operations_${O.from}_${O.to}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      }
    });
    $$(".da-subnav-btn[data-bst-sub]").forEach((b) => b.addEventListener("click", () => {
      const sub = b.dataset.bstSub;
      $$(".da-subnav-btn[data-bst-sub]").forEach((x) => x.classList.toggle("is-active", x === b));
      $$(".da-subpage[data-bst-sub]").forEach((p) => p.classList.toggle("is-active", p.dataset.bstSub === sub));
      if (sub === "ops" && !O.loaded) loadOps();
    }));
  }

  bind();
  bindOps();
  window.billingStats = { load, render, state: S, loadOps, renderOps, ops: O };
})();
