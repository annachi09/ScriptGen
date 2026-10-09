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
# bills are rebilled like Invoiced ones, no warning.
REBILLABLE_STATUSES = (BILL_STATUS_INVOICED, "ESTFAC0008")


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


def build_rebilling_script(rows: list[dict], *, user: str = "RMA", program: str = FIX_PROGRAM_DEFAULT,
                           creation_user: int = 10000008456, batch: bool = True, clean: bool = False) -> dict:
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
    n_reb = n_bills = 0
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
        for st_ in ([r.get("san_status")] + wst):
            if st_ and st_ not in REBILLABLE_STATUSES:
                lines.append(f"-- WARNING: a bill is {st_}, not Invoiced/Generated ({', '.join(REBILLABLE_STATUSES)}) - check before running")
                warnings.append(f"Bill {san} (account {acct}): a bill in the activity is {st_}, not Invoiced/Generated.")
                break
        bills = [int(san)] + water
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
        f"-- Selected Sanitary bills: {len(rows)} - rebilling activities: {n_reb} with {n_bills} bill row(s) (Sanitary + Water)",
        f"-- Activity {_ditb.REB_ACTIVITY_STATUS_PENDING} (In process) / {_ditb.REB_ACTIVITY_TYPE} (Massive substitutive), "
        f"DESCRIPTION '{REB_DESCRIPTION}', bill notes in {dn}" + (" (batch recalculation)" if batch else "")
        + f", bills -> {_ditb.BILL_STATUS_DISPUTED} (Disputed)",
        f"-- User / program / creation user: {user} / {program} / {creation_user}",
        "-- Run as ONE batch (each @ID_REB_n links the activity to its information rows).",
    ] + [f"-- WARNING: {w}" for w in warnings] + [""]
    text = "\n".join(header) + ("\n\n".join(blocks) if blocks else "-- Nothing to rebill.") + "\n\n-- Review before running.\n"
    if clean:
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("--")) + "\n"
    text += _ditb.followup_block(REB_DESCRIPTION)
    rollback = (_ditb.rollback_header("High Sanitary rebilling", user, program) + "\n\n".join(rb_blocks) + "\n") if rb_blocks else ""
    return {"sql_text": text, "rollback_sql": rollback, "rebilling_count": n_reb, "bill_count": n_bills, "warnings": warnings}


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
