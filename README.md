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

**Or, more simply**: `python run_web.py` does the same thing but on a fixed port (8420) and
automatically opens it in your default browser once the server is up - no separate `uvicorn`
command to remember, no manually typing the URL. Package it as a standalone `ScriptGen-Web.exe`
with `build_web.bat` (see that file's header for how it differs from `build.bat` and
`build_web_desktop.bat`, the other two packaging options for the Tkinter app and the
pywebview-native-window wrapper respectively).

**RJ, 2026-09-14: "i want to share my local to my teammate ... we are on the same network."**
`run_web.py` binds `0.0.0.0` (every network interface), not just `127.0.0.1`, so it's reachable
from other machines on the same LAN out of the box - it auto-detects this machine's own LAN IP and
prints a second URL (`A teammate on the same network can also reach it at: http://<your-lan-ip>:8420/`)
right alongside the `127.0.0.1` one it opens for you. See "Sharing on your local network" below for
the one Windows Firewall step this usually needs the first time.

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

#### Sharing on your local network (2026-09-14)

`python run_web.py` already binds every network interface (`0.0.0.0`), so a teammate on the same
LAN can open the URL it prints (e.g. `http://192.168.1.42:8420/`) in their own browser - no config
change needed. Two things to check the first time:

1. **Windows Firewall.** The first time the server actually gets a connection attempt from another
   machine, Windows may show "Windows Defender Firewall has blocked some features of this app" -
   click **Allow access** (at least for **Private** networks; only tick Public if you know this
   network is trusted). If that prompt never appeared, or you dismissed it as Block, add the rule
   yourself from an **Administrator** PowerShell:
   ```
   New-NetFirewallRule -DisplayName "ScriptGen Web" -Direction Inbound -LocalPort 8420 -Protocol TCP -Action Allow
   ```
2. **A login for your teammate.** They authenticate with their own ScriptGen account, not yours -
   add one from **Settings > Users** (or `python -m web.manage_users add <username>` on this
   machine) rather than sharing your own credentials. They never see the SQL Server connection
   password either way - every query runs server-side on your machine, exactly as the "server-side
   and shared" note above describes; their login only gates the *tool*, and the active DB connection
   is still one shared setting for everyone on this server.

This is still plain HTTP on a LAN, not the internet - fine for a trusted office/home network per the
caveat above, but don't port-forward this through your router to the public internet without putting
TLS in front of it first.

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
app/core/hierarchy_analysis.py system-wide scan for pending PRIMARY meter hierarchies + drill-down + reading-history popup
app/core/bill_issuance_validator.py system-wide scan for accounts whose next Electricity/Water bill is stuck (ESTFAC0015) behind a Rate (176) bill still being issued (see "Bill Issuance Validator" below)
app/core/bulk_checker.py     Bulk Checker: pending bulk-account search + bill drill-down queries - ported from the standalone EWA Bulk Checker project (see "Bulk Checker" below)
app/db/bulk_checker_db.py    Bulk Checker's local collaboration state: per-account notes, shared saved searches, Trend search history (all in the shared internal SQLite db)
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

## Bulk Checker

Added 2026-09-12, ported from a standalone project (EWA Bulk Checker) into ScriptGen as its own
sidebar page, per RJ's request - "add it as new menu, implementation is the same". Finds bulk
accounts (`GCCOM_ACCOUNT_BUNCHER` groupings) that don't have a generated lot/file yet for a billing
cycle, and drills into one bulk account to see every underlying bill for that cycle. Uses the same
SQL Server connection as the rest of the app - no separate login, no separate connection config.

Two queries, both RJ's own analyst-supplied SQL from the source project, copied over unchanged in
shape/logic (see `app/core/bulk_checker.py`'s module docstring for the one real technical
difference: the source project used pytds's own `%s` bind-parameter substitution, this port inlines
each value via `sql_format.format_sql_literal` instead, matching every other `app/core` module's
convention, since `app.db.mssql.run_query` doesn't take query parameters):

- **Pending bulks search** (`build_pending_bulks_sql`) - one row per bulk account for a billing
  cycle (date range + billing period), with a status filter: **All**, **Pending** (no lot/file
  generated yet), **Generated** (a lot/file already exists), **Missing bill** (at least one
  contracted service under the bulk has no bill at all this period), **In invoicing** (a bill
  exists but hasn't been picked up into a sent lot yet).
- **Bill detail drill-down** (`build_bill_detail_sql`) - every bill under one bulk account for the
  same cycle, with its own filter: **All**, **Pending** (bill exists, not yet in a sent lot),
  **Missing** (no bill generated at all).

The page (Search card, Results grid with a status filter/search box/CSV+Excel export, per-account
Notes via a 📝 button, and the Bills drill-down card) lives in `web/static/index.html` /
`web/static/app.js` under `#page-bulkchecker` / the `bc*` functions, reusing the app's existing
`.card`/`.data-grid`/`.kpi-row`/`.hier-reading-modal-overlay` CSS rather than the source project's
own separate stylesheet. Notes (`STATUS_OPEN`/`being_handled`/`resolved` + free text per account per
billing period, any signed-in user can set) live in `app/db/bulk_checker_db.py`, a new set of tables
in ScriptGen's one shared internal SQLite db (same file every other `app/db` module writes to - see
that module's own docstring for why this project doesn't introduce three more standalone `.db`
files the way the source project did).

Live-verified against the real tunnel DB (2026-09-12): 242 real bulk accounts, 64 real bills for
one drilled-down account, notes persistence, and both export paths all confirmed working. That pass
also caught a real bug - the Outstanding KPI always summed to 0 because SQL Server money/decimal
columns come back from pytds as `decimal.Decimal`, and the original `isinstance(..., (int, float))`
check silently excluded every real value. Fixed in `web/server.py` (`bulk_checker_search`) by adding
`decimal.Decimal` to that isinstance check - written but not yet re-verified live pending a restart.

**Round 2 (2026-09-12, same day, before the restart above)** - RJ: "in the bulk bills drill down,
add a link again to open readings same as the hierarchy checker, also add filters and a quick
summary of the status... make the design better, in the billing period dropdown display id and
description only, in the date only month and year should be pickable as its always on first":
- **Readings link on each bill row.** `build_bill_detail_sql` now also selects `ss.ID_SECTOR_SUPPLY`
  so the Bills drill-down table can offer the exact same reading-history popup Hierarchy Analysis
  uses - same `/api/hierarchy-analysis/reading-history` route, same `hierRenderReadingModal`/
  `#hier-reading-modal-overlay`, not a duplicate. The column itself is hidden from the table/export
  (`BC_DETAIL_HIDDEN_COLUMNS`) since it's only there to key the popup lookup.
- **Status summary + click-to-filter, replacing the old dropdown.** The Bills drill-down now always
  fetches the full unfiltered bill list once (`bill_filter: "all"`), then computes Done (has a bill
  AND a file number) / Pending (has a bill, no file number yet) / Missing (no bill at all) counts
  and lets you click a summary card to filter the table to that status - click again to clear
  (`bcRenderDetailKpiRow`/`bcClassifyDetailRow`, same `.kpi-card-clickable`/`.is-active` pattern
  Detect All's "Needs status advance" card already used). The old `<select>` filter (which re-fetched
  from the server on every change) is gone.
- **Billing-period dropdown** now shows only `{id} — {DESCRIPTION}` (falling back to `PERIOD_NAME`
  if description is blank) instead of the first two arbitrary columns, and clicking a period also
  auto-fills the date pickers from that period's own `INITIAL_DATE`.
- **Date pickers are month-only** (`<input type="month">`, labeled "Cycle start/end month") since a
  billing cycle always starts on the 1st - `bcMonthToDate`/`bcDateToMonth`/`bcNextMonth` convert
  between the `"YYYY-MM"` picker value and the full `"YYYY-MM-01"` ISO date the backend expects.

**Live-verified 2026-09-13** against the tunnel DB after a restart (PID 27780): Outstanding KPI now
non-zero (6,926,433 for a real 248-row search - decimal fix confirmed), month pickers, billing-period
dropdown (id + description, auto-fills dates), detail status KPI cards + click-to-filter, and CSV/
Excel export all working as built.

**Bug found and fixed during that same live-verify session: the Readings button opened the reading-
history modal but nothing appeared on screen.** Root cause: `#hier-reading-modal-overlay` was still
physically nested inside `<section class="page" id="page-hierarchy">` in `index.html` - fine when
opened FROM the Hierarchy Analysis page (that section is the active one then), but Bulk Checker's
Bills drill-down opens the exact same shared element from a DIFFERENT page. `.page:not(.is-active)`
is `display:none`, and a `display:none` ancestor collapses a descendant to a 0×0 box no matter that
the descendant itself is `position: fixed` with `inset: 0` - fixed positioning escapes the normal
document flow, not the ancestor's own display. The API call and DOM update (title, 110+ reading rows)
both worked correctly every time; only the paint was affected, which is why it wasn't obvious from
the network tab. Fixed by moving the modal's markup out of `#page-hierarchy` to be a direct sibling
of every `.page` section (just before `</main>`), so it renders regardless of which page opened it.
Re-verified both call sites after the fix: Hierarchy Analysis's own Detail drill-down (still works,
no regression) and Bulk Checker's Bills drill-down (now actually visible, 110-156 real reading rows
confirmed in a live screenshot). This was a static-file-only fix - no Python changed, no restart
needed, just a browser refresh.

**Round 3 (2026-09-13)** - RJ pasted a large generic "bulk billing dashboard" feature roadmap and
asked for (1) single-account analysis and (2) "implement" from that list. Most of the 25-item
roadmap is written for a different kind of system (imports, multi-currency/branch, its own RBAC,
notifications, scheduled reports, async job queues, dark mode, API versioning) that doesn't fit
this tool - a read-only lookup against one SQL Server connection plus a small local SQLite
collaboration db, already inside ScriptGen's own single RBAC. Confirmed the scope with RJ first;
built the realistic subset instead of the full list:
- **Analyze One Account** - a standalone lookup box (new card above Results) that opens the Bills
  drill-down for one typed-in account number directly, without running the bulk search first. Reuses
  the same cycle-date/billing-period fields; `bcRunSingleAccountLookup` in `app.js`.
- **Interactive KPI cards.** The Results KPI row's Pending/Missing bill/In invoicing cards are now
  click-to-filter (`kpi-card-clickable`/`is-active`, same pattern as the Bills-detail summary above
  and Detect All's "Needs status advance" card) - click one to set that status filter and rerun,
  click again to clear. Total/Outstanding have no matching single-status filter so stay plain.
- **Sortable columns**, Results and Bills-detail tables both - click a header to sort, click again to
  reverse, same `.stats-table-th-sortable`/`.stats-table-sort-arrow` CSS and comparator
  (`_hierCompareValues`) Hierarchy Analysis already uses (task #153), just re-wired after every
  render since these two tables rebuild their `<thead>` from dynamic SQL columns instead of using a
  static one.
- **Amount-range filter** (`bc-amount-min`/`bc-amount-max`) on the Results table, filtering the
  already-loaded `pending_amount` column client-side, same as the existing text search box.
- **Saved Searches wired to the UI.** The CRUD routes below existed since round 1 with no frontend -
  now there's a "💾 Saved searches ▾" dropdown (load/delete) and a "💾 Save current as…" button in
  the Search card.
- **Trend / vs-previous-period context.** After a full (`status_filter=all`) search, a small line
  under the KPI row compares this billing period's Total/Outstanding against the nearest lower
  billing period ScriptGen has ever fully searched (`GET /api/bulk-checker/trend`, previously built
  but unused). Deliberately NOT an "aging"/days-overdue metric - nothing in the analyst-supplied
  queries defines a due date, so rather than invent one, this compares by billing period number and
  says so in the label instead of implying a calendar guarantee.

Explicitly out of scope, and why: RBAC/notifications/scheduled reports/async job queues/API
versioning/dark mode/duplicate detection/data-quality dashboard don't fit a single-connection
read-only internal tool with no import pipeline; bulk multi-select actions and an "aging" dashboard
were skipped because there's no write-back workflow or due-date concept in the underlying analyst
SQL to build them on honestly.

**Backend routes now fully wired to the frontend** (built in round 1, left unwired until this round):
- `GET/POST/DELETE /api/bulk-checker/saved-searches` - Saved Searches dropdown above.
- `GET /api/bulk-checker/trend` - Trend context line above.

**Still backend-only, no frontend UI** (out of scope for this round - no clear UI need identified
yet):
- `POST /api/bulk-checker/bulk-detail` - the same bill-detail query run once per selected account
  and concatenated, so several accounts can be exported together (`bulk_checker.MAX_BULK_ACCOUNTS`
  = 50 at a time).

**Not yet live-verified** against the tunnel DB - the query text was ported and unit-tested
(`tests/test_bulk_checker.py`, SQL-text-only, no DB) but needs a real run through the UI once the
local server is restarted (Python changes - `web/server.py`, `app/core/bulk_checker.py`,
`app/db/bulk_checker_db.py`, `web/menu_access.py` - need an actual process restart to take effect;
the static `index.html`/`app.js` changes don't).

## Performance notes

### DIFF DATES Anomaly Batch: connection reuse + live progress/rate (2026-09-13)

RJ reported the Batch/Multi-NISS page on the DIFF DATES Anomaly tab felt slow and asked for a
different approach, plus a progress readout (processed/pending/percent/items-per-second).

**Root cause.** Every NISS in a batch runs ~6 queries (detect, correct-date, item-to-bill, XML
lookup, anomalous, item-status), and `app/db/mssql.py`'s `run_query`/`get_table_columns` opened
and closed a brand-new SQL Server connection for *every single call*. That's the right default
for every other page's one-off queries, but RJ connects through a local tunnel, so the connection
**handshake** - not the query itself - dominates. A 50-NISS batch was paying that handshake cost
roughly 300 times over.

**Fix - share one connection per batch job.** `app/db/mssql.py` gained
`reuse_connection(conn_cfg)`, a context manager (built on `threading.local()`, since each batch
job runs on its own background thread) that opens one connection and makes every
`run_query()`/`get_table_columns()` call on that thread transparently reuse it for the life of the
`with` block - `web/batch_jobs.py`'s per-NISS query call sites needed zero changes. It self-heals
too: if the tunnel drops mid-batch, a cheap `SELECT 1` health check tells a merely-slow connection
from a genuinely dead one, and only a dead one triggers a single reconnect-and-retry instead of
failing every remaining NISS in the batch. `web/batch_jobs.py`'s `_run_batch` now wraps the whole
job (the up-front audit-column schema check plus the entire per-NISS loop) in
`with mssql.reuse_connection(conn_cfg):`.

**Progress + rate reporting.** `BatchJob` gained `started_at_utc`/`finished_at_utc` timestamps
(set when the background thread actually starts running, and when it reaches a terminal status),
both exposed through `to_public_dict()`. The frontend (`app.js`) computes elapsed time,
processed/pending counts, percent complete, and items-per-second from those timestamps plus the
existing `processed`/`niss_total` fields - no new server-side math needed. The Batch tab now shows
a thin progress bar plus a 4-card KPI strip (Processed / Pending / Complete % / Rate) that appears
once the job actually starts and updates on every poll.

**Test coverage.** New `tests/test_mssql_reuse.py` (7 tests) covers `reuse_connection` in
isolation: normal reuse across many queries, the self-healing reconnect-and-retry path, that a
genuinely bad query on a healthy connection is *not* retried, and that the non-reused path is
unchanged. `tests/test_batch_jobs.py` gained two tests for the new timestamp fields. Every
existing batch integration test in `tests/test_web_api.py` fakes `mssql.run_query`/
`get_table_columns` wholesale but never touched `mssql.reuse_connection` directly - since
`_run_batch` now calls it directly, those tests needed a `reuse_connection` no-op stand-in
(`_noop_reuse_connection`) added alongside each of the three batch-fixture monkeypatch sites, so
they never attempt a real connection to the fake test config's `"localhost"`.

**Live-verify this one after your next restart** - `app/db/mssql.py` and `web/batch_jobs.py` are
Python files, so the connection-reuse speedup needs a fresh server process before it's testable
against the real tunnel DB.

### Detect All / Single NISS: clearer "Needs status advance" label, and a real removal-vs-reconnection breakdown for "All cycle" (2026-09-13)

RJ flagged that "Needs status advance" wasn't clear, and asked whether the "All cycle" filter's
"No" means only removal-type readings, or reconnection readings too - then asked for the actual
breakdown to be implemented, in both Single NISS ("individual") and Detect All.

**"Needs status advance" -> renamed and explained.** This flag means an item-to-bill's
`GCCOM_ITEMS_TO_BILL.STATUS` is still `STTOBILL00` ("pending") and hasn't been advanced to
`STTOBILL01` yet - Generate Cleanup Script performs that one-time move for whatever's checked. The
column header, filter label, filter options, and the KPI card are all reworded to say this
directly ("Status still pending (STTOBILL00)?" / "Billing status still pending?") instead of the
unexplained "Needs status advance", with a full tooltip for anyone who wants the exact mechanics.

**"All cycle" "No": confirmed live, not guessed.** Queried `OUC_COMMON_ADMIN.GCGT_RE_READING_TYPE`
directly through the app's own Workspace query runner (server restarted and reachable by then) -
the full table, all 16 rows:

| Code | Meaning | | Code | Meaning |
|---|---|---|---|---|
| TIPTL00001 | Installation | | TIPTL00010 | Disconnection |
| TIPTL00002 | **Removal** | | TIPTL00011 | **Reconnection** |
| TIPTL00003 | Cycle | | TIPTL00012 | Prepayment |
| TIPTL00004 | Control | | TIPTL00014 | Prepayment Control |
| TIPTL00005 | Direct Connection | | TIPTL00015 | Net Adjustment |
| TIPTL00006 | Out Of Cycle | | TIPTL00016 | Credit Adjustment |
| TIPTL00007 | Usage Adjustment | | TIPTL00017 | Distribution |
| TIPTL00009 | Off Season Adjustment | | TIPTL00018 | Sale of Water |

`CYCLE_READING_TYPES` (what makes `ALL_CYCLE = 1`) is `TIPTL00003`/`TIPTL00005` - Cycle and Direct
Connection only. So the answer to RJ's question: **"No" includes Reconnection (TIPTL00011) just as
much as Removal (TIPTL00002)** - it's a catch-all across the other 14 codes above, never scoped to
just one. Side-finding worth flagging: `READING_TYPE_ORPHAN_USAGE` (TIPTL00011), the code Part 6's
own cleanup rule already singles out for special handling, turns out to actually be the
**Reconnection** type by this lookup - its "orphan usage" name in the code was always just this
module's own functional label for what Part 6 does with it, not its real business meaning.

**The actual breakdown, implemented, both places RJ asked for it:**
- *Single NISS* (`app/core/date_anomaly.py`'s `build_detect_query`) - each anomalous reading now
  gets a `READING_TYPE_DESC` column via a `LEFT JOIN` to `GCGT_RE_READING_TYPE`, plus an
  `IS_CYCLE_READING` bit. The Detect table's new "Reading Type" column shows the real word
  ("Removal", "Reconnection", "Cycle", ...) per reading, with a non-cycle one visually flagged
  (orange `.badge-noncycle` pill).
- *Detect All* (`build_detect_all_anomalies_query`) - a new `NON_CYCLE_READING_TYPES` column, a
  correlated subquery (`STUFF` + `FOR XML PATH` - the broadly-compatible SQL Server string-concat
  idiom, not `STRING_AGG`, since the SQL Server version here isn't confirmed to be 2017+) that
  lists the *actual* distinct non-cycle type name(s) linked to that item, comma-separated (e.g.
  "Removal, Reconnection"), blank when All Cycle is Yes. Shown as a new "Non-cycle type(s)" table
  column (same orange pill styling) and included in both the CSV and the server-built `.xlsx`
  export.

Server-side change this time (`app/core/date_anomaly.py`, `web/server.py`), not just static files -
**needs a server restart** before the new columns are live-testable, same as the batch fix above.
Tests: `tests/test_date_anomaly.py` gained coverage for both new query shapes; `tests/test_web_api.py`
gained `test_date_anomaly_detect_includes_reading_type_description`,
`test_date_anomaly_detect_all_returns_non_cycle_reading_types`, and a
`..._blank_when_column_missing` degrade-gracefully counterpart, plus the existing xlsx-export
shape tests were updated for the renamed/added columns.

### DIFF DATES correction script: strip the removed reading's own `<reading>` block from XML (2026-09-13)

RJ: "in the update of the xml for diff date, we need to remove the section of the non cycle that
we are removing sample: `<reading><idReading>1045434621</idReading></reading>` but only for the
idReading that we are deleting in reading item to bill."

Part 6 of `build_correction_script` already `DELETE`s a non-cycle (Reconnection/`TIPTL00011`)
reading's `GCCOM_READINGS_ITEMSTOBILL` link and resets the reading itself. What it didn't do until
now: once that link is gone, the reading no longer belongs to the bill, but its own
`<reading><idReading>...</idReading></reading>` block was still left sitting in
`GCCOM_ITEMS_TO_BILL_XML.XML_TO_BILL` - the XML and the relational data would disagree about which
readings the bill actually covers.

**New:** `remove_reading_nodes(xml_text, id_readings)` in `app/core/date_anomaly.py` - parses the
XML (same namespace-agnostic-by-local-name approach as `patch_xml_dates`), finds every `<reading>`
element whose `<idReading>` child matches one of the given ids, and removes *only those* from the
tree (every other `<reading>` block, including ones for readings that stay linked, is left
untouched). Matches by string comparison (an id might come back from the DB driver as int, string,
or Decimal - a strict type check would silently match nothing). Standard-library `ElementTree` has
no `.getparent()` like lxml does, so a parent map is built up front to actually detach a matched
element from its enclosing node.

**Wired into Part 4, not a separate step:** `build_correction_script` now computes `orphan_ids`
(Part 6's list) up front, maps each one to the item-to-bill/XML document(s) it's linked to (via the
same pre-deletion `item_to_bill_map` Part 2 already uses - the `DELETE` hasn't run yet at
script-generation time), and for any XML document affected, strips the matching `<reading>`
block(s) on the *same* parsed tree `patch_xml_dates` already patched the dates on - so each
`GCCOM_ITEMS_TO_BILL_XML` row still gets exactly one `UPDATE`, not two competing ones. The
statement's own comment says what happened either way, e.g. `-- XML 500: updated node(s): initDate,
readingFromDate; removed <reading> node(s) for id_reading [101]`. If an orphan reading's `<reading>`
block isn't actually found in its XML (shouldn't normally happen, but data doesn't always agree
with itself), that's surfaced as a warning rather than silently doing nothing.

No caller changes needed - `web/server.py`, `web/batch_jobs.py`, and the desktop app's
`app/ui/main_window.py` already pass `item_to_bill_map`/`orphan_usage_id_readings` into
`build_correction_script` (see the earlier Part 6 round), so this activates automatically
everywhere a correction script is generated: Single NISS, Batch, and Detect All's
Generate-for-Selected (which delegates to Batch).

Tests: `tests/test_date_anomaly.py` gained 8 unit tests for `remove_reading_nodes` in isolation
(matching one/multiple ids, string-vs-type matching, unmatched ids left alone, malformed XML,
namespaced XML, independence from `patch_xml_dates`) plus 3 integration tests through
`build_correction_script` itself (the matching reading's block disappears while a sibling one
survives; an orphan reading linked to a *different* item's XML doesn't touch this one; a missing
expected `<reading>` block surfaces as a warning).

Two more things were fixed after early testing showed the app feeling laggy, both around loading
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

### DIFF DATES correction script: fixed a real bug - the XML UPDATE was targeting the wrong row (2026-09-13, same day)

RJ caught this live: "you are updating using id_item_to_bill, you need to get first the id_xml
from gccom_item_to_bill and update with that id." He was right, and it was a real, pre-existing
bug, not just a wording issue.

**What was wrong:** since the very first Diff Date Anomaly round, `date_anomaly.py` assumed
`GCCOM_ITEMS_TO_BILL_XML.ID_XML` was the SAME value as `GCCOM_ITEMS_TO_BILL.ID_ITEM_TO_BILL` - the
functional spec's original example query filtered `ID_XML` directly by a value already known to be
an item-to-bill id, with no join shown, so that was the working assumption (flagged from day one as
unverified). It was wrong. The real schema (confirmed in `docs/db_schema_reference.md`'s live FK
list) has `GCCOM_ITEMS_TO_BILL.ID_XML -> GCCOM_ITEMS_TO_BILL_XML.ID_XML` - a real foreign-key column
on `GCCOM_ITEMS_TO_BILL` that has to be looked up first. Every `UPDATE ... WHERE ID_XML = ...` this
tool ever generated was targeting the wrong row (or no row at all) whenever an item's real `ID_XML`
differs from its own `ID_ITEM_TO_BILL` - which, going by the item ids seen in testing so far, looks
like the normal case, not an edge case.

**Fix:** a new `build_item_xml_id_query(id_item_to_bills)` in `app/core/date_anomaly.py` runs
FIRST - `SELECT ID_ITEM_TO_BILL, ID_XML FROM GCCOM_ITEMS_TO_BILL WHERE ID_ITEM_TO_BILL IN (...)` -
and its result (`item_to_xml_map: dict[item_id] -> real id_xml`, skipping any NULL `ID_XML`) feeds
`build_xml_lookup_query` instead of the item ids themselves. `build_correction_script` gained a new
`item_to_xml_map` parameter and Part 4 now resolves each item-to-bill id's real `ID_XML` through it
before touching `GCCOM_ITEMS_TO_BILL_XML` at all - an item with no `ID_XML` (NULL column) or whose
`ID_XML` has no matching row gets a warning and no Part 4 statement, instead of a guess. Wired into
all three surfaces that build a correction script: `web/server.py` (Single NISS resolve/generate),
`web/batch_jobs.py` (Batch, and Detect All's Generate-for-Selected which delegates to it), and the
desktop app's `app/ui/main_window.py`. 5 new tests in `tests/test_date_anomaly.py` (the new query
builder, and a dedicated case proving the UPDATE targets the real `ID_XML` - 9001 - not the item id
- 500 - plus the two "can't resolve" warning paths); `tests/test_web_api.py`'s Date Anomaly fixtures
updated to answer the new lookup query (defaulting item id == id_xml so existing tests didn't need
rewriting, since the fixture's whole point is to isolate what each test is actually checking).

**Also added, same message:** a finer non-cycle breakdown filter on the Detect All page - "Cycle
reads only" became "Cycle / non-cycle composition" with four new options (Removal only,
Reconnection only, both, or neither) alongside the existing Cycle-only/any-non-cycle choices -
substring-matched against the existing `non_cycle_reading_types` column text, no new query needed.

### DIFF DATES correction script: fixed a second real bug - the "correct date" could be the wrong reading when two tie on billing period (2026-09-14)

RJ caught this live too, on NISS `20022221-101`: "why detected the correct date as 2026-03-09
00:00:00 where it should be 26/03/2026."

**What was wrong:** `build_correct_date_query` picks the single reference date to apply to every
anomalous reading via `SELECT TOP 1 ... ORDER BY r.ID_BILLING_PERIOD DESC` - but that `ORDER BY` has
no tiebreaker. When more than one reading shares the same (highest) `ID_BILLING_PERIOD` and is also
`READ_STATUS = 7000STSRED` (correctly billed), SQL Server's `TOP 1` can return *either* one - there's
no guaranteed order for ties. Confirmed live: NISS `20022221-101`'s billing period `10000000232` had
TWO correctly-billed readings - one Removal-type (`TIPTL00002`) dated 2026-03-09, and one Cycle-type
(`TIPTL00003`) dated 2026-03-26 (the anomalous reading itself is also Cycle-type) - and the query was
returning the earlier, wrong one.

**Fix:** added `r.READING_DATE DESC, r.ID_READING DESC` as secondary/tertiary sort keys, so "the most
recent correctly-billed reading" (the spec's own wording) means literally the reading with the latest
`READING_DATE` among the tied rows, with `ID_READING` as a final purely-deterministic tiebreak if even
the date ties. No caller changes needed - every surface (Single NISS, Batch, desktop) calls this one
query builder. New test in `tests/test_date_anomaly.py` asserting the full `ORDER BY` clause.

**Worth flagging:** this bug could have silently applied the wrong correct-date to any NISS whose
correctly-billed history happens to have two readings tied on the same billing period - not just
`20022221-101`. Any correction script generated before this fix, for a NISS where that's possible,
is worth a second look.

### DIFF DATES correction script: fixed a third real bug - billing-period-first sort skipped a later, correctly-billed removal reading (2026-09-15)

RJ caught this live too, on NISS `10328684-101`: "we did not consider billed removal reading as
correct previous billed reading, the correct date should be 30/07/2026."

**What was wrong:** the 2026-09-14 fix above sorted `ORDER BY r.ID_BILLING_PERIOD DESC, r.READING_DATE
DESC, r.ID_READING DESC` - billing period first, date only as a tiebreaker *within* the same period.
That assumes `ID_BILLING_PERIOD` always increases with `READING_DATE`, which isn't true across a
removal/reinstall: confirmed live, this NISS's billing period `10000000235` held a billed Removal
reading dated **2026-07-30**, while the higher-numbered period `10000000236` held a billed Cycle
reading dated only 2026-07-22. Sorting by billing period first picked period 236's earlier-dated
reading over period 235's later, correctly-billed removal - the opposite of "most recent."

**Fix:** flipped the sort priority to `ORDER BY r.READING_DATE DESC, r.ID_BILLING_PERIOD DESC,
r.ID_READING DESC` - `READING_DATE` is now the primary key, matching "most recent correctly-billed
reading" literally, with billing period and reading id kept only as tiebreakers for same-date ties
(preserves the 2026-09-14 fix's own case). Verified live both ways: the old order returned
2026-07-22 (wrong), the new order returns 2026-07-30 (RJ-confirmed correct). No caller changes
needed - same shared query builder. Test updated in `tests/test_date_anomaly.py`.

**Worth flagging:** same caveat as the 2026-09-14 fix - any correction script generated before this
one, for a NISS whose recent billing periods aren't in strict date order (removals/reinstalls are
the known trigger), is worth a second look.

## Bill Issuance Validator (new, 2026-09-15)

New top-level page (sidebar: **Bill Issuance Validator**, 🧾). Finds accounts whose next
Electricity/Water bill is stuck behind a Rate bill still being put to collection - a real,
recurring billing-pipeline bottleneck, not a data-quality bug like DIFF DATES Anomaly.

**RJ's own starting query and rules** (verbatim, 2026-09-15):

```sql
select distinct pf.id_payment_form, pf.reference, nt.update_date
from gccom_notice_tmp nt
join gccom_bill b on b.id_bill = nt.id_bill
join gccom_payment_form pf on pf.id_payment_form = b.id_payment_form
where nt.cod_status = '5000NOTEMP'
  and b.billing_status = 'ESTFAC0012'
order by nt.update_date;
```

"base on this, I wan to get all records where it has only 1 recored for the billing period and the
id_offered_service is 176, and using the same id_payment_form, check the table gccom_bill with
id_offered_service = 1 or 19 and the billing_period is 1 more than our bill with 176 id_offered
service, next the status of the bill on the + 1 id_billing_period should be ESTFAC0015."

**What each code means**, confirmed live against `GCCOM_BILL_STATUS` and
`GCCOM_COMPANY_OFFERED_SERVICE` before building anything: `ESTFAC0012` = "En proceso de puesta al
cobro" (bill still being issued); `ESTFAC0015` = "En espera de otros servicios" (bill waiting on
another service's bill) - literally the system's own "I'm stuck on a dependency" flag; offered
service `176` = "Rate", `1` = "Electricity", `19` = "Water".

**The full chain** (`app/core/bill_issuance_validator.build_stuck_bills_query`): start from RJ's own
query (pending notice + bill still issuing), narrow to accounts whose billing period has **exactly
one** `GCCOM_BILL` row and that row is the Rate (176) bill, then look up the SAME account's
Electricity/Water bill for the **next** billing period (`+1`) and keep only the ones still sitting
at `ESTFAC0015`.

**No duplicates (RJ, 2026-09-15): "i dont want duplicates, if you find ele, stop otherwise if it is
not found check water."** An account whose next period has BOTH Electricity and Water stuck used to
surface as two rows; it now surfaces as exactly one, via `ROW_NUMBER() OVER (PARTITION BY account,
period ORDER BY <Electricity first>)` keeping only rank 1 - Electricity wins whenever it's itself
one of the stuck rows, Water only shows up when Electricity isn't.

**Verified live:** of ~3,800 accounts matching RJ's own starting query, ~880 have exactly one bill
in their period and it's a Rate bill, and (after the Electricity-first dedupe) ~593 rows - one per
account, zero duplicates - match the full chain: 576 Electricity-preferred, 17 fell back to Water.
Exact counts drift slightly run to run (live production data), the one-row-per-account shape is
what's guaranteed.

**Page:** a single **Scan for Stuck Bills** button runs the query system-wide (capped at 2,000 rows,
`BILL_ISSUANCE_DEFAULT_LIMIT`), a KPI row summarizes accounts blocked / Electricity blocked / Water
blocked (Electricity OK), the table is click-to-sort on every column (same convention as Hierarchy
Analysis), and **Export CSV** downloads whatever's currently sorted/visible. No filters or drill-down
yet - the flat result set already carries both the blocking (Rate) bill and the blocked (next-period)
bill's ids - happy to add either if it turns out to be needed day-to-day.

**API:** `POST /api/bill-issuance/detect` (stateless, same shape as `/api/hierarchy-analysis/detect`
- no request body, re-runs the query fresh every call). Menu id `billissuance`, same per-role
visibility control as every other page (Settings > Menu Access).

**Restructured into an in-page sub-nav (2026-09-15):** the page now opens with a tab bar (same
`.da-subnav` idiom as DIFF DATES Anomaly), with the feature above living under **Case 1 - Prev Month
Rate Only**, to make room for further account-level "billing didn't complete cleanly" patterns.

### Case 2 - Terminated Account Period Mismatch (new 2026-09-15, redesigned 2026-09-16/17)

RJ's own follow-up request (verbatim): accounts where every `GCCOM_CONTRACTED_SERVICE` row is
Terminated, but the account still has bills whose `ID_BILLING_PERIOD` doesn't agree with each other.
RJ's own worked example, account `342702`: 3 such bills - 2 at period 236 (Water, Sanitary), 1 at
period 237 (Electricity).

**Redesign (2026-09-16/17), RJ's own words:** "mostly this will be the final bill for each service,
so we need to be sure that all service has bills in invoicing status, having the same billing date as
the termination date, or the bill is in status invoiced but also have the same termination date as
the billing date of gccom_bill... I need a filter for this cases where the bill is already invoiced
and having same termiantion date." Plus: "the idea is i only want to see the accounts, maybe its a
drill down to show the bills... the filter is for me to know the cases where it is complete only that
the other service bills are already invoiced." Three business-rule decisions confirmed with RJ:
termination date = `GCCOM_CONTRACTED_SERVICE.END_DATE` (confirmed live - matches a real terminated
service's bill `BILLING_DATE` exactly; `DROP_DATE` does not, it trails by ~1 day); target period =
`MAX(ID_BILLING_PERIOD)` among the account's own matched final bills; and the UI groups by account
(one row per account) with a drill-down for per-service detail and a Complete/Needs-action filter.

**The query** (`app/core/bill_issuance_validator.build_terminated_period_mismatch_query`): for every
Terminated service (optionally scoped to the last `days_back` days by `END_DATE` - 60 by default),
find its own final bill - the `GCCOM_BILL` row for that same service (joined on
`ID_CONTRACTED_SERVICE`, not `ID_PAYMENT_FORM`+`ID_OFFERED_SERVICE`) dated exactly its termination
date and either invoicing (`ESTFAC0012`) or invoiced (`ESTFAC0005`). A service with no such bill at
all is itself flagged `NEEDS_UPDATE` (nothing to align, but worth investigating). Target period is
`MAX(ID_BILLING_PERIOD)` among an account's matched final bills; `ACCOUNT_HAS_ISSUE` rolls that up
per account for the Complete filter. Only accounts where every service is Terminated are returned.
`web/server.py` groups the per-service rows into one object per account for the UI.

**Performance note - a real, significant bug found and fixed this round.** Every shape of this query
(the original UNION/NOT EXISTS version, and two different rewrites of the 2026-09-17 redesign,
including one already joining on the correctly-indexed `ID_CONTRACTED_SERVICE`) timed out at the
app's 120s cap. The cause wasn't the query shape at all: `app/core/sql_format.py`'s
`format_sql_literal` rendered every string literal as `N'...'` (nvarchar), but every status/code
column this app filters on (`GCCOM_BILL.BILLING_STATUS`, `GCCOM_CONTRACTED_SERVICE.STATUS`, etc. -
confirmed live via `sys.columns`) is plain `varchar`. Comparing a `varchar` column to an `N'...'`
literal makes SQL Server implicitly `CONVERT()` the column to compare them, which silently disables
index seeks on every index leading with that column - regardless of how well-designed the join
otherwise is. Fixed by changing `format_sql_literal` to emit plain `'...'` (safe either way - it
still matches correctly against genuinely `nvarchar` columns, it just doesn't force a conversion on
the `varchar` ones this app actually has). This is a **project-wide fix**: every query builder that
goes through `format_sql_literal` benefits, not just this one - it's the most likely real cause behind
other slow/timing-out queries in this app (see the DIFF DATES Batch performance task). Live-confirmed
before/after on the exact same query shape: `N'...'` literals - 120s timeout; plain `'...'` literals -
~6-8s.

**Verified live** (60-day default window, 2026-09-17): a 6-month window returned 94,218 terminated-
service rows across 27,166 accounts, 16,138 of them needing action, in ~8s. The 60-day default keeps
a normal scan well under the row cap so results aren't truncated mid-account; RJ can widen the lookback
via the UI dropdown (14/30/60/180 days or All time) or the API's `days_back` param. Exact counts drift
run to run since this is live production data.

**Page (Case 2 tab):** **Scan for Mismatches** runs the query (capped at 2,000 rows,
`TERMINATED_PERIOD_DEFAULT_LIMIT`), a KPI row summarizes accounts/bills involved and how many need an
update, the table is click-to-sort with orange-highlighted rows for `NEEDS_UPDATE = Yes`, and
**Export CSV** downloads whatever's currently sorted/visible. A **Generate Update Script** button
(one `UPDATE GCCOM_BILL SET ID_BILLING_PERIOD = <target>` + audit-column statement per bill needing
it, same header/footer/clean-script conventions as DIFF DATES Anomaly's correction scripts) re-runs
the detection query fresh right before generating rather than trusting the last scan - checking
specific rows in the table scopes the script to just those accounts; leaving nothing checked
generates for every flagged account. Every generated script is recorded to Analysis History
(`script_history.KIND_BILL_ISSUANCE`), same as every other script this app produces.

**API:** `POST /api/bill-issuance/case2/detect` (stateless, no request body) and
`POST /api/bill-issuance/case2/generate` (`id_payment_forms: string[]` to scope, empty = every
flagged account; `program`, `clean` - Editor/Admin only, same `require_editor` gate as every other
Generate route).

**Further scoping + design pass (2026-09-17, later same day).** RJ, verbatim: "for the invoicing
validator the color is not good, make it more modern design, also we are only checking where the
accounts exists in gccom_notic_tmp where it is in status pending validation, add the filters and
sort." Three changes:

- **Business-rule scoping.** `build_terminated_period_mismatch_query`'s `terminated_services` CTE
  now requires an `EXISTS` match against `GCCOM_NOTICE_TMP` for the same account with
  `COD_STATUS = '5000NOTEMP'` - confirmed live via `GCCOM_NOTICE_TMP_STATUS` that this code means
  "For validation", the same "pending" status Case 1 already keys off. `GCCOM_NOTICE_TMP` has its
  own `ID_PAYMENT_FORM` column with a supporting index (`IDX_GCCOM_NOTICE_TMP_02` on
  `(ID_PAYMENT_FORM, COD_STATUS)`), so this is a plain indexed `EXISTS`, not a join through `ID_BILL`
  like Case 1 needs. This is a real scoping rule, not cosmetic: live-confirmed on the 60-day window,
  9,593 terminated accounts narrows to 1,217 with a pending-validation notice - most terminated
  accounts have nothing pending review at all.
- **Modernized colors, scoped to this page.** The "How this works" banners on both Case 1 and Case 2
  moved from the shared `.warning-banner` (an olive/amber "caution" look that was a semantic mismatch
  for a purely informational callout) to a new `.biss-info-banner` in the app's own accent blue.
  Case 2's "Needs action" row highlight moved off `.row-multi-period` (borrowed from an unrelated Date
  Anomaly concept, a heavier/muddier orange) onto a new dedicated `.row-needs-action` class - same
  lighter-pastel-background + colored-left-border shape as the app's other semantic row classes
  (`.row-not-billed`, `.row-checking-period`, `.row-primary-only`), in amber to match the KPI card's
  own "Accounts needing action" styling. Both cases' KPI cards got the Dashboard's colored top-accent-
  bar treatment (amber for needs-action, green for complete, blue for services/electricity/water).
- **Search filter.** Case 2 already had sortable column headers; added a plain substring search box
  (`#biss2-search`, filters by account reference) alongside a proper 3-way status filter (see below)
  - same client-side, re-query-free convention as Detect All's own search box.
- **Status filter.** The old single "Show Complete accounts too" checkbox became a `#biss2-status-
  filter` dropdown: Needs action only (default) / Complete only / All - RJ's own words, "I need to
  have a filter to see the complete one, or to see only the ones with missing."

**A real false-positive found and fixed the same day.** RJ flagged account `1076362704`
(`ID_PAYMENT_FORM` 3134) as wrongly included, verbatim: "where there is no bill in notice tmp with
status pending validation and bill is in status invoicing." Investigated live: the account DOES have
a `GCCOM_NOTICE_TMP` row with `COD_STATUS = 5000NOTEMP` - but that notice's own linked bill
(`nt.ID_BILL` -> `GCCOM_BILL` 1072467931) is `ESTFAC0007` ("Anulada" / voided), not `ESTFAC0012`
(invoicing). A pending-validation notice pointing at a bill that's since been voided isn't a live
"needs review" signal. Fixed by joining `GCCOM_NOTICE_TMP` to `GCCOM_BILL` on `ID_BILL` and requiring
the linked bill still be `ESTFAC0012` - the same combined "notice pending + bill invoicing" pattern
Case 1 already uses, just not carried over to Case 2's first pass. Live-confirmed: narrows the 60-day
window further, from 1,335 accounts (notice-status-only) to 1,064 (notice + bill still invoicing);
account 3134's own match count against the tightened check is 0.

**Row redesign + account copy button (2026-09-13, same-day follow-up).** RJ, verbatim: "its difficult
to copy the account, also can you use a different look and feel, a modern one where each row is
clearly recognized the design is so bad." The previous design toggled the account's row open/closed
on click of the number itself, with no way to grab just the account for pasting elsewhere - and the
row highlight was a single flat amber wash across every cell, easy to lose track of where one row
ended and the next began. Redesign, scoped entirely to `#biss2-table` (Case 1 and every other
`.data-grid` in the app keep their previous look):

- **Copy button.** Each account number now has its own copy button (`.biss2-copy-btn`) next to it -
  `biss2CopyAccount()` tries `navigator.clipboard.writeText()` first, falls back to a hidden-textarea
  `execCommand("copy")` if that's unavailable or denied (some embedded/kiosk browser contexts block
  the async Clipboard API outright even for a genuine click), and flashes a checkmark/X on the button
  itself for 1.2s as feedback. `stopPropagation()` keeps the click from also triggering row-expand.
- **Real per-row separation.** Rows get their own bottom border, generous padding, and a hover
  background instead of relying purely on a background-color wash to read as distinct rows.
- **Status pills.** Plain "Needs action"/"Complete" text became rounded pill badges
  (`.biss2-status-pill`, amber/green) matching the KPI cards' own colors, both in the account rows and
  ("Needs update"/"OK") in the per-service drill-down rows.
- **Monospace account number.** `.biss2-account-num` uses a monospace font stack so the digits are
  easier to scan and compare at a glance.

Live-verified in-browser: toggling a row's expand/collapse still works via the account number or the
chevron, is fully decoupled from the copy button, and the copy button's clipboard-write/fallback/visual
-feedback logic all run correctly (confirmed via DOM inspection - the automated browser tool's own
script-dispatched clicks don't carry real user-activation, so clipboard permission is denied in that
harness specifically; a genuine mouse click from RJ carries user-activation and should succeed via the
primary `navigator.clipboard.writeText()` path in a normal browser tab).

**Real bug fixed: exact-datetime join was silently mis-flagging most accounts (2026-09-13, same
day).** RJ flagged account REFERENCE `1059650711`, verbatim: "check account 1059650711, it has all
the service bill on the termination date, by the way we need cycle bills TFGEN0001 something in
bill_type of gccom_bill, the only issue is that it is in a different billing period and its already
issued, so this account should be marked ok, then tagged as complete with other bill invoiced in
another billing period." Investigated live and found two compounding issues:

- **The final-bill join compared full datetimes, not just dates.** `GCCOM_CONTRACTED_SERVICE.END_DATE`
  can carry a non-midnight time-of-day component (this account's Rate/176 service: `2026-08-30
  19:45:30`), while `GCCOM_BILL.BILLING_DATE` is always midnight - so the old `b.BILLING_DATE =
  ts.END_DATE` equality could never match that service's real final bill at all. Confirmed this isn't
  a one-off: ~59% of Rate/176 terminations in a 60-day sample carry a non-midnight `END_DATE`. Fixed
  by comparing DATE only, via a `>=` / `< DATEADD(DAY, 1, ...)` range on the un-wrapped
  `BILLING_DATE` column rather than a `CAST()` on it - keeps any index on `BILLING_DATE` sargable,
  the same lesson as the earlier `N'...'`-literal performance fix.
- **An already-invoiced bill in a different period isn't a live problem.** Once date-matched, this
  account's Rate service's real final bill (period 236) turned out to already be
  `BILL_STATUS_INVOICED` (`ESTFAC0005`) while the account's other 3 services landed at period 237
  (still `BILL_STATUS_ISSUING`/`ESTFAC0012`). RJ's own call: an already-issued bill has gone out to
  the customer and "correcting" its period now would be pointless - and the generated UPDATE script
  could never safely have targeted it anyway. `NEEDS_UPDATE` now only fires on a period mismatch when
  the bill is still `ESTFAC0012` (not yet finalized); an already-`ESTFAC0005` bill in a different
  period is accepted as-is.
- **Added a `BILL_TYPE = 'TFGEN00001'` requirement** to the final-bill match per RJ's explicit ask
  (`GCCOM_BILL.BILL_TYPE`, confirmed live via `OUC_ADMIN.GCCOM_GENERABLE_BILL_TYPE`: `TFGEN00001` =
  "Contracted Service Bill", the regular/cycle type, vs. ~13 other `TFGEN1xxxx` one-off charge types
  like deposits and reconnection fees) - a defensive correctness fix so a one-off charge bill sharing
  the termination date can't be mistaken for the real final bill.

**Live-confirmed impact, same 60-day window:** total account count is unchanged (963 - the
notice-pending scoping wasn't touched), but the Complete/Needs-action split flips dramatically: from
946 needing action / 17 Complete to **344 needing action / 619 Complete**. The exact-datetime bug
alone was silently mis-flagging the large majority of these accounts - not because their billing was
actually broken, but because the query itself couldn't see their real final bill. Account 1059650711
itself: all 4 services now show `NEEDS_UPDATE = 0`, confirmed live.

## Bill Issuance Validator Case 3: All Contract Status - Bills Complete (2026-09-14)

RJ, verbatim: "for the third case of bill issuance validator we will call it 'All Contract Status -
Bills Complete', add filters and dashboards as well ... here is the query to detect them, you can
always optimize, provided that it will give us the same result", followed by his own working SQL
(one hardcoded billing period, `10000000237`). Unlike Case 1 and Case 2, this case is
**read-only/informational** - it finds accounts where billing is already complete for a period, so
there's nothing to generate an UPDATE script for.

**What it finds.** Per account (scoped to accounts with a pending notice on a still-invoicing bill -
the same `GCCOM_NOTICE_TMP`/`ESTFAC0012` gate Case 1 and Case 2 both use), compares the account's
total contracted-service count across every non-deleted status (`ESTSC00002` Vigente/Active,
`ESTSC00007` Suspendido/Suspended, `ESTSC00003` "Baja pendiente de facturar", `ESTSC00004`
Baja/Terminated - confirmed live via `GCCOM_CONTRACT_SERV_STATUS`) to its count of still-invoicing
cycle bills (`BILL_TYPE = 'TFGEN00001'`, `BILLING_STATUS = 'ESTFAC0012'`) for ONE billing period.
When the two counts are exactly equal, nothing's missing for that account in that period - "Bills
Complete." `WITH_ACTIVE_CONTRACT` is `YES` when the account still has any non-Terminated service,
`NO` when every service is already Terminated (RJ's own CASE expression, unchanged).

**"Repeat this for all the billing period of 2026 ... 1 by 1 to obtain the correct result."**
Confirmed live via `GCCOM_BILLING_PERIOD`: 2026 is exactly 12 periods (IDs `10000000229`-
`10000000240`, one per calendar month, no gaps/overlap). Rather than hardcode those 12 IDs or
literally loop/UNION the query 12 times, `build_bills_complete_query` widens the single `WHERE
B.ID_BILLING_PERIOD = <one id>` filter to a live subquery against `GCCOM_BILLING_PERIOD` itself
(`WHERE YEAR(INITIAL_DATE) = 2026`), keeping the bill subquery's own `GROUP BY ID_PAYMENT_FORM,
ID_BILLING_PERIOD` unchanged. This is mathematically identical to running the original query once
per period and UNIONing the results - the GROUP BY already partitions bill counts strictly by
(account, period), and `GCCOM_CONTRACTED_SERVICE` carries no period column at all, so widening the
period filter can only ever add more (account, period) combinations to check, never change what
"equal" means for any one of them. **Verified live (2026-09-14):** a literal translation of RJ's own
query shape (`WHERE ... IN (12 real 2026 period ids)`) and this module's optimized rewrite returned
byte-for-byte identical rows against the real tunnel DB.

**Optimization** (RJ's own explicit permission - "you can always optimize, provided that it will
give us the same result"): RJ's own `cs` subquery aggregates `GCCOM_CONTRACTED_SERVICE` with no
account scoping first - confirmed live, that's 4,620,339 rows, most of the table, before any join
narrows it down. Since the final result can only ever include accounts that already passed the
pending-notice gate AND have a matching period bill (confirmed live: 2,960 such accounts, against a
table with millions of contracted-service rows), `build_bills_complete_query` computes the
pending-notice account set and the per-period bill counts FIRST, then aggregates
`GCCOM_CONTRACTED_SERVICE` only for that already-narrow account list - same "filter before you
aggregate a huge table" lesson as Case 2's own `account_status` CTE. Confirmed live: only 1 account
in the real 2026 dataset (as of 2026-09-14) currently satisfies the full "Bills Complete" condition
- the equality is a narrow filter by design, most accounts with a pending notice still have services
whose bill count doesn't yet match.

**UI.** New "Case 3 - All Contract Status - Bills Complete" sub-nav tab, flat one-row-per-
(account, period) table (an account can appear more than once if more than one 2026 period is
already complete for it), sortable columns, a client-side account-number search box, a KPI row
(row count, distinct accounts, count with an active contract), and two server-side filters (billing
period dropdown - populated live from `GCCOM_BILLING_PERIOD` - and With Active Contract YES/NO)
that re-scan on demand, same idiom as Case 2's own look-back dropdown. `POST
/api/bill-issuance/case3/detect` and `GET /api/bill-issuance/case3/billing-periods` (the period
dropdown's own lookup) are both read-only/`require_login`, no `require_editor` needed since this
case never writes anything.

## Bill Issuance Validator Case 2: split "needs action" into missing-bill vs period-mismatch (2026-09-14, same day)

RJ, verbatim: "you did not add the filter to see the ones that need action where the bill is
missing." The existing "Needs action" status filter already existed but conflated two different
reasons a service can be flagged (see `build_terminated_period_mismatch_query`'s own `flagged` CTE):
`ID_BILL IS NULL` (no bill at all was found dated the service's termination date - nothing for the
Generate button to correct, needs manual investigation) and a bill that WAS found but its
`ID_BILLING_PERIOD` disagrees with `TARGET_PERIOD` while still `ESTFAC0012` (the Generate button's
UPDATE script fixes this one automatically). No query change was needed - `ID_BILL` was already in
the result set - only the account-grouping and UI needed to surface the distinction that was already
sitting in the data.

**What changed:** `_case2_group_rows_by_account` (`web/server.py`) now computes a per-service
`reason` (`"missing_bill"` / `"period_mismatch"` / `null`) and rolls it up into two new account-level
counts, `missing_bill_count` and `period_mismatch_count` (they always sum to the existing
`needs_update_count`, kept unchanged for backward compat). The detect route also returns a new
`accounts_with_missing_bill` summary count. The Case 2 status-filter dropdown gained two options -
**"Needs action - bill missing"** and **"Needs action - period mismatch"** - alongside the original
"Needs action (all reasons)" / "Complete only" / "All", plus a new "Accounts with a missing bill" KPI
card and an updated scan summary line. CSV export now includes both new count columns. 3 new/updated
tests in `tests/test_web_api.py` confirm the split against the existing Case 2 fixture data (one
account with 1 missing-bill + 2 period-mismatch services, another with only a period mismatch).

## Bill Issuance Validator Case 1: widen next-period match to up to 11 periods ahead (2026-09-14, same day)

RJ, verbatim: "enhancing Case 1, I found cases that the next water or ele bills can be up to 11
billing period ahead of the rate bills, and they are valid. So can you include them if the next
bill of water and rate is up to 11 billing period ahead, then add a column on how much months and
then a filter as well, i noticed that the excel download is also missing."

**The bug:** `build_stuck_bills_query`'s `matched` CTE only ever checked `PERIOD_RATE + 1` - the
immediate next billing period - for a stuck Electricity/Water bill. **Live-confirmed this round:
that exact-next-period version currently finds ZERO accounts against real data.** Widening the
join to a range, `PERIOD_RATE + 1` through `PERIOD_RATE + 11`, finds **74** accounts - live
distribution: 47 at 2 periods ahead, 12 at 3, 9 at 4, and a scattered tail out to 10. Real
operations can leave Water/Electricity billing lagging the Rate bill by several months, not just
one - every one of those is still a genuine `ESTFAC0015` ("En espera de otros servicios") bill,
the exact same defining status the whole tool is keyed off, so widening the range doesn't relax
what counts as "stuck," it just stops assuming the lag is always exactly 1 period.

**Fix:** the range is now `PERIOD_RATE + 1 .. PERIOD_RATE + max_periods_ahead`
(`max_periods_ahead` defaults to the new `NEXT_PERIOD_MAX_AHEAD_DEFAULT = 11`, RJ's own confirmed
number, and is a real parameter - `/api/bill-issuance/detect?max_periods_ahead=N` - for future
tuning without a code change). A new `PERIODS_AHEAD` column (`ID_BILLING_PERIOD - PERIOD_RATE`) is
computed per match and carried through to the API/UI. The existing "no duplicates, Electricity
first" dedupe rule (RJ, 2026-09-15) still applies but is now ordered by closest period first, then
Electricity-before-Water only as a same-period tiebreak - an account with more than one qualifying
period surfaces its nearest one, not an arbitrary one.

**UI:** new **Months Ahead** column, plus a client-side **min/max** filter (two number inputs,
defaulting to the full 1-11 range) over the already-fetched rows - narrowing it doesn't re-query.
A new **Avg. months ahead** KPI card. The info banner text was updated to describe the range instead
of "next" (singular).

**Also fixed same round:** Case 1 had no Excel export (RJ: "i noticed that the excel download is
also missing") - Case 2/3 also only have CSV, but RJ flagged Case 1 specifically here. Added a new
`POST /api/bill-issuance/export-xlsx` route (identical pattern to Detect All's own
`export-xlsx` - the frontend sends back whatever rows are currently visible after the Months-Ahead
filter, server turns them into a real `.xlsx` via `openpyxl`) and an **Export Excel** button next
to the existing Export CSV one.

## Case 1: search-by-account-number box (2026-09-14, same day)

RJ, verbatim: "add an option for all to filter by account number" - Case 2/3/4 all already had a
"Search account #..." box; Case 1 was the odd one out (it only had the Months Ahead min/max filter).
Added `#billiss-search`, wired into `billissVisibleIndices()` the same way as every other Case's own
search box - client-side substring match on `reference`, skipped when the Export All checkbox's
`ignoreFilters` is set. All four Bill Issuance Validator tabs now filter by account number
consistently. Frontend-only change (`index.html`/`app.js` serve fresh per request - no restart
needed), live-verified the element renders and is wired correctly.

## Case 2: standalone "Missing bill only" checkbox (2026-09-14, same day)

RJ, verbatim: "add additional filter on case 2, check box to say with missing bill or not." The
Status dropdown already had a dedicated "Needs action - bill missing" option (from the earlier
same-day round), but RJ asked for a quick checkbox specifically. Added `#biss2-missing-bill-only` as
a standalone checkbox that **ANDs** on top of whatever the Status dropdown already shows - e.g.
"All" + checked narrows to every account (complete or not) that has a missing bill, a distinct
combination the dropdown alone couldn't express.

## Bill Issuance Validator Case 4: "Unclassified" (2026-09-14, same day)

RJ, verbatim: "now, create a 4th case, 'Unclassified' those that are pending validation in notice
TMP, and not in case 1, case 2, case 3, and any other case that we will add in the future. Make it
look like case 2, where there is a drill down on the bills and just showing the accounts on the row
and option to copy and export to excel."

**Design:** rather than re-encoding Case 1/2/3's own business rules a second time here (which would
silently drift the moment any of those Cases' own logic changes), the new `POST
/api/bill-issuance/case4/detect` route calls `build_stuck_bills_query`, `build_terminated_period_
mismatch_query`, and `build_bills_complete_query` directly - each **unlimited** (`limit=None`) - to
get every Case's own real account-ID set, then excludes any account already in one of those sets
from `build_unclassified_query`'s general "pending validation" universe (the new query builder in
`app/core/bill_issuance_validator.py`: same NOTICE_TMP/ESTFAC0012 gate every other Case already
uses, one row per currently-issuing bill). This guarantees Case 4 can never disagree with what Case
1/2/3 actually flag, and a future Case 5 only needs its own account-ID set added to the same
exclusion list - nothing else changes. Confirmed live (2026-09-14) before writing any code: 2,940
total pending accounts; 74 in Case 1; 645 in Case 2 (all-time); 2 in Case 3 (2026) - each unlimited
query ran in ~1-1.6s, so a full Case 4 scan costs a few seconds per click.

**UI:** new **Case 4 - Unclassified** sub-nav tab, styled like Case 2 per RJ's own instruction - one
row per account (no select-all/checkbox column and no Generate button, unlike Case 2, since
Unclassified is read-only/informational: every account here needs its own manual investigation).
Click an account to expand a drill-down of its pending bills; a dedicated **📋** button copies the
account number. Search box, a KPI row (account/bill counts plus how many accounts each of Case
1/2/3 excluded), **Export CSV** (client-side) and a new **Export Excel** button (`POST
/api/bill-issuance/case4/export-xlsx`) - Case 4 got Excel from day one since RJ's own request
explicitly asked for it, unlike Case 2/3 which still only have CSV.

## Every export gets an "Export all rows" option (2026-09-14, same day)

RJ, verbatim: "also the excel export, i need it to have an option to download all, or download only
selected across all this project." Clarified scope with RJ up front: "selected" means whatever rows
the current search/filters already show (the existing default behavior on every export button in
the app), and "all" means every row from the last scan, ignoring filters - not a new per-row
checkbox-selection UI.

Every CSV/Excel export button across the whole app now sits next to a small **Export all rows**
checkbox (unchecked by default, so existing behavior is unchanged unless RJ opts in): Bill Issuance
Case 1/2/3/4, Detect All (date anomaly), Hierarchy Analysis, and Bulk Checker's Results and Detail
tables. Implemented by adding an `ignoreFilters` parameter to each table's own `*VisibleIndices()`
filter helper (skips every filter/search check but keeps the current sort when `true`) and wiring
each export handler to read the new checkbox. No backend changes were needed - every `/export-xlsx`
route already just turns whatever rows the frontend sends into a workbook, so "Export all" simply
means more rows in the same request.

## Real bug fixed: Case 1 missed accounts blocked by a coexisting non-cycle bill (2026-09-14, same day)

RJ asked why account REFERENCE 1100068871 wasn't showing up in Case 1. Investigated live:
1100068871's Rate (176) bill genuinely has a stuck next-period Electricity bill (ESTFAC0015) - exactly
the pattern Case 1 is supposed to catch - but it was filtered out earlier by the "Rate bill must be the
ONLY bill in its period" check (`period_counts`/`pc.BILL_COUNT = 1`), because a **Deposito** bill
(`BILL_TYPE` `TFGEN10006`, already invoiced) happened to share that same billing period. `period_counts`
was counting every bill regardless of type/status, so a one-off charge bill unrelated to the cycle-
billing dependency this tool is actually about could wrongly disqualify a real match.

RJ's own fix direction, verbatim: "we only check TFGEN0001 and in status INVOICING for case 1." `period_
counts` in `build_stuck_bills_query` now only counts bills that are BOTH the cycle bill type
(`BILL_TYPE = 'TFGEN00001'`) AND still invoicing (`BILLING_STATUS = 'ESTFAC0012'`) - the same
distinction Case 2/3 already learned to make, just never carried back into Case 1 (which was built
earlier). Live-confirmed before the fix: 1,714 candidate accounts (pending notice + issuing Rate bill)
were disqualified purely by a coexisting non-cycle bill; scoping by `BILL_TYPE` alone raised Case 1's
own unlimited match count from 74 to 1,272 in that same check. After the server restarted with the real
fix (`BILL_TYPE` + `BILLING_STATUS` both scoped) live, `/api/bill-issuance/detect` now returns **1,359**
matches (well under the 2,000 row cap, not truncated), and account 1100068871 is in that result with
exactly the expected next-period bill (Electricity, period 237, ESTFAC0015, 1 period ahead).

## Case 1: "New Contract Match" (2026-09-14, same day)

RJ, verbatim (with his own exact SQL template): "incorporate in case 1, the existing case 1 is ok,
now i only want to add the case that its is only rate which is in pending validation notice_tmp and
invoicing gccom_bill, the contract start (from_date) of gccom_contracted service is same as
last_billing_date of gccom_bill, see this example query but i did not check that it is the only
bill in pending validation":

```sql
select pf.reference, b.id_bill, b.billing_status, t.COD_STATUS, b.LAST_BILLING_DATE,
  b.billing_date, cs.FROM_DATE, cs.STATUS, b.bill_type
from OUC_COMMON_ADMIN.gccom_bill b
  join OUC_ADMIN.GCCOM_NOTICE_TMP t on t.id_bill = b.id_bill
  join GCCOM_CONTRACTED_SERVICE cs on cs.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE
  join gccom_payment_form pf on pf.id_payment_form = b.ID_PAYMENT_FORM
where b.BILLING_STATUS = 'ESTFAC0012'
  and t.COD_STATUS = '5000NOTEMP'
  and b.LAST_BILLING_DATE = cs.FROM_DATE
  and bill_type = 'TFGEN00001'
  and cs.status = 'ESTSC00002'
  and cs.ID_OFFERED_SERVICE = 176;
```

**Not a new numbered Case** - RJ was explicit that Case 1 itself is unchanged; this is a second,
independent detection pattern living in the same Case 1 tab. It shares no logic with the existing
Stuck Bills query (no next-period Electricity/Water lookup at all) - it instead catches a brand-new
contract whose very first cycle bill is still pending: a Rate (176) bill still invoicing
(`ESTFAC0012`) with a pending notice (`5000NOTEMP`), where the account's contract is still Active
(`ESTSC00002`) and its own **start date** (`GCCOM_CONTRACTED_SERVICE.FROM_DATE`) exactly equals this
bill's `LAST_BILLING_DATE`.

**RJ's own explicit caveat, fixed:** "i did not check that it is the only bill in pending
validation... add a filter for this cases." His example query can multi-match an account that has
more than one bill currently pending validation. Added a `pending_counts` CTE - `COUNT(*)` of every
`GCCOM_NOTICE_TMP` row still `COD_STATUS = '5000NOTEMP'` for that account (any billing period, via
the linked bill's own `ID_PAYMENT_FORM`) - and require exactly 1: this Rate bill's own pending
notice must be the *only* one on the account right now. Deliberately a different uniqueness scope
than the existing Stuck Bills query's own `period_counts` (which counts bills sharing one billing
period, not every pending notice on the account).

**New:** `build_new_contract_match_query(limit=...)` in `app/core/bill_issuance_validator.py`, new
route `POST /api/bill-issuance/case1/new-contract-match/detect` (`require_login`, stateless - same
"every call re-runs the query fresh" pattern as every other detect route). New **New Contract
Match** card under the Case 1 tab (below Generate Release Script) - a flat, read-only table (no
drill-down, no Generate button, same pattern as Case 3) with search, KPI row, sortable columns, and
CSV export with an Export-all-rows checkbox.

**Live-verified after restart:** 656 matches against the real DB (well under the 2,000 row cap).
Also spot-checked the uniqueness fix's actual impact: running RJ's own raw query (no `pending_counts`
filter) returns 706 - confirming the fix correctly excludes 50 accounts that had more than one bill
pending validation, exactly the false-positive case RJ flagged.

## Real bug fixed: New Contract Match wasn't excluded from Case 4 "Unclassified" (2026-09-14, same day)

RJ, after the above shipped and was live-verified, asked directly: "was this new rule considered in
case for unclassified?" It was not. Case 4's `case4/detect` route excludes an account from
"Unclassified" only if it appears in Case 1's Stuck Bills, Case 2's, or Case 3's own account sets -
New Contract Match (a same-tab addition to Case 1, not a new numbered Case) was never added to that
exclusion list, so all 656 accounts it matches were silently still showing up as Unclassified even
though Case 1 now explains them.

**Fix:** `bill_issuance_case4_detect` now also calls `build_new_contract_match_query(limit=None)` and
unions its account set into `excluded_ids`, same as Case 1/2/3. Response gained a
`new_contract_match_account_count` field, and the Case 4 KPI row gained a matching "Excluded - New
Contract Match" card, alongside the existing Case 1/2/3 cards.

**Takeaway documented in both the module comment and the route's own docstring:** any *future*
addition to the Case 1 tab - numbered Case or not - needs this same manual step: add its own
account-ID set to `excluded_ids` in `bill_issuance_case4_detect`. Case 4's own exclusion list does
not update itself just because a query builder exists elsewhere in the module.

## Real bug fixed: New Contract Match matched a Rate bill sharing its period with another bill (2026-09-14, later same day)

RJ, checking a specific account: "check 1102978994, it is wrong, i explicitly told you that it
should only be rate bill for that specific billing period." Investigated live: account 1102978994's
billing period `10000000236` actually has **three** bills - a Water (19) cycle/invoicing bill, the
Rate (176) cycle/invoicing bill New Contract Match wrongly matched, and a `TFGEN10006` ("Deposito",
a one-off charge type, already invoiced) bill. `build_new_contract_match_query` had an account-level
uniqueness check (`pending_counts` - only one pending notice on the whole account) but **no
period-level check at all** - unlike Case 1's own Stuck Bills query, which already learned (see the
1100068871 fix above) that a Rate bill must be the *only* cycle+invoicing bill in its own specific
billing period.

**Fix:** added a `period_counts` CTE to `build_new_contract_match_query` - identical shape to Stuck
Bills' own (`BILL_TYPE = 'TFGEN00001' AND BILLING_STATUS = 'ESTFAC0012'`, grouped by
`ID_PAYMENT_FORM, ID_BILLING_PERIOD`) - and require `BILL_COUNT = 1` alongside the existing
`PENDING_COUNT = 1` check. Same proven pattern reused rather than inventing a new one, for
consistency and correctness confidence.

## Case 1: Stuck Bills + New Contract Match merged into one table (2026-09-14, later same day)

RJ, verbatim: "I wanted the 2 cases merged in 1 table, maybe you can do union but there will be
clear identifier of the case that i can use to filter." The two patterns were previously two
separate cards/tables in the Case 1 tab; they're now one table (`#billiss-table`) with a **Pattern**
column and filter (`stuck_bill` / `new_contract`).

**Design:** merged in Python at the route layer (`bill_issuance_detect` in `web/server.py`), not a
raw SQL `UNION` - each query builder (`build_stuck_bills_query`, `build_new_contract_match_query`)
stays the single source of truth for its own business rule, matching Case 4's own "call the existing
builder, don't re-derive it" convention. Common columns (account, notice-updated date, bill,
period) are populated for both patterns; pattern-specific columns (Next Bill/Service/Next
Period/Months Ahead/Next Status for Stuck Bill; Bill Status/Last Billing Date/Contract Start/
Contract Status for New Contract Match) are blank on rows where they don't apply and render as "-".

Response gained `stuck_bill_count` and `new_contract_match_count` alongside the merged `rows`.
**Generate Release Script** now re-runs both queries fresh and releases every bill from either
pattern (still one `UPDATE ... WHERE id_notice_tmp IN (...)` statement). The **Excel export**
gained a Pattern column plus the four New Contract Match-only columns.

## Real bug fixed: New Contract Match could double-detect a Stuck Bill account (2026-09-15)

The merged table surfaced something the two separate tables had been hiding: account 1102980263
appeared as BOTH a Stuck Bill row and a New Contract Match row. RJ asked why, then gave the fix
direction himself: "for contract match, there should no bill on the succeeding cycles which is in
waiting for other services as it is already covered by stuck bill."

**Investigated live:** the account's Rate bill (1072985046, period 236) is genuinely both - it's
the account's only pending-validation Rate bill (satisfies New Contract Match) AND it's blocking
that same account's Electricity bill (1075198521, period 237, `ESTFAC0015` "En espera de otros
servicios") one period later (satisfies Stuck Bill independently). Not a bug in either query on its
own - the two patterns model the same underlying notice from two different angles, and both were
genuinely true here.

**Fix:** added a `NOT EXISTS` check to `build_new_contract_match_query` - no bill on the same
account with a LATER `ID_BILLING_PERIOD` than the matched Rate bill's own period is currently
`ESTFAC0015`. Deliberately broader than an exact mirror of Stuck Bill's own scan (which only checks
Electricity/Water within the next 11 periods) - RJ's own words state the general principle, not a
scoped one, so any `ESTFAC0015` bill on the account in a later period disqualifies the match,
regardless of service or how far ahead - New Contract Match defers to Stuck Bill whenever that
overlap exists, rather than flagging the same root cause twice.

**Live-verified:** 12 of 674 candidate matches are excluded by this filter, including both of Case
1's own current Stuck Bill accounts (1102980263 and 1102980503) - exactly the overlap RJ flagged.

The old standalone New Contract Match card/table is gone from the UI. The standalone API route
(`POST /api/bill-issuance/case1/new-contract-match/detect`) is unchanged and still callable
directly - Case 4 "Unclassified" still calls the query builder function directly, not through this
route or the merged `/detect` route.

## Every filterable table now shows how many rows the current filters hid (2026-09-15)

RJ: "for case 2, if i filter, i want to see how many rows filtered, do this also for all of the
project." A shared `renderFilteredCount(elId, shownCount, totalCount)` helper in `app.js` is now
called from the end of every filterable table's own `RenderTable()` function - which already runs
on both a fresh detect AND every filter/search change, so it always reflects the live filter state
without any new event wiring. Writes into a small `<div class="hint-text" id="X-filtered-count">`
placed right under each table's existing summary line; blank when no filter is actually narrowing
the rows (so the common unfiltered case isn't cluttered with a redundant "N of N").

Covers every filterable table in the app: Case 1 (`#billiss-filtered-count`), Case 2
(`#biss2-filtered-count`), Case 3 (`#biss3-filtered-count`), Case 4
(`#biss4-filtered-count`), Hierarchy Analysis (`#hier-filtered-count`), and Detect All
(`#da-cleanup-filtered-count`). Bulk Checker was left out - its search is a server-side query, not
a client-side filter over an already-fetched row set, so there's no "filtered out" count to show.

## Case 2: generated script now also updates GCCOM_ITEMS_TO_BILL (2026-09-15)

RJ, verbatim: "we need to also update GCCOM_ITEMS_TO_BILL in the script generated: update
GCCOM_ITEMS_TO_BILL set ID_BILLING_PERIOD = :billing_period where id_bill = :ID_BILL, similar to
how we are doing now gccom_bill." Confirmed live (`INFORMATION_SCHEMA.COLUMNS`) that
`OUC_ADMIN.GCCOM_ITEMS_TO_BILL` has its own `ID_BILL`/`ID_BILLING_PERIOD`/`UPDATE_DATE`/
`UPDATE_PROGRAM`/`UPDATE_USER` columns, same shape as `GCCOM_BILL`'s own audit columns.

**Fix:** `build_terminated_period_fix_script` now emits TWO `UPDATE` statements per bill -
`GCCOM_BILL` (unchanged) and a new `GCCOM_ITEMS_TO_BILL` one, same `SET ID_BILLING_PERIOD =
target`, same audit columns, same defensive `WHERE ID_BILL = id AND ID_BILLING_PERIOD <> target`
guard as the existing statement. A bill with `id_bill = None` (no matching final bill) is still
skipped for both tables, same as before.

`TerminatedPeriodFixScript.update_count` stays "bills affected" (what the UI shows, e.g. "3
bill(s)") - a new `statement_count` field is the actual raw SQL statement count, now 2x
`update_count` since each bill touches both tables. Kept as two separate fields rather than
`update_count` silently doubling, so the frontend's own "N bill(s)" wording doesn't change meaning.

## Case 2: CSV export now includes bill/status detail (2026-09-14, same day)

RJ, verbatim: "for case 2, i need the bills and status to be included in the export, now it only
gives me the account and service count." The CSV export (`#biss2-export-csv-btn`, client-side, no
backend route) was account-level only (`service_count`, `needs_update_count`, etc.) - the actual
per-service bill id/billing period/status detail lived in `acct.services[]` and was only ever shown
in the UI's own drill-down, never exported.

Added four joined-string columns built from each account's `services` array - `offered_services`,
`bills`, `billing_periods`, `billing_statuses` - same "row per account, bill-level detail as
semicolon-joined columns" convention Case 4's own CSV export already uses. The four new columns are
**positionally aligned** (not independently filtered): a service with no matching bill (the
"missing bill" case) still gets a slot in every column (`(none)` / `-`) so the Nth entry always
refers to the same service across all four columns, rather than one column silently dropping an
empty value the others keep and drifting out of sync.

## Case 1: "Generate Release Script" (2026-09-14, same day)

RJ, verbatim (with his own exact SQL template): "for case 1, create script for all detected,
'Generate Release script' this is the script: `update gccom_notice_tmp set cod_status =
'1000NOTEMP', update_date = getdate(), update_user = 'RMA', update_program = 'VALIDATION
RELEASE_TERMINATED' where id_notice_tmp in ( select nt.id_notice_tmp from gccom_notice_tmp nt join
gccom_bill b on b.id_bill = nt.id_bill where nt.cod_status = '5000NOTEMP' and b.billing_status =
'ESTFAC0012' and b.id_bill in ( <all the rate bills detected here>)) and cod_status =
'5000NOTEMP';`"

**What it does:** releases the pending-validation notice (`GCCOM_NOTICE_TMP.COD_STATUS`
`5000NOTEMP` "For validation" → `1000NOTEMP` "Pendiente de generar factura", both confirmed live
via `GCCOM_NOTICE_TMP_STATUS`) for every currently detected Rate bill's own blocking notice. Does
**not** touch `GCCOM_BILL` itself - only `GCCOM_NOTICE_TMP`. RJ's own exact template shape is
preserved: ONE `UPDATE` statement with an `ID_NOTICE_TMP IN (...)` subquery (not one statement per
bill, unlike Case 2's own Generate script), keeping both of his `COD_STATUS = '5000NOTEMP'` guards
verbatim - the outer one makes re-running the script against an already-released notice a safe
no-op, same "only touch if still in the state we expect" convention every other correction script
in this app already follows.

**New:** `build_release_notice_script(bill_ids, ...)` in `app/core/bill_issuance_validator.py`.
`program`/`user` are real, overridable parameters (same shape as every other `build_*_script`
function here) but default to RJ's own literal values (`VALIDATION RELEASE_TERMINATED` / `RMA`) so
the out-of-the-box script matches his template byte-for-byte - unlike every other Generate script's
`program` default (`JIRAXXXX`, a per-run Jira placeholder), these were given as a fixed template
for this one action, not something to fill in.

New route `POST /api/bill-issuance/generate-release` (`require_editor`) re-runs
`build_stuck_bills_query` fresh right before generating - the same "re-verify at generate time"
pattern every other Generate route in this app already uses - and releases every `ID_BILL_RATE`
from that fresh scan (optionally narrowed via `id_bill_rates`, though the current UI always acts on
everything detected, matching RJ's own "for all detected" request). Recorded to script history the
same way as every other generated script.

**Updated (2026-09-14, later same day, table-merge round):** now also re-runs
`build_new_contract_match_query` fresh and releases every matched bill from *either* pattern - see
"Case 1: Stuck Bills + New Contract Match merged into one table" below.

**UI:** new **Generate Release Script** card under Case 1's Stuck Bills table - Program/User text
inputs (pre-filled with RJ's own literal defaults), a Clean-script toggle, Generate/Copy/Download
buttons, matching Case 2's own Generate card layout. No row-selection checkboxes on Case 1's table
(unlike Case 2/4) - RJ's request was "for all detected", not a per-row pick, so Generate always acts
on the full current scan.

## Real bug fixed: query timeouts crashed as a bare 500 (2026-09-16)

RJ reported timeouts while scanning Bill Issuance Validator's Case 2 (a heavier query - UNION +
window functions - over a tunneled connection). Root cause: `app/db/mssql.py`'s `run_query` /
`get_primary_key_columns` / `get_table_columns` only caught `pytds.Error` around the actual
EXECUTE/FETCH call. A query that times out mid-flight raises a plain `OSError`/`TimeoutError`
instead (same class of exception `_connect` already handles at connect time) - not a `pytds.Error`
- so it slipped past both that `except` clause AND `web/server.py`'s own `except mssql.
ConnectionError_` handlers, and came back to the browser as a bare, detail-less 500 instead of a
clear "query timed out" message.

**Fix:** every EXECUTE/FETCH call site now catches `(pytds.Error, OSError)` (the same tuple
`_connect` already used), via a new `_friendly_query_error()` helper that gives a specific
"timed out after Ns - raise the connection Timeout in Settings > Connections, or narrow the
query" message when the underlying error looks timeout-shaped. Also bumped the default
`ConnectionConfig.timeout_seconds` for brand-new connections from 10 to 30 - existing saved
connections keep whatever value they already have, so raise it yourself in Settings > Connections
if a specific heavier query (Case 2, Hierarchy Analysis, Detect All) still needs more room.

New `tests/test_mssql.py` (7 tests, monkeypatched fake connection/cursor - no real DB needed)
locks in that a `TimeoutError`/plain `OSError` during query execution now wraps as
`ConnectionError_` with a helpful message, same as a genuine `pytds.Error`.

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
