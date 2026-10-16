"""
Wrong Bill CASE 1 - Unusual high Sanitary (RJ, 2026-09-30).

RJ's query, verbatim logic: bills (ID_BILLING_PERIOD > 10000000228) with a
SANITARY billing concept, not Rebilled (ESTFAC0042) / Cancelled
(ESTFAC0007), whose TOTAL_AMOUNT > 14158; BASE = TOTAL_AMOUNT / 0.155. NISS
comes from the account's Water (19) contracted service. Live 2026-09-30:
4 rows in ~3s (top: account 1040244947, bill 1075383385, 16,594.46).

Also builds the daily alert e-mail (RJ: "when it is detected i want you to
send email starting 7 am until it is sent to ... attaching the detected
case telling me to review and apply rebilling as early as possible") -
subject/body text and a CSV attachment. Sending/scheduling lives in
web/alerts.py. Pure logic: builds text only.
"""
from __future__ import annotations

import csv
import io

MIN_BILLING_PERIOD = 10000000228
AMOUNT_THRESHOLD = 14158
SANITARY_RATE = 0.155
EXCLUDED_STATUSES = ("ESTFAC0042", "ESTFAC0007")  # Rebilled, Cancelled

ALERT_KEY = "unusual_sanitary"
ALERT_TITLE = "Wrong Bill CASE 1 - Unusual high Sanitary"
DEFAULT_RECIPIENTS = ("dev.rj.magdurulan@gmail.com", "rjmagdurulan@indracompany.com")

BILLING_TYPE_CREDIT_NOTE = "TIPFAC0011"
OFFERED_SERVICE_WATER = 19

COLUMNS = ("REFERENCE", "NISS", "ID_BILLING_PERIOD", "ID_BILL", "BILL_NUMBER", "BILLING_DATE", "CREATE_DATE",
           "BILLING_STATUS", "COD_CONCEPT", "PRINT_DESCRIPTION", "TOTAL_AMOUNT", "BASE",
           "WATER_ID_BILL", "WATER_BILL_COUNT", "WATER_TOTAL_AMOUNT", "SANITARY_MINUS_WATER")


def build_query() -> str:
    """RJ 2026-09-30 (round 2): a candidate Sanitary bill is only kept when
    its TOTAL_AMOUNT is greater than the Water bill of the same payment form
    (GCCOM_BILL.ID_PAYMENT_FORM) with the same ID_BILLING_PERIOD and
    BILLING_DATE, billed on a Water (19) contracted service, not a credit
    note (TIPFAC0011) and not Cancelled/Rebilled. If an account has several
    such Water bills their amounts are summed. No Water bill -> excluded
    (nothing to compare against)."""
    excluded = ", ".join(f"'{s}'" for s in EXCLUDED_STATUSES)
    return f"""
SELECT pf.REFERENCE AS REFERENCE,
       nis.NISS AS NISS,
       b.ID_BILLING_PERIOD AS ID_BILLING_PERIOD,
       b.ID_BILL AS ID_BILL,
       b.BILL_NUMBER AS BILL_NUMBER,
       b.BILLING_DATE AS BILLING_DATE,
       b.CREATE_DATE AS CREATE_DATE,
       b.BILLING_STATUS AS BILLING_STATUS,
       bc.COD_CONCEPT AS COD_CONCEPT,
       bc.PRINT_DESCRIPTION AS PRINT_DESCRIPTION,
       b.TOTAL_AMOUNT AS TOTAL_AMOUNT,
       b.TOTAL_AMOUNT / {SANITARY_RATE} AS BASE,
       wb.WATER_ID_BILL AS WATER_ID_BILL,
       wb.WATER_BILL_COUNT AS WATER_BILL_COUNT,
       wb.WATER_TOTAL_AMOUNT AS WATER_TOTAL_AMOUNT,
       b.TOTAL_AMOUNT - wb.WATER_TOTAL_AMOUNT AS SANITARY_MINUS_WATER
FROM GCCOM_BILLING_CONCEPT bc WITH (NOLOCK)
JOIN GCCOM_BILL b WITH (NOLOCK)
     ON b.ID_BILL = bc.ID_BILL AND b.ID_BILLING_PERIOD > {int(MIN_BILLING_PERIOD)}
JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
CROSS APPLY (
    SELECT COUNT(*) AS WATER_BILL_COUNT,
           SUM(w.TOTAL_AMOUNT) AS WATER_TOTAL_AMOUNT,
           MIN(w.ID_BILL) AS WATER_ID_BILL
    FROM GCCOM_BILL w WITH (NOLOCK)
    JOIN GCCOM_CONTRACTED_SERVICE wcs WITH (NOLOCK)
         ON wcs.ID_CONTRACTED_SERVICE = w.ID_CONTRACTED_SERVICE AND wcs.ID_OFFERED_SERVICE = {OFFERED_SERVICE_WATER}
    WHERE w.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
      AND w.ID_BILLING_PERIOD = b.ID_BILLING_PERIOD
      AND w.BILLING_DATE = b.BILLING_DATE
      AND ISNULL(w.BILLING_TYPE, '') <> '{BILLING_TYPE_CREDIT_NOTE}'
      AND w.BILLING_STATUS NOT IN ({excluded})
) wb
OUTER APPLY (
    SELECT TOP 1 ss.NISS
    FROM GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
    JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
    WHERE cs.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM AND cs.ID_OFFERED_SERVICE = {OFFERED_SERVICE_WATER}
    ORDER BY cs.ID_CONTRACTED_SERVICE DESC
) nis
WHERE bc.COD_CONCEPT = 'SANITARY'
  AND b.BILLING_STATUS NOT IN ({excluded})
  AND b.TOTAL_AMOUNT > {AMOUNT_THRESHOLD}
  AND wb.WATER_BILL_COUNT > 0
  AND b.TOTAL_AMOUNT > wb.WATER_TOTAL_AMOUNT
ORDER BY b.TOTAL_AMOUNT DESC
"""


# ---------------------------------------------------------------------------
# Automatic rebilling (RJ, 2026-10-09): "for wrong bill, case 1 high sanitary,
# automatically generate a rebilling activity, same as double itb criteria,
# get the water bill and add both in the same rebilling activity, generate
# script for insert and monitoring, description should be
# 'WRONG-SANITARY HIGH BILL'".
# Same insert as DOUBLE ITB (double_itb.rebilling_insert_lines): activity
# In process ESTREF0002 / Massive substitutive TIPREF0005 / REBRES003, one
# GCCOM_REB_ACTIVITY_INFORMATION per bill (Sanitary + its Water bill(s), same
# payment form / billing period / billing date), bills -> Disputed, and the
# /* */ monitoring query at the end.
# ---------------------------------------------------------------------------
REB_DESCRIPTION = "WRONG-SANITARY HIGH BILL"
FIX_PROGRAM_DEFAULT = "WRONG_SANITARY_HIGH"
BILL_STATUS_INVOICED = "ESTFAC0005"
# RJ 2026-10-09: "yes rebill now, even generated" - Generated (ESTFAC0008)
# bills were rebilled like Invoiced ones.
# RJ 2026-10-14: "double itb is working in my system but the one from high
# bill sanitary cannot be processed by my system". Live comparison: every
# processed DOUBLE ITB activity held INVOICED bills (real BILL_NUMBER,
# EXPIRATION_DATE, COLLECTION_STATUS); the High Sanitary ones held freshly
# GENERATED bills (BILL_NUMBER = ID_BILL, no expiry / collection status) and
# the 2026-10-09 ones were rejected (bills ended Cancelled). So only Invoiced
# bills are rebilled now; an activity with a Generated (or other) bill is
# skipped with a warning - rebill it once the bill is invoiced.
BILL_STATUS_GENERATED = "ESTFAC0008"
REBILLABLE_STATUSES = (BILL_STATUS_INVOICED,)


def build_rebilling_context_query(sanitary_bill_ids) -> str:
    """Fresh state of the selected Sanitary bills at generate time: customer,
    status, the matching Water bill(s) and any OPEN rebilling activity that
    already holds one of these bills (not Rejected/Rebilled)."""
    ids = sorted({int(x) for x in sanitary_bill_ids if str(x).strip()})
    if not ids:
        raise ValueError("No bills selected.")
    excluded = ", ".join(f"'{s}'" for s in EXCLUDED_STATUSES)
    return f"""
SELECT b.ID_BILL AS SAN_ID_BILL, b.BILLING_STATUS AS SAN_STATUS, b.TOTAL_AMOUNT AS SAN_AMOUNT,
       pf.REFERENCE AS ACCOUNT, pf.ID_CUSTOMER AS ID_CUSTOMER,
       wb.WATER_IDS, wb.WATER_STATUSES,
       (SELECT STRING_AGG(CAST(x.ID_REBILLING_ACTIVITY AS varchar(20)), ',')
          FROM (SELECT DISTINCT ri.ID_REBILLING_ACTIVITY
                  FROM OUC_ADMIN.GCCOM_REB_ACTIVITY_INFORMATION ri WITH (NOLOCK)
                  JOIN OUC_ADMIN.GCCOM_REBILLING_ACTIVITY ra WITH (NOLOCK) ON ra.ID_REBILLING_ACTIVITY = ri.ID_REBILLING_ACTIVITY
                 WHERE ra.REB_ACTIVITY_STATUS NOT IN ('ESTREF0003', 'ESTREF0004')
                   AND (ri.ID_BILL = b.ID_BILL
                        OR ri.ID_BILL IN (SELECT w2.ID_BILL FROM GCCOM_BILL w2 WITH (NOLOCK)
                                          JOIN GCCOM_CONTRACTED_SERVICE c2 WITH (NOLOCK)
                                               ON c2.ID_CONTRACTED_SERVICE = w2.ID_CONTRACTED_SERVICE AND c2.ID_OFFERED_SERVICE = {OFFERED_SERVICE_WATER}
                                          WHERE w2.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM AND w2.ID_BILLING_PERIOD = b.ID_BILLING_PERIOD
                                            AND w2.BILLING_DATE = b.BILLING_DATE))) x) AS OPEN_REBILLING_IDS
FROM GCCOM_BILL b WITH (NOLOCK)
JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
OUTER APPLY (
    SELECT STRING_AGG(CAST(w.ID_BILL AS varchar(20)), ',') WITHIN GROUP (ORDER BY w.ID_BILL) AS WATER_IDS,
           STRING_AGG(w.BILLING_STATUS, ',') WITHIN GROUP (ORDER BY w.ID_BILL) AS WATER_STATUSES
    FROM GCCOM_BILL w WITH (NOLOCK)
    JOIN GCCOM_CONTRACTED_SERVICE wcs WITH (NOLOCK)
         ON wcs.ID_CONTRACTED_SERVICE = w.ID_CONTRACTED_SERVICE AND wcs.ID_OFFERED_SERVICE = {OFFERED_SERVICE_WATER}
    WHERE w.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM
      AND w.ID_BILLING_PERIOD = b.ID_BILLING_PERIOD
      AND w.BILLING_DATE = b.BILLING_DATE
      AND ISNULL(w.BILLING_TYPE, '') <> '{BILLING_TYPE_CREDIT_NOTE}'
      AND w.BILLING_STATUS NOT IN ({excluded})
) wb
WHERE b.ID_BILL IN ({', '.join(str(i) for i in ids)})
ORDER BY b.ID_BILL
"""


# ---------------------------------------------------------------------------
# RJ 2026-10-14: "for high bill case, if the bill is still in generated
# status, the correction is to update the XML of both water and sanitary,
# cancel the SANITARY bill and change the ITB status to pending for it to be
# billed again, only when the bill is in generated state, no rebilling
# needed. Rebilling is only when it is invoiced".
# Live analysis of the 2026-10-14 cases: the Sanitary item's reading row
# (OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL) had PREV_VALUE > VALUE (meter
# rollover, ~99,999 usage) while the Water row of the SAME reading and the
# reading itself (GCGT_RE_READING) were right; both XMLs (GCCOM_ITEMS_TO_BILL_
# XML.XML_TO_BILL) carried the rollover <consumption>. Correction per
# Generated Sanitary bill:
#   1. Sanitary reading row = Water reading row of the same reading
#      (PREV_VALUE, VALUE, READY_USAGE, IND_ESTIMATE, READING_DATE)
#   2. <consumption> of the Sanitary XML and of the Water XML = the Water
#      reading rows' READY_USAGE (sum over the item's readings); the XMLs'
#      own sectorSupplyId / idContractedService / idBillingService are kept
#   3. Sanitary bill -> ESTFAC0007 Cancelled (Water bill untouched)
#   4. Sanitary item to bill STTOBILL07 Billed -> STTOBILL01 Pending
# Each statement is guarded by the value it expects (safe to re-run); a
# rollback puts everything back.
# ---------------------------------------------------------------------------
BILL_STATUS_CANCELLED = "ESTFAC0007"
ITB_STATUS_BILLED = "STTOBILL07"
ITB_STATUS_PENDING = "STTOBILL01"
READINGS_ITB_TABLE = "OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL"
ITB_XML_TABLE = "GCCOM_ITEMS_TO_BILL_XML"


def build_generated_fix_context_query(sanitary_bill_ids) -> str:
    """One row per reading row of each selected Sanitary bill's item(s) to
    bill, with the Water reading row of the same reading (on the Water bill
    of the same payment form / billing period / billing date) and both XMLs'
    current <consumption> text + number of <consumption> nodes."""
    ids = sorted({int(x) for x in sanitary_bill_ids if str(x).strip()})
    if not ids:
        raise ValueError("No bills selected.")
    excluded = ", ".join(f"'{s}'" for s in EXCLUDED_STATUSES)
    cons = ("SUBSTRING(CAST({x} AS varchar(max)), CHARINDEX('<consumption>', CAST({x} AS varchar(max))) + 13, "
            "CHARINDEX('</consumption>', CAST({x} AS varchar(max))) - CHARINDEX('<consumption>', CAST({x} AS varchar(max))) - 13)")
    cnt = "(LEN(CAST({x} AS varchar(max))) - LEN(REPLACE(CAST({x} AS varchar(max)), '<consumption>', ''))) / 13"
    return f"""
SELECT sb.ID_BILL AS SAN_ID_BILL, sb.BILLING_STATUS AS SAN_STATUS,
       si.ID_ITEM_TO_BILL AS SAN_ITB, si.STATUS AS SAN_ITB_STATUS, si.ID_XML AS SAN_XML,
       sr.ID_READING_ITEM2BILL AS SAN_RIT, sr.ID_READING AS ID_READING,
       sr.PREV_VALUE AS SAN_PREV, sr.VALUE AS SAN_VALUE, sr.READY_USAGE AS SAN_USAGE,
       sr.IND_ESTIMATE AS SAN_EST, sr.READING_DATE AS SAN_RDATE,
       w0.W_BILL AS WAT_ID_BILL, w0.W_STATUS AS WAT_STATUS,
       w0.W_ITB AS WAT_ITB, w0.W_XML AS WAT_XML,
       w0.W_PREV AS WAT_PREV, w0.W_VALUE AS WAT_VALUE, w0.W_USAGE AS WAT_USAGE,
       w0.W_EST AS WAT_EST, w0.W_RDATE AS WAT_RDATE,
       CASE WHEN sx.XML_TO_BILL LIKE '%<consumption>%' THEN {cons.format(x='sx.XML_TO_BILL')} END AS SAN_XML_CONS,
       {cnt.format(x='sx.XML_TO_BILL')} AS SAN_XML_CONS_N,
       CASE WHEN wx.XML_TO_BILL LIKE '%<consumption>%' THEN {cons.format(x='wx.XML_TO_BILL')} END AS WAT_XML_CONS,
       {cnt.format(x='wx.XML_TO_BILL')} AS WAT_XML_CONS_N
FROM GCCOM_BILL sb WITH (NOLOCK)
JOIN GCCOM_ITEMS_TO_BILL si WITH (NOLOCK) ON si.ID_BILL = sb.ID_BILL
JOIN {READINGS_ITB_TABLE} sr WITH (NOLOCK) ON sr.ID_ITEM_TO_BILL = si.ID_ITEM_TO_BILL
LEFT JOIN {ITB_XML_TABLE} sx WITH (NOLOCK) ON sx.ID_XML = si.ID_XML
OUTER APPLY (
    SELECT TOP 1 w.ID_BILL AS W_BILL, w.BILLING_STATUS AS W_STATUS, i2.ID_ITEM_TO_BILL AS W_ITB, i2.ID_XML AS W_XML,
           r2.PREV_VALUE AS W_PREV, r2.VALUE AS W_VALUE, r2.READY_USAGE AS W_USAGE, r2.IND_ESTIMATE AS W_EST,
           r2.READING_DATE AS W_RDATE
    FROM {READINGS_ITB_TABLE} r2 WITH (NOLOCK)
    JOIN GCCOM_ITEMS_TO_BILL i2 WITH (NOLOCK) ON i2.ID_ITEM_TO_BILL = r2.ID_ITEM_TO_BILL
    JOIN GCCOM_BILL w WITH (NOLOCK) ON w.ID_BILL = i2.ID_BILL
    JOIN GCCOM_CONTRACTED_SERVICE wcs WITH (NOLOCK)
         ON wcs.ID_CONTRACTED_SERVICE = w.ID_CONTRACTED_SERVICE AND wcs.ID_OFFERED_SERVICE = {OFFERED_SERVICE_WATER}
    WHERE r2.ID_READING = sr.ID_READING AND r2.ID_ITEM_TO_BILL <> sr.ID_ITEM_TO_BILL
      AND w.ID_PAYMENT_FORM = sb.ID_PAYMENT_FORM AND w.ID_BILLING_PERIOD = sb.ID_BILLING_PERIOD
      AND w.BILLING_DATE = sb.BILLING_DATE
      AND ISNULL(w.BILLING_TYPE, '') <> '{BILLING_TYPE_CREDIT_NOTE}' AND w.BILLING_STATUS NOT IN ({excluded})
    ORDER BY w.ID_BILL
) w0
LEFT JOIN {ITB_XML_TABLE} wx WITH (NOLOCK) ON wx.ID_XML = w0.W_XML
WHERE sb.ID_BILL IN ({', '.join(str(i) for i in ids)})
  AND si.STATUS <> 'STTOBILL04'
ORDER BY sb.ID_BILL, si.ID_ITEM_TO_BILL, sr.ID_READING
"""


def _num(v) -> str:
    """SQL numeric literal from a DB value (None -> NULL)."""
    if v is None or str(v).strip() == "":
        return "NULL"
    s = str(v).strip()
    try:
        float(s)
    except ValueError:
        return "NULL"
    return s


def _dt(v) -> str:
    if v is None or str(v).strip() == "":
        return "NULL"
    return "'" + str(v).replace("T", " ")[:23] + "'"


def _cons_text(v) -> str:
    """XML consumption text in the system's format (e.g. 1.0, 99999.0)."""
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return "0.0"


def build_generated_fix_blocks(san_bill: int, fix_rows: list[dict], *, u: str, p: str, pr: str) -> tuple[list[str], list[str], list[str]]:
    """Correction lines + rollback lines + warnings for ONE Generated
    Sanitary bill. fix_rows = its rows from build_generated_fix_context_query
    (lower-case keys). u / p are formatted SQL literals; pr = rollback program literal."""
    lines: list[str] = []
    rb: list[str] = []
    warns: list[str] = []
    upd = f"UPDATE_DATE = GETDATE(), UPDATE_USER = {u}, UPDATE_PROGRAM = {p}"
    upd_rb = f"UPDATE_DATE = GETDATE(), UPDATE_USER = {u}, UPDATE_PROGRAM = {pr}"
    missing = [r for r in fix_rows if not r.get("wat_itb")]
    if not fix_rows:
        return [], [], [f"Bill {san_bill}: no item to bill / reading rows found - skipped."]
    if missing:
        return [], [], [f"Bill {san_bill}: reading(s) {', '.join(str(r.get('id_reading')) for r in missing)} have no Water "
                         "item to bill on the same reading - skipped (fix by hand)."]
    # 1. Sanitary reading rows = Water reading rows of the same reading
    for r in fix_rows:
        lines.append(f"-- reading {r['id_reading']}: Sanitary {_num(r['san_prev'])}>{_num(r['san_value'])} "
                     f"({_num(r['san_usage'])}) -> Water {_num(r['wat_prev'])}>{_num(r['wat_value'])} ({_num(r['wat_usage'])})")
        lines.append(
            f"UPDATE {READINGS_ITB_TABLE} SET PREV_VALUE = {_num(r['wat_prev'])}, VALUE = {_num(r['wat_value'])}, "
            f"READY_USAGE = {_num(r['wat_usage'])}, IND_ESTIMATE = {_num(r['wat_est'])}, READING_DATE = {_dt(r['wat_rdate'])}, {upd}\n"
            f" WHERE ID_READING_ITEM2BILL = {int(r['san_rit'])} AND ID_READING = {int(r['id_reading'])}"
            + (f" AND PREV_VALUE = {_num(r['san_prev'])};" if _num(r['san_prev']) != "NULL" else " AND PREV_VALUE IS NULL;"))
        rb.append(
            f"UPDATE {READINGS_ITB_TABLE} SET PREV_VALUE = {_num(r['san_prev'])}, VALUE = {_num(r['san_value'])}, "
            f"READY_USAGE = {_num(r['san_usage'])}, IND_ESTIMATE = {_num(r['san_est'])}, READING_DATE = {_dt(r['san_rdate'])}, {upd_rb}\n"
            f" WHERE ID_READING_ITEM2BILL = {int(r['san_rit'])};")
    # 2. XML <consumption> of the Sanitary and the Water item(s)
    xml_targets: dict[int, dict] = {}
    for r in fix_rows:
        for side in ("san", "wat"):
            xid = r.get(f"{side}_xml")
            if not xid:
                continue
            t = xml_targets.setdefault(int(xid), {"side": side, "cur": r.get(f"{side}_xml_cons"),
                                                  "n": r.get(f"{side}_xml_cons_n"), "usage": 0.0, "itb": r.get(f"{side}_itb")})
            try:
                t["usage"] += float(r.get("wat_usage") or 0)
            except (TypeError, ValueError):
                pass
    for xid, t in xml_targets.items():
        who = "Sanitary" if t["side"] == "san" else "Water"
        cur = (str(t["cur"]).strip() if t["cur"] is not None else "")
        new = _cons_text(t["usage"])
        try:
            n = int(float(t["n"] or 0))
        except (TypeError, ValueError):
            n = 0
        if n != 1 or not cur:
            warns.append(f"Bill {san_bill}: {who} XML {xid} has {n} <consumption> node(s) - not changed, check by hand.")
            lines.append(f"-- {who} XML {xid}: {n} <consumption> node(s) - NOT changed, check by hand")
            continue
        if cur == new:
            lines.append(f"-- {who} XML {xid}: <consumption> already {new}")
            continue
        lines.append(f"-- {who} XML {xid} (ITB {t['itb']}): <consumption> {cur} -> {new}")
        lines.append(
            f"UPDATE {ITB_XML_TABLE} SET XML_TO_BILL = REPLACE(XML_TO_BILL, '<consumption>{cur}</consumption>', "
            f"'<consumption>{new}</consumption>'), {upd}\n WHERE ID_XML = {xid} AND XML_TO_BILL LIKE '%<consumption>{cur}</consumption>%';")
        rb.append(
            f"UPDATE {ITB_XML_TABLE} SET XML_TO_BILL = REPLACE(XML_TO_BILL, '<consumption>{new}</consumption>', "
            f"'<consumption>{cur}</consumption>'), {upd_rb}\n WHERE ID_XML = {xid} AND XML_TO_BILL LIKE '%<consumption>{new}</consumption>%';")
    # 3. cancel the Sanitary bill  4. Sanitary item(s) to bill -> Pending
    lines.append(f"UPDATE OUC_COMMON_ADMIN.GCCOM_BILL SET BILLING_STATUS = '{BILL_STATUS_CANCELLED}', {upd}"
                 f" WHERE ID_BILL = {int(san_bill)} AND BILLING_STATUS = '{BILL_STATUS_GENERATED}';")
    rb.append(f"UPDATE OUC_COMMON_ADMIN.GCCOM_BILL SET BILLING_STATUS = '{BILL_STATUS_GENERATED}', {upd_rb}"
              f" WHERE ID_BILL = {int(san_bill)} AND BILLING_STATUS = '{BILL_STATUS_CANCELLED}';")
    for itb, st in sorted({(int(r["san_itb"]), r.get("san_itb_status")) for r in fix_rows}):
        if st != ITB_STATUS_BILLED:
            lines.append(f"-- Sanitary ITB {itb} is {st}, not {ITB_STATUS_BILLED} - status left as is")
            continue
        lines.append(f"UPDATE GCCOM_ITEMS_TO_BILL SET STATUS = '{ITB_STATUS_PENDING}', {upd}"
                     f" WHERE ID_ITEM_TO_BILL = {itb} AND STATUS = '{ITB_STATUS_BILLED}';")
        rb.append(f"UPDATE GCCOM_ITEMS_TO_BILL SET STATUS = '{ITB_STATUS_BILLED}', {upd_rb}"
                  f" WHERE ID_ITEM_TO_BILL = {itb} AND STATUS = '{ITB_STATUS_PENDING}';")
    return lines, rb, warns


def build_rebilling_script(rows: list[dict], *, user: str = "RMA", program: str = FIX_PROGRAM_DEFAULT,
                           creation_user: int = 10000008456, batch: bool = True, clean: bool = False,
                           fix_rows: list[dict] | None = None) -> dict:
    """rows: dicts from build_rebilling_context_query (lower-case keys).
    One rebilling activity per Sanitary bill with the Sanitary + Water
    bill(s). Skips (with a warning) bills already in an open rebilling, bills
    already Disputed/Cancelled/Rebilled, and bills with no customer."""
    import datetime as _dt
    from . import double_itb as _ditb
    from .sql_format import format_sql_literal
    u, p = format_sql_literal(user), format_sql_literal(program)
    dn = _ditb.REB_DN_STATUS_BATCH if batch else _ditb.REB_DN_STATUS_CLAIMED
    warnings: list[str] = []
    blocks: list[str] = []
    rb_blocks: list[str] = []
    n_reb = n_bills = n_fix = 0
    for i, r in enumerate(rows, start=1):
        san = r.get("san_id_bill")
        acct = r.get("account") or ""
        water = [int(x) for x in str(r.get("water_ids") or "").split(",") if x.strip().isdigit()]
        wst = [x for x in str(r.get("water_statuses") or "").split(",") if x.strip()]
        head = (f"-- [{i}] Account {acct}: Sanitary bill {san} ({r.get('san_status')}, {r.get('san_amount')}) "
                f"+ Water bill(s) {', '.join(map(str, water)) or 'none'}")
        if r.get("open_rebilling_ids"):
            warnings.append(f"Bill {san} (account {acct}): already in open rebilling activity {r.get('open_rebilling_ids')} - skipped.")
            blocks.append(head + f"\n-- SKIPPED: already in open rebilling activity {r.get('open_rebilling_ids')}")
            continue
        if not r.get("id_customer"):
            warnings.append(f"Bill {san} (account {acct}): no customer on the payment form - skipped.")
            blocks.append(head + "\n-- SKIPPED: no customer found")
            continue
        if r.get("san_status") in EXCLUDED_STATUSES or r.get("san_status") == _ditb.BILL_STATUS_DISPUTED:
            warnings.append(f"Bill {san} (account {acct}): status {r.get('san_status')} - skipped.")
            blocks.append(head + f"\n-- SKIPPED: Sanitary bill status {r.get('san_status')}")
            continue
        lines = [head]
        if not water:
            warnings.append(f"Bill {san} (account {acct}): no Water bill found - rebilling has the Sanitary bill only.")
            lines.append("-- WARNING: no Water bill found - rebilling has the Sanitary bill only")
        elif len(water) > 1:
            warnings.append(f"Bill {san} (account {acct}): {len(water)} Water bills for that period/date - all included.")
            lines.append(f"-- NOTE: {len(water)} Water bills match - all included")
        # RJ 2026-10-14: Sanitary bill still GENERATED -> no rebilling; correct
        # the reading rows + XMLs, cancel the Sanitary bill, item to bill Pending.
        if r.get("san_status") == BILL_STATUS_GENERATED:
            mine = [f for f in (fix_rows or []) if str(f.get("san_id_bill")) == str(san)]
            fl, frb, fw = build_generated_fix_blocks(int(san), mine, u=u, p=p,
                                                     pr=format_sql_literal(f"{program} ROLLBACK"))
            warnings.extend(fw)
            if not fl:
                blocks.append(head + "\n-- SKIPPED (Generated): " + "; ".join(fw or ["no correction data"]))
                continue
            n_fix += 1
            blocks.append("\n".join([head, "-- Sanitary bill is GENERATED (not invoiced) -> correction, no rebilling:"] + fl))
            rb_blocks.append("\n".join([f"-- [{i}] ROLLBACK of the Generated-bill correction, account {acct}: Sanitary bill {san}"] + frb))
            continue
        # RJ 2026-10-14: every bill of the activity must be Invoiced, else skip
        not_inv = [s for s in ([r.get("san_status")] + wst) if s and s not in REBILLABLE_STATUSES]
        if not_inv:
            what = ", ".join(sorted(set(not_inv)))
            hint = " (Generated = not invoiced yet - rebill after invoicing)" if BILL_STATUS_GENERATED in not_inv else ""
            warnings.append(f"Bill {san} (account {acct}): a bill in the activity is {what}, not Invoiced{hint} - skipped.")
            blocks.append(head + f"\n-- SKIPPED: a bill is {what}, not Invoiced ({BILL_STATUS_INVOICED}){hint}")
            continue
        # RJ 2026-10-14: same order as DOUBLE ITB (processed fine): Water bill(s)
        # first, the Sanitary bill (which follows the Water) last.
        bills = water + [int(san)]
        n_reb += 1
        n_bills += len(bills)
        lines.append(f"-- rebilling of bill(s) {', '.join(map(str, bills))}" + (" (processed by batch)" if batch else ""))
        lines += _ditb.rebilling_insert_lines(f"@ID_REB_{i}", int(r.get("id_customer")), bills, u=u, p=p, dn=dn,
                                              creation_user=creation_user, description=REB_DESCRIPTION)
        blocks.append("\n".join(lines))
        # RJ 2026-10-09: matching rollback (bills back to their status at generate time)
        prior = {int(san): r.get("san_status")}
        prior.update({w: s for w, s in zip(water, wst)})
        rb_blocks.append("\n".join([f"-- [{i}] ROLLBACK account {acct}: Sanitary bill {san} + Water bill(s) {', '.join(map(str, water)) or '-'}"]
                                   + _ditb.rebilling_rollback_lines(int(r.get("id_customer")), bills, description=REB_DESCRIPTION,
                                                                    prior_bill_status=prior, u=u, p=p)))
    header = [
        "-- Wrong Bill CASE 1 - Unusual high Sanitary: REBILLING script - generated by ScriptGen",
        f"-- Generated (UTC): {_dt.datetime.now(_dt.timezone.utc).isoformat()}",
        f"-- Selected Sanitary bills: {len(rows)} - rebilling activities (INVOICED bills): {n_reb} with {n_bills} bill row(s) (Water + Sanitary)"
        f" - corrections (GENERATED bills, no rebilling): {n_fix}",
        "-- Generated Sanitary bill: Sanitary reading row = Water row of the same reading, Sanitary + Water XML <consumption>",
        "--   = real usage, Sanitary bill -> ESTFAC0007 Cancelled, Sanitary item to bill STTOBILL07 -> STTOBILL01 Pending.",
        f"-- Activity {_ditb.REB_ACTIVITY_STATUS_PENDING} (In process) / {_ditb.REB_ACTIVITY_TYPE} (Massive substitutive), "
        f"DESCRIPTION '{REB_DESCRIPTION}', bill notes in {dn}" + (" (batch recalculation)" if batch else "")
        + f", bills -> {_ditb.BILL_STATUS_DISPUTED} (Disputed)",
        f"-- User / program / creation user: {user} / {program} / {creation_user}",
        "-- Run as ONE batch (each @ID_REB_n links the activity to its information rows).",
    ] + [f"-- WARNING: {w}" for w in warnings] + [""]
    text = "\n".join(header) + ("\n\n".join(blocks) if blocks else "-- Nothing to rebill.") + "\n\n-- Review before running.\n"
    if clean:
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("--")) + "\n"
    if n_reb:
        text += _ditb.followup_block(REB_DESCRIPTION)
    rollback = (_ditb.rollback_header("High Sanitary rebilling / correction", user, program) + "\n\n".join(rb_blocks) + "\n") if rb_blocks else ""
    return {"sql_text": text, "rollback_sql": rollback, "rebilling_count": n_reb, "bill_count": n_bills,
            "fix_count": n_fix, "warnings": warnings}


def rows_to_csv(rows: list[dict]) -> str:
    """rows: dicts with lowercase keys (as the web layer builds them)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow([r.get(c.lower(), "") for c in COLUMNS])
    return buf.getvalue()


def build_email(rows: list[dict], run_date: str) -> tuple[str, str]:
    """(subject, plain-text body) for the alert."""
    n = len(rows)
    bills = len({r.get("id_bill") for r in rows})
    subject = f"[ScriptGen] ACTION NEEDED - {ALERT_TITLE}: {bills} bill(s) detected ({run_date})"
    lines = [
        "Hello RJ,",
        "",
        f"ScriptGen detected {bills} bill(s) with an unusually high Sanitary charge "
        f"(TOTAL_AMOUNT > {AMOUNT_THRESHOLD:,}, SANITARY concept, not Rebilled/Cancelled, "
        f"and higher than the account's Water bill for the same billing period / billing date).",
        "",
        "Please REVIEW them and APPLY REBILLING AS EARLY AS POSSIBLE.",
        "",
        "Detected bills (full list in the attached CSV):",
    ]
    for r in rows[:20]:
        lines.append(
            f"  - Account {r.get('reference', '')} | NISS {r.get('niss', '')} | bill {r.get('id_bill', '')} "
            f"| period {r.get('id_billing_period', '')} | status {r.get('billing_status', '')} "
            f"| sanitary {r.get('total_amount', '')} vs water {r.get('water_total_amount', '')}"
        )
    if n > 20:
        lines.append(f"  ... and {n - 20} more row(s) in the attachment.")
    lines += ["", "The rebilling script (Sanitary + Water bill in one activity, DESCRIPTION "
              f"'{REB_DESCRIPTION}') is attached as .sql and pasted at the end of this e-mail.",
              "Open ScriptGen > Wrong Bill CASE 1 - Unusual high Sanitary for details.", "",
              "-- Sent automatically by ScriptGen"]
    return subject, "\n".join(lines)
