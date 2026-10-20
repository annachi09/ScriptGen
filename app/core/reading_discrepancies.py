"""
Reading Discrepancies (RJ, 2026-10-13).

Tab 1 - "Pending validation > 3 days". RJ: "show all reading which is still
pending validation yet the reading date is already past 3 days from today.
Show Account, Supply, Read_status, Billing period, reading_type, prev_value,
value, reading_usage, ready_usage. Show for the past 6 months only add the
usual export, then create update to change the status of RE_READING from
pending validation to available, and set ind_usage_to_cal = 1".

Codes (live, OUC_COMMON_ADMIN.GCGT_RE_READ_STATUS):
  9000STSRED = "Pending validation"  -> 1000STSRED = "Disponible" (Available)
Column GCGT_RE_READING.IND_USAGE_TO_CAL (numeric) is set to 1.

Filter: READ_STATUS = 9000STSRED, READING_DATE < today - 3 days and
READING_DATE >= today - 6 months. A plain READ_STATUS filter scans the whole
reading table (timed out live), so the readings are first narrowed to the
billing periods overlapping the last 6 months (GCCOM_BILLING_PERIOD, ~1.5s)
into a session-local #temp table, then enriched. Account = REFERENCE of the
supply's contracted service (active one first, then the latest).

Pure logic: builds SQL text only. The update script is shown, never run.
"""
from __future__ import annotations

from app.core.sql_format import format_sql_literal

STATUS_PENDING_VALIDATION = "9000STSRED"
STATUS_AVAILABLE = "1000STSRED"
MIN_DAYS_PENDING = 3
MONTHS_BACK = 6
INACTIVE_CS_STATUSES = ("ESTSC00001", "ESTSC00004", "ESTSC00005")
SCRIPT_DEFAULT_PROGRAM = "READING DISCREPANCIES PENDING VALIDATION"
SCRIPT_DEFAULT_USER = "RMA"
CHUNK_SIZE = 1000


def date_window(today=None, min_days: int = MIN_DAYS_PENDING, months_back: int = MONTHS_BACK):
    """(date_from, date_to_exclusive): reading date >= today - N months and < today - 3 days."""
    import datetime as _dt
    today = today or _dt.date.today()
    m = today.month - int(months_back)
    y = today.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    import calendar as _cal
    d = min(today.day, _cal.monthrange(y, m)[1])
    return _dt.date(y, m, d), today - _dt.timedelta(days=int(min_days))


def build_period_ids_query(date_from, today) -> str:
    """Billing periods overlapping the window (run first; literal ids keep the main query fast)."""
    return (f"SELECT ID_BILLING_PERIOD FROM GCCOM_BILLING_PERIOD WITH (NOLOCK) "
            f"WHERE END_DATE >= '{date_from:%Y-%m-%d}' AND INITIAL_DATE <= '{today:%Y-%m-%d}' "
            f"ORDER BY ID_BILLING_PERIOD")


def build_pending_validation_query(period_ids, date_from, date_to) -> str:
    """Live 2026-10-13: with GETDATE()/subquery predicates the optimizer picked a
    full scan (43s-120s timeout); with literal period ids + dates it is ~2s."""
    ids = [int(p) for p in period_ids or []]
    if not ids:
        ids = [-1]
    inactive = ", ".join(f"'{s}'" for s in INACTIVE_CS_STATUSES)
    id_list = ", ".join(str(i) for i in ids)
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#r') IS NOT NULL DROP TABLE #r;

SELECT r.ID_READING, r.ID_SECTOR_SUPPLY, r.READ_STATUS, r.ID_BILLING_PERIOD, r.READING_TYPE,
       r.READING_PREV_DATE, r.READING_DATE, r.PREV_VALUE, r.VALUE, r.READING_USAGE, r.READY_USAGE,
       r.IND_USAGE_TO_CAL
INTO #r
FROM GCGT_RE_READING r WITH (NOLOCK)
WHERE r.ID_BILLING_PERIOD IN ({id_list})
  AND r.READ_STATUS = '{STATUS_PENDING_VALIDATION}'
  AND r.READING_DATE < '{date_to:%Y-%m-%d}'
  AND r.READING_DATE >= '{date_from:%Y-%m-%d}';

SELECT acc.ACCOUNT, ss.NISS AS SUPPLY, r.ID_READING, r.READ_STATUS,
       COALESCE(drs.TEXT, rs.DESCRIPTION) AS READ_STATUS_NAME,
       r.ID_BILLING_PERIOD, bp.DESCRIPTION AS BILLING_PERIOD,
       r.READING_TYPE, COALESCE(drt.TEXT, rt.DESCRIPTION) AS READING_TYPE_NAME,
       r.READING_PREV_DATE, r.READING_DATE, DATEDIFF(day, r.READING_DATE, GETDATE()) AS DAYS_PENDING,
       r.PREV_VALUE, r.VALUE, r.READING_USAGE, r.READY_USAGE, r.IND_USAGE_TO_CAL
FROM #r r
LEFT JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
OUTER APPLY (SELECT TOP 1 pf.REFERENCE AS ACCOUNT
               FROM GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
               JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
              WHERE cs.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
              ORDER BY CASE WHEN cs.STATUS IN ({inactive}) THEN 1 ELSE 0 END, cs.ID_CONTRACTED_SERVICE DESC) acc
LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READ_STATUS rs ON rs.COD_DEVELOP = r.READ_STATUS
LEFT JOIN GCTS_DICTIONARY drs ON drs.ID = rs.DESCRIPTION_XI18N AND drs.LOCALE = 'EN'
LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING_TYPE rt ON rt.COD_DEVELOP = r.READING_TYPE
LEFT JOIN GCTS_DICTIONARY drt ON drt.ID = rt.DESCRIPTION_XI18N AND drt.LOCALE = 'EN'
LEFT JOIN GCCOM_BILLING_PERIOD bp WITH (NOLOCK) ON bp.ID_BILLING_PERIOD = r.ID_BILLING_PERIOD
ORDER BY r.READING_DATE, ss.NISS;

DROP TABLE #r;
"""


# ---------------------------------------------------------------------------
# Tab 2 - "Unbilled readings" (RJ 2026-10-16): "add a new tab, unbilled
# readings per billing period that i can check all. idea is direct connection/
# distribution/removal/cycle/disconnection that are not billed or terminated
# not billed or uau billed" + "make a good dashboard".
# Reading types (GCGT_RE_READING_TYPE, live): TIPTL00003 Cycle, TIPTL00005
# Direct Connection, TIPTL00017 Distribution, TIPTL00002 Removal, TIPTL00010
# Disconnection. "Unbilled" = any status except the 3 billed/closed ones
# (GCGT_RE_READ_STATUS, live): 7000STSRED Billed, 7001STSRED UAU - Billed,
# 8000STSRED Terminated Not Billed. The status is filtered with an explicit IN
# list (a NOT IN scanned the table and timed out live).
# ---------------------------------------------------------------------------
UNBILLED_READING_TYPES = {
    "TIPTL00003": "Cycle", "TIPTL00005": "Direct Connection", "TIPTL00017": "Distribution",
    "TIPTL00002": "Removal", "TIPTL00010": "Disconnection",
}
UNBILLED_READ_STATUSES = {
    "0500STSRED": "Pending reading", "0600STSRED": "Pending reading (backup)", "1000STSRED": "Available",
    "1500STSRED": "Pending curve treatment", "3000STSRED": "Processing", "4000STSRED": "Consumption calculated",
    "5000STSRED": "Anomalous consumption", "6000STSRED": "Sent to bill", "9000STSRED": "Pending validation",
}
CLOSED_READ_STATUSES = {"7000STSRED": "Billed", "7001STSRED": "UAU - Billed", "8000STSRED": "Terminated Not Billed"}
# Live 2026-10-16: the current period (October, 73,506 open readings) with a
# 20,000-row table took > 3 minutes; 5,000 rows + one account lookup per supply.
UNBILLED_ROW_CAP = 5000


def build_recent_periods_query(months_back: int = 12) -> str:
    """Billing periods for the picker: from N months ago up to the current one."""
    return (f"SELECT ID_BILLING_PERIOD, DESCRIPTION, INITIAL_DATE, END_DATE FROM GCCOM_BILLING_PERIOD WITH (NOLOCK) "
            f"WHERE END_DATE >= DATEADD(month, -{int(months_back)}, CAST(GETDATE() AS date)) AND INITIAL_DATE <= GETDATE() "
            f"ORDER BY ID_BILLING_PERIOD DESC")


def _unbilled_where(period_ids, reading_types, statuses) -> str:
    ids = [int(p) for p in period_ids or []] or [-1]
    types = [t for t in (reading_types or UNBILLED_READING_TYPES) if t in UNBILLED_READING_TYPES] or list(UNBILLED_READING_TYPES)
    sts = [s for s in (statuses or UNBILLED_READ_STATUSES) if s in UNBILLED_READ_STATUSES] or list(UNBILLED_READ_STATUSES)
    return (f"r.ID_BILLING_PERIOD IN ({', '.join(str(i) for i in ids)})\n"
            f"  AND r.READ_STATUS IN ({', '.join(chr(39) + s + chr(39) for s in sts)})\n"
            f"  AND r.READING_TYPE IN ({', '.join(chr(39) + t + chr(39) for t in types)})")


def build_unbilled_summary_query(period_ids, reading_types=None, statuses=None) -> str:
    """Counts per billing period x reading type x status (the dashboard numbers)."""
    return f"""
SELECT r.ID_BILLING_PERIOD, bp.DESCRIPTION AS BILLING_PERIOD, r.READING_TYPE, r.READ_STATUS, COUNT(*) AS N,
       SUM(CASE WHEN r.READING_DATE < DATEADD(day, -7, CAST(GETDATE() AS date)) THEN 1 ELSE 0 END) AS OLDER_7D
FROM GCGT_RE_READING r WITH (NOLOCK)
LEFT JOIN GCCOM_BILLING_PERIOD bp WITH (NOLOCK) ON bp.ID_BILLING_PERIOD = r.ID_BILLING_PERIOD
WHERE {_unbilled_where(period_ids, reading_types, statuses)}
GROUP BY r.ID_BILLING_PERIOD, bp.DESCRIPTION, r.READING_TYPE, r.READ_STATUS
ORDER BY r.ID_BILLING_PERIOD DESC, r.READING_TYPE, r.READ_STATUS
"""


def build_unbilled_detail_query(period_ids, reading_types=None, statuses=None, cap: int = UNBILLED_ROW_CAP) -> str:
    inactive = ", ".join(f"'{s}'" for s in INACTIVE_CS_STATUSES)
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#u') IS NOT NULL DROP TABLE #u;

SELECT TOP ({int(cap)}) r.ID_READING, r.ID_SECTOR_SUPPLY, r.READ_STATUS, r.ID_BILLING_PERIOD, r.READING_TYPE,
       r.READING_PREV_DATE, r.READING_DATE, r.PREV_VALUE, r.VALUE, r.READING_USAGE, r.READY_USAGE, r.IND_USAGE_TO_CAL
INTO #u
FROM GCGT_RE_READING r WITH (NOLOCK)
WHERE {_unbilled_where(period_ids, reading_types, statuses)}
ORDER BY r.READING_DATE;

-- one account lookup per supply (not per reading)
IF OBJECT_ID('tempdb..#acc') IS NOT NULL DROP TABLE #acc;
SELECT s.ID_SECTOR_SUPPLY, acc.ACCOUNT
INTO #acc
FROM (SELECT DISTINCT ID_SECTOR_SUPPLY FROM #u) s
OUTER APPLY (SELECT TOP 1 pf.REFERENCE AS ACCOUNT
               FROM GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
               JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
              WHERE cs.ID_SECTOR_SUPPLY = s.ID_SECTOR_SUPPLY
              ORDER BY CASE WHEN cs.STATUS IN ({inactive}) THEN 1 ELSE 0 END, cs.ID_CONTRACTED_SERVICE DESC) acc;

SELECT a.ACCOUNT, ss.NISS AS SUPPLY, r.ID_READING, r.READ_STATUS, r.ID_BILLING_PERIOD,
       bp.DESCRIPTION AS BILLING_PERIOD, r.READING_TYPE, r.READING_PREV_DATE, r.READING_DATE,
       DATEDIFF(day, r.READING_DATE, GETDATE()) AS DAYS_OPEN,
       r.PREV_VALUE, r.VALUE, r.READING_USAGE, r.READY_USAGE, r.IND_USAGE_TO_CAL
FROM #u r
LEFT JOIN #acc a ON a.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_BILLING_PERIOD bp WITH (NOLOCK) ON bp.ID_BILLING_PERIOD = r.ID_BILLING_PERIOD
ORDER BY r.READING_DATE, ss.NISS;

DROP TABLE #u; DROP TABLE #acc;
"""


def _clean_ids(reading_ids) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for v in reading_ids or []:
        try:
            i = int(str(v).strip())
        except (TypeError, ValueError):
            continue
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def build_release_script(reading_ids, *, user: str = SCRIPT_DEFAULT_USER,
                         program: str = SCRIPT_DEFAULT_PROGRAM, clean: bool = False) -> str:
    """Pending validation (9000STSRED) -> Available (1000STSRED) + IND_USAGE_TO_CAL = 1.
    Guarded by READ_STATUS = 9000STSRED so a reading that moved on is left alone."""
    ids = _clean_ids(reading_ids)
    if not ids:
        raise ValueError("No readings selected.")
    usr, prog = format_sql_literal(user), format_sql_literal(program)
    pend, avail = format_sql_literal(STATUS_PENDING_VALIDATION), format_sql_literal(STATUS_AVAILABLE)
    parts = []
    if not clean:
        parts.append(
            f"-- Reading Discrepancies: {len(ids)} reading(s) Pending validation ({STATUS_PENDING_VALIDATION}) "
            f"-> Available ({STATUS_AVAILABLE}), IND_USAGE_TO_CAL = 1\n"
            "-- Only rows still in Pending validation are touched.\n")
    for start in range(0, len(ids), CHUNK_SIZE):
        chunk = ids[start:start + CHUNK_SIZE]
        id_list = ",\n       ".join(str(i) for i in chunk)
        parts.append(
            "UPDATE GCGT_RE_READING\n"
            f"SET READ_STATUS      = {avail},\n"
            "    IND_USAGE_TO_CAL = 1,\n"
            f"    UPDATE_USER      = {usr},\n"
            f"    UPDATE_PROGRAM   = {prog},\n"
            "    UPDATE_DATE      = GETDATE()\n"
            "WHERE ID_READING IN\n"
            f"      ({id_list})\n"
            f"  AND READ_STATUS = {pend};\n")
    return "\n".join(parts)


def build_release_rollback(rows, *, user: str = SCRIPT_DEFAULT_USER,
                           program: str = SCRIPT_DEFAULT_PROGRAM) -> str:
    """Puts each reading back to Pending validation with its previous IND_USAGE_TO_CAL
    (taken from the scan rows), only where the fix's values are still in place."""
    usr, prog = format_sql_literal(user), format_sql_literal(program + " ROLLBACK")
    pend, avail = format_sql_literal(STATUS_PENDING_VALIDATION), format_sql_literal(STATUS_AVAILABLE)
    groups: dict[str, list[int]] = {}
    for r in rows or []:
        ids = _clean_ids([r.get("id_reading")])
        if not ids:
            continue
        prev = str(r.get("ind_usage_to_cal") if r.get("ind_usage_to_cal") not in (None, "") else "NULL")
        groups.setdefault(prev, []).append(ids[0])
    parts = ["-- ROLLBACK of the Reading Discrepancies release script: back to Pending validation "
             f"({STATUS_PENDING_VALIDATION}) with the IND_USAGE_TO_CAL value each reading had at scan time.\n"]
    for prev, ids in groups.items():
        for start in range(0, len(ids), CHUNK_SIZE):
            id_list = ",\n       ".join(str(i) for i in ids[start:start + CHUNK_SIZE])
            parts.append(
                "UPDATE GCGT_RE_READING\n"
                f"SET READ_STATUS      = {pend},\n"
                f"    IND_USAGE_TO_CAL = {prev if prev == 'NULL' else int(float(prev))},\n"
                f"    UPDATE_USER      = {usr},\n"
                f"    UPDATE_PROGRAM   = {prog},\n"
                "    UPDATE_DATE      = GETDATE()\n"
                "WHERE ID_READING IN\n"
                f"      ({id_list})\n"
                f"  AND READ_STATUS = {avail};\n")
    return "\n".join(parts)
