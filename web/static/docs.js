/* =====================================================================
   ScriptGen - Documentation page renderer (RJ, 2026-10-02).
   Content lives in docs-content.js (guide + tech) and docs-queries.js
   (query library); this file only renders it. Loaded after app.js, so it
   reuses $, $$, escapeHtml, showToast and biss2CopyAccount from there.
   ===================================================================== */
(function () {
  const D = window.SG_DOCS || { guide: [], tech: [], queries: [] };
  let sub = "guide";
  let rendered = false;

  const esc = (s) => escapeHtml(String(s ?? ""));

  // --- tiny T-SQL highlighter (escape first, then wrap tokens) ---------
  const KW = ["SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "EXISTS", "JOIN", "LEFT", "INNER", "OUTER", "CROSS", "APPLY",
    "ON", "AS", "WITH", "NOLOCK", "GROUP", "BY", "ORDER", "HAVING", "UNION", "ALL", "DISTINCT", "TOP", "CASE", "WHEN", "THEN",
    "ELSE", "END", "IS", "NULL", "BETWEEN", "ASC", "DESC", "OVER", "PARTITION", "UPDATE", "SET", "DELETE", "INSERT", "INTO",
    "VALUES", "FOR", "XML", "PATH", "TYPE", "LIKE"];
  const FN = ["COUNT", "SUM", "MIN", "MAX", "ISNULL", "COALESCE", "CAST", "DATEADD", "DATEDIFF", "GETDATE", "ROW_NUMBER",
    "STRING_AGG", "ABS", "REPLACE", "NCHAR", "STUFF", "YEAR", "CONCAT", "CONVERT"];
  const kwRe = new RegExp(`\\b(${KW.join("|")})\\b`, "g");
  const fnRe = new RegExp(`\\b(${FN.join("|")})(?=\\s*\\()`, "g");
  function highlightSql(sql) {
    return sql.split("\n").map((line) => {
      const ci = line.indexOf("--");
      let code = ci >= 0 ? line.slice(0, ci) : line;
      const comment = ci >= 0 ? line.slice(ci) : "";
      // split on string literals so keywords inside strings stay plain
      code = code.split(/('(?:[^']|'')*')/).map((part, i) => {
        if (i % 2 === 1) return `<span class="sql-str">${esc(part)}</span>`;
        return esc(part)
          .replace(kwRe, '<span class="sql-kw">$1</span>')
          .replace(fnRe, '<span class="sql-fn">$1</span>')
          .replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="sql-num">$1</span>')
          .replace(/(@[A-Z_]+)/g, '<span class="sql-param">$1</span>');
      }).join("");
      return code + (comment ? `<span class="sql-com">${esc(comment)}</span>` : "");
    }).join("\n");
  }

  // --- search ------------------------------------------------------------
  function q() { return ($("#docs-search")?.value || "").trim().toLowerCase(); }
  function hay(item) {
    return JSON.stringify(item).toLowerCase();
  }
  function filtered(list) {
    const s = q();
    if (!s) return list;
    const terms = s.split(/\s+/).filter(Boolean);
    return list.filter((it) => { const h = hay(it); return terms.every((t) => h.includes(t)); });
  }

  // --- renderers ---------------------------------------------------------
  function renderGuide(items) {
    return items.map((g) => `
      <article class="card docs-card" id="docs-${esc(g.id)}">
        <div class="docs-card-head">
          <h2><span class="docs-icon" aria-hidden="true">${esc(g.icon)}</span>${esc(g.title)}</h2>
          ${document.querySelector(`.nav-item[data-page="${g.id}"]`) && g.id !== "documentation"
            ? `<button type="button" class="btn btn-pill-sm" data-docs-open="${esc(g.id)}">Open menu →</button>` : ""}
        </div>
        <p class="docs-lead">${esc(g.summary)}</p>
        ${g.steps?.length ? `<h3>How to use</h3><ol class="docs-steps">${g.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : ""}
        ${g.tips?.length ? `<div class="docs-tip"><strong>Tip</strong> ${g.tips.map(esc).join("<br>")}</div>` : ""}
      </article>`).join("");
  }

  function renderTech(items) {
    return items.map((t) => `
      <article class="card docs-card docs-prose" id="docs-${esc(t.id)}">
        <h2>${esc(t.title)}</h2>
        ${t.html}
      </article>`).join("");
  }

  function renderExample(ex) {
    if (!ex) return "";
    const table = ex.rows?.length
      ? `<div class="grid-wrap docs-example-grid"><table class="data-grid"><thead><tr>${ex.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
         <tbody>${ex.rows.map((r) => `<tr>${r.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
      : `<p class="hint-text">Columns: ${ex.columns.map((c) => `<code>${esc(c)}</code>`).join(" ")}</p>`;
    return `<h3>Example</h3>
      <p class="docs-example-input"><strong>Input:</strong> ${esc(ex.input)}</p>
      ${table}
      ${ex.note ? `<p class="hint-text docs-example-note">${esc(ex.note)}</p>` : ""}`;
  }

  function renderQueries(items) {
    const byMenu = new Map();
    items.forEach((x) => { if (!byMenu.has(x.menu)) byMenu.set(x.menu, []); byMenu.get(x.menu).push(x); });
    return [...byMenu.entries()].map(([menu, list]) => `
      <h2 class="docs-group-title">${esc(menu)}</h2>
      ${list.map((x) => `
      <article class="card docs-card docs-query" id="docs-${esc(x.id)}">
        <div class="docs-card-head">
          <h2>${esc(x.title)}</h2>
          <span class="hx-pill hx-pill-gray">${esc(x.menu)}</span>
        </div>
        <p class="docs-lead">${esc(x.purpose)}</p>
        <p class="hint-text docs-source">Source: <code>${esc(x.source)}</code></p>
        ${x.params?.length ? `<h3>Parameters</h3>
          <table class="docs-table"><thead><tr><th>Parameter</th><th>Meaning</th><th>Default</th></tr></thead>
          <tbody>${x.params.map((p) => `<tr><td><code>${esc(p[0])}</code></td><td>${esc(p[1])}</td><td><code>${esc(p[2])}</code></td></tr>`).join("")}</tbody></table>` : ""}
        <div class="docs-sql-head">
          <h3>SQL</h3>
          <div class="docs-sql-actions">
            <button type="button" class="btn btn-pill-sm" data-docs-copy="${esc(x.id)}" title="Copy the SQL">📋 Copy</button>
            <button type="button" class="btn btn-pill-sm" data-docs-workspace="${esc(x.id)}" title="Put this SQL in the Workspace editor">↗ Open in Workspace</button>
            <button type="button" class="btn btn-pill-sm" data-docs-download="${esc(x.id)}" title="Download as .sql">⬇ .sql</button>
          </div>
        </div>
        <pre class="docs-sql"><code>${highlightSql(x.sql)}</code></pre>
        ${renderExample(x.example)}
      </article>`).join("")}`).join("");
  }

  function renderToc(items, label) {
    const toc = $("#docs-toc");
    if (!toc) return;
    toc.innerHTML = `<div class="docs-toc-title">${esc(label)}</div>` + (items.length
      ? `<ul>${items.map((i) => `<li><a href="#docs-${esc(i.id)}" data-docs-jump="${esc(i.id)}">${esc(i.icon ? i.icon + " " : "")}${esc(i.title)}</a></li>`).join("")}</ul>`
      : `<p class="hint-text">No match.</p>`);
  }

  function render() {
    rendered = true;
    const lists = { guide: D.guide || [], tech: D.tech || [], queries: D.queries || [] };
    const counts = Object.fromEntries(Object.entries(lists).map(([k, v]) => [k, filtered(v).length]));
    const items = filtered(lists[sub]);
    const body = $("#docs-body");
    const labels = { guide: "User guide", tech: "Technical specifications", queries: "Query library" };
    let html = sub === "guide" ? renderGuide(items) : sub === "tech" ? renderTech(items) : renderQueries(items);
    if (!items.length) {
      const elsewhere = Object.entries(counts).filter(([k, n]) => k !== sub && n).map(([k, n]) => `${labels[k]} (${n})`);
      html = `<div class="card docs-card"><p>No match for “${esc(q())}” in ${labels[sub]}.${elsewhere.length ? ` Found in: ${elsewhere.join(", ")}.` : ""}</p></div>`;
    }
    body.innerHTML = html;
    renderToc(items, labels[sub]);
    const qc = $("#docs-query-count");
    if (qc) { qc.textContent = String(counts.queries); qc.classList.toggle("is-zero", !counts.queries); }
  }

  function findQuery(id) { return (D.queries || []).find((x) => x.id === id); }

  // --- events ------------------------------------------------------------
  document.querySelector('.nav-item[data-page="documentation"]')?.addEventListener("click", () => { if (!rendered) render(); });
  $$(".da-subnav-btn[data-docs-sub]").forEach((btn) => btn.addEventListener("click", () => {
    sub = btn.dataset.docsSub;
    $$(".da-subnav-btn[data-docs-sub]").forEach((b) => b.classList.toggle("is-active", b === btn));
    render();
    $(".content")?.scrollTo({ top: 0 });
  }));
  let t = null;
  $("#docs-search")?.addEventListener("input", () => { clearTimeout(t); t = setTimeout(render, 120); });

  $("#docs-toc")?.addEventListener("click", (ev) => {
    const a = ev.target.closest("[data-docs-jump]");
    if (!a) return;
    ev.preventDefault();
    document.getElementById(`docs-${a.dataset.docsJump}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("#docs-body")?.addEventListener("click", (ev) => {
    const open = ev.target.closest("[data-docs-open]");
    if (open) { document.querySelector(`.nav-item[data-page="${open.dataset.docsOpen}"]`)?.click(); return; }
    const cp = ev.target.closest("[data-docs-copy]");
    if (cp) { const x = findQuery(cp.dataset.docsCopy); if (x) { biss2CopyAccount(x.sql, cp); showToast("SQL copied."); } return; }
    const ws = ev.target.closest("[data-docs-workspace]");
    if (ws) {
      const x = findQuery(ws.dataset.docsWorkspace);
      const ed = $("#sql-editor");
      if (x && ed) {
        ed.value = x.sql;
        ed.dispatchEvent(new Event("input", { bubbles: true }));
        document.querySelector('.nav-item[data-page="workspace"]')?.click();
        showToast("Query placed in Workspace - replace the @PARAMETERS / sample values before running.");
      }
      return;
    }
    const dl = ev.target.closest("[data-docs-download]");
    if (dl) {
      const x = findQuery(dl.dataset.docsDownload);
      if (!x) return;
      const header = `-- ScriptGen query library: ${x.menu} - ${x.title}\n-- Source: ${x.source}\n\n`;
      const blob = new Blob([header + x.sql + "\n"], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `scriptgen_${x.id}.sql`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    }
  });

  // Render immediately if the page is already the active one (e.g. reload).
  if ($("#page-documentation")?.classList.contains("is-active")) render();
  window.docsRender = render;
})();
