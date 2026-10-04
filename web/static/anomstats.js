/* =====================================================================
   ScriptGen - Anomalies Statistics dashboard (RJ, 2026-10-03).
   "billing anomaly (GCCOM_ANOMALOUS) and reading anomaly (RE_ANOMALOUS),
   only pending / pending after batch ... show it nicely with graphs and
   everything ... show the types as well".

   Data: GET /api/anomaly-stats/billing | /reading -> shaped open records
   (see app/core/anomaly_stats.py). Everything else - KPIs, charts, types
   table, records list - is computed here, so any click (bar, slice, row,
   heat cell) cross-filters the whole page instantly without a new query.

   Charts are a tiny dependency-free kit: HTML bars for rankings (crisp,
   wrapping labels), SVG for columns / donuts, an HTML table heatmap. All
   colours come from CSS tokens or the fixed series palettes below, so
   they work in light and dark themes. Loaded after app.js (uses $, $$,
   escapeHtml, api, showToast, biss2CopyAccount).
   ===================================================================== */
(function () {
  const esc = (s) => escapeHtml(String(s ?? ""));
  const fmt = (n) => (n == null || isNaN(n) ? "–" : Number(n).toLocaleString());
  const pct = (a, b) => (b ? Math.round((a / b) * 1000) / 10 : 0);
  const DAY = 86400000;
  const today = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
  const parseDay = (s) => (s ? new Date(`${s}T00:00:00`) : null);
  const ageDays = (s) => { const d = parseDay(s); return d ? Math.max(0, Math.round((today() - d) / DAY)) : null; };
  const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const weekStart = (s) => { const d = parseDay(s); if (!d) return null; const k = (d.getDay() + 6) % 7; d.setDate(d.getDate() - k); return iso(d); };

  const AGE_BUCKETS = [
    { key: "0-7 d", min: 0, max: 7 }, { key: "8-30 d", min: 8, max: 30 }, { key: "31-60 d", min: 31, max: 60 },
    { key: "61-90 d", min: 61, max: 90 }, { key: "91-180 d", min: 91, max: 180 }, { key: "181-365 d", min: 181, max: 365 },
    { key: "> 1 year", min: 366, max: Infinity },
  ];
  const ageBucket = (r) => { const a = r._age; if (a == null) return "(no date)"; return (AGE_BUCKETS.find((b) => a >= b.min && a <= b.max) || {}).key; };

  const PALETTE = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444", "#06b6d4", "#ec4899", "#84cc16", "#64748b", "#f97316", "#14b8a6", "#a855f7"];

  // --- per-kind configuration -------------------------------------------
  const KINDS = {
    billing: {
      url: "/api/anomaly-stats/billing",
      label: "billing anomalies",
      scope: "GCCOM_ANOMALOUS with status <b>ESTAN00001</b> Pending without billing and <b>ESTAN00009</b> Pending (after batch).",
      seriesKey: (r) => r.st,
      series: [
        { key: "ESTAN00001", name: "Pending", color: "#f59e0b" },
        { key: "ESTAN00009", name: "Pending after batch", color: "#6366f1" },
      ],
      seriesDim: "status",
      dims: {
        status: { label: "Status", get: (r) => r.st, show: (v) => ({ ESTAN00001: "Pending", ESTAN00009: "Pending after batch" }[v] || v) },
        type: { label: "Type", get: (r) => r.tc },
        category: { label: "Category", get: (r) => r.cat },
        service: { label: "Service", get: (r) => r.svc },
        period: { label: "Billing period", get: (r) => r.bpd || r.bp || "(none)" },
        age: { label: "Age", get: (r) => r._ageB },
        week: { label: "Detected week", get: (r) => r._wk, show: (v) => `week of ${v}` },
        day: { label: "Detected on", get: (r) => r.dt },
        account: { label: "Account", get: (r) => r.acct },
      },
    },
    reading: {
      url: "/api/anomaly-stats/reading",
      label: "reading anomalies",
      scope: "GCGT_RE_ANOMALOUS with status <b>1000ANMSTA</b> Pending to resolve (reading anomalies have no “after batch” status). Severity = the most severe detail anomaly.",
      seriesKey: (r) => r.lv,
      series: [
        { key: "Blocking", name: "Blocking", color: "#ef4444" },
        { key: "Stop Billing", name: "Stop billing", color: "#f97316" },
        { key: "Warning", name: "Warning", color: "#eab308" },
        { key: "(no level)", name: "No level", color: "#94a3b8" },
      ],
      seriesDim: "level",
      dims: {
        level: { label: "Severity", get: (r) => r.lv },
        type: { label: "Type", get: (r) => r.tc },
        group: { label: "Group", get: (r) => r.grp },
        rtype: { label: "Reading type", get: (r) => r.rt },
        service: { label: "Service", get: (r) => r.svc },
        period: { label: "Billing period", get: (r) => r.bpd || "(none)" },
        age: { label: "Age", get: (r) => r._ageB },
        week: { label: "Detected week", get: (r) => r._wk, show: (v) => `week of ${v}` },
        day: { label: "Detected on", get: (r) => r.dt },
        account: { label: "Account", get: (r) => r.acct },
      },
    },
  };

  const S = {
    kind: "billing",
    billing: { data: null, filters: {}, loading: false, sort: { key: "_age", dir: -1 }, limit: 200, search: "" },
    reading: { data: null, filters: {}, loading: false, sort: { key: "_age", dir: -1 }, limit: 200, search: "", typeMode: "main" },
    shown: false,
  };

  // --- data -----------------------------------------------------------------
  async function load(kind, force) {
    const st = S[kind];
    if (st.loading || (st.data && !force)) return;
    st.loading = true;
    setCount(kind, "…");
    if (kind === S.kind) render();
    try {
      const data = await api(KINDS[kind].url);
      data.records.forEach((r) => { r._age = ageDays(r.dt); r._ageB = ageBucket(r); r._wk = weekStart(r.dt); });
      st.data = data;
      setCount(kind, data.count);
    } catch (err) {
      setCount(kind, "!");
      st.error = err.message;
      showToast(err.message, true);
    } finally {
      st.loading = false;
      if (kind === S.kind) render();
    }
  }
  function setCount(kind, v) {
    const el = document.querySelector(`[data-as-count="${kind}"]`);
    if (!el) return;
    el.textContent = typeof v === "number" ? v.toLocaleString() : v;
    el.classList.toggle("is-alarm", typeof v === "number" && v > 0);
  }

  // --- filtering --------------------------------------------------------------
  function typeMatch(kind, r, val) {
    if (kind === "reading" && S.reading.typeMode === "all") return (r.types || []).some((t) => t.tc === val);
    return r.tc === val;
  }
  function filtered(kind, except) {
    const st = S[kind], cfg = KINDS[kind];
    const q = (st.search || "").trim().toLowerCase();
    return (st.data?.records || []).filter((r) => {
      for (const [dim, val] of Object.entries(st.filters)) {
        if (dim === except) continue;
        if (dim === "type") { if (!typeMatch(kind, r, val)) return false; continue; }
        if (String(cfg.dims[dim].get(r)) !== String(val)) return false;
      }
      if (q) {
        const hay = [r.id, r.acct, r.niss, r.tc, r.td, r.bill, r.rd, r.mp].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }
  function toggleFilter(dim, val) {
    const st = S[S.kind];
    if (st.filters[dim] === val) delete st.filters[dim]; else st.filters[dim] = val;
    st.limit = 200;
    render();
  }

  // --- aggregation helpers ------------------------------------------------------
  function countBy(rows, keyFn) {
    const m = new Map();
    rows.forEach((r) => { const k = keyFn(r) ?? "(none)"; m.set(k, (m.get(k) || 0) + 1); });
    return m;
  }
  function stackBy(rows, keyFn, seriesFn) {
    const m = new Map();
    rows.forEach((r) => {
      const keys = keyFn(r);
      (Array.isArray(keys) ? keys : [keys]).forEach((k) => {
        k = k ?? "(none)";
        if (!m.has(k)) m.set(k, { total: 0, by: {} });
        const o = m.get(k); const s = seriesFn(r);
        o.total += 1; o.by[s] = (o.by[s] || 0) + 1;
      });
    });
    return m;
  }
  const median = (arr) => { if (!arr.length) return null; const a = [...arr].sort((x, y) => x - y); const m = Math.floor(a.length / 2); return a.length % 2 ? a[m] : Math.round((a[m - 1] + a[m]) / 2); };

  // --- chart kit -----------------------------------------------------------------
  function tipAttr(text) { return ` data-as-tip="${esc(text)}"`; }
  function fAttr(dim, val) { return ` data-as-f="${esc(dim)}" data-as-v="${esc(val)}"`; }

  /** Horizontal ranked bars, stacked by series (HTML). items: [{key,label,sub,total,by}] */
  function hbars(items, series, dim, opts = {}) {
    if (!items.length) return `<p class="as-empty">No data for the current filters.</p>`;
    const max = Math.max(...items.map((i) => i.total), 1);
    const active = S[S.kind].filters[dim];
    return `<div class="as-hbars${opts.compact ? " is-compact" : ""}">` + items.map((it) => {
      const segs = series.map((s) => {
        const v = it.by?.[s.key] || 0;
        if (!v) return "";
        return `<span class="as-seg" style="width:${(v / max) * 100}%;background:${s.color}"${tipAttr(`${it.label} · ${s.name}: ${fmt(v)}`)}></span>`;
      }).join("") || `<span class="as-seg" style="width:${(it.total / max) * 100}%;background:${opts.color || "var(--accent)"}"${tipAttr(`${it.label}: ${fmt(it.total)}`)}></span>`;
      return `<button type="button" class="as-hbar${active === it.key ? " is-active" : ""}${active && active !== it.key ? " is-dim" : ""}"${fAttr(dim, it.key)}>
        <span class="as-hbar-label"><span class="as-hbar-name">${esc(it.label)}</span>${it.sub ? `<span class="as-hbar-sub">${esc(it.sub)}</span>` : ""}</span>
        <span class="as-hbar-track">${segs}</span>
        <span class="as-hbar-val">${fmt(it.total)}${opts.pctOf ? `<small>${pct(it.total, opts.pctOf)}%</small>` : ""}</span>
      </button>`;
    }).join("") + `</div>`;
  }

  /** Vertical stacked columns (SVG). cols: [{key,label,by,total}] */
  function columns(cols, series, dim, opts = {}) {
    if (!cols.length) return `<p class="as-empty">No data for the current filters.</p>`;
    const W = 640, H = opts.height || 220, padL = 40, padR = 8, padT = 10, padB = 34;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const max = Math.max(...cols.map((c) => c.total), 1);
    const nice = niceMax(max);
    const bw = plotW / cols.length;
    const active = S[S.kind].filters[dim];
    let g = "";
    for (let i = 0; i <= 4; i++) {
      const y = padT + plotH - (plotH * i) / 4;
      g += `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}" class="as-grid"/><text x="${padL - 6}" y="${y + 3}" class="as-axis" text-anchor="end">${fmt(Math.round((nice * i) / 4))}</text>`;
    }
    const labelEvery = Math.max(1, Math.ceil(cols.length / (opts.maxLabels || 13)));
    cols.forEach((c, i) => {
      const x = padL + i * bw + bw * 0.14, w = bw * 0.72;
      let y = padT + plotH;
      const dimCls = active && active !== c.key ? " is-dim" : "";
      g += `<g class="as-col${dimCls}"${fAttr(dim, c.key)}${tipAttr(`${c.label}: ${fmt(c.total)}` + series.map((s) => (c.by?.[s.key] ? ` · ${s.name} ${fmt(c.by[s.key])}` : "")).join(""))}>`;
      g += `<rect x="${padL + i * bw}" y="${padT}" width="${bw}" height="${plotH}" class="as-hit"/>`;
      const parts = series.length ? series.map((s) => [s.color, c.by?.[s.key] || 0]) : [[opts.color || "var(--accent)", c.total]];
      parts.forEach(([color, v]) => {
        if (!v) return;
        const h = (v / nice) * plotH;
        y -= h;
        g += `<rect x="${x}" y="${y}" width="${w}" height="${Math.max(h, 0.5)}" rx="2" fill="${color}"/>`;
      });
      g += `</g>`;
      if (i % labelEvery === 0) g += `<text x="${padL + i * bw + bw / 2}" y="${H - padB + 14}" class="as-axis" text-anchor="middle">${esc(c.short || c.label)}</text>`;
    });
    return `<svg class="as-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.aria || "column chart")}">${g}</svg>`;
  }
  function niceMax(v) { const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }

  /** Donut (SVG) + legend. items: [{key,label,value,color}] */
  function donut(items, dim, centerLabel) {
    const total = items.reduce((a, b) => a + b.value, 0);
    if (!total) return `<p class="as-empty">No data for the current filters.</p>`;
    const R = 70, r = 46, C = 90;
    let a0 = -Math.PI / 2, paths = "";
    const active = S[S.kind].filters[dim];
    items.forEach((it) => {
      if (!it.value) return;
      const a1 = a0 + (it.value / total) * Math.PI * 2;
      const large = a1 - a0 > Math.PI ? 1 : 0;
      const p = (ang, rad) => `${C + rad * Math.cos(ang)} ${C + rad * Math.sin(ang)}`;
      const d = it.value === total
        ? `M ${C} ${C - R} A ${R} ${R} 0 1 1 ${C - 0.01} ${C - R} L ${C - 0.01} ${C - r} A ${r} ${r} 0 1 0 ${C} ${C - r} Z`
        : `M ${p(a0, R)} A ${R} ${R} 0 ${large} 1 ${p(a1, R)} L ${p(a1, r)} A ${r} ${r} 0 ${large} 0 ${p(a0, r)} Z`;
      paths += `<path d="${d}" fill="${it.color}" class="as-slice${active && active !== it.key ? " is-dim" : ""}"${fAttr(dim, it.key)}${tipAttr(`${it.label}: ${fmt(it.value)} (${pct(it.value, total)}%)`)}/>`;
      a0 = a1;
    });
    const legend = items.filter((i) => i.value).map((it) => `<button type="button" class="as-legend-item${active === it.key ? " is-active" : ""}"${fAttr(dim, it.key)}>
      <span class="as-dot" style="background:${it.color}"></span><span class="as-legend-name">${esc(it.label)}</span>
      <span class="as-legend-val">${fmt(it.value)} <small>${pct(it.value, total)}%</small></span></button>`).join("");
    return `<div class="as-donut"><svg viewBox="0 0 180 180" class="as-donut-svg" role="img" aria-label="${esc(centerLabel)}">${paths}
      <text x="90" y="88" text-anchor="middle" class="as-donut-total">${fmt(total)}</text>
      <text x="90" y="106" text-anchor="middle" class="as-donut-caption">${esc(centerLabel)}</text></svg>
      <div class="as-legend">${legend}</div></div>`;
  }

  /** Heatmap table: rows = types, cols = age buckets. */
  function heatmap(rowsDef, colsDef, cell, rowDim, colDim) {
    let max = 1;
    const vals = rowsDef.map((rw) => colsDef.map((c) => { const v = cell(rw.key, c); if (v > max) max = v; return v; }));
    return `<div class="as-heat-wrap"><table class="as-heat"><thead><tr><th>Type</th>${colsDef.map((c) => `<th>${esc(c)}</th>`).join("")}<th>Total</th></tr></thead><tbody>` +
      rowsDef.map((rw, i) => `<tr><th><button type="button" class="as-link"${fAttr(rowDim, rw.key)} title="${esc(rw.label)}">${esc(rw.short)}</button></th>` +
        vals[i].map((v, j) => {
          const a = v ? 0.12 + 0.88 * (v / max) : 0;
          return `<td${v ? fAttr(colDim, colsDef[j]) + tipAttr(`${rw.label} · ${colsDef[j]}: ${fmt(v)}`) : ""} style="--a:${a.toFixed(3)}" class="${v ? "has-v" : ""}${a > 0.55 ? " is-strong" : ""}">${v ? fmt(v) : ""}</td>`;
        }).join("") + `<td class="as-heat-total">${fmt(vals[i].reduce((x, y) => x + y, 0))}</td></tr>`).join("") +
      `</tbody></table></div>`;
  }

  // --- render ---------------------------------------------------------------------
  function card(title, body, opts = {}) {
    return `<section class="card as-card${opts.wide ? " is-wide" : ""}${opts.full ? " is-full" : ""}">
      <header class="as-card-head"><h2>${title}</h2>${opts.extra || ""}</header>
      <div class="as-card-body">${body}</div>${opts.foot ? `<footer class="as-card-foot">${opts.foot}</footer>` : ""}</section>`;
  }

  function kpi(label, value, sub, opts = {}) {
    return `<div class="as-kpi${opts.tone ? " is-" + opts.tone : ""}"${opts.f ? fAttr(opts.f[0], opts.f[1]) : ""}>
      <div class="as-kpi-label">${esc(label)}</div><div class="as-kpi-value">${value}</div>${sub ? `<div class="as-kpi-sub">${sub}</div>` : ""}</div>`;
  }

  function render() {
    const root = $("#as-root");
    if (!root) return;
    const kind = S.kind, st = S[kind], cfg = KINDS[kind];
    const upd = $("#as-updated");
    if (upd) upd.textContent = st.data ? `Updated ${st.data.generated_at.replace("T", " ")} · ${st.data.seconds}s` : "";
    if (!st.data) {
      root.innerHTML = st.loading
        ? `<div class="as-skeleton">${'<div class="as-sk"></div>'.repeat(8)}</div><p class="hint-text as-loading">Loading ${cfg.label}…</p>`
        : `<div class="card as-card"><p>${st.error ? `⚠ ${esc(st.error)}` : "No data loaded yet."}</p></div>`;
      return;
    }

    const all = st.data.records;
    const rows = filtered(kind);
    const series = cfg.series;
    const sKey = cfg.seriesKey;

    // --- KPIs
    const t0 = today().getTime();
    const new7 = rows.filter((r) => r._age != null && r._age <= 6).length;
    const prev7 = rows.filter((r) => r._age != null && r._age >= 7 && r._age <= 13).length;
    const ages = rows.map((r) => r._age).filter((a) => a != null);
    const older30 = ages.filter((a) => a > 30).length;
    const accounts = new Set(rows.map((r) => r.acct).filter(Boolean)).size;
    const supplies = new Set(rows.map((r) => r.niss).filter(Boolean)).size;
    const typesN = kind === "reading" && st.typeMode === "all"
      ? new Set(rows.flatMap((r) => (r.types || []).map((t) => t.tc))).size
      : new Set(rows.map((r) => r.tc)).size;
    const bySeries = countBy(rows, sKey);
    const delta = new7 - prev7;
    const kpis = [
      kpi(`Open ${cfg.label}`, fmt(rows.length), rows.length !== all.length ? `filtered from ${fmt(all.length)}` : "all open records", { tone: "accent" }),
      ...series.filter((s) => bySeries.get(s.key) || s.key !== "(no level)").map((s) =>
        kpi(s.name, fmt(bySeries.get(s.key) || 0), `${pct(bySeries.get(s.key) || 0, rows.length)}% of open`, { f: [cfg.seriesDim, s.key], tone: "series", })),
      kpi("New last 7 days", fmt(new7), `${delta === 0 ? "same as" : delta > 0 ? `▲ ${fmt(delta)} vs` : `▼ ${fmt(-delta)} vs`} previous 7 days (${fmt(prev7)})`, { tone: delta > 0 ? "bad" : "good" }),
      kpi("Median age", ages.length ? `${fmt(median(ages))} d` : "–", `oldest ${ages.length ? fmt(Math.max(...ages)) : "–"} days`),
      kpi("Older than 30 days", `${pct(older30, rows.length)}%`, `${fmt(older30)} records`, { tone: pct(older30, rows.length) > 50 ? "bad" : "" }),
      kpi("Types", fmt(typesN), kind === "reading" ? `${fmt(rows.reduce((a, r) => a + (r.nd || 0), 0))} detail anomalies` : "distinct anomaly types"),
      kpi("Accounts affected", fmt(accounts), `${fmt(supplies)} supplies (NISS)`),
    ].join("");

    // --- chips + filter bar
    const chips = Object.entries(st.filters).map(([dim, val]) => {
      const d = cfg.dims[dim];
      const shown = d?.show ? d.show(val) : (dim === "type" ? typeLabel(kind, val) : val);
      return `<button type="button" class="as-chip" data-as-unf="${esc(dim)}" title="Remove filter">${esc(d?.label || dim)}: <b>${esc(shown)}</b> ✕</button>`;
    }).join("");
    const typeToggle = kind === "reading" ? `<div class="hx-seg as-typemode" role="group" aria-label="Count types by">
        <button type="button" data-as-tm="main" class="${st.typeMode === "main" ? "is-active" : ""}" title="Each reading anomaly counted once, under its most severe type">Main type</button>
        <button type="button" data-as-tm="all" class="${st.typeMode === "all" ? "is-active" : ""}" title="Every detail type of each reading anomaly is counted">Every type</button></div>` : "";
    const bar = `<div class="as-toolbar">
      <input type="search" class="text-input as-search" id="as-search" placeholder="Search account, NISS, id, type…" value="${esc(st.search)}" />
      ${typeToggle}
      <div class="as-chips">${chips || `<span class="hint-text">Tip: click any bar, slice, cell or row to filter.</span>`}</div>
      ${chips ? `<button type="button" class="btn btn-pill-sm" id="as-clear">✕ Clear filters</button>` : ""}
    </div>`;

    // --- trend: daily (last 30 days, default) or weekly (last 26 weeks)
    const daily = (st.trend || "day") === "day";
    const trendDim = daily ? "day" : "week";
    const buckets = [];
    if (daily) {
      for (let i = 29; i >= 0; i--) buckets.push(iso(new Date(today().getTime() - i * DAY)));
    } else {
      const ws = parseDay(weekStart(iso(today())));
      for (let i = 25; i >= 0; i--) buckets.push(iso(new Date(ws.getTime() - i * 7 * DAY)));
    }
    const rowsNoTrend = filtered(kind, trendDim);
    const tmapT = stackBy(rowsNoTrend, (r) => (daily ? r.dt : r._wk), sKey);
    const before = rowsNoTrend.filter((r) => { const k = daily ? r.dt : r._wk; return k && k < buckets[0]; }).length;
    const trendCols = buckets.map((b) => ({ key: b, label: daily ? b : `Week of ${b}`, short: b.slice(5), ...(tmapT.get(b) || { total: 0, by: {} }) }));
    const trend = columns(trendCols, series, trendDim, { aria: daily ? "Detected per day" : "Detected per week", maxLabels: daily ? 10 : 13 });
    const trendToggle = `<div class="hx-seg as-trend-toggle" role="group" aria-label="Trend granularity">
      <button type="button" data-as-trend="day" class="${daily ? "is-active" : ""}">Daily</button>
      <button type="button" data-as-trend="week" class="${daily ? "" : "is-active"}">Weekly</button></div>`;

    // --- types
    const rowsNoType = filtered(kind, "type");
    let tmap;
    if (kind === "reading" && st.typeMode === "all") {
      // every detail type counted, coloured by THAT detail's own level
      tmap = new Map();
      rowsNoType.forEach((r) => (r.types || []).forEach((t) => {
        if (!tmap.has(t.tc)) tmap.set(t.tc, { total: 0, by: {} });
        const o = tmap.get(t.tc); o.total += 1; o.by[t.lv] = (o.by[t.lv] || 0) + 1;
      }));
    } else {
      tmap = stackBy(rowsNoType, (r) => r.tc, sKey);
    }
    const typeItems = [...tmap.entries()].map(([k, v]) => ({ key: k, label: typeLabel(kind, k), sub: k, total: v.total, by: v.by })).sort((a, b) => b.total - a.total);
    const typesChart = `<div class="as-scroll">${hbars(typeItems, series, "type", { pctOf: typeItems.reduce((a, t) => a + t.total, 0) })}</div>`;

    // --- status / severity donut
    const sDonutRows = filtered(kind, cfg.seriesDim);
    const sCounts = countBy(sDonutRows, sKey);
    const sItems = series.map((s) => ({ key: s.key, label: s.name, value: sCounts.get(s.key) || 0, color: s.color }));
    const sDonut = donut(sItems, cfg.seriesDim, kind === "billing" ? "open" : "open");

    // --- service donut
    const svcRows = filtered(kind, "service");
    const svcItems = [...countBy(svcRows, (r) => r.svc).entries()].sort((a, b) => b[1] - a[1]).map(([k, v], i) => ({ key: k, label: k, value: v, color: PALETTE[i % PALETTE.length] }));
    const svcDonut = donut(svcItems, "service", "by service");

    // --- age
    const ageRows = filtered(kind, "age");
    const am = stackBy(ageRows, (r) => r._ageB, sKey);
    const ageCols = [...AGE_BUCKETS.map((b) => b.key), "(no date)"].filter((k) => am.has(k) || k !== "(no date)").map((k) => ({ key: k, label: k, ...(am.get(k) || { total: 0, by: {} }) }));
    const ageChart = columns(ageCols, series, "age", { aria: "Age buckets", height: 200 });

    // --- period
    const pRows = filtered(kind, "period");
    const pm = stackBy(pRows, cfg.dims.period.get, sKey);
    const pItems = [...pm.entries()].map(([k, v]) => ({ key: k, label: k, total: v.total, by: v.by, _id: periodOrder(k) }))
      .sort((a, b) => b._id - a._id).slice(0, 14);
    const periodChart = hbars(pItems, series, "period", { compact: true });

    // --- category (billing) / reading type + group (reading)
    let third = "";
    if (kind === "billing") {
      const cRows = filtered(kind, "category");
      const cm = stackBy(cRows, (r) => r.cat, sKey);
      const cItems = [...cm.entries()].map(([k, v]) => ({ key: k, label: k, total: v.total, by: v.by })).sort((a, b) => b.total - a.total);
      third = card("Category", hbars(cItems, series, "category", { compact: true }), { full: true });
    } else {
      const rRows = filtered(kind, "rtype");
      const rm = stackBy(rRows, (r) => r.rt, sKey);
      const rItems = [...rm.entries()].map(([k, v]) => ({ key: k, label: k, total: v.total, by: v.by })).sort((a, b) => b.total - a.total);
      const gRows = filtered(kind, "group");
      const gItems = [...countBy(gRows, (r) => r.grp).entries()].map(([k, v], i) => ({ key: k, label: k, value: v, color: PALETTE[(i + 3) % PALETTE.length] }));
      third = card("Reading type (main detail)", hbars(rItems, series, "rtype", { compact: true }), { wide: true }) + card("Group", donut(gItems, "group", "by group"));
    }

    // --- heatmap types x age
    const topTypes = typeItems.slice(0, 15);
    const hm = new Map();
    rowsNoType.forEach((r) => {
      const keys = kind === "reading" && st.typeMode === "all" ? (r.types || []).map((t) => t.tc) : [r.tc];
      keys.forEach((k) => { const key = `${k}|${r._ageB}`; hm.set(key, (hm.get(key) || 0) + 1); });
    });
    const heat = heatmap(topTypes.map((t) => ({ key: t.key, label: t.label, short: shorten(t.label, 34) })), AGE_BUCKETS.map((b) => b.key),
      (tk, ab) => hm.get(`${tk}|${ab}`) || 0, "type", "age");

    // --- types table
    const typesTable = renderTypesTable(kind, rowsNoType, typeItems);

    // --- records
    const recordsHtml = renderRecords(kind, rows);

    const legend = `<div class="as-series-legend">${series.filter((s) => bySeries.get(s.key) || s.key !== "(no level)").map((s) => `<span><span class="as-dot" style="background:${s.color}"></span>${esc(s.name)}</span>`).join("")}</div>`;

    root.innerHTML = `
      <p class="as-scope">ℹ️ ${cfg.scope}</p>
      ${bar}
      <div class="as-kpis">${kpis}</div>
      <div class="as-grid">
        ${card(daily ? "Detected per day <small>(last 30 days)</small>" : "Detected per week <small>(last 26 weeks)</small>", trend, { wide: true, extra: `<div class="as-head-tools">${legend}${trendToggle}</div>`, foot: before ? `${fmt(before)} open record(s) were detected before ${buckets[0]} (not shown - switch to ${daily ? "Weekly" : "the Age chart"} to see older ones).` : "" })}
        ${card(kind === "billing" ? "Status" : "Severity", sDonut)}
        ${card(`Anomaly types <small>(${fmt(typeItems.length)})</small>`, typesChart, { wide: true, extra: legend })}
        ${card("Service", svcDonut)}
        ${card("Age since detection", ageChart, { wide: true })}
        ${card("Billing period", periodChart)}
        ${third}
        ${card("Types × age <small>(top 15 types)</small>", heat, { full: true })}
        ${card(`Types summary`, typesTable, { full: true, extra: `<button type="button" class="btn btn-pill-sm" id="as-export-types">⬇ CSV</button>` })}
        ${card(`Records <small>(${fmt(rows.length)})</small>`, recordsHtml, { full: true, extra: `<div class="as-rec-actions"><button type="button" class="btn btn-pill-sm" id="as-copy-acct">📋 Copy accounts</button><button type="button" class="btn btn-pill-sm" id="as-export">⬇ Export CSV</button></div>` })}
      </div>`;
    const s = $("#as-search");
    if (s && document.activeElement?.id !== "as-search" && S._refocus) { s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
  }

  function typeLabel(kind, code) {
    const recs = S[kind].data?.records || [];
    if (kind === "reading") {
      for (const r of recs) { const t = (r.types || []).find((x) => x.tc === code); if (t) return t.td; }
    }
    const r = recs.find((x) => x.tc === code);
    return r ? r.td : code;
  }
  function shorten(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function periodOrder(label) {
    const recs = S[S.kind].data?.records || [];
    const r = recs.find((x) => (x.bpd || x.bp) === label);
    return r && r.bp ? Number(r.bp) : -1;
  }

  function renderTypesTable(kind, rows, typeItems) {
    const series = KINDS[kind].series.filter((s) => s.key !== "(no level)" || typeItems.some((t) => t.by[s.key]));
    const total = typeItems.reduce((a, t) => a + t.total, 0) || 1;
    const stats = new Map();
    rows.forEach((r) => {
      const keys = kind === "reading" && S.reading.typeMode === "all" ? (r.types || []).map((t) => t.tc) : [r.tc];
      keys.forEach((k) => {
        if (!stats.has(k)) stats.set(k, { ages: [], new7: 0, cat: r.cat });
        const o = stats.get(k);
        if (r._age != null) { o.ages.push(r._age); if (r._age <= 6) o.new7 += 1; }
      });
    });
    const active = S[kind].filters.type;
    const head = `<tr><th>Code</th><th>Type</th>${kind === "billing" ? "<th>Category</th>" : ""}<th class="hx-num">Total</th>${series.map((s) => `<th class="hx-num">${esc(s.name)}</th>`).join("")}<th>Share</th><th class="hx-num">New 7 d</th><th class="hx-num">Avg age</th><th class="hx-num">Oldest</th></tr>`;
    const body = typeItems.map((t) => {
      const o = stats.get(t.key) || { ages: [], new7: 0 };
      const avg = o.ages.length ? Math.round(o.ages.reduce((a, b) => a + b, 0) / o.ages.length) : null;
      const share = pct(t.total, total);
      return `<tr class="as-row${active === t.key ? " is-active" : ""}"${fAttr("type", t.key)}>
        <td class="hx-mono">${esc(t.key)}</td><td>${esc(t.label)}</td>${kind === "billing" ? `<td>${esc(o.cat || "")}</td>` : ""}
        <td class="hx-num"><b>${fmt(t.total)}</b></td>${series.map((s) => `<td class="hx-num">${fmt(t.by[s.key] || 0)}</td>`).join("")}
        <td><span class="as-share"><span style="width:${share}%"></span></span> <small>${share}%</small></td>
        <td class="hx-num">${fmt(o.new7)}</td><td class="hx-num">${avg == null ? "–" : fmt(avg) + " d"}</td><td class="hx-num">${o.ages.length ? fmt(Math.max(...o.ages)) + " d" : "–"}</td></tr>`;
    }).join("");
    return `<div class="grid-wrap as-table-wrap"><table class="data-grid as-table">${head}${body}</table></div>`;
  }

  const REC_COLS = {
    billing: [
      ["id", "ID"], ["st", "Status"], ["tc", "Type"], ["td", "Description"], ["cat", "Category"], ["svc", "Service"],
      ["acct", "Account"], ["niss", "NISS"], ["dt", "Detected"], ["_age", "Age (d)"], ["bd", "Billing date"], ["bpd", "Billing period"], ["bill", "ID bill"], ["amt", "Expected amount"],
    ],
    reading: [
      ["id", "ID"], ["lv", "Severity"], ["tc", "Main type"], ["td", "Description"], ["nd", "Details"], ["grp", "Group"], ["rt", "Reading type"],
      ["svc", "Service"], ["acct", "Account"], ["niss", "NISS"], ["mp", "Measuring point"], ["dt", "Detected"], ["_age", "Age (d)"], ["bpd", "Billing period"], ["rd", "Reading"],
    ],
  };
  function sortedRows(kind, rows) {
    const { key, dir } = S[kind].sort;
    return [...rows].sort((a, b) => {
      const x = a[key], y = b[key];
      if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * dir;
    });
  }
  function renderRecords(kind, rows) {
    const st = S[kind];
    const cols = REC_COLS[kind];
    const list = sortedRows(kind, rows).slice(0, st.limit);
    const statusName = (v) => ({ ESTAN00001: "Pending", ESTAN00009: "Pending after batch" }[v] || v);
    const sevColor = Object.fromEntries(KINDS.reading.series.map((s) => [s.key, s.color]));
    const head = cols.map(([k, l]) => `<th class="stats-table-th-sortable${["_age", "amt", "nd"].includes(k) ? " hx-num" : ""}" data-as-sort="${k}">${esc(l)}${st.sort.key === k ? `<span class="stats-table-sort-arrow">${st.sort.dir === 1 ? "▲" : "▼"}</span>` : ""}</th>`).join("");
    const body = list.map((r) => "<tr>" + cols.map(([k]) => {
      let v = r[k];
      if (k === "st") return `<td><span class="as-pill" style="--c:${k === "st" && v === "ESTAN00009" ? "#6366f1" : "#f59e0b"}">${esc(statusName(v))}</span></td>`;
      if (k === "lv") return `<td><span class="as-pill" style="--c:${sevColor[v] || "#94a3b8"}">${esc(v)}</span></td>`;
      if (k === "tc" && kind === "reading" && (r.types || []).length > 1) return `<td class="hx-mono">${esc(v)} <span class="as-more"${tipAttr((r.types || []).map((t) => `${t.tc} ${t.td} (${t.lv})`).join(" | "))}>+${r.types.length - 1}</span></td>`;
      if (k === "acct" || k === "niss") return `<td class="hx-mono">${esc(v || "")}${v ? ` <button type="button" class="biss2-copy-btn" data-as-copy="${esc(v)}" title="Copy">📋</button>` : ""}</td>`;
      if (k === "amt") return `<td class="hx-num">${v == null ? "" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>`;
      if (k === "_age" || k === "nd") return `<td class="hx-num">${fmt(v)}</td>`;
      return `<td class="${["id", "tc", "bill", "rd", "mp"].includes(k) ? "hx-mono" : ""}">${esc(v ?? "")}</td>`;
    }).join("") + "</tr>").join("");
    const more = rows.length > list.length ? `<div class="as-more-row"><button type="button" class="btn btn-pill-sm" id="as-more">Show ${fmt(Math.min(500, rows.length - list.length))} more</button> <span class="hint-text">showing ${fmt(list.length)} of ${fmt(rows.length)} - export includes all</span></div>` : "";
    return `<div class="grid-wrap as-table-wrap as-rec-wrap"><table class="data-grid as-table"><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${cols.length}" class="hint-text">No records match.</td></tr>`}</tbody></table></div>${more}`;
  }

  // --- export -------------------------------------------------------------------
  function download(name, lines) {
    const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }
  const csvEsc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  function exportRecords() {
    const kind = S.kind, rows = sortedRows(kind, filtered(kind));
    const cols = REC_COLS[kind];
    const lines = [cols.map(([, l]) => csvEsc(l)).concat(kind === "reading" ? [csvEsc("All types")] : []).join(",")];
    rows.forEach((r) => lines.push(cols.map(([k]) => csvEsc(r[k])).concat(kind === "reading" ? [csvEsc((r.types || []).map((t) => `${t.tc} ${t.td} (${t.lv})`).join(" | "))] : []).join(",")));
    download(`anomalies_${kind}_${iso(today())}.csv`, lines);
  }
  function exportTypes() {
    const kind = S.kind;
    const table = $("#as-root .as-table");
    if (!table) return;
    const lines = [...table.querySelectorAll("tr")].map((tr) => [...tr.children].map((c) => csvEsc(c.textContent.trim())).join(","));
    download(`anomaly_types_${kind}_${iso(today())}.csv`, lines);
  }

  // --- events -----------------------------------------------------------------------
  function bind() {
    const root = $("#as-root");
    const tip = document.createElement("div");
    tip.className = "as-tooltip"; tip.hidden = true; document.body.appendChild(tip);
    root.addEventListener("mousemove", (e) => {
      const t = e.target.closest("[data-as-tip]");
      if (!t) { tip.hidden = true; return; }
      tip.textContent = t.dataset.asTip; tip.hidden = false;
      const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
      tip.style.left = `${x}px`; tip.style.top = `${e.clientY + 14}px`;
    });
    root.addEventListener("mouseleave", () => { tip.hidden = true; });
    root.addEventListener("click", (e) => {
      const cp = e.target.closest("[data-as-copy]");
      if (cp) { e.stopPropagation(); biss2CopyAccount(cp.dataset.asCopy, cp); return; }
      const f = e.target.closest("[data-as-f]");
      if (f) { tip.hidden = true; toggleFilter(f.dataset.asF, f.dataset.asV); return; }
      const un = e.target.closest("[data-as-unf]");
      if (un) { delete S[S.kind].filters[un.dataset.asUnf]; render(); return; }
      const so = e.target.closest("[data-as-sort]");
      if (so) { const st = S[S.kind]; const k = so.dataset.asSort; st.sort = { key: k, dir: st.sort.key === k ? -st.sort.dir : (k === "_age" ? -1 : 1) }; render(); return; }
      const tr = e.target.closest("[data-as-trend]");
      if (tr) { const st = S[S.kind]; st.trend = tr.dataset.asTrend; delete st.filters.day; delete st.filters.week; render(); return; }
      const tm = e.target.closest("[data-as-tm]");
      if (tm) { S.reading.typeMode = tm.dataset.asTm; delete S.reading.filters.type; render(); return; }
      if (e.target.closest("#as-clear")) { S[S.kind].filters = {}; S[S.kind].search = ""; render(); return; }
      if (e.target.closest("#as-more")) { S[S.kind].limit += 500; render(); return; }
      if (e.target.closest("#as-export")) { exportRecords(); return; }
      if (e.target.closest("#as-export-types")) { exportTypes(); return; }
      if (e.target.closest("#as-copy-acct")) {
        const vals = [...new Set(filtered(S.kind).map((r) => r.acct).filter(Boolean))];
        if (!vals.length) return;
        biss2CopyAccount(vals.join("\n"), null);
        showToast(`Copied ${vals.length.toLocaleString()} account(s) (one per line).`);
      }
    });
    let t = null;
    root.addEventListener("input", (e) => {
      if (e.target.id !== "as-search") return;
      S[S.kind].search = e.target.value;
      clearTimeout(t);
      t = setTimeout(() => { S._refocus = true; render(); S._refocus = false; }, 250);
    });
    $$(".da-subnav-btn[data-as-kind]").forEach((b) => b.addEventListener("click", () => {
      S.kind = b.dataset.asKind;
      $$(".da-subnav-btn[data-as-kind]").forEach((x) => x.classList.toggle("is-active", x === b));
      render();
      load(S.kind);
    }));
    $("#as-refresh-btn")?.addEventListener("click", () => { load("billing", true); load("reading", true); });
    document.querySelector('.nav-item[data-page="anomalystats"]')?.addEventListener("click", () => {
      if (!S.shown) { S.shown = true; load("billing"); load("reading"); } else render();
    });
  }

  bind();
  window.anomStats = { load, render, state: S };
})();
