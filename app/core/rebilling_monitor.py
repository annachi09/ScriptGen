"""
Rebilling Monitor (RJ, 2026-10-09 - picked from the suggested features).

Every GCCOM_REBILLING_ACTIVITY that a ScriptGen script inserts carries a
DESCRIPTION tag (DOUBLE ITB: 'AUTO REBILLING DOUBLE ITB'; Wrong Bill Case 1:
'WRONG-SANITARY HIGH BILL'). This page lists them all with the status in
English (GCCOM_REB_ACTIVITY_STATUS + GCTS_DICTIONARY), the bills inside
(GCCOM_REB_ACTIVITY_INFORMATION) with their current bill status, the account
and the age in days, so stuck activities are easy to spot.

Live statuses: ESTREF0001 Pending, 0002 In process, 0003 Rejected,
0004 Rebilled. "Open" = not 0003 / 0004 (same rule as the follow-up query at
the end of every script).

Pure logic: builds SELECT text only.
"""
from __future__ import annotations

from . import double_itb, unusual_sanitary
from .sql_format import format_sql_literal

# tag -> source menu (add a line here when another menu starts tagging)
TAGS = {
    double_itb.REB_DESCRIPTION: "DOUBLE ITB",
    unusual_sanitary.REB_DESCRIPTION: "Wrong Bill Case 1 - High Sanitary",
}
OPEN_EXCLUDED = double_itb.REB_ACTIVITY_FINAL_STATUSES  # Rejected, Rebilled
DEFAULT_STUCK_DAYS = 3


def build_query() -> str:
    tags = ", ".join(format_sql_literal(t) for t in TAGS)
    return f"""
SELECT ra.ID_REBILLING_ACTIVITY,
       ra.DESCRIPTION,
       ra.CREATE_DATE,
       DATEDIFF(DAY, ra.CREATE_DATE, GETDATE()) AS AGE_DAYS,
       ra.REB_ACTIVITY_STATUS,
       COALESCE(d.TEXT, st.NAME_TYPE) AS STATUS_EN,
       ra.REB_ACTIVITY_TYPE,
       ra.ID_CUSTOMER,
       ra.BILLS_NUMBER,
       ra.UPDATE_DATE,
       ra.UPDATE_USER,
       ra.UPDATE_PROGRAM,
       ra.RESOLUTION_DATE,
       bi.BILL_IDS,
       bi.BILL_STATUSES,
       bi.ACCOUNT
FROM OUC_ADMIN.GCCOM_REBILLING_ACTIVITY ra WITH (NOLOCK)
LEFT JOIN OUC_ADMIN.GCCOM_REB_ACTIVITY_STATUS st ON st.COD_DEVELOP = ra.REB_ACTIVITY_STATUS
LEFT JOIN GCTS_DICTIONARY d ON d.ID = st.NAME_TYPE_XI18N AND d.LOCALE = 'EN'
OUTER APPLY (
    SELECT STRING_AGG(CAST(ri.ID_BILL AS varchar(20)), ', ') WITHIN GROUP (ORDER BY ri.ID_BILL) AS BILL_IDS,
           STRING_AGG(CAST(b.ID_BILL AS varchar(20)) + ':' + ISNULL(b.BILLING_STATUS, '?'), ', ') WITHIN GROUP (ORDER BY ri.ID_BILL) AS BILL_STATUSES,
           MIN(pf.REFERENCE) AS ACCOUNT
    FROM OUC_ADMIN.GCCOM_REB_ACTIVITY_INFORMATION ri WITH (NOLOCK)
    LEFT JOIN GCCOM_BILL b WITH (NOLOCK) ON b.ID_BILL = ri.ID_BILL
    LEFT JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
    WHERE ri.ID_REBILLING_ACTIVITY = ra.ID_REBILLING_ACTIVITY
) bi
WHERE ra.DESCRIPTION IN ({tags})
ORDER BY ra.CREATE_DATE DESC
"""


# ---------------------------------------------------------------------------
# Main tab "Open rebillings" (RJ 2026-10-18): "in the rebilling monitor, the
# existing will be moved to 2nd tab, add a main tab to show all rebilling that
# are not closed, i want it to have KPI, type and status. and aging and by
# period". Every GCCOM_REBILLING_ACTIVITY not Rejected (ESTREF0003) / Rebilled
# (ESTREF0004), whoever created it. English names from GCCOM_REB_ACTIVITY_STATUS
# / _TYPE / GCCOM_REB_REASON_TYPE + GCTS_DICTIONARY. Bills come from
# GCCOM_REB_ACTIVITY_INFORMATION (live: only 604 of 2,510 open activities have
# bills - massive activities usually have none), so "by period" uses the
# creation month; the bills' latest billing period is a column.
# Live 2026-10-18: correlated OUTER APPLY per activity took 109s; #temp tables
# (open activities, then one grouped pass over the information rows) 3.5s.
# ---------------------------------------------------------------------------
AGING_BUCKETS = ((7, "0-7 days"), (30, "8-30 days"), (90, "31-90 days"), (180, "91-180 days"),
                 (365, "181-365 days"), (None, "Over 1 year"))


# RJ 2026-10-18 (2): "i want to see the state of the rebilling, or if there is anomalous
# on the reb activity information". Each bill line (GCCOM_REB_ACTIVITY_INFORMATION) has
# REB_DN_STATUS (GCCOM_REB_DELIVERY_NOTE_STATUS, live):
#   EREALB0001 Disputed, 0002 Compliant, 0003 Rebilled, 0004 Complementary rebilled,
#   0005 Batch Recalculate, 0006 Batch Recalculation in process, 0007 Cancelled,
#   0008 Anomalous in batch rebill process, 0009 Substitutive Rebilling.
# The anomaly reason is GCCOM_REB_STATUS_HISTORY.COMMENTS of the line's latest
# EREALB0008 history row (live: "NOSUFDATFO - Lacks Data; ", "DIFFDATES - BILLING DATES
# AND READING DATES ARE DIFFERENT; ", "OUTRAMOUNT - Amount Out of Range; ", ...).
# Activity-level GCCOM_REBILLING_ACTIVITY.PROCESSING_INFO is returned too.
DN_ANOMALOUS = "EREALB0008"
DN_STATUS_NAMES = {
    "EREALB0001": "Disputed", "EREALB0002": "Compliant", "EREALB0003": "Rebilled",
    "EREALB0004": "Complementary rebilled", "EREALB0005": "Batch Recalculate",
    "EREALB0006": "Batch Recalculation in process", "EREALB0007": "Cancelled",
    "EREALB0008": "Anomalous in batch rebill process", "EREALB0009": "Substitutive Rebilling",
}
# Activity "rebilling state" from its lines - first rule that matches wins.
REB_STATES = (
    ("Anomalous", ("EREALB0008",)),
    ("Batch recalculation", ("EREALB0005", "EREALB0006")),
    ("Substitutive rebilling", ("EREALB0009",)),
    ("Partly rebilled", ("EREALB0003", "EREALB0004")),
    ("Disputed - waiting", ("EREALB0001",)),
    ("Compliant", ("EREALB0002",)),
    ("Cancelled lines", ("EREALB0007",)),
)
STATE_NO_LINES = "No bill lines"


def rebilling_state(dn_codes) -> str:
    codes = {c for c in (dn_codes or []) if c}
    if not codes:
        return STATE_NO_LINES
    for name, members in REB_STATES:
        if codes & set(members):
            return name
    return "Other"


def build_open_query() -> str:
    final = ", ".join(f"'{s}'" for s in OPEN_EXCLUDED)
    dn_cols = ",\n       ".join(
        f"SUM(CASE WHEN ri.REB_DN_STATUS = '{c}' THEN 1 ELSE 0 END) AS DN_{c[-4:]}" for c in DN_STATUS_NAMES)
    dn_final = ", ".join(f"ISNULL(bi.DN_{c[-4:]}, 0) AS DN_{c[-4:]}" for c in DN_STATUS_NAMES)
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#ra') IS NOT NULL DROP TABLE #ra;
IF OBJECT_ID('tempdb..#bi') IS NOT NULL DROP TABLE #bi;
IF OBJECT_ID('tempdb..#an') IS NOT NULL DROP TABLE #an;

SELECT ra.ID_REBILLING_ACTIVITY, ra.REB_ACTIVITY_STATUS, ra.REB_ACTIVITY_TYPE, ra.REASON_TYPE, ra.DESCRIPTION,
       ra.CREATE_DATE, ra.UPDATE_DATE, ra.UPDATE_USER, ra.CREATION_USER, ra.ID_CUSTOMER, ra.BILLS_NUMBER,
       LEFT(CAST(ra.PROCESSING_INFO AS nvarchar(4000)), 400) AS PROCESSING_INFO
INTO #ra
FROM OUC_ADMIN.GCCOM_REBILLING_ACTIVITY ra WITH (NOLOCK)
WHERE ra.REB_ACTIVITY_STATUS NOT IN ({final});

SELECT ri.ID_REBILLING_ACTIVITY, COUNT(*) AS BILL_COUNT,
       STRING_AGG(CAST(ri.ID_BILL AS varchar(20)), ', ') AS BILL_IDS,
       MIN(pf.REFERENCE) AS ACCOUNT, SUM(b.TOTAL_AMOUNT) AS TOTAL_AMOUNT, MAX(b.ID_BILLING_PERIOD) AS MAX_PERIOD,
       {dn_cols}
INTO #bi
FROM OUC_ADMIN.GCCOM_REB_ACTIVITY_INFORMATION ri WITH (NOLOCK)
JOIN #ra a ON a.ID_REBILLING_ACTIVITY = ri.ID_REBILLING_ACTIVITY
LEFT JOIN GCCOM_BILL b WITH (NOLOCK) ON b.ID_BILL = ri.ID_BILL
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
GROUP BY ri.ID_REBILLING_ACTIVITY;

-- anomaly reason: latest Anomalous history comment of each line that is still Anomalous
SELECT x.ID_REBILLING_ACTIVITY, STRING_AGG(x.REASON, ' | ') AS ANOMALY_REASON
INTO #an
FROM (
    SELECT DISTINCT h1.ID_REBILLING_ACTIVITY, h1.REASON
    FROM (
        SELECT ri.ID_REBILLING_ACTIVITY,
               RTRIM(REPLACE(LEFT(CAST(h.COMMENTS AS nvarchar(4000)), 300), '; ', ';')) AS REASON,
               ROW_NUMBER() OVER (PARTITION BY ri.ID_REB_ACTIVITY_INFORMATION ORDER BY h.CHANGE_DATE DESC, h.ID_REB_STATUS_HISTORY DESC) AS RN
        FROM OUC_ADMIN.GCCOM_REB_ACTIVITY_INFORMATION ri WITH (NOLOCK)
        JOIN #ra a ON a.ID_REBILLING_ACTIVITY = ri.ID_REBILLING_ACTIVITY
        JOIN OUC_ADMIN.GCCOM_REB_STATUS_HISTORY h WITH (NOLOCK)
             ON h.ID_REB_ACTIVITY_INFORMATION = ri.ID_REB_ACTIVITY_INFORMATION AND h.REB_STATUS = '{DN_ANOMALOUS}'
        WHERE ri.REB_DN_STATUS = '{DN_ANOMALOUS}'
    ) h1
    WHERE h1.RN = 1 AND h1.REASON <> ''
) x
GROUP BY x.ID_REBILLING_ACTIVITY;

SELECT a.ID_REBILLING_ACTIVITY, a.REB_ACTIVITY_STATUS, COALESCE(ds.TEXT, st.NAME_TYPE) AS STATUS_EN,
       a.REB_ACTIVITY_TYPE, COALESCE(dt.TEXT, ty.NAME_TYPE) AS TYPE_EN,
       a.REASON_TYPE, COALESCE(dr.TEXT, rs.NAME_TYPE) AS REASON_EN,
       a.DESCRIPTION, a.CREATE_DATE, CONVERT(char(7), a.CREATE_DATE, 120) AS CREATED_MONTH,
       DATEDIFF(DAY, a.CREATE_DATE, GETDATE()) AS AGE_DAYS,
       a.UPDATE_DATE, a.UPDATE_USER, a.CREATION_USER, a.ID_CUSTOMER, a.BILLS_NUMBER,
       ISNULL(bi.BILL_COUNT, 0) AS BILL_COUNT, bi.BILL_IDS, bi.ACCOUNT, bi.TOTAL_AMOUNT,
       bi.MAX_PERIOD AS ID_BILLING_PERIOD, bp.DESCRIPTION AS BILLING_PERIOD,
       {dn_final},
       an.ANOMALY_REASON, a.PROCESSING_INFO
FROM #ra a
LEFT JOIN #bi bi ON bi.ID_REBILLING_ACTIVITY = a.ID_REBILLING_ACTIVITY
LEFT JOIN #an an ON an.ID_REBILLING_ACTIVITY = a.ID_REBILLING_ACTIVITY
LEFT JOIN OUC_ADMIN.GCCOM_REB_ACTIVITY_STATUS st ON st.COD_DEVELOP = a.REB_ACTIVITY_STATUS
LEFT JOIN GCTS_DICTIONARY ds ON ds.ID = st.NAME_TYPE_XI18N AND ds.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCOM_REB_ACTIVITY_TYPE ty ON ty.COD_DEVELOP = a.REB_ACTIVITY_TYPE
LEFT JOIN GCTS_DICTIONARY dt ON dt.ID = ty.NAME_TYPE_XI18N AND dt.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCOM_REB_REASON_TYPE rs ON rs.COD_DEVELOP = a.REASON_TYPE
LEFT JOIN GCTS_DICTIONARY dr ON dr.ID = rs.NAME_TYPE_XI18N AND dr.LOCALE = 'EN'
LEFT JOIN GCCOM_BILLING_PERIOD bp WITH (NOLOCK) ON bp.ID_BILLING_PERIOD = bi.MAX_PERIOD
ORDER BY a.CREATE_DATE;

DROP TABLE #ra; DROP TABLE #bi; DROP TABLE #an;
"""


def shape_open_row(r: dict) -> dict:
    """Adds rebilling_state, line counts per DN status (dn_lines), anomalous flag."""
    lines = {}
    for code, name in DN_STATUS_NAMES.items():
        n = int(float(r.get(f"dn_{code[-4:].lower()}") or 0))
        if n:
            lines[name] = n
    present = [c for c in DN_STATUS_NAMES if int(float(r.get(f"dn_{c[-4:].lower()}") or 0))]
    r["rebilling_state"] = rebilling_state(present)
    r["dn_lines"] = lines
    r["lines_status"] = ", ".join(f"{k} {v}" for k, v in lines.items())
    r["anomalous_lines"] = int(float(r.get(f"dn_{DN_ANOMALOUS[-4:].lower()}") or 0))
    return r


def aging_bucket(age_days) -> str:
    try:
        d = int(float(age_days))
    except (TypeError, ValueError):
        return "?"
    for limit, label in AGING_BUCKETS:
        if limit is None or d <= limit:
            return label
    return AGING_BUCKETS[-1][1]


def shape(rows: list[dict]) -> dict:
    """rows: lower-case dicts. Adds source + is_open; returns rows + counts."""
    out = []
    for r in rows:
        r = dict(r)
        r["source"] = TAGS.get(r.get("description") or "", r.get("description") or "")
        r["is_open"] = (r.get("reb_activity_status") or "") not in OPEN_EXCLUDED
        out.append(r)
    by_status: dict[str, int] = {}
    for r in out:
        k = r.get("status_en") or r.get("reb_activity_status") or "?"
        by_status[k] = by_status.get(k, 0) + 1
    return {
        "rows": out,
        "count": len(out),
        "open_count": sum(1 for r in out if r["is_open"]),
        "by_status": by_status,
        "tags": TAGS,
    }
