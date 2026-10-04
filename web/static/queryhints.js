/* =====================================================================
   ScriptGen - "How this works" query blocks (RJ 2026-10-04): "for all the
   menu and cases, in the how it works, add the explanation on the query
   and a way to copy the query".

   Reuses the Query library (docs-queries.js, SG_DOCS.queries) so there is
   one hand-written copy of each query. For every menu / case below, the
   block goes into that section's existing "ⓘ How this works" panel (made
   by app.js hxCollapseInfo); a section without one gets a new panel.
   Loaded after app.js and docs-queries.js.
   ===================================================================== */
(function () {
  const Q = Object.fromEntries(((window.SG_DOCS && SG_DOCS.queries) || []).map((q) => [q.id, q]));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // section selector -> query ids (in display order)
  const MAP = [
    ['#page-wrongbill .da-subpage[data-wb-sub="case1"]', ["wb-case1"]],
    ['#page-wrongbill .da-subpage[data-wb-sub="case2"]', ["wb-case2"]],
    ['#page-wrongbill .da-subpage[data-wb-sub="case3"]', ["wb-case3"]],
    ['#page-wrongbill .da-subpage[data-wb-sub="case4"]', ["wb-case4"]],
    ['#page-billissuance .da-subpage[data-biss-sub="case1"]', ["biss-case1", "biss-ncm"]],
    ['#page-billissuance .da-subpage[data-biss-sub="case2"]', ["biss-case2"]],
    ['#page-billissuance .da-subpage[data-biss-sub="case3"]', ["biss-case3"]],
    ['#page-billissuance .da-subpage[data-biss-sub="case4"]', ["biss-case4"]],
    ['#page-dateanomaly .da-subpage[data-da-sub="single"]', ["da-detect"]],
    ['#page-dateanomaly .da-subpage[data-da-sub="detectall"]', ["da-detect-all"]],
    ['#page-wrongstuckhierarchy .da-subpage[data-wsh-sub="primary"]', ["wrong-stuck"]],
    ['#page-wrongstuckhierarchy .da-subpage[data-wsh-sub="sanitary"]', ["wrong-stuck-san"]],
    ["#page-anomalystats", ["as-billing", "as-reading"]],
    ["#page-tnbcycledisc", ["tnb-cycle-disc"]],
    ["#page-disconnectiontnb", ["disc-tnb"]],
    ["#page-doubleitb", ["double-itb"]],
    ["#page-hierarchy", ["hier-pending"]],
    ["#page-readingvalidation", ["rv-readings"]],
    ["#page-incorrectbillingperiod", ["ibp-detect"]],
    ["#page-wrongbilledconsumption", ["wbc"]],
    ["#page-bulkchecker", ["bulk-pending"]],
  ];

  function block(q) {
    const params = (q.params || []).length
      ? `<table class="sg-qhint-params"><tr><th>Parameter</th><th>Meaning</th><th>Default</th></tr>${q.params.map((p) => `<tr><td>${esc(p[0])}</td><td>${esc(p[1])}</td><td class="hx-mono">${esc(p[2])}</td></tr>`).join("")}</table>`
      : "";
    return `<div class="sg-qhint" data-qid="${esc(q.id)}">
      <div class="sg-qhint-head">
        <span class="sg-qhint-title">🧾 Query: ${esc(q.title)}</span>
        <span class="sg-qhint-actions">
          <button type="button" class="btn btn-pill-sm" data-qh-toggle>Show SQL</button>
          <button type="button" class="btn btn-pill-sm" data-qh-copy>📋 Copy query</button>
        </span>
      </div>
      <p class="sg-qhint-purpose">${esc(q.purpose)}</p>
      ${params}
      ${q.example?.note ? `<div class="hint-text">${esc(q.example.note)}</div>` : ""}
      <div class="sg-qhint-src">Source: ${esc(q.source || "")}</div>
      <pre class="sg-qhint-sql" hidden>${esc(q.sql)}</pre>
    </div>`;
  }

  function hostPanel(host) {
    // the section's own "How this works" panel (not one inside a nested sub-page)
    const own = [...host.querySelectorAll("details.hx-hint")].find((d) =>
      /How this works/i.test(d.querySelector("summary")?.textContent || "") &&
      (d.closest(".da-subpage") === host || (!host.classList.contains("da-subpage") && !d.closest(".da-subpage"))));
    if (own) return own;
    const d = document.createElement("details");
    d.className = "hx-hint";
    d.innerHTML = "<summary>ⓘ How this works</summary>";
    const header = host.querySelector(":scope > header.page-header");
    if (header) header.insertAdjacentElement("afterend", d);
    else host.insertAdjacentElement("afterbegin", d);
    return d;
  }

  function install() {
    MAP.forEach(([sel, ids]) => {
      const host = document.querySelector(sel);
      if (!host) return;
      const qs = ids.map((id) => Q[id]).filter(Boolean);
      if (!qs.length) return;
      const panel = hostPanel(host);
      if (panel.querySelector(".sg-qhint")) return;
      panel.insertAdjacentHTML("beforeend", qs.map(block).join(""));
    });
  }

  async function copy(text, btn) {
    try { await navigator.clipboard.writeText(text); }
    catch (_) {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } finally { ta.remove(); }
    }
    if (typeof showToast === "function") showToast("Query copied to clipboard.");
    if (btn) { const t = btn.textContent; btn.textContent = "✓ Copied"; setTimeout(() => { btn.textContent = t; }, 1500); }
  }

  document.addEventListener("click", (e) => {
    const box = e.target.closest(".sg-qhint");
    if (!box) return;
    const q = Q[box.dataset.qid];
    if (e.target.closest("[data-qh-copy]")) { copy(q?.sql || "", e.target.closest("button")); return; }
    if (e.target.closest("[data-qh-toggle]")) {
      const pre = box.querySelector(".sg-qhint-sql");
      pre.hidden = !pre.hidden;
      e.target.closest("button").textContent = pre.hidden ? "Show SQL" : "Hide SQL";
    }
  });

  try { install(); } catch (err) { console.error("queryhints", err); }
})();
