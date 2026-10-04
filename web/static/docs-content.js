/* =====================================================================
   ScriptGen - Documentation content (RJ, 2026-10-02: "add documentation
   menu, and technical specifications, make the main queries readily
   available in the documentation and do examples").

   Plain data, rendered by docs.js into the Documentation page:
     SG_DOCS.guide   - user guide, one entry per menu
     SG_DOCS.tech    - technical specification sections (HTML)
     SG_DOCS.queries - hand-written query library (RJ chose a hand-written
                       copy over live SQL) with parameters + examples.

   MAINTENANCE: the SQL below is a cleaned-up copy of what the app runs
   (comments removed, default parameter values filled in). When a query
   builder in app/core/*.py changes, update its entry here too - each
   entry's `source` names the builder. Example rows are real results from
   2026-10-02 with account numbers / NISS partly masked (•).
   ===================================================================== */
window.SG_DOCS = window.SG_DOCS || {};

/* ------------------------------------------------------------------ */
/* User guide                                                          */
/* ------------------------------------------------------------------ */
SG_DOCS.guide = [
  {
    id: "overview", icon: "🏠", title: "Overview",
    summary: "Home page. One card per menu/case with its live count, so you see at a glance where work is waiting.",
    steps: [
      "Opens automatically after sign-in; counts load in parallel (about 20-30 s for all cards).",
      "Critical cards (Wrong Bill cases) are shown first in red when their count is above 0.",
      "Click a card to jump straight to that menu and tab.",
    ],
    tips: ["Use Refresh (top right) to reload every count. A card showing \"!\" failed to load - open the menu to see the error."],
  },
  {
    id: "workspace", icon: "📝", title: "Workspace",
    summary: "Free SQL editor against the connected database (read-only login). Run a SELECT, edit values in the grid, and generate an UPDATE script from your edits.",
    steps: [
      "Type or paste a SELECT in the editor and press Run.",
      "Edit cells in the result grid; changed cells are highlighted.",
      "Pick the key column(s) and press Generate script - the UPDATE + rollback script opens in Script.",
      "AI Assist can suggest, explain or optimise the query (needs an AI key in Settings).",
    ],
    tips: ["Any query from the Documentation > Query library can be sent here with \"Open in Workspace\"."],
  },
  { id: "script", icon: "📄", title: "Script", summary: "The last generated script (UPDATE + rollback). Copy or download it as .sql and run it yourself in SSMS - ScriptGen never executes scripts.", steps: ["Review every statement before running it in your own SQL tool."], tips: [] },
  { id: "history", icon: "🕓", title: "History", summary: "Every script generated, with who/when/what, so a change can be traced or re-used.", steps: ["Toggle \"Mine only\" to see the whole team's history."], tips: [] },
  { id: "dashboard", icon: "📊", title: "Dashboard", summary: "Statistics, histograms and correlations for the last Workspace result.", steps: ["Run a query in Workspace first, then open Dashboard."], tips: [] },
  { id: "ai", icon: "🤖", title: "AI Assist", summary: "Write SQL from plain English, explain or optimise a query, or review a script.", steps: ["Requires an AI API key (Settings, admin only)."], tips: [] },
  {
    id: "dateanomaly", icon: "🩹", title: "DIFF DATES Anomaly",
    summary: "Fixes Diff Date System anomalies (GCCOM_ANOMALOUS types 201/202): readings stuck in Anomalous (6000STSRED) whose dates must follow the last correctly billed reading.",
    steps: [
      "Single NISS: enter a NISS, Detect, review the anomalous readings and the 'correct date' (latest billed 7000STSRED reading), then Resolve and Generate the correction script.",
      "Batch: paste many NISS; the job runs in the background with progress.",
      "Detect All: lists every open 201/202 anomaly system-wide (ALL_CYCLE, billing-period count, non-cycle reading types). Select rows and Generate to correct them in one batch.",
      "History: past analyses and generated scripts.",
    ],
    tips: ["Orange rows = the anomaly spans more than one billing period - check them before generating.", "Clean script option removes the explanatory comments."],
  },
  {
    id: "hierarchy", icon: "🗂️", title: "Hierarchy Analysis",
    summary: "Primary (main) measuring points with a pending Cycle/Distribution reading (Available / Anomalous / Sent to bill), oldest period first, with how many secondaries are still not sent.",
    steps: ["Press Scan; filter by billing period, period-billed flag or 'primary only'.", "Open a row's Hierarchy detail to see every secondary and its reading; 📖 shows the reading history."],
    tips: ["Green rows = every secondary is already sent to bill - only the primary is pending."],
  },
  {
    id: "readingvalidation", icon: "📏", title: "Reading Validation/Modif",
    summary: "Every reading of one supply (NISS or account) with the reading chain highlighted; edit values inline and generate an UPDATE + rollback script.",
    steps: ["Search by NISS or account (an account opens one tab per supply).", "Click a row to highlight the readings it references.", "Edit value / dates / status; usages recalculate; Generate Update Script."],
    tips: ["Three detail levels (important / medium / full) control how many columns show."],
  },
  { id: "tnbcycledisc", icon: "🔌", title: "TNB CYCLE/DISC Analysis", summary: "Supplies with a Cycle and a Disconnection reading on the same date (same usage type) where one of them is Terminated Not Billed and at least one has ready usage.", steps: ["Scan, then filter by billing period / NISS. 'In contract' shows if a non-cancelled contract covers the reading date."], tips: [] },
  { id: "wrongstuckhierarchy", icon: "🪜", title: "Wrong Stuck in Hierarchy ITB", summary: "Secondary items to bill held in hierarchy (STTOBILL09) although their primary's item in the same period is already billed (STTOBILL07). Second tab: Sanitary held while Water is done.", steps: ["Scan (all periods, or pick one).", "Select rows and Generate the STTOBILL09 → STTOBILL01 release script."], tips: [] },
  { id: "disconnectiontnb", icon: "⛔", title: "Disconnection TNB", summary: "Disconnection readings in Terminated Not Billed with ready usage ≠ 0, with the nearest contracted service (by end date) and days between.", steps: ["Scan; sort by DAYS_FROM_END to spot readings far from the contract end."], tips: [] },
  { id: "doubleitb", icon: "👯", title: "DOUBLE ITB", summary: "A billing service with two items to bill for the same INI/END dates: one Billed, one Anomalous. 'Needs rebilling' when the anomalous one carries ready usage.", steps: ["Scan; filter 'Needs rebilling'."], tips: [] },
  {
    id: "wrongbill", icon: "🚨", title: "Wrong Bill",
    summary: "Critical wrongly issued bills that need rebilling as early as possible. One tab per case, each with its count.",
    steps: [
      "Case 1 - Unusual high Sanitary: SANITARY bill > 14,158 and higher than the same account's Water bill (same period and billing date). Daily 7 AM e-mail alert (Settings > Email alerts).",
      "Case 2 - % Distribution with metered secondary: primaries on calculation module 1150 with a non-inactive secondary that has a current device (Main coupled excluded).",
      "Case 3 - Sanitary 0 with water consumption: charging-tariff sanitary bill with SANITARY = 0 while the Water bill has CONCSMO003 > 0. Scope by billing periods and/or creation dates.",
      "Case 4 - First bill regularized: a contracted service's first bill (LAST_BILLING_DATE = contract FROM_DATE) carrying a regularization concept ≠ 0, with the bills it regularized. Scope by periods or 📅 Last 7 days.",
    ],
    tips: ["Copy accounts / NISS buttons copy every visible row (one per line) for a bulk rebilling request."],
  },
  { id: "wrongbilledconsumption", icon: "⚖️", title: "Wrong Billed Consumption", summary: "Per bill of a period, compares 3 usage sources: calculation base (CONCSMO003/CC210), READINGS_ITEMSTOBILL ready usage and GCGT_RE_READING ready usage. Lists bills where any two differ.", steps: ["Pick a billing period and Scan - runs as a background job in chunks (a few minutes for a full period)."], tips: [] },
  {
    id: "billissuance", icon: "🧾", title: "Bill Issuance Validator",
    summary: "Accounts whose billing did not complete because a notice is pending validation (GCCOM_NOTICE_TMP 5000NOTEMP on an issuing bill). Four cases, each excluding accounts already explained by an earlier case.",
    steps: [
      "Case 1 - Prev Month Rate Only: the Rate (176) bill is the only cycle bill invoicing in its period and a later Electricity/Water bill waits (ESTFAC0015) - or the New Contract Match pattern. Generate Release Script releases the notice.",
      "Case 2 - Terminated Account Period Mismatch: every service terminated, but final bills sit in different periods. Generate moves them to the account's latest period.",
      "Case 3 - All Contract Status - Bills Complete: services count = invoicing cycle bills for the period (nothing missing).",
      "Case 4 - Unclassified: pending accounts not explained by Cases 1-3.",
    ],
    tips: [],
  },
  { id: "incorrectbillingperiod", icon: "📅", title: "Incorrect Billing Period", summary: "Open anomalies whose LAST_BILLING_DATE is after BILLING_DATE, grouped by offered service. Select and Generate the cancel script (ESTAN00005).", steps: [], tips: [] },
  { id: "bulkchecker", icon: "📋", title: "Bulk Checker", summary: "Bulk (grouped) accounts for a billing cycle: lot/file generated or pending, missing bills, bills still invoicing. Notes and saved searches are kept locally.", steps: ["Pick period + date range + status filter, Search; open an account for its bill detail."], tips: [] },
  {
    id: "anomalystats", icon: "📈", title: "Anomalies Statistics",
    summary: "Statistics of the OPEN anomalies. Billing anomalies = GCCOM_ANOMALOUS in Pending (ESTAN00001) or Pending after batch (ESTAN00009). Reading anomalies = GCGT_RE_ANOMALOUS in Pending to resolve (1000ANMSTA) - that table has no 'after batch' status.",
    steps: [
      "Pick the Billing or Reading tab (each shows its open count). Data loads once; Refresh reloads it from the database.",
      "KPIs: open total, split by status (billing) or severity (reading: Blocking / Stop billing / Warning), new in the last 7 days vs the previous 7, median and oldest age, share older than 30 days, types, accounts and supplies affected.",
      "Charts: detected per day (30 days) or per week (26 weeks), anomaly types ranking, status/severity, service, age since detection, billing period, category (billing) or reading type + group (reading), and a types × age heatmap.",
      "Click any bar, slice, heat cell, KPI or types-table row to filter the WHOLE page; the filters show as chips - click a chip (or Clear filters) to remove them.",
      "Reading tab: 'Main type' counts each anomaly once under its most severe detail; 'Every type' counts all its detail types (one reading anomaly can carry several).",
      "Records: the filtered list (sortable), Copy accounts, Export CSV (all filtered rows; reading export includes every type).",
    ],
    tips: ["Billing period comes from the anomaly's item to bill; most HOLDPREVBI anomalies have none and show as 'No item to bill'."],
  },
  { id: "documentation", icon: "📚", title: "Documentation", summary: "This page: user guide, technical specifications and the query library with examples.", steps: [], tips: [] },
  { id: "tools", icon: "🧰", title: "Tools", summary: "Utilities: snapshots of query results into the internal database, snapshot diff, schema validation.", steps: [], tips: [] },
  { id: "settings", icon: "⚙️", title: "Settings", summary: "Connections (with Test connection), users and roles, menu access per role, AI key, e-mail alerts.", steps: ["Admin only for connections, users, menu access and e-mail settings."], tips: [] },
];

/* ------------------------------------------------------------------ */
/* Technical specifications                                            */
/* ------------------------------------------------------------------ */
SG_DOCS.tech = [
  {
    id: "architecture", title: "1. Architecture",
    html: `
<p>ScriptGen is a single-process web application: a Python <strong>FastAPI</strong> server (served by <strong>uvicorn</strong>) and a static, framework-free HTML/CSS/JavaScript front-end. It analyses the billing database (MS SQL Server) and <strong>generates</strong> correction scripts; it never runs them.</p>
<table class="docs-table">
<thead><tr><th>Layer</th><th>Component</th><th>Responsibility</th></tr></thead>
<tbody>
<tr><td>Front-end</td><td><code>web/static/index.html</code>, <code>app.js</code>, <code>styles.css</code> + <code>polish.css</code>, <code>docs*.js</code></td><td>Pages, filters, KPIs, exports (CSV/XLSX), copy helpers. No build step.</td></tr>
<tr><td>Web / API</td><td><code>web/server.py</code></td><td>REST routes (<code>/api/...</code>), authentication, role checks, parallel query execution, background jobs.</td></tr>
<tr><td>Business logic</td><td><code>app/core/*.py</code></td><td>One module per menu/case. <em>Pure functions</em> that build SQL text and shape rows - no I/O, easy to review.</td></tr>
<tr><td>Data access</td><td><code>app/db/mssql.py</code></td><td>Connections (pytds, TDS protocol), query execution, timeouts, friendly errors.</td></tr>
<tr><td>Local storage</td><td><code>app/db/internal_store.py</code> (SQLite)</td><td>Script history, analysis history, snapshots, Bulk Checker notes, alert log.</td></tr>
<tr><td>Background</td><td><code>web/batch_jobs.py</code>, <code>web/wbc_jobs.py</code>, <code>web/alerts.py</code></td><td>DIFF DATES batch, Wrong Billed Consumption scan, 7 AM e-mail alert.</td></tr>
</tbody></table>
<p><strong>Request flow:</strong> browser → <code>/api/&lt;menu&gt;/detect</code> → <code>app/core</code> builds the SELECT → <code>mssql.run_query</code> executes it (read-only) → rows shaped to JSON → front-end renders table, KPIs and filters.</p>`,
  },
  {
    id: "stack", title: "2. Technology stack & runtime",
    html: `
<table class="docs-table"><tbody>
<tr><th>Language / server</th><td>Python 3, FastAPI, uvicorn</td></tr>
<tr><th>Database driver</th><td>python-tds (pytds) - pure Python, no ODBC driver to install</td></tr>
<tr><th>Target database</th><td>MS SQL Server (billing system), reached through the local tunnel <code>localhost:1433</code></td></tr>
<tr><th>Internal database</th><td>SQLite (standard library)</td></tr>
<tr><th>Front-end</th><td>Vanilla JavaScript + CSS, no framework, no CDN dependency (works offline / on the LAN)</td></tr>
<tr><th>Start</th><td><code>python run_web.py</code> → <code>http://127.0.0.1:8420/</code>; listens on <code>0.0.0.0:8420</code> so LAN colleagues can connect</td></tr>
<tr><th>Restarts</th><td>The server does <strong>not</strong> auto-reload. Python changes need a restart; HTML/CSS/JS changes only need Ctrl+F5.</td></tr>
</tbody></table>`,
  },
  {
    id: "security", title: "3. Security",
    html: `
<ul>
<li><strong>Read-only database login</strong> (<code>ouc_read_only</code>): the app can only SELECT. Every correction is delivered as a script for a person to review and run in their own tool.</li>
<li><strong>Users &amp; roles</strong>: <code>viewer</code> (read/scan), <code>editor</code> (+ generate scripts), <code>admin</code> (+ connections, users, menu access, e-mail). Passwords hashed with <strong>bcrypt</strong>; first login can force a password change.</li>
<li><strong>Menu access</strong> per role hides pages in the sidebar (convenience only - every API route still enforces its own role check).</li>
<li><strong>Secrets</strong>: DB / AI / SMTP passwords are Fernet-encrypted at rest in the config file, or supplied through environment variables <code>SCRIPTGEN_DB_PASSWORD</code>, <code>SCRIPTGEN_AI_API_KEY</code>, <code>SCRIPTGEN_SMTP_PASSWORD</code> (<code>.env</code>). Nothing secret is hard-coded.</li>
<li><strong>SQL safety</strong>: values are inlined only through <code>sql_format.format_sql_literal</code> (quotes escaped, numbers/dates typed) and ids are cast to integers before being placed in a query.</li>
<li>Generated scripts carry audit columns (<code>UPDATE_USER</code>, <code>UPDATE_PROGRAM</code>, <code>UPDATE_DATE = GETDATE()</code>) and a status guard in the WHERE clause, so re-running a script is a safe no-op.</li>
</ul>`,
  },
  {
    id: "performance", title: "4. Performance design",
    html: `
<ul>
<li><strong>READ UNCOMMITTED</strong> on every connection (= <code>WITH (NOLOCK)</code> on every table): analysis queries never wait behind, or block, the billing system's writes.</li>
<li><strong>Parallel queries</strong>: independent SELECTs of one screen run at the same time on separate connections (Bill Issuance cases, DIFF DATES detect, Wrong Bill Case 4 periods, Wrong Billed Consumption chunks).</li>
<li><strong>ID_BILL windows</strong>: <code>GCCOM_BILL.CREATE_DATE</code> is not indexed and a period has ~1.7 M bills, so date and period scopes first find the <code>ID_BILL</code> range (primary-key probes) and then range-scan it - seconds instead of minutes.</li>
<li><strong>Chunked background jobs</strong> for whole-period scans that cannot fit the 120 s query timeout.</li>
<li><strong>Row caps</strong> (<code>TOP (N)</code>) on system-wide scans; the UI says when a result was capped.</li>
<li>Lookups in English through <code>GCTS_DICTIONARY</code> (<code>LOCALE = 'EN'</code>) with a fallback to the native description.</li>
</ul>`,
  },
  {
    id: "database", title: "5. Database reference",
    html: `
<p>Schemas: <code>OUC_COMMON_ADMIN</code> (bills, contracts, readings, measuring points) and <code>OUC_ADMIN</code> (items to bill, billing concepts, anomalies, notices, status tables). Unqualified names resolve through the login's default schema.</p>
<table class="docs-table">
<thead><tr><th>Table</th><th>Used for</th></tr></thead>
<tbody>
<tr><td><code>GCCOM_BILL</code></td><td>Bills: <code>ID_PAYMENT_FORM</code>, <code>ID_CONTRACTED_SERVICE</code>, <code>ID_BILLING_PERIOD</code>, <code>BILLING_DATE</code>, <code>LAST_BILLING_DATE</code>, <code>BILLING_STATUS</code>, <code>BILLING_TYPE</code>, <code>BILL_TYPE</code>, <code>TOTAL_AMOUNT</code>, <code>ID_REG_BILL</code></td></tr>
<tr><td><code>GCCOM_BILLING_CONCEPT</code></td><td>Bill lines: <code>COD_CONCEPT</code>, <code>CONCEPT_AMOUNT</code>, <code>ID_FARE</code></td></tr>
<tr><td><code>GCCOM_CONTRACTED_SERVICE</code></td><td>Services: <code>ID_OFFERED_SERVICE</code>, <code>STATUS</code>, <code>FROM_DATE</code>, <code>END_DATE</code>, <code>ID_SECTOR_SUPPLY</code></td></tr>
<tr><td><code>GCCOM_PAYMENT_FORM</code> / <code>GCCOM_SECTOR_SUPPLY</code></td><td>Account (<code>REFERENCE</code>) / supply (<code>NISS</code>)</td></tr>
<tr><td><code>GCCOM_ITEMS_TO_BILL</code>, <code>GCCOM_READINGS_ITEMSTOBILL</code></td><td>Items to bill and their readings (ready usage)</td></tr>
<tr><td><code>GCGT_RE_READING</code>, <code>GCGT_RE_MEASUREMENT_POINT</code></td><td>Readings and measuring points (hierarchy via <code>ID_MAIN_MP</code>)</td></tr>
<tr><td><code>GCCOM_ANOMALOUS</code>, <code>GCCOM_NOTICE_TMP</code></td><td>Billing anomalies, notices pending validation</td></tr>
</tbody></table>
<h4>Codes</h4>
<table class="docs-table"><thead><tr><th>Area</th><th>Code</th><th>Meaning</th></tr></thead><tbody>
<tr><td rowspan="6">Bill status</td><td><code>ESTFAC0005</code></td><td>Invoiced</td></tr>
<tr><td><code>ESTFAC0007</code></td><td>Cancelled</td></tr>
<tr><td><code>ESTFAC0012</code></td><td>Invoicing (in process)</td></tr>
<tr><td><code>ESTFAC0015</code></td><td>Waiting for other services</td></tr>
<tr><td><code>ESTFAC0042</code></td><td>Rebilled</td></tr>
<tr><td><code>ESTFAC0050</code></td><td>Rebilled for back-dated process</td></tr>
<tr><td rowspan="2">Billing type</td><td><code>TIPFAC0001</code></td><td>In cycle</td></tr>
<tr><td><code>TIPFAC0011</code></td><td>Credit note (always excluded)</td></tr>
<tr><td>Bill type</td><td><code>TFGEN00001</code></td><td>Cycle bill</td></tr>
<tr><td rowspan="5">Contract status</td><td><code>ESTSC00002</code></td><td>Active</td></tr>
<tr><td><code>ESTSC00003</code></td><td>Pending termination</td></tr>
<tr><td><code>ESTSC00004</code></td><td>Terminated</td></tr>
<tr><td><code>ESTSC00005</code></td><td>Cancelled</td></tr>
<tr><td><code>ESTSC00007</code></td><td>Suspended</td></tr>
<tr><td rowspan="4">Offered service</td><td><code>1</code></td><td>Electricity</td></tr>
<tr><td><code>19</code></td><td>Water</td></tr>
<tr><td><code>176</code></td><td>Rate</td></tr>
<tr><td><code>190</code></td><td>Sanitary</td></tr>
<tr><td rowspan="5">Read status</td><td><code>1000STSRED</code></td><td>Available</td></tr>
<tr><td><code>5000STSRED</code></td><td>Anomalous</td></tr>
<tr><td><code>6000STSRED</code></td><td>Sent to bill / stuck</td></tr>
<tr><td><code>7000STSRED</code></td><td>Billed</td></tr>
<tr><td><code>8000STSRED</code></td><td>Terminated Not Billed (TNB)</td></tr>
<tr><td rowspan="4">Reading type</td><td><code>TIPTL00003</code></td><td>Cycle</td></tr>
<tr><td><code>TIPTL00010</code></td><td>Disconnection</td></tr>
<tr><td><code>TIPTL00011</code></td><td>Reconnection</td></tr>
<tr><td><code>TIPTL00017</code></td><td>Distribution</td></tr>
<tr><td rowspan="4">Item to bill</td><td><code>STTOBILL00</code></td><td>Anomalous</td></tr>
<tr><td><code>STTOBILL01</code></td><td>Pending</td></tr>
<tr><td><code>STTOBILL07</code></td><td>Billed</td></tr>
<tr><td><code>STTOBILL09</code></td><td>Held in hierarchy</td></tr>
<tr><td rowspan="3">MP type / status</td><td><code>TIPEQM0002 / 0003 / 0005</code></td><td>Secondary / Main / Main coupled</td></tr>
<tr><td><code>3000STAMPO</code></td><td>Inactive measuring point</td></tr>
<tr><td><code>1150</code> (calc module)</td><td>% distribution primary/secondary</td></tr>
<tr><td>Notice</td><td><code>5000NOTEMP → 1000NOTEMP</code></td><td>For validation → released</td></tr>
</tbody></table>`,
  },
  {
    id: "conventions", title: "6. Conventions & good practice",
    html: `
<ul>
<li>Every detection excludes <strong>credit notes</strong> and <strong>Cancelled / Rebilled</strong> bills unless the case says otherwise.</li>
<li>Dates are compared as dates (<code>&gt;= day AND &lt; day + 1</code>), never with <code>CAST</code> on an indexed column.</li>
<li>Every output column has an explicit alias (rows are shaped by column name).</li>
<li>Correction scripts: one statement per table, <code>IN (...)</code> lists in chunks, audit columns, status guard, optional comment-free "clean" version.</li>
<li>Exports: CSV with UTF-8 BOM (opens correctly in Excel), "Export all" vs "Export selected".</li>
<li>UI: same layout on every menu - info panel, scope, command bar (Scan, summary, copy, export), KPI dashboard, filters, table.</li>
</ul>`,
  },
  {
    id: "operations", title: "7. Operations",
    html: `
<ul>
<li><strong>Configuration</strong>: Settings &gt; Connections (Test connection button), stored in the app's config file; internal SQLite database path shown there.</li>
<li><strong>E-mail alert</strong>: Wrong Bill Case 1 checks daily from 7 AM and retries until sent once that day (Settings &gt; Email alerts; SMTP password entered by the admin or via <code>SCRIPTGEN_SMTP_PASSWORD</code>).</li>
<li><strong>Timeouts</strong>: per-query timeout is set per connection in Settings (120 s on the current connection); scans that cannot fit run as background jobs instead.</li>
<li><strong>Logs / diagnostics</strong>: the sidebar footer shows the server start time and process id, to confirm a restart took effect.</li>
</ul>`,
  },
];
