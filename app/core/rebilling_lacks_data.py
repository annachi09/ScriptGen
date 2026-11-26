"""
Rebilling Monitor tab 4 - "Rebilling with anomaly in bill date (LACKS DATA)" (RJ 2026-11-26).

RJ: "find all bills in status disputed, and is having an anomalous reb_dn_information. get the reading item to
bill from the item to bill using the id_bill, then get the re_reading, i need to add a column in our report for
the last billing date, billing date and reading_prev_date and reading date, add a filter for those with 1 only
reading and filter for more than 1, in the update script, we will update the disputed bill with the reading
prev_date value of re reading to its last_billing_date, make it look nice".

Chain (read live 2026-11-26):
  GCCOM_BILL (BILLING_STATUS ESTFAC0010 Disputed)
    <- GCCOM_REB_ACTIVITY_INFORMATION (ID_BILL, REB_DN_STATUS EREALB0008 Anomalous in batch rebill process)
       latest GCCOM_REB_STATUS_HISTORY EREALB0008 COMMENTS 'NOSUFDATFO - Lacks Data;'
    -> GCCOM_ITEMS_TO_BILL (ID_BILL) -> GCCOM_READINGS_ITEMSTOBILL (ID_ITEM_TO_BILL) -> GCGT_RE_READING (ID_READING)
Live: 102 Lacks-Data lines, all on Disputed bills. Typical case: bill LAST_BILLING_DATE = READING_PREV_DATE + 1 day
(e.g. bill 7174654: LAST_BILLING_DATE 2022-06-05, reading 55989170 prev 2022-06-04 -> 2022-06-05).

Proposed LAST_BILLING_DATE = READING_PREV_DATE of the bill's reading; with more than one reading, the earliest
READING_PREV_DATE (start of the billed range) - confirmed by RJ 2026-11-26 ("yes for multi, use the earliest").
RJ 2026-11-26 (3): "focus on the billing date and last billing date are the same. remove the rest, show it better" -
only bills with CAST(LAST_BILLING_DATE AS date) = CAST(BILLING_DATE AS date) are kept (live 12:44: 14 of 100; the
fix gives them a real period: READING_PREV_DATE -> BILLING_DATE).
RJ 2026-11-26 (2): "show also related RCCS rebilling, status, and rebilling activity and status" - the rebilling
activity of the line (status / type in English) and its RCCS via ID_RECLAMATION = ID_COMPLAINT (live: 84 of 102
bills have one; the others have no ID_RECLAMATION). Bills already equal to it are "no change" and never scripted.

Pure logic: builds SQL text only.
"""
from __future__ import annotations

from .sql_format import format_sql_literal
from .search_inputs import sql_list

BILL_DISPUTED = "ESTFAC0010"
DN_ANOMALOUS = "EREALB0008"
DN_DISPUTED = "EREALB0001"   # RJ 2026-11-26 (4): "include disputed in the checks" - line not processed yet
LINE_STATUS_NAMES = {DN_ANOMALOUS: "Anomalous - Lacks Data", DN_DISPUTED: "Disputed - not processed yet"}
REASON_CODE = "NOSUFDATFO"
REASON_LABEL = "NOSUFDATFO - Lacks Data"
DEFAULT_PROGRAM = "REB_LACKS_DATA"
DEFAULT_USER = "RMA"
READ_SEP = ";"


def build_detect_query(inputs: list[str] | None = None) -> str:
    """inputs (RJ 2026-11-26 Search screen): ID_BILL / bill number / account / RCCS / rebilling activity."""
    L = sql_list(inputs) if inputs else ""
    search = (f"""
-- Search: only the bills / accounts / RCCS / rebilling activities entered
DELETE b FROM #b b WHERE NOT (CAST(b.ID_BILL AS varchar(30)) IN ({L}) OR CAST(b.ID_REBILLING_ACTIVITY AS varchar(30)) IN ({L})
    OR EXISTS (SELECT 1 FROM GCCOM_BILL bl WITH (NOLOCK) LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = bl.ID_PAYMENT_FORM
               WHERE bl.ID_BILL = b.ID_BILL AND (bl.BILL_NUMBER IN ({L}) OR pf.REFERENCE IN ({L})))
    OR EXISTS (SELECT 1 FROM OUC_ADMIN.GCCOM_REBILLING_ACTIVITY ra WITH (NOLOCK) JOIN GCCC_COMPLAINT cp WITH (NOLOCK) ON cp.ID_COMPLAINT = ra.ID_RECLAMATION
               WHERE ra.ID_REBILLING_ACTIVITY = b.ID_REBILLING_ACTIVITY AND cp.COD_REF_COMPLAINT IN ({L})));
""" if inputs else "")
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#b') IS NOT NULL DROP TABLE #b;
IF OBJECT_ID('tempdb..#r') IS NOT NULL DROP TABLE #r;

-- 1) Disputed bills whose rebilling line is Anomalous with the latest anomaly 'NOSUFDATFO - Lacks Data',
--    or (RJ 2026-11-26 (4)) still Disputed - not processed yet, it will hit the same anomaly
SELECT x.ID_BILL, x.ID_REB_ACTIVITY_INFORMATION, x.ID_REBILLING_ACTIVITY, x.REB_DN_STATUS AS LINE_STATUS, x.REASON, x.ANOMALY_DATE
INTO #b
FROM (
    SELECT ri.ID_BILL, ri.ID_REB_ACTIVITY_INFORMATION, ri.ID_REBILLING_ACTIVITY, ri.REB_DN_STATUS,
           RTRIM(LEFT(CAST(h.COMMENTS AS nvarchar(4000)), 200)) AS REASON, h.CHANGE_DATE AS ANOMALY_DATE,
           ROW_NUMBER() OVER (PARTITION BY ri.ID_REB_ACTIVITY_INFORMATION ORDER BY h.CHANGE_DATE DESC, h.ID_REB_STATUS_HISTORY DESC) AS RN
    FROM OUC_ADMIN.GCCOM_REB_ACTIVITY_INFORMATION ri WITH (NOLOCK)
    JOIN GCCOM_BILL b WITH (NOLOCK) ON b.ID_BILL = ri.ID_BILL AND b.BILLING_STATUS = '{BILL_DISPUTED}'
         AND CAST(b.LAST_BILLING_DATE AS date) = CAST(b.BILLING_DATE AS date)
    LEFT JOIN OUC_ADMIN.GCCOM_REB_STATUS_HISTORY h WITH (NOLOCK)
         ON h.ID_REB_ACTIVITY_INFORMATION = ri.ID_REB_ACTIVITY_INFORMATION AND h.REB_STATUS = '{DN_ANOMALOUS}'
    WHERE ri.REB_DN_STATUS IN ('{DN_ANOMALOUS}', '{DN_DISPUTED}')
) x
WHERE x.RN = 1 AND ((x.REB_DN_STATUS = '{DN_ANOMALOUS}' AND x.REASON LIKE '%{REASON_CODE}%') OR x.REB_DN_STATUS = '{DN_DISPUTED}');
{search}
-- RJ 2026-11-26 (3): focus on bills whose BILLING_DATE and LAST_BILLING_DATE are the same day (zero-day period)
DELETE b FROM #b b JOIN GCCOM_BILL bl WITH (NOLOCK) ON bl.ID_BILL = b.ID_BILL
WHERE CAST(bl.LAST_BILLING_DATE AS date) <> CAST(bl.BILLING_DATE AS date) OR bl.LAST_BILLING_DATE IS NULL OR bl.BILLING_DATE IS NULL;

-- 2) the bill's readings: item to bill (ID_BILL) -> reading item to bill -> GCGT_RE_READING
SELECT DISTINCT b.ID_BILL, rd.ID_READING, rd.READING_PREV_DATE, rd.READING_DATE, rd.READ_STATUS, rd.USAGE_TYPE,
       rd.ID_MEASURING_POINT
INTO #r
FROM #b b
JOIN GCCOM_ITEMS_TO_BILL itb WITH (NOLOCK) ON itb.ID_BILL = b.ID_BILL
JOIN GCCOM_READINGS_ITEMSTOBILL rib WITH (NOLOCK) ON rib.ID_ITEM_TO_BILL = itb.ID_ITEM_TO_BILL
JOIN GCGT_RE_READING rd WITH (NOLOCK) ON rd.ID_READING = rib.ID_READING;

SELECT b.ID_BILL, bl.BILL_NUMBER, pf.REFERENCE AS ACCOUNT, bl.BILLING_STATUS, bl.TOTAL_AMOUNT, bl.BILL_TYPE,
       bl.LAST_BILLING_DATE, bl.BILLING_DATE, bl.ID_BILLING_PERIOD, bp.DESCRIPTION AS BILLING_PERIOD,
       bl.UPDATE_DATE AS BILL_UPDATE_DATE, bl.UPDATE_USER AS BILL_UPDATE_USER, bl.UPDATE_PROGRAM AS BILL_UPDATE_PROGRAM,
       b.ID_REBILLING_ACTIVITY, b.ID_REB_ACTIVITY_INFORMATION, b.LINE_STATUS, b.REASON, b.ANOMALY_DATE,
       ISNULL(r.READING_COUNT, 0) AS READING_COUNT, r.MIN_PREV_DATE, r.MAX_READING_DATE, r.READINGS,
       -- RJ 2026-11-26 (2): related rebilling activity + its RCCS (ID_RECLAMATION = ID_COMPLAINT), with statuses
       ra.REB_ACTIVITY_STATUS, COALESCE(dst.TEXT, rst.NAME_TYPE) AS REB_STATUS_EN,
       ra.REB_ACTIVITY_TYPE, COALESCE(dty.TEXT, rty.NAME_TYPE) AS REB_TYPE_EN,
       ra.CREATE_DATE AS REB_CREATED, ra.UPDATE_DATE AS REB_UPDATED, ra.DESCRIPTION AS REB_DESCRIPTION,
       cp.COD_REF_COMPLAINT AS RCCS, cp.COD_STATUS_COMPLAINT AS RCCS_STATUS, COALESCE(dcs.TEXT, cs.NAME_TYPE) AS RCCS_STATUS_EN,
       cp.CREATE_DATE AS RCCS_CREATED
FROM #b b
JOIN GCCOM_BILL bl WITH (NOLOCK) ON bl.ID_BILL = b.ID_BILL
LEFT JOIN OUC_ADMIN.GCCOM_REBILLING_ACTIVITY ra WITH (NOLOCK) ON ra.ID_REBILLING_ACTIVITY = b.ID_REBILLING_ACTIVITY
LEFT JOIN OUC_ADMIN.GCCOM_REB_ACTIVITY_STATUS rst ON rst.COD_DEVELOP = ra.REB_ACTIVITY_STATUS
LEFT JOIN GCTS_DICTIONARY dst ON dst.ID = rst.NAME_TYPE_XI18N AND dst.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCOM_REB_ACTIVITY_TYPE rty ON rty.COD_DEVELOP = ra.REB_ACTIVITY_TYPE
LEFT JOIN GCTS_DICTIONARY dty ON dty.ID = rty.NAME_TYPE_XI18N AND dty.LOCALE = 'EN'
LEFT JOIN GCCC_COMPLAINT cp WITH (NOLOCK) ON cp.ID_COMPLAINT = ra.ID_RECLAMATION
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_STATUS cs ON cs.COD_DEVELOP = cp.COD_STATUS_COMPLAINT
LEFT JOIN GCTS_DICTIONARY dcs ON dcs.ID = cs.NAME_TYPE_XI18N AND dcs.LOCALE = 'EN'
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = bl.ID_PAYMENT_FORM
LEFT JOIN GCCOM_BILLING_PERIOD bp WITH (NOLOCK) ON bp.ID_BILLING_PERIOD = bl.ID_BILLING_PERIOD
LEFT JOIN (
    SELECT ID_BILL, COUNT(*) AS READING_COUNT, MIN(READING_PREV_DATE) AS MIN_PREV_DATE, MAX(READING_DATE) AS MAX_READING_DATE,
           STRING_AGG(CAST(ID_READING AS varchar(20)) + '|' + ISNULL(CONVERT(varchar(19), READING_PREV_DATE, 120), '') + '|'
                      + ISNULL(CONVERT(varchar(19), READING_DATE, 120), '') + '|' + ISNULL(READ_STATUS, '') + '|'
                      + ISNULL(USAGE_TYPE, '') + '|' + ISNULL(CAST(ID_MEASURING_POINT AS varchar(20)), ''), '{READ_SEP}')
               WITHIN GROUP (ORDER BY READING_PREV_DATE, ID_READING) AS READINGS
    FROM #r GROUP BY ID_BILL
) r ON r.ID_BILL = b.ID_BILL
ORDER BY bl.LAST_BILLING_DATE, b.ID_BILL;

DROP TABLE #b; DROP TABLE #r;
"""


def _d(v) -> str:
    return str(v or "")[:19]


def shape_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        reads = []
        for part in (r.get("readings") or "").split(READ_SEP):
            if not part:
                continue
            f = (part.split("|") + [""] * 6)[:6]
            reads.append({"id_reading": f[0], "reading_prev_date": f[1], "reading_date": f[2], "read_status": f[3],
                          "usage_type": f[4], "id_measuring_point": f[5]})
        r["reading_list"] = reads
        r["line_status_en"] = LINE_STATUS_NAMES.get(r.get("line_status") or "", r.get("line_status") or "")
        n = len(reads)
        r["reading_count"] = n
        r["reading_group"] = "none" if n == 0 else ("one" if n == 1 else "many")
        proposed = _d(r.get("min_prev_date")) if n else ""
        r["proposed_last_billing_date"] = proposed
        lbd = _d(r.get("last_billing_date"))
        r["needs_change"] = bool(proposed) and proposed != lbd
        try:
            from datetime import datetime
            r["diff_days"] = (datetime.fromisoformat(lbd) - datetime.fromisoformat(proposed)).days if proposed and lbd else None
        except ValueError:
            r["diff_days"] = None
        try:   # RJ 2026-11-26 (3): the bill period after the fix (new LAST_BILLING_DATE -> BILLING_DATE)
            from datetime import datetime
            bd = _d(r.get("billing_date"))
            r["period_days_after"] = (datetime.fromisoformat(bd[:10]) - datetime.fromisoformat(proposed[:10])).days if proposed and bd else None
        except ValueError:
            r["period_days_after"] = None
    return rows


def summary(rows: list[dict]) -> dict:
    return {
        "count": len(rows),
        "one": sum(1 for r in rows if r["reading_group"] == "one"),
        "many": sum(1 for r in rows if r["reading_group"] == "many"),
        "none": sum(1 for r in rows if r["reading_group"] == "none"),
        "fixable": sum(1 for r in rows if r["needs_change"]),
        "already_ok": sum(1 for r in rows if r["reading_count"] and not r["needs_change"]),
        "anomalous": sum(1 for r in rows if r.get("line_status") == DN_ANOMALOUS),
        "disputed_line": sum(1 for r in rows if r.get("line_status") == DN_DISPUTED),
    }


def build_fix_script(rows: list[dict], bill_ids: list[str], *, user: str, program: str,
                     clean: bool = False) -> tuple[str, str, dict]:
    want = {str(b).strip() for b in bill_ids if str(b).strip()}
    pick = [r for r in rows if str(r.get("id_bill")) in want and r.get("needs_change")]
    skipped = sorted(want - {str(r.get("id_bill")) for r in pick})
    if not pick:
        raise ValueError("None of the selected bills still needs a LAST_BILLING_DATE change - re-scan.")
    user = (user or DEFAULT_USER).strip()[:25]
    program = (program or DEFAULT_PROGRAM).strip()[:100]
    q = format_sql_literal
    out = [f"-- Rebilling with anomaly in bill date (LACKS DATA) - {len(pick)} disputed bill(s)",
           "-- GCCOM_BILL.LAST_BILLING_DATE := READING_PREV_DATE of the bill's reading (earliest when more than one)",
           "-- guarded on: bill still Disputed (ESTFAC0010) and LAST_BILLING_DATE unchanged since the scan", ""]
    # RJ 2026-11-26: "do not use begin, and delcare, just the normal script" - plain UPDATEs, values inline
    rb = [f"-- ROLLBACK of: Rebilling with anomaly in bill date (LACKS DATA) - {len(pick)} bill(s)",
          "-- puts back the original LAST_BILLING_DATE / UPDATE_* where the bill still has the new date", ""]
    for r in pick:
        bid = int(float(r["id_bill"]))
        old, new = _d(r.get("last_billing_date")), r["proposed_last_billing_date"]
        reads = ", ".join(x["id_reading"] for x in r["reading_list"])
        out += [f"-- bill {r.get('bill_number')} · account {r.get('account')} · {r['reading_count']} reading(s) {reads}"
                f" · {old[:10]} -> {new[:10]}",
                "UPDATE GCCOM_BILL",
                f"SET LAST_BILLING_DATE = {q(new)}, UPDATE_DATE = GETDATE(), UPDATE_USER = {q(user)}, UPDATE_PROGRAM = {q(program)}",
                f"WHERE ID_BILL = {bid} AND BILLING_STATUS = '{BILL_DISPUTED}' AND LAST_BILLING_DATE = {q(old)};", ""]
        rb += [f"-- bill {r.get('bill_number')}",
               "UPDATE GCCOM_BILL",
               f"SET LAST_BILLING_DATE = {q(old)}, UPDATE_DATE = {q(str(r.get('bill_update_date') or '')[:23]) if r.get('bill_update_date') else 'NULL'},"
               f" UPDATE_USER = {q(r['bill_update_user']) if r.get('bill_update_user') else 'NULL'},"
               f" UPDATE_PROGRAM = {q(r['bill_update_program']) if r.get('bill_update_program') else 'NULL'}",
               f"WHERE ID_BILL = {bid} AND LAST_BILLING_DATE = {q(new)} AND UPDATE_PROGRAM = {q(program)};", ""]
    sql, rollback = "\n".join(out), "\n".join(rb)
    if clean:
        sql = "\n".join(l for l in sql.splitlines() if l.strip() and not l.lstrip().startswith("--")) + "\n"
        rollback = "\n".join(l for l in rollback.splitlines() if l.strip() and not l.lstrip().startswith("--")) + "\n"
    return sql, rollback, {"bills": len(pick), "one": sum(1 for r in pick if r["reading_group"] == "one"),
                           "many": sum(1 for r in pick if r["reading_group"] == "many"), "skipped": skipped}
