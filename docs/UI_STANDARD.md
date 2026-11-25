# ScriptGen UI standard

RJ 2026-11-19: "suggest for me to better organize our script gen, the menus and the contents, maybe we can
make something standard across the application" -> "ok proceed and maybe colors to main menu and some icon".

## 1. Menu sections (navx.js `GROUPS` / `GROUP_OF`)

Menus are grouped by the job you do on them. Each section has a colour that tints its label chip, its menu
icons and the active bar.

| Section | Colour | Menus |
|---|---|---|
| (top) | indigo | Overview |
| Monitor (read only) | sky `#38bdf8` | Anomaly Statistics, Billing Statistics, Temp Dashboard, Pending Service Requests, Rebilling Monitor, Sanitary Status, TNB Dashboard |
| Billing fixes | amber `#f59e0b` | Bill Issuance Validator, Double ITBs, Wrong Bills, Wrong Billed Consumption, Incorrect Billing Period, Fix Billing Campaign 2026 |
| Reading fixes | emerald `#34d399` | Diff Dates Anomalies, Hierarchy Analysis, Reading Validation, TNB Cycle & Disconnection, Stuck Hierarchy ITBs, TNB Disconnections, Reading Discrepancies |
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

## 3. Script rules (all generators)

- Every UPDATE is guarded on the value it expects (`WHERE STATUS = '<old>'`), stamps UPDATE_DATE /
  UPDATE_USER / UPDATE_PROGRAM, and has a matching rollback guarded on the new value.
- No BEGIN TRANSACTION / COMMIT (RJ 2026-11-18).
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
