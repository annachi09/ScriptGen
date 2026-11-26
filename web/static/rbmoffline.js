/* =====================================================================
   ScriptGen - Rebilling Monitor tab 3 "Rebilling complete with offline task error" (RJ 2026-11-26).
   RJ: "there will be 2 main case, 1 the RCCS, 2 the Service request. Reverse this correction scripts to find all
   the cases, add the correction script to correct them". RCCS = MODULE0010 / ACTI600007, SR = MODULE0011 /
   ACTI600008 "End Massive Rebilling Activity" still ACTSTA0001 while the rebilling is Rebilled.
   POST /api/rebilling-monitor/offline-error/detect | /generate (app/core/rebilling_offline_error.py).
   UI standard: docs/UI_STANDARD.md (Summary KPIs, table with checkboxes, Fix script + rollback).
   ===================================================================== */
(function () {
  const el = (id) => document.getElementById(id);
  if (!el("rbx-detect-btn")) return;
  const esc = (s) => escapeHtml(String(s ?? ""));
  const fmt = (n) => Number(n || 0).toLocaleString();
  const day = (s) => String(s || "").slice(0, 10);
  const S = { rows: [], sel: new Set(), filter: "complete", loaded: false, searched: [] };
  const key = (r) => String(r.id_activity);
  const STATE_TONE = { complete: "bad", open: "info", rejected: "warn", none: "neutral" };
  const STATE_SHORT = { complete: "Rebilled - action pending", open: "Rebilling still open", rejected: "Latest rebilling rejected", none: "No rebilling found" };

  const PRED = {
    complete: (r) => r.state === "complete",
    rccs: (r) => r.state === "complete" && r.case_type === "RCCS",
    sr: (r) => r.state === "complete" && r.case_type === "SR",
    open: (r) => r.state === "open",
    other: (r) => r.state === "rejected" || r.state === "none",
    selected: (r) => S.sel.has(key(r)),
  };

  function visible() {
    const terms = sgStd.terms(el("rbx-search"));   // RJ 2026-11-26: paste-many filter
    const ct = el("rbx-case").value;
    return S.rows.filter((r) => (!S.filter || PRED[S.filter](r)) && (!ct || r.case_type === ct)
      && sgStd.matches(terms, [r.account_ref, r.id_account, r.ref_code, r.last_reb_id, r.reb_ids, r.id_activity]));
  }

  function render() {
    const c = (p) => S.rows.filter(p).length;
    sgStd.kpis(el("rbx-kpis"), [
      { key: "complete", label: "Rebilled - action still pending", value: c(PRED.complete), tone: c(PRED.complete) ? "bad" : "ok",
        sub: "ready to fix", tip: "Rebilling activity Rebilled (ESTREF0004), nothing open, End Massive Rebilling action ACTSTA0001" },
      { key: "rccs", label: "1 · RCCS", value: c(PRED.rccs), tone: "warn", sub: "ACTI600007 · MODULE0010" },
      { key: "sr", label: "2 · Service requests", value: c(PRED.sr), tone: "warn", sub: "ACTI600008 · MODULE0011" },
      { key: "open", label: "Rebilling still open", value: c(PRED.open), tone: "info", sub: "waiting - not an error yet" },
      { key: "other", label: "Rejected / no rebilling", value: c(PRED.other), tone: "neutral", sub: "review manually" },
      { key: "selected", label: "Ticked", value: S.sel.size, tone: "neutral" },
    ], { active: S.filter, onPick: (k) => { S.filter = S.filter === k ? null : k; render(); } });

    const rows = visible();
    el("rbx-shown").textContent = `${fmt(rows.length)} of ${fmt(S.rows.length)} rows shown`;
    const fixable = rows.filter(PRED.complete);
    const allBox = el("rbx-check-all");
    allBox.checked = fixable.length > 0 && fixable.every((r) => S.sel.has(key(r)));
    el("rbx-table").querySelector("tbody").innerHTML = rows.map((r) => {
      const fix = r.state === "complete";
      return `<tr>
      <td><input type="checkbox" data-rbx-id="${esc(key(r))}" ${S.sel.has(key(r)) ? "checked" : ""} ${fix ? "" : "disabled title=\"Only 'Rebilled - action pending' cases get the fix\""} /></td>
      <td>${r.case_type === "RCCS" ? "1 · RCCS" : "2 · SR"}</td>
      <td><b>${esc(r.ref_code)}</b><div class="hint-text">${esc(r.obj_status)} · ID ${esc(r.id_object)}</div></td>
      <td>${esc(r.account_ref || r.id_account)}<div class="hint-text">ID ${esc(r.id_account)}</div></td>
      <td>${esc(day(r.action_date_ts))}<div class="hint-text">${esc(r.cod_step)} · activity ${esc(r.id_activity)} · ${fmt(r.action_age_days)} d</div></td>
      <td>${r.last_reb_id ? `${esc(r.last_reb_id)} ${sgStd.pill(r.last_reb_status, r.last_reb_status_en)}` : "-"}
        ${Number(r.reb_count) > 1 ? `<div class="hint-text" title="All rebilling activities linked">${esc(r.reb_ids)}</div>` : ""}
        ${r.last_reb_info ? `<div class="hint-text">${esc(r.last_reb_info)}</div>` : ""}</td>
      <td>${r.last_reb_status === "ESTREF0004" ? `${esc(String(r.last_reb_updated || "").slice(0, 16))}<div class="hint-text">${esc(r.last_reb_program)} · ${fmt(r.last_reb_bills)} bill(s)</div>` : "-"}</td>
      <td>${fix ? `<b>${fmt(r.stuck_days)}</b>` : "-"}</td>
      <td><span class="sg-pill" data-tone="${STATE_TONE[r.state]}" title="${esc(r.state_en)}">${esc(STATE_SHORT[r.state])}</span></td>
    </tr>`;
    }).join("") || `<tr><td colspan="9" class="hint-text">${S.loaded ? "No case matches the current filters." : "Click Scan to load."}</td></tr>`;
    el("rbx-gen-btn").disabled = S.sel.size === 0;
    el("rbx-gen-btn").textContent = S.sel.size ? `🛠 Generate script (${S.sel.size})` : "🛠 Generate script";
    el("rbx-export-btn").disabled = !S.rows.length;
  }

  // reuse the reb status pill colours
  Object.assign(sgStd.STATUS, { ESTREF0001: ["Pending", "warn"], ESTREF0002: ["In process", "info"], ESTREF0003: ["Rejected", "neutral"], ESTREF0004: ["Rebilled", "ok"] });

  async function scan(inputs = "") {
    const btn = el("rbx-detect-btn"), sbtn = el("rbx-find-btn");
    btn.disabled = sbtn.disabled = true; el("rbx-meta").textContent = inputs ? "Searching…" : "Scanning…";
    try {
      const d = await api("/api/rebilling-monitor/offline-error/detect", { method: "POST", body: { inputs } });
      S.searched = d.searched || [];
      if (S.searched.length) S.filter = null;   // a search shows every state of the values entered
      S.rows = d.rows || []; S.loaded = true;
      const ids = new Set(S.rows.filter(PRED.complete).map(key));
      [...S.sel].forEach((k) => { if (!ids.has(k)) S.sel.delete(k); });
      el("rbx-filter-row").hidden = false;
      sgStd.scanMeta(el("rbx-meta"), { rows: S.rows.length, elapsedMs: d.elapsed_ms,
        note: `${sgStd.scopeNote(S.searched)} · ${fmt(d.complete)} to fix (${fmt(d.by_case?.RCCS?.complete)} RCCS, ${fmt(d.by_case?.SR?.complete)} SR) of ${fmt(d.count)} pending End Massive Rebilling actions` });
      render();
    } catch (e) {
      el("rbx-meta").textContent = ""; showToast(e.message, true);
    } finally { btn.disabled = sbtn.disabled = false; }
  }

  async function generate() {
    const btn = el("rbx-gen-btn");
    btn.disabled = true;
    try {
      const d = await api("/api/rebilling-monitor/offline-error/generate", { method: "POST", body: {
        activity_ids: [...S.sel], user: el("rbx-user").value.trim(), program_rccs: el("rbx-prog-rccs").value.trim(),
        program_sr: el("rbx-prog-sr").value.trim(), clean: el("rbx-clean").checked } });
      panel.show(d.sql, d.rollback_sql);
      el("rbx-out-wrap").hidden = false;
      el("rbx-out-info").textContent = `${d.rccs} RCCS + ${d.sr} SR action(s) → Solved. Saved in History.` +
        (d.skipped?.length ? ` Skipped ${d.skipped.length} no longer in that state (re-read live): ${d.skipped.join(", ")}.` : "");
    } catch (e) { showToast(e.message, true); }
    finally { btn.disabled = S.sel.size === 0; }
  }

  function exportCsv() {
    const list = S.sel.size ? S.rows.filter(PRED.selected) : visible();
    const cols = ["case_type", "ref_code", "id_object", "id_account", "account_ref", "obj_status", "id_activity", "cod_step", "action_date_ts",
      "last_reb_id", "last_reb_status_en", "last_reb_updated", "last_reb_program", "last_reb_bills", "reb_ids", "stuck_days", "state_en"];
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = [cols.map((c) => c.toUpperCase()).join(","), ...list.map((r) => cols.map((c) => q(r[c])).join(","))].join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `rebilling_offline_task_error_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
  }

  const panel = sgStd.scriptPanel({ out: "rbx-out", rollback: "rbx-rb-out", copy: "rbx-copy-btn", download: "rbx-download-btn",
    rbCopy: "rbx-rb-copy-btn", rbDownload: "rbx-rb-download-btn", name: () => `REBILLING_OFFLINE_ERROR_${new Date().toISOString().slice(0, 10)}` });

  el("rbx-table").addEventListener("change", (e) => {
    if (e.target.id === "rbx-check-all") {
      visible().filter(PRED.complete).forEach((r) => (e.target.checked ? S.sel.add(key(r)) : S.sel.delete(key(r))));
    } else {
      const cb = e.target.closest("[data-rbx-id]"); if (!cb) return;
      cb.checked ? S.sel.add(cb.dataset.rbxId) : S.sel.delete(cb.dataset.rbxId);
    }
    render();
  });
  el("rbx-detect-btn").addEventListener("click", () => scan(""));
  // RJ 2026-11-26: Search screen (Search | Detect all)
  sgStd.find(document.querySelector('[data-sg-find="rbx"]'), { onSearch: (t) => scan(t) });
  el("rbx-gen-btn").addEventListener("click", generate);
  el("rbx-export-btn").addEventListener("click", exportCsv);
  sgStd.bindFilter(el("rbx-search"), el("rbx-search-count"), render);
  el("rbx-case").addEventListener("change", render);
  el("rbx-clear").addEventListener("click", () => { el("rbx-search").value = ""; el("rbx-search-count").textContent = ""; el("rbx-case").value = ""; S.filter = null; render(); });
  // first time the tab is opened -> scan
  document.querySelector('.da-subnav-btn[data-rbm-sub="offlineerr"]')?.addEventListener("click", () => { if (!S.loaded) scan(""); });

  window.rbmOffline = { scan, state: S };
})();
