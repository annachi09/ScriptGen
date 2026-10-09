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
