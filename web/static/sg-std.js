/* =====================================================================
   ScriptGen - page standard kit (RJ 2026-11-19: "suggest for me to better organize our script gen, the
   menus and the contents, maybe we can make something standard across the application" -> "ok proceed").

   Every page follows the same 5 blocks (docs/UI_STANDARD.md):
     1 Header   - title, one-line purpose, "Last scan hh:mm · n s", Refresh, Query used
     2 Find     - input(s) and/or "Detect all"; Enter = scan
     3 Summary  - 3-5 KPI cards (click = filter) + filters
     4 Results  - one table: checkbox column, sticky header, status pills, Export CSV
     5 Fix      - "What this does", Jira / user, Clean, Generate (n) -> Script | Rollback, Copy / Download
   This file holds the shared pieces so a page only declares its own data. Loaded after app.js;
   uses escapeHtml, showToast, api (app.js). Pages migrate to it one by one (pilot: Double ITBs).
   ===================================================================== */
(function () {
  const esc = (s) => escapeHtml(String(s ?? ""));
  const fmt = (n) => (n === null || n === undefined || n === "" ? "-" : Number(n).toLocaleString());

  /** Block 1: "Last scan 10:42 · 6.1 s · 1,262 rows" line. */
  function scanMeta(el, { rows, elapsedMs, note } = {}) {
    if (!el) return;
    const t = new Date();
    el.textContent = [`Last scan ${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`,
      elapsedMs != null ? `${(elapsedMs / 1000).toFixed(1)} s` : "",
      rows != null ? `${fmt(rows)} row(s)` : "", note || ""].filter(Boolean).join(" · ");
  }

  /** Block 3: KPI cards. cards = [{key, label, value, sub, tone}] (tone = ok|warn|bad|info|neutral).
   *  onPick(key|null) is called on click (click again = clear). Returns nothing; re-call to refresh. */
  function kpis(el, cards, { active = null, onPick = null } = {}) {
    if (!el) return;
    el.classList.add("sg-kpis");
    el.innerHTML = cards.map((c) =>
      `<button type="button" class="sg-kpi${active && active === c.key ? " is-active" : ""}" data-tone="${esc(c.tone || "neutral")}"` +
      ` data-sg-kpi="${esc(c.key || "")}"${c.key && onPick ? "" : " disabled"} title="${esc(c.tip || c.label)}">` +
      `<span class="sg-kpi-label">${esc(c.label)}</span><span class="sg-kpi-value">${typeof c.value === "number" ? fmt(c.value) : esc(c.value)}</span>` +
      (c.sub ? `<span class="sg-kpi-sub">${esc(c.sub)}</span>` : "") + `</button>`).join("");
    if (onPick && !el.dataset.sgBound) {
      el.dataset.sgBound = "1";
      el.addEventListener("click", (e) => {
        const b = e.target.closest("[data-sg-kpi]"); if (!b || b.disabled) return;
        onPick(b.dataset.sgKpi || null);
      });
    }
  }

  /** Status pill: code -> coloured pill with its meaning ("Anomalous (STTOBILL00)"). */
  const STATUS = {
    STTOBILL00: ["Anomalous", "bad"], STTOBILL01: ["Pending", "warn"], STTOBILL04: ["Cancelled", "neutral"], STTOBILL07: ["Billed", "ok"],
    "1000STSRED": ["Available", "info"], "4000STSRED": ["Calculated usage", "warn"], "6000STSRED": ["Sent to bill", "info"],
    "7000STSRED": ["Billed", "ok"], "7001STSRED": ["UAU - Billed", "bad"], "8000STSRED": ["Terminated not billed", "neutral"],
    "1000STAMPO": ["Connected", "ok"], "3000STAMPO": ["Inactive", "bad"], "2000STAMPO": ["Disconnected", "neutral"],
    ESTAN00001: ["Pending", "warn"], ESTAN00005: ["Solved after review", "ok"], ESTAN00009: ["Pending after batch", "warn"],
  };
  function pill(code, label) {
    const [txt, tone] = STATUS[code] || [label || code, "neutral"];
    return `<span class="sg-pill" data-tone="${tone}" title="${esc(code)}">${esc(txt)}</span>`;
  }

  /** Block 5: wires a script panel. ids = {out, rollback, copy, download, rbCopy, rbDownload, name}. */
  function scriptPanel(ids) {
    const $id = (k) => ids[k] && document.getElementById(ids[k]);
    const dl = (text, name) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([text || ""], { type: "text/plain" }));
      a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    };
    const copy = async (el, what) => {
      try { await navigator.clipboard.writeText(el?.textContent || ""); showToast(`${what} copied.`); }
      catch (_) { showToast("Couldn't copy - select and copy manually.", true); }
    };
    $id("copy")?.addEventListener("click", () => copy($id("out"), "Script"));
    $id("download")?.addEventListener("click", () => dl($id("out")?.textContent, `${ids.name?.() || "script"}.sql`));
    $id("rbCopy")?.addEventListener("click", () => copy($id("rollback"), "Rollback"));
    $id("rbDownload")?.addEventListener("click", () => dl($id("rollback")?.textContent, `${ids.name?.() || "script"}_ROLLBACK.sql`));
    return {
      show(sql, rollback) {
        if ($id("out")) $id("out").textContent = sql || "";
        if ($id("rollback")) { $id("rollback").textContent = rollback || ""; const w = $id("rollback").closest("details"); if (w) w.hidden = !rollback; }
      },
    };
  }

  /** Block 4: selection set helper bound to a table (checkbox column + header select-all). */
  function selection(table, { rowAttr = "data-sg-id", allId, onChange } = {}) {
    const sel = new Set();
    table.addEventListener("change", (e) => {
      if (allId && e.target.id === allId) {
        table.querySelectorAll(`[${rowAttr}]`).forEach((cb) => { cb.checked = e.target.checked; if (cb.checked) sel.add(cb.getAttribute(rowAttr)); else sel.delete(cb.getAttribute(rowAttr)); });
        onChange?.(sel); return;
      }
      const cb = e.target.closest(`[${rowAttr}]`); if (!cb) return;
      if (cb.checked) sel.add(cb.getAttribute(rowAttr)); else sel.delete(cb.getAttribute(rowAttr));
      onChange?.(sel);
    });
    return sel;
  }

  window.sgStd = { scanMeta, kpis, pill, scriptPanel, selection, STATUS };
})();
