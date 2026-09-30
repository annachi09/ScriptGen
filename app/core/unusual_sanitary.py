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
    lines += ["", "Open ScriptGen > Wrong Bill CASE 1 - Unusual high Sanitary for details.", "",
              "-- Sent automatically by ScriptGen"]
    return subject, "\n".join(lines)
