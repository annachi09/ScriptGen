# ScriptGen

A tool that connects to a SQL Server database, lets you edit query results directly in a grid, and
generates a review-ready `UPDATE` script from whatever you changed. It can also snapshot query
results into a local SQLite database, and (optionally) call Google's free-tier Gemini API to
suggest or optimize SQL.

This tool never runs `UPDATE`/`INSERT`/`DELETE` against your source database itself — it only
*generates* the script for someone with write access to review and run. That fits the read-only
account (`ouc_read_only`) this was built around.

## Web + desktop, one codebase (current rework in progress)

ScriptGen is being moved from a Tkinter-only desktop app to a browser-based UI (FastAPI backend +
plain HTML/CSS/JS frontend, styled with a dark sidebar / light content look) that runs two ways
from the exact same code:

- **As a real website** — `uvicorn web.server:app`, reached over the network by you and a few
  teammates, each with their own login.
- **As the desktop app** — `python desktop_launcher.py` (what `ScriptGen.exe` runs once `build.bat`
  is updated to point at it) opens that same UI in a native window via `pywebview` — no browser
  chrome, launches like any other desktop app, but it's the identical frontend and backend.

Only the launch shell differs; the SQL-generation logic (`app/core/*`, unchanged) and the DB access
layer (`app/db/mssql.py`, unchanged) are shared by both. **This is a multi-phase rebuild, not a
one-shot rewrite** — here's where it actually stands:

**Done (Phase 1):** login (see "Web login accounts" below), the **Workspace** page (run a query,
edit the grid, key-column picker with Auto-detect, live SQL Preview, the WHERE-clause
original-value guard, the Format button), the **Script** page (Generate Update/Rollback Script,
Copy, Download .sql), and a **History** page (every script generated here or in the desktop app on
this machine — who/when/table/statement count, full script text on demand, with a "Show
everyone's" toggle so a teammate's generated script is one click away too).

**Done (Phase 2 — the web UI now has everything the desktop app has, except where noted):**
- **Settings** page — the web equivalent of the desktop Config dialog: add/edit/delete/test any
  number of named SQL Server connections and switch which one is active (the Environment switcher,
  folded in here rather than kept as a separate Tools-tab item), plus the Gemini model/API key/
  enabled toggle with its own Test Key button. The one-shared-connection limitation earlier phases
  had is gone — connections work exactly like the desktop app's multi-connection support now.
- **Tools** page — Saved Queries (save/load/delete, separate from the rolling Recent-queries menu),
  Validate Target Schema (query columns + key columns vs. the table's actual `INFORMATION_SCHEMA`
  columns), and the Snapshot Diff Viewer (export the current result to the internal SQLite db,
  then diff any two exported snapshots by key column).
- **AI Assist** page — Suggest / Optimize / Explain on the Workspace query (with an "Apply to
  Workspace query" button when a suggestion returns SQL), and a natural-language → WHERE clause
  builder. The Script page also got an **🤖 AI Review** button — a second opinion on the generated
  script itself, same as desktop.
- **Dashboard** page — modernized KPI cards (rows/columns/numeric/categorical/empty/NULL counts,
  plus **duplicate row count** and an overall **completeness %**), each with a colored top-accent
  bar and icon that follows the metric's meaning (blue=numeric, amber=categorical, red=empty/NULLs,
  purple=duplicates, green=completeness) rather than decorative rank. A **Data Quality** card
  surfaces plain-language observations synthesized from the stats already computed — likely key
  columns (100% unique), constant columns (1 distinct value), high-NULL columns (>30% NULL), and
  numeric columns with statistical outliers — as clickable-nothing, just-informative chips, no
  extra query. The per-column stats table gained **Median**, **Unique %**, and **Outliers**
  (values outside the Tukey IQR fence, Q1-1.5×IQR to Q3+1.5×IQR) columns, a visual completeness bar
  per row instead of a bare Non-null/Null pair, a kind badge (numeric/categorical/empty), and
  click-to-sort column headers (CSV export includes all the new fields too). Histogram, Correlation
  (now also labels the Pearson r as "strong/moderate/weak/negligible positive/negative", not just
  the bare number), a new **Quartiles** box-and-whisker chart (one row per numeric column, drawn
  from the SAME min/P25/median/P75/max/outlier-count stats the table already has — no extra
  request), and Trend History now sit in a responsive 2-column grid instead of one long stack — all
  still drawn with plain `<canvas>`, no charting library.
- A **light/dark theme toggle** (🌗 button above Sign out) for the web UI's content area, persisted
  per-browser — a feature the desktop app already had that didn't exist in Phase 1's web rebuild.
- **Error toasts render as a distinct notification popup, top-left** (`showToast(message, true)` in
  `web/static/app.js`) — different position, size, border-left accent, slide-in animation, longer
  on-screen time, and a ⚠️ prefix from a success toast, which still renders bottom-center as before.
  This applies everywhere `showToast` is called across the app, not just Diff Date Anomaly (where
  the request to change it came from).

**Deliberately deferred, not forgotten:** the desktop Dashboard's **Export Dashboard PDF** button
(bundles every chart into one multi-page PDF via matplotlib) has no web equivalent yet — the web
Dashboard's charts are plain canvas, not matplotlib figures, so reusing that exact code isn't a
small change; CSV export of the stats table covers the same data in the meantime. Excel export of
Workspace results (desktop has CSV *and* Excel; web has CSV only) is the other small gap. **The old
Tkinter desktop app (`app/ui/*`, `main.py`) still has all of this and keeps working exactly as
before** — it isn't being deleted, just superseded now that the web UI has reached near-parity.

166 backend unit/integration tests (see "Running the tests yourself") plus a full
login→run query→edit→Settings→Tools→AI Assist→Dashboard→theme-toggle walkthrough verified
end-to-end in a real headless browser (Playwright), console-error-free.

### Running the web version

```
pip install -r requirements.txt
uvicorn web.server:app --host 0.0.0.0 --port 8420
```

**Or, for local single-user use**: `python run_web.py` does the same thing but on a fixed local
port (127.0.0.1:8420) and automatically opens it in your default browser once the server is up -
no separate `uvicorn` command to remember, no manually typing the URL. Package it as a standalone
`ScriptGen-Web.exe` with `build_web.bat` (see that file's header for how it differs from
`build.bat` and `build_web_desktop.bat`, the other two packaging options for the Tkinter app and
the pywebview-native-window wrapper respectively).

**Restarting doesn't always restart.** `run_web.py` binds that FIXED port on purpose (so a
bookmark/shortcut keeps working across restarts) — but if port 8420 is already held by an earlier
ScriptGen process you forgot was still running (an old console window, a background process), a
new launch silently just opens a browser tab to that OLD process instead of starting a fresh one.
Every code change will look like it "didn't take" in that case, no matter how many times you
re-launch. To tell the two apart, both the **login screen** and the **sidebar** show a small
"Server started ..." line (from the unauthenticated `GET /api/server-info`) — if that timestamp
doesn't move after you restart, an old process is still running: close every ScriptGen/Python
console window (check the taskbar for ones you forgot about), or find and stop whatever is on the
port with `netstat -ano | findstr 8420` then `taskkill /PID <pid> /F`, and launch again.

First launch with no `data/web_users.json` yet creates one `admin` account with a random generated
password, printed to the console and written once to `data/web_admin_first_run.txt` (deleted
automatically after the first successful login — copy the password out before then). Add real
teammate accounts from a terminal on the machine running the server:

```
python -m web.manage_users add <username>          # prompts for a password and a role
python -m web.manage_users set-password <username>
python -m web.manage_users role <username> <admin|editor|viewer>
python -m web.manage_users deactivate <username>
python -m web.manage_users activate <username>
python -m web.manage_users remove <username>
python -m web.manage_users list
```

**Or from the browser**: any admin account can manage users from **Settings > Users** — add, change
role, deactivate/reactivate, reset password, or delete, without touching a terminal. Every account
has a role (least-privilege, enforced server-side on every request, not just hidden in the UI):

- **Viewer** — run queries, browse results/Dashboard/history, AI Explain. Can't generate or save
  any script, can't export snapshots.
- **Editor** (the default for new accounts) — everything Viewer can, plus generate Update/Rollback/
  Date-Anomaly-correction scripts, export snapshots, and the rest of AI Assist. Can't touch
  connections, AI settings, or other users' accounts.
- **Admin** — everything, plus manage DB connections, the AI (Gemini) key/model, and other users.

**Settings > Menu Access (admin only)** — a checkbox grid (page × role) controlling which sidebar
pages each role's nav shows at all, separate from the permission matrix above. Nothing changes until
an admin opens this panel and saves: with no saved config, every role sees every page, same as
before this existed. **This hides a nav button, it does not gate anything new** — every route behind
a hidden page still enforces the exact same `require_editor`/`require_admin` check it always did, so
hiding, say, Batch from Viewer is purely decluttering their sidebar, not a way to actually restrict
what Viewer could do if they hit that page's API directly (they already couldn't generate a script
there — see the role matrix above). Admin's own **Settings** checkbox is permanently locked checked
— there'd be no way back into this panel for anyone if an admin could hide it from admin. A change
takes effect for each affected user on their next login or page refresh, not live mid-session.
Backed by `web/menu_access.py` / `GET`+`PUT /api/config/menu-access`, stored in its own
`data/web_menu_access.json` (separate file from `web_users.json`, no CLI equivalent yet — browser
only).

A new account an admin creates starts with a temporary password and `must_change_password` set -
the person is walked through setting their own real password on first login rather than the admin
permanently knowing it. Deactivating (not deleting) is the normal way to revoke someone's access -
their username stays attached to whatever they generated in Script History; a real delete is still
available separately for cleaning up a genuine mistake. You can't deactivate, delete, or demote
yourself, and you can't deactivate, delete, or demote the last remaining active admin - see
`web/auth.py`'s module docstring for the full reasoning.

**Read this before putting it on a network reachable by others:** `--host 0.0.0.0` above makes it
reachable from other machines, but by default this serves plain **HTTP**, not HTTPS — login
passwords and the session cookie would cross the network unencrypted. Fine for `127.0.0.1`
(yourself only) or a fully trusted, already-encrypted network path (e.g. everyone's already on a
VPN); for anything else, put it behind a reverse proxy with a real TLS certificate (nginx/Caddy) or
tunnel it through the same kind of locked-down VPN the DB tunnel already uses, before handing the
URL to teammates. Also note that SQL Server connections (Settings page) are **server-side and
shared** — same `data/config.json` mechanism as the desktop app, same file on disk — so switching
the active connection in Settings changes it for everyone using this server, not just you; a web
login here only gates access to the *tool*, it does not carry its own per-person DB credentials.
See `web/auth.py`'s docstring for why that's a deliberate, different trust model from the desktop
app's one-person-one-machine one.

### Running the desktop version

```
pip install -r requirements.txt
python desktop_launcher.py
```

Opens the same UI in a native window (via `pywebview`, using the OS's built-in WebView2/Edge
runtime on Windows — nothing extra to install). **This needs to be verified on an actual Windows
machine** — same caveat this README has always had for the `.exe` build itself and for testing the
live DB tunnel: the sandbox this was built in has no Windows GUI to test a native webview window
against. If `pywebview`'s window doesn't open cleanly, running `uvicorn web.server:app` and opening
`http://127.0.0.1:<port>/` in a normal browser exercises the identical UI/backend as a fallback
while that gets sorted out.

## What's in this v1 (the Tkinter desktop app, `main.py`)

- Connect to SQL Server (tested against `localhost:1433` via your local tunnel), with support for
  **multiple named connections** (e.g. a local tunnel plus a staging/prod-readonly entry) that you
  can add, delete, and switch between from Settings or the Tools tab's Environment switcher.
- Run a SQL query, see results in an editable grid, with **SQL keyword/string/number/comment
  syntax highlighting** in the query editor as you type.
- Edit any cell, then generate an `UPDATE` script for just the rows/columns you changed, with a
  `BEGIN TRANSACTION` / commented `COMMIT`/`ROLLBACK` wrapper. Every statement also stamps three
  **audit columns**: a mandatory Jira/ticket number you enter (`update_program`), `update_date`
  (`GETDATE()`, computed when the script actually runs, not when it was generated), and
  `update_user` (`'RMA'`) — see "Generated script format" below.
- **Pick exactly which column(s) go in the `WHERE` clause** with a stack of toggle chips, in a
  resizable left-sidebar panel, built from the query's actual columns — no typing column names by
  hand. Primary key auto-detection (via `INFORMATION_SCHEMA`) pre-selects the right chip(s) when
  it can find one; a clear warning shows if nothing is selected (full-row match).
- **Edited cells are highlighted right in the grid** as you type — the specific field you changed
  gets an amber tint, and that row's number gets a blue tint, so you can see at a glance exactly
  what will end up in the generated script before you even open the Script tab. The generated
  script itself notes which column(s) changed on each `UPDATE`, e.g.
  `-- Row 3: changed column(s): amount, status`.
- **A live "🔗 SQL Preview" column sits right next to the results grid**, row-synced with it: the
  moment a row has an edit, that row's own `UPDATE` statement appears right beside it — no need to
  click Generate Update Script first just to see what a change will produce. It updates live as you
  edit, pick key column(s), or change Target schema/table/Jira #. This is a live preview, not the
  reviewable output — Generate Update Script (Script tab) is still the actual thing you copy/save
  to hand off, with the full transaction wrapper and per-column `-- was:` comments this preview
  skips for space.
- **Cancel** button next to Run Query for a long-running query — the app can't truly abort a query
  mid-flight, but Cancel invalidates it so a slow query's result can never land on top of a newer
  one you've since run.
- A **🛠 Tools** sidebar tab with six standalone tools: **Saved Queries** (named, kept until you
  delete them), **Validate Target Schema** (catches a typo'd column/table before the generated
  script does), **Rollback Script** (the exact reverse of the last Update Script), **Snapshot Diff
  Viewer** (added/removed/changed rows between two exported snapshots), **Script History** (every
  Update/Rollback script generated on this machine — desktop or web — with who/when/table/statement
  count and the full text on demand; see below), and the **Environment** switcher mentioned above.
  See "The Tools tab" below.
- A **🩹 DIFF DATES Anomaly** page (desktop tab and web page — same underlying logic in
  `app/core/date_anomaly.py`) for the "Diff Date System" correction workflow. The web page is split
  into a small top-of-page sub-nav — **Single NISS** (Detect/Resolve/Generate, items 1-3 below),
  **Batch**, **Detect All**, and **History** — rather than one long scroll of every function stacked
  on top of each other; each tab shows one function's cards at a time (desktop keeps its own
  existing single-scroll layout, unchanged).
  1. **Detect** — enter a NISS + **billing period floor** (only readings with
     `ID_BILLING_PERIOD` greater than this number are considered, in both the anomaly search and
     the "correct date" lookup below; leave it at `0` to consider every billing period for that
     NISS). Finds every reading stuck at `READ_STATUS = 6000STSRED` and the one correctly-billed
     (`7000STSRED`) reading above that floor to source the fix date from. Detect also runs a
     best-effort **account/offered-service/contract-status lookup** for the NISS
     (`date_anomaly.build_niss_account_query` — `GCCOM_SECTOR_SUPPLY` → `GCCOM_CONTRACTED_SERVICE`
     → `GCCOM_PAYMENT_FORM`, `TOP 1`), shown in the summary line and never blocking Detect if it
     can't resolve (e.g. no matching contracted-service row). Also records this NISS to
     **Analysis History** (see below) and shows a plain-language **case explanation** — what's
     wrong and, once known, what's being fixed. A separate **🤖 Analyze with AI** button (enabled
     once Detect has run) is an optional Gemini second opinion on top of that deterministic
     explanation — pulls the NISS/threshold/anomaly/resolve state straight from the server-side
     session, no re-entry needed (`POST /api/date-anomaly/explain-ai`). Its context now includes
     the account/offered-service/contract-status found above, plus a per-reading list (billing
     period, reading date, reading type — capped at 25 rows) instead of only aggregate counts, so
     the AI's response is grounded in this specific case rather than reading like a generic
     template. Detect also counts the **distinct `ID_BILLING_PERIOD` values** among this NISS's
     anomalous readings (`billing_period_count`); when that count is **more than 1**, the whole
     results table is highlighted orange (row background + a left accent bar) — a NISS whose
     anomalous readings span more than one billing period is worth a second look before
     correcting. The same highlighting and count apply to Detect All's and Batch's results tables
     too, computed the same way for each.
  2. **Resolve Bill Links** — maps each anomalous reading to its `GCCOM_ITEMS_TO_BILL` /
     `GCCOM_ITEMS_TO_BILL_XML` rows. A single reading can legitimately map to **more than one**
     item-to-bill row (e.g. separate energy/demand billing items generated off the same reading) —
     every item-to-bill id found is corrected, not just the first one seen for a reading.
  3. **Generate Correction Script** — produces a reviewable six-part script:
     1. `GCGT_RE_READING.READING_PREV_DATE` (always, per anomalous reading)
     2. `GCCOM_ITEMS_TO_BILL.INI_DATE` (note: no "T" — `INIT_DATE` doesn't exist on that table)
     3. `GCCOM_ITEMS_TO_BILL.STATUS`: advances any item-to-bill row still at `STTOBILL00` to
        `STTOBILL01` — its own statement, separate from the INI_DATE update, so INI_DATE always
        gets fixed unconditionally while the status transition only fires when the row is
        confirmed still pending (protects against reverting an item that's already moved further
        along in its billing lifecycle)
     4. the `initDate`/`readingFromDate` nodes inside `XML_TO_BILL`
     5. cancelling any OPEN `OUC_ADMIN.GCCOM_ANOMALOUS` record for an item-to-bill this correction
        touches, by setting `ANOMALOUS_STATUS = ESTAN00005` (cancelled) — filtered from
        `ANOMALOUS_STATUS IN (ESTAN00009, ESTAN00001)`. **Note:** `GCCOM_ANOMALOUS`'s status
        column is `ANOMALOUS_STATUS`, not `STATUS` — `STATUS` only exists on
        `GCCOM_ITEMS_TO_BILL`; confirmed against the real schema after a live "Invalid column
        name" error.
     6. **non-cycle reading cleanup** — for any anomalous reading whose `READING_TYPE` is the
        non-cycle `TIPTL00011` type (analyst-supplied rule, same "not TIPTL00003/TIPTL00005"
        non-cycle framing the Detect All `ALL_CYCLE` column already uses, narrowed to specifically
        `TIPTL00011`): a single `DELETE FROM OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL WHERE ID_READING
        IN (...)` plus a single `UPDATE GCGT_RE_READING SET READ_STATUS = '1000STSRED',
        IND_USAGE_TO_CAL = 0 WHERE ID_READING IN (...)`, covering every matching id in one
        DELETE/UPDATE pair (not one statement per id, unlike Parts 1-5) so the normal billing
        cycle can reprocess those readings from scratch. This is the one place in the whole app
        that emits a `DELETE` — every other correction here is a status-flip `UPDATE`, by design
        (see `app/core/date_anomaly.py`'s module docstring). Applies uniformly across Single NISS,
        Batch, and Detect All's Generate-for-Selected (which delegates to Batch) — see
        `date_anomaly.READING_TYPE_ORPHAN_USAGE` for the constant and full rationale.

     A part with nothing to do (e.g. no item still at `STTOBILL00`, or no `TIPTL00011` readings)
     simply contributes no
     statement — that's the normal case, not a warning. A **Clean script** checkbox strips every
     comment line (header, per-statement labels, the case explanation, footer reminders), leaving
     just the bare `UPDATE`/`DELETE` statements — same statement content either way, just
     without the annotations. There is deliberately no `BEGIN TRANSACTION` / `COMMIT`/`ROLLBACK`
     wrapper on this script (removed per explicit analyst request) — each statement runs and
     commits on its own, autocommit-style; wrap it yourself if you want transactional
     all-or-nothing behavior. Everything is logged to Script History like any other generated
     script (using whatever clean/non-clean text was actually generated).

     A **"Lowest billing period only (if multiple)"** checkbox sits next to Clean script on this
     card (and its Batch-tab / Detect-All-Generate-Cleanup-Script equivalents — see items 4 and 6
     below; all three ultimately drive the same `date_anomaly.lowest_billing_period_rows()`
     helper). When checked and a NISS's anomalous readings do span more than one
     `ID_BILLING_PERIOD`, the script is narrowed to correct only the reading(s) from the
     **earliest** of those billing periods — readings from later periods are left untouched
     entirely (no statement for them at all, not even a skipped/no-op one). `billing_period_count`
     in the response/summary still always reports the **full**, unfiltered count (so you can still
     see "this NISS actually had 3 periods" even though the script only touched 1) — filtering
     happens one level up, before the id lists ever reach `build_correction_script()`, which
     otherwise only sees flat reading ids and can't itself tell which period a given id came from.
     When checked but the NISS turns out to have only one billing period, the option is a no-op
     (nothing to narrow) and no note about it appears in the script. When it does narrow something,
     the header gains an explicit `-- SCOPED TO LOWEST BILLING PERIOD ONLY` comment naming how many
     of the total anomalous readings were included, so the narrowing is never silent even under
     Clean script's normal comment-stripping (Clean script still strips it like every other header
     line — the note is for the reviewable, non-clean version).
  4. **Batch / Multi-NISS Processing** (web only) — paste in a list of NISS (one per line or
     comma-separated) plus a shared billing-period floor, Jira/program #, and the same **Clean
     script** option, and it runs Detect → Resolve → Generate for every one of them **in the
     background**: `POST /api/date-anomaly/batch/start` returns a job id almost immediately (see
     `web/batch_jobs.py`), and the page polls `GET /api/date-anomaly/batch/{job_id}` every ~1.5s
     for progress instead of blocking on one long request — safe for a NISS list large enough to
     take minutes against a real tunnel-hopped DB. Each NISS succeeds or fails independently (no
     anomalies found, no correctly-billed reading to source the date from, or a connection error
     don't stop the rest of the batch); the per-NISS status table includes a **Non-cycle reset**
     column for Part 6's count and a **Billing periods** column (`billing_period_count`) — a row
     is highlighted orange, same as Single NISS, when that NISS's anomalous readings span more
     than one billing period. The same **"Lowest billing period only"** checkbox from Single NISS
     is available here too (`lowest_billing_period_only` on `POST /api/date-anomaly/batch/start`,
     threaded through `BatchJob`/`_process_one_niss` in `web/batch_jobs.py`) — applies
     independently per NISS in the run, so a batch can correctly narrow one multi-period NISS while
     leaving a single-period NISS in the same run untouched; the per-NISS status table marks a
     scoped row with a small 🔽 note next to its status when that NISS both had more than one
     billing period and was actually narrowed. The result is a per-NISS status table plus one combined,
     downloadable `.sql` file where each NISS keeps its own self-contained script (no
     `BEGIN TRANSACTION` / `COMMIT`/`ROLLBACK` wrapper — statements run and commit on their own,
     autocommit-style), so any one NISS can still be reviewed and run independently of the
     others (the clean option, when checked, strips the WHOLE combined document — including the
     per-NISS summary lines — not just each embedded script). **Copy/Download the combined script
     from two places**: the usual card-header pair (same spot as every other script-output card in
     this app) and a second identical pair right above the script itself — added because the header
     pair sits above the NISS list/results table, easy to lose track of on a long run; both stay
     **disabled with a "Run a batch first" tooltip** until a job actually has a script to hand back,
     rather than looking clickable with nothing behind them. The job keeps running even if you
     navigate away or close the tab — reopen the page and pick it from the **Recent runs** dropdown
     (`GET /api/date-anomaly/batch/jobs`, scoped to your own runs) to reattach, or **Cancel** an
     in-flight run (`POST /api/date-anomaly/batch/{job_id}/cancel` — stops before the next NISS,
     doesn't interrupt one already in flight). Requires the editor role or higher (same gate as the
     single-NISS Generate step); a job is visible only to whoever started it, or an admin. Jobs are
     in-memory only — a server restart loses any job history, same trade-off as the rest of this
     app's per-session state (see `web/session_store.py`). A **🤖 Analyze with AI** button next to
     Copy/Download (enabled once a job has at least one result) asks Gemini to summarize the run as
     a whole — how many NISS succeeded/failed and any pattern across the failures — since batch has
     no per-row selection the way Detect All does (`POST /api/date-anomaly/batch/{job_id}/explain-ai`).
  5. **Analysis History** — every NISS run through Detect (single-NISS or batch) is recorded to a
     local SQLite table (`app/db/date_anomaly_history.py`, same db file as Script History and
     snapshots), including ones that never made it to Generate (Detect found nothing, or the
     analyst stopped to review after Resolve). One row per analysis pass — recorded at Detect with
     whatever's known then, refined in place as Resolve/Generate add more (batch instead records
     once with everything already known, since it runs the whole pipeline in one uninterrupted
     pass). `GET /api/date-anomaly/history` (`mine_only=true` by default, same convention as
     `/api/history`) lists them; the web page's card 5 and the desktop tab's card 4 both show a
     refreshable table you can click/double-click into for the full case explanation.
  6. **Detect All Pending Anomalies** (web only, its own sub-nav tab) — a system-wide scan,
     independent of any one NISS: **Detect All** runs a lookup over every `OUC_ADMIN.GCCOM_ANOMALOUS`
     row whose `ID_PRINCIPAL_ANOMALY` is a "Diff Date System" type (`202` or `201`) and whose
     `ANOMALOUS_STATUS` is still open (`ESTAN00009`/`ESTAN00001`), ordered by `DETECTION_DATE`.
     Before the scan runs, the `ID_PRINCIPAL_ANOMALY` column name itself is checked against
     `INFORMATION_SCHEMA` — if it doesn't actually exist on `GCCOM_ANOMALOUS` you get a clear
     "column not found" error instead of a raw DB exception. The scan is also capped at the first
     1,000 open anomalies (`TOP` in the query — this is the one Date Anomaly query with no NISS to
     naturally scope it down); if the result hits that cap the summary line says so, since the list
     may not be complete.

     The results table shows **descriptions, not raw codes**: the anomaly type is looked up against
     `GCCOM_BILL_ANOMALY_COMPANY.DESCRIPTION` (plus its own short code, `ANOMALY_COD`, shown
     alongside it) and the anomaly status against `GCCOM_ANOMALOUS_STATUS.NAME_TYPE` — both keyed by
     the codes on `GCCOM_ANOMALOUS` (`ID_BILL_ANOM_COMP`/`COD_DEVELOP` respectively) and both LEFT
     JOINed, so a code with no matching lookup row still shows the raw code instead of a blank cell.
     Each row is also enriched with **Account** (`GCCOM_PAYMENT_FORM.REFERENCE`), **Supply/NISS**
     (`GCCOM_SECTOR_SUPPLY.NISS`), **Offered service** (`GCCOM_CONTRACTED_SERVICE.ID_OFFERED_SERVICE`)
     and **Contract status** (`GCCOM_CONTRACTED_SERVICE.STATUS`), via the join chain
     `GCCOM_BILLING_SERVICE → GCCOM_CONTRACTED_SERVICE → GCCOM_PAYMENT_FORM` /
     `GCCOM_SECTOR_SUPPLY`, matched on `GCCOM_BILLING_SERVICE.ID_BILLING_SERVICE = GCCOM_ANOMALOUS.
     ID_BILLING_SERVICE` (its own column — **not** the same as `ID_ITEM_TO_BILL`) — again LEFT
     JOINed, so a row whose billing-service chain doesn't resolve still surfaces with blank
     enrichment cells rather than disappearing from the list. An **All cycle?** column reports
     whether every reading linked to the item (via `GCCOM_READINGS_ITEMSTOBILL`) is a "cycle"
     reading type (`TIPTL00003`/`TIPTL00005`) — a correlated `EXISTS` check against
     `GCGT_RE_READING`, per the analyst's own supplied query. **Unverified against the live schema
     like the rest of this enrichment**: `GCCOM_ANOMALOUS` has no `ID_READING` of its own, so this
     reuses the same reading↔item-to-bill bridge table the rest of this module already relies on —
     see `date_anomaly.CYCLE_READING_TYPES`'s comment if a row's All Cycle value looks wrong. A
     **Billing periods** column reports the count of distinct `ID_BILLING_PERIOD` values among the
     item's linked readings (same `GCCOM_READINGS_ITEMSTOBILL`/`GCGT_RE_READING` join, a correlated
     `COUNT(DISTINCT ...)` instead of `EXISTS`) — a row with more than 1 is highlighted orange, same
     convention as Single NISS and Batch.

     **A mini dashboard sits above the results table**, reacting live to whatever's currently
     filtered/visible (not the raw unfiltered scan): a KPI row (rows shown, how many need a status
     advance, distinct offered services, distinct accounts) plus two small bar charts — **by
     offered service** and **by anomaly type** (top 8 each, the rest folded into an "Other" bar) —
     reusing the same canvas bar-chart helper as the Dashboard page's histogram, so this doesn't
     introduce a second charting library. Unlike that single-hue histogram, these two breakdown
     charts use **one color per category** (a fixed palette, with a light/dark-theme variant, and
     "Other"/"(none)" always rendered in neutral gray) plus a **legend** under each chart, since here
     color is standing in for a real distinct category rather than a continuous distribution —
     hovering a bar shows a tooltip with its exact count, and **clicking a bar (or its legend entry)
     filters the table to that value** and scrolls it into view; clicking "Other" or "(none)" instead
     shows a toast pointing at the dropdown filter, since those bars fold together multiple values
     with nothing single to filter to.

     A **filter row** above it narrows the table (and therefore the dashboard) by a free-text
     **Search** box (matches account, supply/NISS, or item-to-bill ID, live as you type), **offered
     service**, **anomaly type**, **needs status advance** (yes/no/all), **all cycle** (yes/no/
     all — a row whose ALL_CYCLE came back unknown/null matches neither "Yes only" nor "No only",
     same as an unresolved lookup value elsewhere on this page), and **multi billing period**
     (yes/no/all — "Multiple periods only" keeps rows with `billing_period_count > 1`, "Single
     period only" keeps rows with a count of `0`/`1`; a null/unknown count, like an unresolved All
     Cycle value, matches neither of the two narrowed options, only "all") — the last one is a
     quick way to isolate exactly the rows the orange highlighting is calling out, e.g. before
     checking them and using the **Lowest billing period only** option below when generating. A
     selection made before
     filtering stays selected even if a filter then hides that row (matching how most filter+select
     UIs behave) — **Select all** only ever affects the rows currently visible. The **"Needs status
     advance" KPI card is itself clickable** as a shortcut for that same filter — click it to jump
     straight to the "yes only" view (it highlights while active), click again to clear it. Filters
     and the dashboard are populated from the full scan result client-side (no extra server
     round-trip), so switching filters is instant. An **⬇ Export CSV** button next to Detect All
     downloads whatever's currently visible (i.e. respecting the active filters/search) as a CSV,
     mirroring the Dashboard page's own CSV export — built entirely client-side (`Blob`, temp
     `<a download>`), no server round-trip. A sibling **⬇ Export Excel** button downloads the same
     visible rows as a real `.xlsx` workbook instead: a browser can't write that binary format on
     its own, so this one round-trips through `POST /api/date-anomaly/detect-all/export-xlsx`,
     which builds the workbook server-side with `openpyxl` (already a project dependency —
     `requirements.txt` had it earmarked for exactly this: "`.xlsx` export for the results grid /
     Dashboard stats table") — bold header row, sized columns, one frozen header row, same 11
     columns as the CSV in the same order. Deliberately **not** a client-side JS Excel library:
     this app doesn't load any external CDN scripts (the breakdown charts above reuse the app's
     own canvas helper for the same reason), so adding one just for this one button would be a new
     dependency for a feature `openpyxl` already covers server-side. `require_login` only (same
     as every other read-only export on this page) — the endpoint takes the exact rows the caller
     already has on screen (whatever Detect All's own filters left visible) rather than
     re-deriving Detect All's filter logic a second time on the server.

     Results land in a checkbox table — select individually, or **select all** (visible rows only)
     — then, in the **Generate Cleanup Script** card below, **Generate Script for Selected** runs
     the **full original correction** (same as Single NISS/Batch: `READING_PREV_DATE`, `INI_DATE`,
     `XML_TO_BILL`, the `GCCOM_ITEMS_TO_BILL.STATUS` transition, cancelling the matching open
     `GCCOM_ANOMALOUS` record(s), and Part 6's non-cycle `TIPTL00011` reading cleanup) for each
     selected row's own **Supply/NISS** (already known from
     the billing-service enrichment above — no separate lookup needed). Concretely, it collects the
     distinct NISS values across the checked rows, switches you to the **Batch** tab, fills in its
     NISS list/Jira-program #/Clean-script/**Lowest-billing-period-only** toggles from what you
     just set on Detect All, and starts a normal background batch run there — so results, polling,
     and the combined script all show up exactly where a manual Batch run's would. Rows with no known Supply/NISS (a billing-service
     chain that didn't resolve) are skipped with a toast saying how many were left out, since the
     full correction can't run without a NISS to key off of. Requires the editor role or higher
     (same gate the Batch tab itself enforces).

     A narrower "just the bookkeeping" script generator (`GCCOM_ITEMS_TO_BILL.STATUS` +
     `GCCOM_ANOMALOUS` cancel only — no `READING_PREV_DATE`/`INI_DATE`/`XML_TO_BILL`) still exists
     server-side (`POST /api/date-anomaly/detect-all/generate`, `date_anomaly.build_cleanup_script`)
     but the Generate button above no longer calls it, per explicit request to make Detect All fix
     everything rather than just the anomaly bookkeeping — it's available for a future round to wire
     back in as an explicit "quick cleanup only" option if that's ever wanted again.

     A **🤖 Explain Selected** button sits next to Generate, enabled only when **exactly one** row
     is checked — it sends that row's account/supply/service/status/anomaly context to AI Assist
     (Gemini) and shows a plain-English second opinion on what the finding likely means and whether
     the bulk-cleanup action looks appropriate for it, flagging anything unusual before you include
     it in a run. Requires the Gemini key to be configured (Settings → AI Assist), same as the
     Suggest/Optimize/Explain/Review AI actions elsewhere in the app; it only reads/explains, it
     never mutates anything, so any logged-in role can use it.

     **Schema history**: `ID_PRINCIPAL_ANOMALY` (originally supplied as the typo "id_principal_
     anomlay") and its type-id values were corrected once already — the type ids were first guessed
     as `10000000026`/`201` from an early example query, then corrected by the analyst to the real
     values `202`/`201`. The billing-service enrichment join was also corrected: it was first
     guessed as joining on `ID_ITEM_TO_BILL`, but `GCCOM_ANOMALOUS` has its own separate
     `ID_BILLING_SERVICE` column, which is the real join key. `GCCOM_ANOMALOUS_STATUS`'s
     description column was first guessed as `DESCRIPTION`; the real column is `NAME_TYPE`.
     `GCCOM_BILL_ANOMALY_COMPANY.DESCRIPTION`/`ANOMALY_COD` and every other column in the
     enrichment chain were confirmed correct on the first pass. All of the above came from the
     analyst pasting back a corrected, working query after hitting a real error — the same pattern
     this project has hit before (`INIT_DATE`→`INI_DATE`, `STATUS`→`ANOMALOUS_STATUS`): treat any
     not-yet-exercised column name in this area as still worth a second look if something looks off.

  See `app/core/date_anomaly.py` for the query/XML-patch logic and its documented assumptions
  (notably that `GCCOM_ITEMS_TO_BILL_XML.ID_XML` matches `GCCOM_ITEMS_TO_BILL.ID_ITEM_TO_BILL` —
  unverified against a live schema, flagged clearly if it's wrong when run for real).
- A **🗂️ Hierarchy Analysis** page (web only, own nav item — logic in `app/core/hierarchy_
  analysis.py`) — **draft, pending the analyst's own try-it-out pass**. A metering "hierarchy" is a
  group of `GCGT_RE_MEASUREMENT_POINT` rows that share one parent (a child's `ID_MAIN_MP` points at
  the parent's `ID_MEASURING_POINT`); within a hierarchy, one measuring point is the "primary" — the
  point whose own reading is what actually gets taken, then distributed (`PERC_DIST`) across the
  rest of the hierarchy. This page answers "which primaries, across the **whole database**, still
  have a not-yet-billed reading" — the same system-wide-scan shape as Detect All, applied to
  hierarchy readings instead of Diff Date anomalies.
  0. **Overview dashboard** — a small "Overview" card sits above the main table (hidden until a scan
     has run), recomputed from whatever's currently visible/filtered rather than the full unfiltered
     scan: pending primaries shown, total secondaries, average secondaries per primary, and distinct
     billing periods in view, plus a second strip breaking that same visible set down by
     `CALC_MODULE_TYPE` (a row with no resolved type is grouped under "Unclassified" rather than
     dropped, so the strip's counts still sum to the total). Same "recompute from the visible set"
     convention `hierRenderKpiRow` already used for the table's own KPI row — this is a second,
     purely additive summary, not a replacement for it.
  1. **🔍 Detect Pending Primaries** — `POST /api/hierarchy-analysis/detect` runs
     `hierarchy_analysis.build_pending_primaries_query()`: every primary (`MP_TYPE IN
     ('TIPEQM0003','TIPEQM0005')` — Principal / Principal acoplado, per `GCGT_RE_MP_TYPE`; `STATUS
     IN ('1000STAMPO','2000STAMPO')` — Active / Disconnected only, per `GCGT_RE_MEASURE_POINT_
     STATUS`) with at least one reading, for a billing period **after `MIN_BILLING_PERIOD`
     (`10000000193`)**, in a not-yet-billed status (`READ_STATUS IN
     ('1000STSRED','5000STSRED','6000STSRED')` — Available/Anomalous/Sent to Bill) for a Cycle or
     Distribution reading (`READING_TYPE IN ('TIPTL00003','TIPTL00017')`) — narrowed via
     `ROW_NUMBER() OVER (PARTITION BY ID_MEASURING_POINT ORDER BY ID_BILLING_PERIOD ASC)` to just
     the **earliest** (of the periods left after the floor) pending billing period per primary, same
     "oldest first" framing as DIFF DATES Anomaly's own lowest-billing-period option. The floor is
     applied inside the reading `JOIN`'s `ON` condition (alongside the status/type filters), not the
     outer `WHERE`, so it can change WHICH reading counts as a primary's earliest pending one, not
     just hide rows after the fact. Each row also carries `SECONDARY_COUNT` — a correlated
     `(SELECT COUNT(*) FROM GCGT_RE_MEASUREMENT_POINT c WHERE c.ID_MAIN_MP = mp.ID_MEASURING_POINT)`
     — how many child measuring points report to that primary (any type/status, not otherwise
     filtered) — and `CALC_MODULE_TYPE`, a `LEFT JOIN` to `GCCOM_CALCULATION_MODULE` on the primary's
     own `ID_CALCULATION_MODULE`, giving that raw code a human label (live-confirmed: `1130` =
     "Hierarchy - Difference billed to the main supply", `1150` = "Hierarchy - Percentage of
     association between Primary and Secondary"). Also carries **`SECONDARIES_NOT_SENT_COUNT`**
     (task, 2026-09-11, confirmed via AskUserQuestion + a follow-up correction) — another correlated
     subquery: of this primary's secondaries, how many still count as "not sent to bill yet"? A
     secondary counts if EITHER it has a Cycle/Distribution reading above `MIN_BILLING_PERIOD` whose
     `READ_STATUS` is `1000STSRED`/`5000STSRED` (Available/Anomalous — `SECONDARY_NOT_SENT_STATUSES`,
     deliberately narrower than `PENDING_READ_STATUSES`: a secondary already at `6000STSRED` Sent to
     Bill counts as handled here, unlike a *primary* at that status, which still counts as pending)
     **OR** it has no Cycle/Distribution reading at all above the floor (per the analyst's own
     follow-up — a secondary with nothing captured yet obviously isn't sent either). When this count
     is **0**, every secondary is already sent-to-bill-or-further, meaning the primary's own pending
     reading is the *only* thing left to fix in that whole hierarchy — those rows get a distinct
     **green** `.row-primary-only` highlight (reassurance, not a warning, unlike this page's other row
     colors) plus a **Sec. Not Sent** column, a **Primary-only** filter checkbox next to the other
     filters, and a "Primary-only fixes" KPI card — both exports carry the column too. `LEFT JOIN`s to
     `GCGT_RE_MP_TYPE`/`GCGT_RE_
     MEASURE_POINT_STATUS`/`GCCOM_BILLING_PERIOD`/`GCGT_RE_READING_TYPE`/`GCCOM_CALCULATION_MODULE`
     add human-readable `MP_TYPE_DESC`/`MP_STATUS_DESC`/`BILLING_PERIOD_DESC`/`READING_TYPE_DESC`/
     `CALC_MODULE_TYPE` columns, shown in the table as a tooltip and parenthetical next to each code
     (also included in both exports). A schema pre-check on `MP_TYPE`
     (mirroring Detect All's own `ID_PRINCIPAL_ANOMALY` check) fails clearly instead of a raw
     "Invalid column name" if that column ever doesn't exist. Capped at `TOP (1000)`
     (`HIERARCHY_DEFAULT_LIMIT`) with the same `possibly_truncated` flag Detect All uses, since this
     is another query with no natural per-call scope. A **Search** box (NISS/measuring point) plus
     **Read status**/**Reading type**/**MP status**/**Billing period** dropdown filters narrow the
     table client-side, same convention as Detect All's filter row (billing-period options are sorted
     numerically, not lexically, since the ids are large numbers); a KPI row above the table (pending
     primaries shown, distinct NISS, anomalous reads, oldest billing period) reflects whatever's
     currently visible. Rows whose `READ_STATUS` is `5000STSRED` (Anomalous) get the same orange
     row-highlight Diff Date's multi-period rows use (`.row-multi-period`) — different condition, same
     "needs a second look" visual intent, so no new CSS was added for it. **★ Ready Usage**
     (`READY_USAGE`) is the analyst's own most-important field on this page — bolded in both the main
     table and both exports, and leads the Overview dashboard's KPI row (`Total ready usage`, summed
     over whatever's currently visible) ahead of the structural counts next to it.
  2. **🔗 drill-down** — clicking a row's 🔗 button calls `POST /api/hierarchy-analysis/detail`
     (`hierarchy_analysis.build_hierarchy_detail_query`), the analyst's own day-to-day query kept
     near-verbatim (parameterized on the id: `WHERE ID_MAIN_MP = :id OR ID_MEASURING_POINT = :id`) —
     every member of that hierarchy (the primary's own row plus any children pointing at it), opened
     in a **Hierarchy Detail** card below the main table. The frontend always sends the clicked row's
     own `id_billing_period` along with the request, so children are scoped to that **same billing
     period** the primary was flagged pending in (`AND r.ID_BILLING_PERIOD = ...` added to the
     reading `LEFT JOIN`'s `ON` condition, not the outer `WHERE`, so a child with no reading in that
     period still surfaces with blank reading columns) — leave `id_billing_period` off the request to
     see a hierarchy's full reading history across every period instead. Same `GCCOM_BILLING_PERIOD`/
     `GCGT_RE_READING_TYPE` `LEFT JOIN`s as the main list add `BILLING_PERIOD_DESC`/
     `READING_TYPE_DESC` here too, and `★ Ready Usage` (bolded) is shown alongside `Value`. Its own
     "Primary?" column is derived client-side from `MP_TYPE IN ('TIPEQM0003','TIPEQM0005')` (the
     corrected definition), not from the `IND_DIST_PPAL` column this particular query still happens to
     return. Its `STATUS <> '3000STAMPO'` outer exclusion is unchanged — that's the analyst's own
     day-to-day query as already used, not part of the pending-primaries correction.

     A member (supply) whose own `READ_STATUS` isn't in `HIER_BILLED_READ_STATUSES`
     (`7000STSRED`/`7001STSRED` — "Facturada"/"UAU - Billed", both literally named "Billed" on
     `GCGT_RE_READ_STATUS`; `8000STSRED` "Terminated Not Billed" is also included here, per the
     analyst's own direction to treat it the same as billed for this purpose despite its name — it's
     a closed/resolved state, not one that still needs a second look; a member with no reading at all
     in scope, from the query's `LEFT JOIN`, still counts as needing attention) gets a distinct red
     `.row-not-billed` highlight — separate from `.row-multi-period`'s orange, since "not billed" is
     a stronger signal and the two can otherwise coexist on the same row elsewhere in the app.
     Not-billed members are **always sorted to the top** of the table (a stable sort — the query's
     own primary-then-children order is preserved within each of the two groups), and a **Not billed
     only** checkbox above the table narrows the same in-memory result set to just those rows without
     re-fetching. This mirrors `date_anomaly.READ_STATUS_BILLED = "7000STSRED"` but also includes
     `7001STSRED`/`8000STSRED`, since this check answers "does this supply still need attention"
     rather than `date_anomaly`'s narrower "find the correctly-billed baseline reading" use of that
     constant — treat these two additions as assumptions worth a second look, not confirmed rules.
  3. **⬇ Export CSV / ⬇ Export Excel** — same pattern as Detect All's own two export buttons: CSV
     is a client-side `Blob`, Excel round-trips through `POST /api/hierarchy-analysis/export-xlsx`
     (`openpyxl`, bold header, sized columns, frozen header row) since a browser can't write that
     binary format itself. Both export whatever's currently visible (filters/search applied), not
     the full unfiltered scan.

  **Reading history popup** — a "📖 Readings" button on each Hierarchy Detail row (task #143) opens
  `POST /api/hierarchy-analysis/reading-history`, which runs `hierarchy_analysis.build_reading_
  history_query` — an adaptation of the analyst's own pasted draft query, confirmed after two rounds
  of review (2026-09-11). Filters directly on `r.ID_SECTOR_SUPPLY` (no join to `GCCOM_SECTOR_SUPPLY`
  needed — that FK already sits on `GCGT_RE_READING` itself), keeps the analyst's own `ID_BILLING_
  PERIOD > 10000000130` floor (`READING_HISTORY_MIN_BILLING_PERIOD` — a *different* number from the
  Detect-primaries floor's `MIN_BILLING_PERIOD = 10000000193`, don't conflate the two) and `READING_
  TYPE <> 'TIPTL00004'` exclusion, and returns `BILLING_PERIOD, ID_READING, READING_TYPE, USAGE_TYPE,
  READ_STATUS, PREV DATE, READING_DATE, PREV_VALUE, VALUE, READING_USAGE, READY_USAGE, IND_ESTIMATE`,
  newest billing period first. `READING_USAGE` stands in for the "READING_VALUE" the analyst
  originally named — no such column exists on `GCGT_RE_READING` (confirmed live against all 55 of its
  columns) — and the analyst did not object to the substitution once flagged. The popup itself
  highlights (blue, `.row-checking-period`) whichever row's `BILLING_PERIOD` matches the Hierarchy
  Detail row it was opened from — the analyst's own follow-up ask ("highlight the reading in the pop
  up window when we click it from the details"), handled entirely client-side in `app.js`, not a SQL
  change. **Not yet live-verified against the tunnel DB** — `web/server.py` and `app/core/hierarchy_
  analysis.py` changes need a server restart to take effect (see run_web.py's own note on this); flag
  for a fresh live-verify pass once the app is restarted.

  **Collapsible sidebar** — the left nav (`#sidebar`) can be collapsed to an icon-only rail via the
  `«` button in the sidebar header, reclaiming horizontal space for wide tables like this page's own
  Hierarchy Detail drill-down. Collapsing adds an `.is-collapsed` class that hides labels (`display:
  none` on `.brand-text`, `.nav-label`, `.btn-label`, etc. — see `styles.css`) while each nav button's
  existing `title` attribute becomes a hover tooltip. State persists across reloads via
  `localStorage["scriptgen_sidebar_collapsed"]`, mirroring the existing `initTheme()` /
  `#theme-toggle-btn` pattern in `app.js` (an IIFE applies the saved state on load; the click handler
  toggles + persists). Live-verified 2026-09-11: collapse, expand, and reload-persistence all confirmed
  against the running app.

  **KNOWN ASSUMPTIONS — confirmed live against the tunnel DB (2026-09-11)**, and the "primary"/MP-
  status rules below are the analyst's own corrections to an earlier draft of this feature (which
  had guessed `IND_DIST_PPAL = 1` and a `STATUS <> '3000STAMPO'` exclusion instead) — see
  `app/core/hierarchy_analysis.py`'s module docstring for the full detail behind each one:
  "primary" = `MP_TYPE IN ('TIPEQM0003','TIPEQM0005')` pulled from the `GCGT_RE_MP_TYPE` lookup, per
  the analyst's own direction to use that lookup rather than `IND_DIST_PPAL` (a cross-tab check had
  shown `IND_DIST_PPAL = 1` isn't 1:1 with `MP_TYPE = 'Principal'` — the analyst's correction settles
  which one actually governs); `MP_STATUS_ALLOWED = ('1000STAMPO','2000STAMPO')` pulled from
  `GCGT_RE_MEASURE_POINT_STATUS`, per the analyst's own direction to keep only Active and
  Disconnected measuring points — an allow-list, replacing the earlier draft's exclude-only-Inactive
  rule (and now also excluding `'0000STAMPO'` "Inexistente", which that earlier rule did not); the
  three `READ_STATUS` codes (`GCGT_RE_READ_STATUS`: 1000STSRED "Disponible", 5000STSRED "Consumo
  Anómalo", 6000STSRED "Enviado a Facturar"); the two `READING_TYPE` codes (`GCGT_RE_READING_TYPE`:
  TIPTL00003 "Cycle", TIPTL00017 "Distribution" — a **different** pair from `date_anomaly.
  CYCLE_READING_TYPES`, which answers an unrelated question). `build_hierarchy_detail_query` (the
  analyst's own drill-down query) is untouched by this correction — it keeps its original `STATUS <>
  '3000STAMPO'` exclusion, since that's the analyst's own day-to-day tool as already used, not part
  of the pending-primaries scan being corrected here. `MIN_BILLING_PERIOD = 10000000193` is a later
  analyst-supplied cutoff with no stated reasoning beyond the number itself — treat it as a business
  decision ("don't chase pending periods this old"), not a schema fact, and revisit if it ever needs
  to move. `SECONDARY_COUNT` counts children of ANY type/status (not filtered to `PRIMARY_MP_TYPES`/
  `MP_STATUS_ALLOWED` — those only gate which rows qualify as a *primary*, not which children count).
  `CALC_MODULE_TYPE` only had two live values in the tunnel DB's `ID_CALCULATION_MODULE` distribution
  (`1130`, `1150`) out of ~980K rows sampled — the `GCCOM_CALCULATION_MODULE` lookup isn't restricted
  to those two, so any other code that shows up still resolves. Treat this whole feature as a draft
  until the analyst tries it for real — same "not yet exercised, worth a second look" stance the
  Detect All schema-history note above takes.
- **Script History**: every script this tool generates (desktop or web) is logged to the same local
  SQLite db as snapshots — who generated it, when (UTC), update vs. rollback, target table, Jira/
  program #, and statement/warning counts, plus the full script text on demand. It's a record of
  what was *generated*, never of anything actually run — ScriptGen has no way to know that. Useful
  for "what did I actually hand off for JIRA-4821 last week" without having kept the .sql file.
- A **Dashboard** window (opens separately, doesn't block the main one) with a KPI card row (rows,
  columns, numeric/text/empty column counts, total NULLs), a searchable per-column summary table
  you can export to CSV/Excel, a histogram for any numeric column, a bar chart of top values for
  any text column, a **Correlation** tab (scatter plot + Pearson r between any two numeric
  columns), and a **Trend History** tab that plots row count over time for a table once you've
  exported two or more snapshots of it — plus a one-click **Export Dashboard PDF** that bundles
  every chart currently on screen into one multi-page PDF.
- **⬇ Export** menu on the results grid: **CSV**, **Excel (.xlsx)** (bold header row, frozen,
  auto-sized columns), or straight to a **new table in the internal SQLite DB** — same snapshot
  mechanism as File > Export Result to Internal DB, just reachable in one place next to the grid.
  Browse and reload past snapshots from File > Browse Snapshots...
- A **Recent** menu next to Run Query remembers your last 15 queries (persisted across launches)
  so you can pull one back up without retyping it.
- **AI Assist** page backed by Gemini's free tier (bring your own free API key, pasted/saved/
  tested right there on the page): Suggest / Optimize / Explain on the current query, plus a
  **natural-language → WHERE clause builder** ("only rows updated in the last 30 days" → a pasted-
  in WHERE clause). The **Script** page also has an **AI Pre-Flight Review** button — a second,
  independent opinion on the generated script itself (risky WHERE clauses, suspicious SET values),
  explicitly framed as a second opinion, not a substitute for your own review.
- A Config dialog for all of the above — connection details (with the New/Delete/Set-Active
  connection picker), timeout, and the AI key — with a **Test Connection** button.
- A modern, web-app-style desktop UI: a slim top bar plus a left sidebar (Workspace / Script /
  AI Assist / Tools / Dashboard) instead of one big stacked form, with a one-click light/dark
  theme toggle (ttkbootstrap).

Not in v1 (by design, see the "Scope decisions" section): Oracle support, multi-table joins in
the script generator, and executing scripts from inside the app. These are straightforward
follow-ups once the core workflow is confirmed to fit how you actually work.

## Why there's no "downloads drivers on first run" step

The original ask was for the app to auto-download its DB drivers on execution. I built it a
different way that gets you the same outcome — nothing to install — more reliably: the SQL
Server driver (`python-tds`, imported as `pytds`) is **pure Python with zero compiled
extensions**, so `pip install` never needs a C compiler and nothing needs a separate ODBC driver
or Oracle Instant Client install. It's baked into the `.exe` at build time, so there is nothing to
fetch over the network when you run the app. Given you're connecting through a local tunnel —
which often means a locked-down network — that seemed safer than having the app try to reach the
internet for a driver download at launch and fail if that network can't reach it. (An earlier
draft used `pymssql` instead, which bundles a *compiled* FreeTDS binding; on a machine where
`pip` couldn't find a prebuilt wheel for the installed Python version, it fell back to building
from source and failed without VC++ build tools present. `python-tds` sidesteps that entirely.)
If you'd genuinely rather have a first-run downloader, say so and I'll add one back in.

## Project layout

```
main.py                     Tkinter desktop app entry point (legacy - still full-featured, see above)
desktop_launcher.py          web-UI-in-a-native-window entry point (pywebview) - the .exe's new target
app/config.py                connections (multi-, with add/remove) + AI settings + recent/saved queries, load/save, encrypted password/key at rest
app/utils/dotenv_lite.py     dependency-free .env loader (SCRIPTGEN_DB_PASSWORD/SCRIPTGEN_AI_API_KEY) - see "Version control (git)" above
.env.example                 template for the two optional secrets above - copy to .env (gitignored), never commit the real one
app/db/mssql.py              SQL Server access (python-tds), connection test, PK lookup, INFORMATION_SCHEMA column lookup (schema validation)
app/db/internal_store.py     SQLite snapshot export/list/load
app/db/script_history.py     SQLite audit trail of generated scripts (who/when/table/kind/text) - shared by desktop + web
app/core/sql_format.py       Python value -> T-SQL literal formatting
app/core/sql_pretty.py       query editor's "Format" button - reindent/prettify via sqlparse
app/core/diff_engine.py      original vs. edited grid -> list of changed cells per row
app/core/script_generator.py changed cells -> UPDATE + rollback statements (WHERE guarded on changed columns' original values), audit-column stamping, script header/footer
app/core/snapshot_diff.py    two independent snapshot exports -> added/removed/changed rows (Snapshot Diff Viewer)
app/core/schema_check.py     query/key columns vs. a live table's actual columns (Validate Target Schema)
app/core/ai_assist.py        Gemini API calls (suggest / optimize / explain / NL-WHERE builder / script review / test key)
app/core/stats.py            per-column summary stats + histogram bucketing + KPI rollup + Pearson correlation for the Dashboard
app/ui/*                     Tkinter desktop UI (legacy) - main window, Config dialog, Dashboard window, theme
web/server.py                 FastAPI app: login/session, query/format, key lookup, grid diff, script generation, history routes - all built on app/core + app/db, unchanged
web/auth.py                   web login accounts (bcrypt) - separate from the DB connection, see "Web login accounts" above for why
web/manage_users.py           `python -m web.manage_users` CLI for adding/removing web login accounts
web/menu_access.py            per-role sidebar visibility config (Settings > Menu Access) - a UX/declutter layer on top of web/auth.py's real role gates, not a second permission system
web/session_store.py          per-session in-memory query state (the "original rows" a diff compares edits against)
web/static/                   the frontend itself: index.html, styles.css, app.js (no build step, no framework)
tests/test_diff_and_script.py  48 unit tests for the diff/script/rollback/audit-column/identifier-quoting/WHERE-guard logic (no DB needed)
tests/test_stats.py          17 unit tests for the Dashboard's stats/histogram/KPI/correlation logic (no DB needed)
tests/test_snapshot_diff.py  5 unit tests for the Snapshot Diff Viewer's row-matching logic (no DB needed)
tests/test_schema_check.py   5 unit tests for the Validate Target Schema logic (no DB needed)
tests/test_ai_assist.py      6 unit tests for ai_assist's offline code paths (no network needed)
tests/test_config.py         14 unit tests for recent/saved queries + multi-connection add/remove
tests/test_sql_pretty.py     5 unit tests for the query editor's "Format" button (sql_pretty)
tests/test_script_history.py 8 unit tests for the script-history audit trail (app/db/script_history.py)
tests/test_web_api.py        58 integration tests for the FastAPI backend (auth, session gating, query/diff/script/history/config/queries/schema/snapshot/AI/dashboard routes) via FastAPI's TestClient
tests/smoke_launch.py        headless GUI smoke test for the Tkinter desktop app (used during its development)
build.bat                    Windows build script -> dist/ScriptGen.exe (still builds the Tkinter app until it's repointed at desktop_launcher.py)
requirements.txt             pinned dependencies (Tkinter-desktop and web-stack sections both marked)
```

## Version control (git)

This project is tracked in git as of 2026-09-12. `.gitignore` already excludes everything that
shouldn't be committed: `data/` (config, encryption keys, the internal SQLite db, `web_users.json`,
`web_admin_first_run.txt`), `.venv/`, `build/`/`dist/`, `__pycache__/`/`*.pyc`, `.pytest_cache/`,
`exports/`, `.env`/`*.key`, and common editor/OS cruft. None of that should ever show up in `git
status` as untracked-but-wanted or, worse, staged — if it does, stop and check before committing.

Cloning fresh (or setting up a new machine)? Copy `.env.example` to `.env` and fill in
`SCRIPTGEN_DB_PASSWORD`/`SCRIPTGEN_AI_API_KEY` (or set them as real environment variables instead) —
both optional, see "Security note on stored credentials" below for the full reasoning. Then the
normal `pip install -r requirements.txt` + `python run_web.py` (or `python main.py` for the legacy
desktop app) from "Building the .exe" / "Using it" below.

## Building the .exe (must be done on Windows)

PyInstaller builds a Windows `.exe` only when run *on* Windows, so this last step has to happen
on your machine, not in the cloud sandbox this was developed in. Everything up to that point —
all the logic (89 unit tests) and a full headless GUI smoke test that exercises every feature
described in this README, including the Dashboard — has already been run and is passing. Note the
app now bundles `ttkbootstrap`, `matplotlib`, and `openpyxl` (for the modern theme, the
Dashboard's charts/PDF export, and Excel export), so both the `pip install` step and the build
itself take noticeably longer than a bare-bones Tkinter app, and the resulting `.exe` is bigger.

1. Copy this whole folder to your Windows machine (e.g. into `C:\MISC_RMA\ClaudeDev\ScriptGen`).
2. Make sure Python 3.11+ is installed and on `PATH` (`python --version` in a terminal).
3. Double-click `build.bat`, or run it from a terminal in this folder.
4. When it finishes, `dist\ScriptGen.exe` is your standalone app. Copy that one file anywhere —
   it doesn't need the rest of the folder or a Python install to run.

The first time `ScriptGen.exe` runs, it creates a `data\` folder next to itself with:
- `config.json` — your connection settings (seeded with the tunnel connection you gave me) and
  AI settings
- `scriptgen.key` — the local encryption key for the stored password/API key (see security note
  below)
- `scriptgen_internal.db` — the SQLite file that snapshots get exported into

## Using it

The window is laid out like a web app: a slim top bar (title, connection status, Test
Connection, Settings, theme toggle), and a left sidebar. Sidebar navigation swaps four pages on
the right — **Workspace**, **Script**, **AI Assist**, **Tools** — but the sidebar's **WHERE
clause key** panel, **Target schema/table**, **Jira/Program #**, **Generate Update Script**
button, and **Dashboard** entry stay put no matter which page you're on, since you'll often want
to jump to Script and back without losing your key-column picks. Drag the thin divider between
the sidebar and the content area left/right to resize it — handy if you're working with long
column names.

1. Launch `ScriptGen.exe`. The default connection (`localhost:1433`, user `ouc_read_only`) is
   pre-filled.
2. **Make sure your tunnel is open**, then click **Test Connection** (top bar, or Connection menu)
   to confirm. This has to be tested from your machine — the cloud sandbox that built this has no
   route to your tunnel.
3. On the **Workspace** page: type a query, hit **Run Query** (or F5). Results land in the grid
   below the editor. Every query you run (even one that fails) is remembered in the **🕘 Recent**
   menu next to Run Query, up to the last 15 — click one to drop it back into the editor. Click
   **🪄 Format** any time to reindent/prettify whatever's in the editor — one column/clause per
   line, keywords upper-cased — purely cosmetic (it never changes what the query does, only how it
   reads); it's a manual click, not automatic-on-every-keystroke, so it never fights with what
   you're mid-typing.
4. Double-click any cell to edit its value. Type the literal text `NULL` in a cell to set that
   column to SQL `NULL` for that row (see "Editing conventions" below). As you edit, the cell you
   changed gets an amber highlight and that row's number gets a blue highlight, so it's obvious at
   a glance which rows/fields will end up in the generated script — and that row's own `UPDATE`
   statement appears live in the **🔗 SQL Preview** column to the left of the grid, so you don't
   have to click Generate Update Script just to see what an edit will produce. Drag the divider
   between the two to resize either one.
5. In the sidebar's **WHERE clause key** panel: click the chip(s) for whichever column(s) uniquely
   identify a row (these become the `WHERE` clause). **Auto-detect Key** looks up the table's
   actual primary key and pre-selects the right chip(s) for you; pick by hand for a view or a
   table with no PK. **Select All** / **Clear** are there for the full-row match case. **Target
   schema** / **Target table**, just below the chips, are auto-filled from the query's `FROM`
   clause; correct them by hand for a multi-table query.
6. Fill in **Jira / Program #** in the sidebar (defaults to the placeholder `JIRAXXXX` — it's
   required, and gets stamped into every statement as `update_program`; see "Generated script
   format" below). Click **Generate Update Script**. This switches you to the **Script** page,
   which shows one `UPDATE` per changed row — each preceded by a comment naming which column(s)
   changed on that row — wrapped in a transaction, with warnings inline for any row that couldn't
   be matched by a reliable key.
7. On the Script page: **Copy Script** or **Save Script As...** to hand it off. Click
   **Review This Script** for a second, independent AI opinion on the script before you do —
   see "AI Assist" below.
8. **File > Export Result to Internal DB...** saves the current grid as a named, timestamped
   snapshot in the local SQLite file. **File > Browse Snapshots...** lists and reloads them back
   onto the Workspace page (read-only view — editing a loaded snapshot won't generate a script
   against the live table). Exporting the same table's result more than once is also what powers
   the Dashboard's Trend History tab (see below). Need the raw data instead? The **⬇ Export** menu
   in the Results header on the Workspace page writes the current grid (with your edits) to a
   **CSV** file, an **Excel workbook**, or a **new table in the internal DB** — pick whichever fits.
9. Click **Dashboard** in the sidebar (or View menu > Open Dashboard...) to open a separate
   window with a per-column summary table and charts for the current result — see "Dashboard"
   below. It's a snapshot of the grid at the moment you open it, not live-updating. The sidebar
   entry is disabled until you've run a query.
10. The **AI Assist** page holds the Gemini-backed suggest/optimize panel — see "AI Assist" below.

### Editing conventions (read before trusting a generated script)

- A cell is only counted as "changed" if its text differs from what was originally loaded.
- Typing `NULL` (any case) into a cell sets that column to SQL `NULL`. There's no separate way to
  set an *empty string* vs `NULL` in this text-grid v1 — an empty cell is treated as the string
  `""`, not `NULL`, unless the original value was already `NULL`.
- The generator does its best to keep numbers unquoted and dates/strings correctly typed based on
  the *original* value's type — a value that started as an `int` is parsed back to `int`, etc. If
  that parsing fails (e.g. you typed letters into a numeric column), it falls back to a quoted
  string literal, which is always valid SQL but may not be what you meant — check the preview.
- **Multi-table queries**: the target-table detection only looks at the first table named after
  `FROM`. If your query joins multiple tables, set **Target schema/table** by hand to whichever
  table you actually want to update, and make sure the **Key column(s)** you pick actually belong
  to that table.
- **No primary key**: if a table has no PK (or you clear the Key column(s) field), the `WHERE`
  clause falls back to matching on every original column's value. The script is generated with a
  loud comment warning about this — if the table has duplicate rows, that statement could update
  more than one row. Review before running.

### Generated script format

Every `UPDATE` statement (forward AND rollback) looks like this:

```sql
-- Row 1: changed column(s): name, age
UPDATE dbo.Accounts
SET name = N'Alicia',  -- was: N'Alice'
    age = 31,  -- was: 30
    update_program = N'JIRA-4821',
    update_date = GETDATE(),
    update_user = N'RMA'
WHERE id = 1 AND name = N'Alice' AND age = 30;
```

Each changed column gets its own `SET` line with an inline `-- was: <original value>` comment, so
a reviewer can see exactly what's changing without cross-referencing the grid. Three fixed audit
columns are appended to every statement: `update_program` (the Jira/ticket number you entered in
the sidebar — required, defaults to the obviously-fake placeholder `JIRAXXXX` so it's impossible
to miss if you forget to fill it in), `update_date` (`GETDATE()`, computed by SQL Server itself
when the script actually runs — not by this app at generation time, since those can be hours or
days apart), and `update_user` (`'RMA'`, fixed by convention). This assumes the target table
actually has `update_program` / `update_date` / `update_user` columns; if it doesn't, remove those
three lines by hand before running, or say the word and I'll make the audit columns configurable
per table.

**The `WHERE` clause always includes the ORIGINAL value of every column being changed**, not just
the key column(s) — `WHERE id = 1 AND name = N'Alice' AND age = 30`, above, not just `WHERE id =
1`. This is a lightweight optimistic-concurrency guard: it makes the statement only fire against a
row that still holds the exact value it was generated from, so if something else already changed
`name` or `age` on that row between "you ran the query" and "someone runs this script," the
statement matches zero rows instead of silently overwriting whatever's there now (and the row
count SQL Server reports after running it will visibly come up short, which is your signal to go
look rather than assume it worked). The rollback script gets the same guard in the other
direction — it checks the value is still what the *forward* script set it to before restoring the
original. A column that's already part of the key predicate (the unusual case of editing the key
column itself) isn't checked twice.

**Table/column names are left unquoted** when that's safe (plain letters/digits/underscore, not a
SQL Server reserved word) — `UPDATE gccom_payment_form`, not `UPDATE [gccom_payment_form]` — so
the script reads like ordinary hand-written SQL. A name only falls back to `[bracket]` quoting
when leaving it bare would actually break the script: it has a space or other special character,
starts with a digit, or collides with a reserved word (a column named `user` or `key`, say). This
is a plain identifier-formatting decision, not something you need to configure per table —
`quote_ident()` in `app/core/sql_format.py` makes the call automatically for every name.

### The Tools tab

Five standalone tools, each in its own card:

- **Environment** — switch which saved connection is active without opening Settings; **Manage
  Connections...** opens the same picker Settings uses (see "Multiple connections" below).
- **Saved Queries** — unlike the Recent menu (a rolling history of your last 15 queries, no
  matter what), a saved query is named on purpose and stays until you delete it. **Save Current
  Query As...** prompts for a name; select a row and **Load Selected** / **Delete Selected**.
- **Validate Target Schema** — checks that the current query's columns (and any key column(s)
  you've picked) actually exist on **Target schema/table**, so a typo surfaces here instead of
  when someone tries to run the generated script against production.
- **Rollback Script** — the exact reverse of the last Update Script you generated: restores every
  changed cell to its original value, re-stamped with the same Jira/program # and its own fresh
  `update_date`/`update_user`. Generate the forward script first (sidebar > Generate Update
  Script), then come back here — meant to be saved *alongside* the forward script, not instead of
  it, so whoever has write access has an undo path ready before they run anything. If a key column
  itself was one of the edited cells, the rollback's `WHERE` correctly matches on the *new*
  (post-forward-script) value, not the original one.
- **Snapshot Diff Viewer** — pick two exported snapshots (File > Export Result to Internal DB, or
  the grid's ⬇ Export menu) of the same table/query, an optional comma-separated key column list
  (blank = match on the full row), and **Compare** to see which rows were added, removed, or
  changed between them.

### Multiple connections

Settings > Database Connection now has a connection picker at the top: **+ New** adds another
named connection (e.g. `staging`, `prod-readonly`) starting from a blank form; **Set Active**
makes the one currently shown the app's active connection; **Delete** removes it (refuses to
delete whichever connection is currently active — switch to another one first). Switching which
connection you're editing in the picker auto-saves your in-progress edits for the one you're
leaving, so browsing between connections doesn't silently discard changes; nothing is written to
disk until you click the dialog's own **Save**. The Tools tab's **Environment** card is a faster
day-to-day way to flip the *active* connection once you've got more than one set up here.

### Dashboard

Opens in its own window (you can keep the main window open behind it), with three tabs:

**Overview**

- A summary bar: how long the query took, when the Dashboard was generated, and the guessed
  source table.
- A row of **KPI cards**: row count, column count, numeric/text/empty column counts, and total
  NULLs across the whole result — the one-glance version before you drill into anything.
- A per-column table: type (numeric/text/empty), non-null count, null count, distinct count, and
  either min/max/mean (numeric columns) or the most common value and its count (text columns).
  There's a **Filter** box above it (handy once a result has a lot of columns) and an
  **⬇ Export Stats** menu (CSV or Excel).
- **Numeric Distribution**: pick any numeric column from the dropdown to see a histogram (12
  equal-width buckets computed in plain Python, no numpy).
- **Category Breakdown**: pick any text column from the dropdown to see a horizontal bar chart of
  its top 8 most common values.
- Both charts have a **💾 Save Chart PNG** button next to the column dropdown.

**Trend History**

If this result's source table has two or more snapshots already exported (File > Export Result to
Internal DB...), this tab plots row count over time for that table — a quick "is this table
growing/shrinking" view, built entirely from data the snapshot feature was already collecting, plus
a list of the matching snapshots and their row counts. With fewer than two matching snapshots (or
no detected source table), it explains that instead of drawing an empty or single-point chart.

**Correlation**

Pick any two numeric columns from the **X**/**Y** dropdowns to see a scatter plot and their
Pearson correlation coefficient (`-1` to `1`, with a plain-English strength/direction label like
"strong positive" or "weak negative"). Needs at least 2 numeric columns in the result; rows with a
non-numeric or missing value in either column are skipped rather than breaking the calculation.

**Export Dashboard PDF**

The **⬇ Export Dashboard PDF...** button in the summary bar bundles every chart currently built
for this result (Numeric Distribution, Category Breakdown, Trend History, Correlation — whichever
of those actually apply) into one multi-page PDF, so you can hand the whole picture to someone
else without screenshotting every tab.

The Dashboard is a read-only snapshot of whatever was in the grid when you clicked the button —
editing cells or re-running the query afterward won't update an already-open Dashboard window;
open a fresh one to see current numbers.

### Light / Dark theme

Click the 🌙/☀ button in the top-right of the header, or **View > Toggle Light / Dark Theme**.
The choice is saved to `config.json` and remembered next launch. Dark mode uses ttkbootstrap's
`sandstone-dark` theme specifically because its muted, warm-gray "secondary" color keeps hint and
status text (like the "double-click a cell to edit" label) readable — most other ttkbootstrap
dark themes map that same slot to a bright magenta/purple accent instead.

### AI Assist (optional)

The app ships with a default Gemini API key and model already filled in (same treatment as the
read-only DB tunnel password below — see "Security note on stored credentials") — on first launch,
or on upgrading an older `data/config.json`, AI Assist just works with no setup. If you'd rather
use your own key:

1. Get a free API key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (no
   credit card required for the free tier).
2. Paste it right on the **AI Assist** sidebar page, in the **Gemini API key** box at the top —
   click **Save**, then **Test Key** to confirm it actually works before you rely on it (this does
   the same thing as Connection menu > Configure... > **AI (Gemini)** tab, which is still there
   too — the two stay in sync, so use whichever's more convenient). Saving your own key replaces
   the shipped default; it's never touched or overwritten once you've set one.
3. Type what you want (e.g. "add a filter for the last 30 days") and click **Suggest**, click
   **Optimize Current Query** to have it review what's already in the editor, or click
   **Explain Current Query** for a plain-English walkthrough of what a query does (useful for a
   query you inherited, or before you trust one enough to build an UPDATE script off it). Click
   **Insert Into Query Editor** to accept a Suggest/Optimize result — this switches you back to
   the Workspace page with it dropped into the SQL box (Explain doesn't offer this, since its
   output is prose, not a query to run).
4. Lower on the same page, **Build a WHERE clause from plain English**: describe which rows you
   want (e.g. "only rows updated in the last 30 days") and click **🔎 Build WHERE Clause**. It uses
   your last query result's columns as context when there is one. **📋 Copy** puts just the clause
   on the clipboard; **↩ Append to Query as WHERE** drops a literal `WHERE <clause>` onto the end
   of the query editor — always review it (and adjust for parentheses/AND if you're combining it
   with an existing `WHERE`) before running.
5. On the **Script** page, after generating a script, **🤖 Review This Script** sends the
   *generated script itself* (not the source query) back to Gemini for a short, independent risk
   read — WHERE clauses that might not uniquely identify a row, SET values that look like the
   wrong type, anything that looks like it could touch more rows than intended. It's a second
   opinion, explicitly not a substitute for reviewing the script yourself.
6. The free tier has a rate limit; if you hit it, the app tells you rather than hanging.
7. Every AI Assist call (Suggest/Optimize/Explain/Review, the NL-WHERE builder, and both Diff Date
   Anomaly "Analyze with AI" buttons) caps Gemini's response at `maxOutputTokens: 2048`
   (`app/core/ai_assist.py`'s `_call_gemini`) — raised from an original `1024` after longer,
   richer prompts (particularly Single NISS's enriched case context) started getting cut off
   mid-sentence. If a response still looks truncated, that cap — not a display/layout bug — is
   the first thing to check.

## Performance notes

Two things were fixed after early testing showed the app feeling laggy, both around loading
query results into the grid (and the freeze that caused right after):

- **Column auto-sizing.** Every query run used to call tksheet's built-in "size columns to fit
  their content" routine, which measures every cell in every row through a real Tk font-metrics
  call - on a few hundred/thousand-row result that's a few hundred milliseconds to multiple
  seconds, running synchronously on the UI thread (nothing to background - the grid can't be
  shown until it's done), which is what made both "run a query" and "the app in general right
  after" feel slow. It's replaced with a much cheaper estimate: one reused, hidden canvas text
  item (the same trick tksheet uses internally, just applied more sparingly) measuring a capped,
  evenly-spaced sample of up to 300 rows per column instead of all of them. Measured in testing:
  a 20,000-row/20-column result went from ~2.8s to ~0.2s. Typing a longer value into a cell later
  still grows that column to fit, same as before - the cap only applies to the initial sizing.
- **Matplotlib's import cost.** The Dashboard's charts use matplotlib, whose import alone can
  take half a second or more. It used to be imported the moment the app started (whether or not
  you ever open the Dashboard), delaying the window's first appearance every single launch.
  It's now imported lazily, the first time you actually open a Dashboard.

If it's still slow on your machine after pulling this update, the next thing worth trying is
switching `build.bat` from `--onefile` to `--onedir` - onefile re-extracts the whole bundled app
to a temp folder on every launch (and antivirus tools often rescan that on-launch extraction),
which can make startup noticeably slower with matplotlib/ttkbootstrap's larger payload; onedir
trades "one .exe you can copy anywhere" for "a folder you copy as a whole" in exchange for
skipping that per-launch extraction. Say the word and I'll make that switch.

## Security note on stored credentials

The connection password and the Gemini API key are encrypted at rest with `cryptography`'s
Fernet (`data/config.json` never contains plaintext). Being fully honest about what that
does and doesn't buy you: the decryption key lives in a plain file right next to the encrypted
data (`data/scriptgen.key`). Anyone who can read files on this machine can decrypt both. That's
reasonable for a **read-only** reporting account behind a locked-down tunnel, but it is not real
secrets management. If this ever needs to hold a higher-privilege credential, the right next step
is swapping this for Windows Credential Manager via the `keyring` package — the code is already
isolated to `app/utils/crypto.py` so that's a contained change, not a rewrite.

The Gemini API key gets the same at-rest treatment (Fernet, same caveats). **Where the *default*
values come from changed in the 2026-09-12 git-integration round**: both the tunnel connection's
password and the Gemini key used to be hardcoded directly in `app/config.py` — fine for a private
zip handoff, not fine once this repo lives in git (a secret in source is a secret in git history
forever, unlike `data/config.json`, which is gitignored and never tracked). They now come from the
environment instead: `SCRIPTGEN_DB_PASSWORD` / `SCRIPTGEN_AI_API_KEY`, read via `app/config.py`'s
`DEFAULT_DB_PASSWORD`/`DEFAULT_AI_API_KEY` constants. Set them as real environment variables, or
copy `.env.example` to `.env` (gitignored, never commit the real one) and fill it in — either
works, via the small dependency-free loader in `app/utils/dotenv_lite.py` (not python-dotenv, to
keep the project's "no extra deps" philosophy; a real env var always wins over a `.env` value if
both are set). Leave both unset and the app just starts with a blank connection/AI key — enter them
once through Settings afterward instead. Both only seed the *first-ever* `data/config.json`; the
auto-backfill in `load_config()` only fills in a key that's still blank, never clobbers one you've
already set yourself.

**Web login accounts get different, stronger treatment on purpose**: passwords in
`data/web_users.json` are hashed with `bcrypt` (a real one-way KDF), not Fernet-"encrypted" like
the DB password/AI key above — a login password only ever needs to be *verified*, never read back,
so it doesn't need to be recoverable the way a DB credential does. The session cookie is signed
(not encrypted) with a key in `data/web_session.key`, same "plain file next to what it protects"
trade-off as `scriptgen.key`. None of this substitutes for **transport security**: this app does
not terminate TLS itself, so anyone who can see the network traffic between a teammate's browser
and the server can see their login password and session cookie go by in plaintext unless you put
it behind HTTPS (reverse proxy) or a VPN first — see "Running the web version" above. This is a
materially different risk profile than the desktop app's one-person-one-machine model, since it's
now reachable by more than just you.

## Scope decisions worth knowing about

- **SQL Server only in v1** (you confirmed Oracle can wait). The connection layer
  (`app/db/mssql.py`) is the only file that's SQL-Server-specific — adding Oracle later means a
  parallel `app/db/oracle.py` using `python-oracledb` in "thin mode" (also driver-free, same
  reasoning as above) plus a provider switch in the Config dialog.
- **SQLite for internal storage** — zero setup, one file, portable with the app folder.
- **Gemini free tier for AI** — no cost, but does need internet access and is subject to Google's
  free-tier rate limits; the "no LLM, rule-based only" and "local model via Ollama" options are
  still on the table if the free API route turns out to be too limited in practice.
- **PyInstaller / Python**, not .NET — matches what you picked; the trade-off is a somewhat larger
  `.exe` and a build step that has to run on Windows (see above), in exchange for an easier-to-read
  codebase if you want to modify it yourself later.
- **Plain HTML/CSS/JS for the web frontend, no build step, no framework** — matches the rest of
  this project's "as few moving parts as possible" bias (see the pure-Python-driver reasoning
  above). No `npm install`, no bundler, no node_modules; `web/static/app.js` is a single file you
  can read top to bottom. The trade-off is more by-hand DOM code than React/Vue would need for a
  bigger UI later (Dashboard's charts, especially) — worth revisiting if/when this grows enough
  that hand-rolled DOM updates start feeling like the wrong tool.
- **One shared server-side DB connection for the web UI, not per-teammate credentials** — every
  logged-in user runs queries as the same configured account (same read-only tunnel model as the
  desktop app always used). A web login only gates access to the tool; it intentionally does not
  let each teammate bring their own DB credentials. Revisit this if teammates ever need different
  levels of DB access from each other.
- **Phase 2 (not started)**: porting Dashboard, AI Assist, the Tools tab, and a browser-based
  Config UI to the web stack — tracked as its own follow-up rather than attempted in the same pass
  as Phase 1, so Phase 1 could actually ship and be tested rather than staying unfinished
  indefinitely. The Tkinter desktop app keeps all of those working in the meantime.

## Running the tests yourself

166 unit/integration tests cover the diff engine, script/rollback generator (incl. audit-column
stamping and the WHERE-clause original-value guard), the query-formatting helper, snapshot diff,
schema validation, Dashboard stats/histogram/correlation logic, the recent/saved queries +
multi-connection config logic, the AI-assist module's offline code paths, the script-history audit
trail (`test_script_history.py`), and (`test_web_api.py`, now 58 tests) the FastAPI backend's full
route surface — auth/session-gating, query/diff/script/history, and every Phase 2 route (Settings'
connections CRUD + test + activate, AI config get/set/test, saved queries, schema validation,
snapshot export/list/diff, all five AI Assist routes, and the four Dashboard routes) — via
FastAPI's TestClient, no database or network required:

```
pip install -r requirements.txt
pytest tests/ --ignore=tests/smoke_launch.py -v
```

All 166 currently pass. The web UI's full login → run query → edit cells → generate script → view
history → manage connections/AI settings → Tools (saved queries, schema validation, snapshot
export/diff) → AI Assist → Dashboard (stats, histogram, correlation, trend) → theme-toggle flow has
also been exercised end-to-end in a real headless browser (Playwright) against a running `uvicorn`
instance with `app.db.mssql` and `app.core.ai_assist` monkeypatched to avoid any real network call,
confirming every Phase 2 page renders and works through actual HTTP + DOM interaction (and is
console-error-free), not just at the Python-function level — not part of the automated suite above
(no live-browser step is wired into CI yet), but worth knowing it's been checked this way, not just
unit-tested.

There's also `tests/smoke_launch.py`, a headless GUI construction check for the **Tkinter desktop
app specifically** (`main.py`) that builds the main window and Dashboard against a fake query
result and exercises essentially every feature that app has: the key-column picker, resizable
sidebar sash, grid highlighting, SQL syntax highlighting, the Format button, Cancel Query, the
mandatory Jira field, CSV/Excel/internal-DB export, the Dashboard (KPI cards, stats filter/export,
Trend History, Correlation, PDF export), every Tools-tab section including Script History, and the
AI Assist NL-WHERE builder + Script page's AI Review button (needs a display or a tool like Xvfb on
Linux; on Windows you can just run `python main.py` directly instead):

```
xvfb-run -a python tests/smoke_launch.py   # Linux/CI
python tests/smoke_launch.py               # Windows (real display)
```
