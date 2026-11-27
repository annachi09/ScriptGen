# ScriptGen UI standard

RJ 2026-11-19: "suggest for me to better organize our script gen, the menus and the contents, maybe we can
make something standard across the application" -> "ok proceed and maybe colors to main menu and some icon".

## 1. Menu sections (navx.js `GROUPS` / `GROUP_OF`)

Menus are grouped by the job you do on them. Each section has a colour that tints its label chip, its menu
icons and the active bar.

| Section | Colour | Menus |
|---|---|---|
| (top) | indigo | Overview |
| Monitor (read only) | sky `#38bdf8` | Anomaly Statistics, Billing Statistics, Temp Dashboard, Pending Service Requests, Rebilling Monitor, Sanitary Status, TNB Dashboard (Summary + 4 TNB cases, incl. the former TNB Cycle & Disconnection / TNB Disconnections menus) |
| Billing fixes | amber `#f59e0b` | Bill Issuance Validator, Double ITBs, Wrong Bills, Wrong Billed Consumption, Incorrect Billing Period, Fix Billing Campaign 2026 |
| Reading fixes | emerald `#34d399` | Diff Dates Anomalies, Hierarchy Analysis, Reading Validation, Stuck Hierarchy ITBs, Reading Discrepancies |
| Quick fixes | violet `#a78bfa` | SR Quick Fix, MP Tools, Reprocess EFTS |
| Lookup & SQL | indigo `#818cf8` | Workspace, Script, AI Assist, Bulk Checker |
| Admin & help | slate `#94a3b8` | History, Dashboard, Documentation, Tools, Settings |

## 2. Page template - 5 blocks, always in this order

1. **Header** - title (menu name), one-line purpose (= the menu's hover description), `sgStd.scanMeta`
   line "Last scan 10:42 · 6.1 s · n rows", Refresh, "Query used".
2. **Find** - the input (NISS / account / SR / MP id, one per line or comma separated) and / or
   **Detect all** (+ date window when the full scan is large). Enter = scan. Tabs: "By input" | "Detect all".
3. **Summary** - 3-5 KPI cards (`sgStd.kpis`, tones ok / warn / bad / info / neutral), click = filter,
   click again = clear; then filters (search, period, type) + "Clear filters" + "n of m rows shown".
4. **Results** - one table: checkbox column + header select-all (`sgStd.selection`), sticky header, status
   codes as pills with their meaning (`sgStd.pill` - "Anomalous" with STTOBILL00 on hover), Export CSV.
5. **Fix script** (fix pages only) - "What this does" numbered list, Jira / Program + UPDATE_USER, Clean
   script, `Generate (n)` -> Script + Rollback (`sgStd.scriptPanel`: copy / download both), a summary line,
   saved in History. The state is always re-read live before generating.

Read-only (Monitor) pages stop after block 4.

RJ 2026-11-26 ("remember we wanted it to be minimised as default, also why no search screen, also the cards looks bad"):
- **Info is always collapsed by default.** Use `.biss-info-banner` / long `.hint-text` (hxCollapseInfo wraps them in
  "ⓘ How this works"); a "What this script does" list goes inside `<details class="hx-hint">`. No always-open hero panels.
- **Find block = `.sg-find` + `sgStd.find(root, { onSearch })`**: segmented *Search | Detect all*; Search = textarea
  (codes one per line / comma, Enter = search, value counter), Detect all keeps the old `<prefix>-detect-btn` id (Scan all /
  Refresh still click it). The endpoint takes an optional body `{ inputs }` (`FindRequest`, parsed by
  `app/core/search_inputs.py`) and narrows the scan in SQL; the response echoes `searched`, the meta line says
  `sgStd.scopeNote(searched)` ("Detect all" / "Search · n values").
- **KPI cards** (`sgStd.kpis`): since 2026-11-27 separate Billing Statistics cards (sg-theme.css) - left colour bar,
  tinted icon dot, uppercase label, big number, sub line; click = filter, active card tinted in its tone.

RJ 2026-11-27 ("uniform design, i like the one in bills statistics"): **web/static/sg-theme.css** (loaded last) gives
every page the Billing Statistics look - hero header with group eyebrow + purpose (navx.js `decorateHeaders`), 16px
cards, KPI cards with a left colour bar, sticky uppercase table headers, pill buttons. New pages get it for free: use
`.page-header > h1`, `.card`, `sgStd.kpis` / `.kpi-card`, `.data-grid` inside `.grid-wrap`. Don't add page-specific
colours / radii - extend sg-theme.css instead.

## 3. Script rules (all generators)

- Every UPDATE is guarded on the value it expects (`WHERE STATUS = '<old>'`), stamps UPDATE_DATE /
  UPDATE_USER / UPDATE_PROGRAM, and has a matching rollback guarded on the new value.
- No BEGIN TRANSACTION / COMMIT (RJ 2026-11-18).
- No DECLARE / BEGIN ... END / GO / @variables either (RJ 2026-11-26: "do not use begin, and delcare, just the normal
  script, its more difficult to execute") - plain UPDATE statements with the user / program / values written inline,
  each ending with `;`.
- Clean script option drops whole-line `--` comments only.
- Record both the script and the rollback with `_record_script` (History).

## 4. Checklist for a new menu

- [ ] `index.html`: nav item + page section using the 5 blocks
- [ ] `navx.js`: `MENU_NAMES`, `MENU_DESC`, `ICONS`, `GROUP_OF`
- [ ] `web/menu_access.py`: `MENU_IDS` + `MENU_LABELS`
- [ ] Overview card (`_OVERVIEW_COUNTERS` + `OVERVIEW_CARDS`) showing the page's own headline number
- [ ] Queries through `mssql.run_query`, tables inside a `.card` ("Query used" button)
- [ ] Own JS file (`web/static/<page>.js`), using `sgStd` helpers
- [ ] README section dated "RJ yyyy-mm-dd"

## 5. Migration order

Pilot: Double ITBs (has a read-only tab and a fix tab). Then 2-3 pages per session:
MP Tools, SR Quick Fix, Reading Discrepancies, Stuck Hierarchy ITBs, Bill Issuance, ...
