"""
TNB Dashboard (RJ, 2026-11-25).

RJ: "create a new menu for TNB Dashboard, 1st tab is TNB billed but transferred transaction is not done. for
EXAMPLE is like this RCALL260814218, so the destination account = select a.* from GCCC_COMPLAINT_ATT a join
GCCC_COMPLAINT c on c.ID_COMPLAINT = a.ID_COMPLAINT where COD_REF_COMPLAINT = 'RCALL260814218', the RCCS is
resolved and the account is billed of the rccs, bill number 11029015172609040001 irregularities, fraud cycle".

How the TNB workflow works (graph 10000000781, read live 2026-11-25 from GCGT_WF_PACTIVITY / STEP_ACTION):
  RCCS type 5164COMTYP "Electricity - Billing of Termination not billed" on the terminated (source) account
  -> STEP122090 Fraud WO Generation -> STEP122091 Billing of TNB Usage (ACTI700305 Fraud Billing: a
  TIPFAC0013 "Fraud Off Cycle" bill on the source account) -> STEP122078 Assignation (ACTI700316 Destination
  Account Search: GCCC_COMPLAINT_ATT COD_MASTER_ATTRIBUTE 'TNBDESTACC', ATT_VALUE = destination REFERENCE)
  -> STEP122093 Validate Owner Account -> STEP122094 Debt Transfer (ACTI700308) -> Solved (COMPLST054).

What a DONE debt transfer leaves (RCALL261035493, 33.02, checked live): wfLaunchAction settles the source TNB
bill with an online collection of payment mode TIMOPA0006 "Credit Allocation" (GCCB_COLLECTION_NOTICE_BILL ->
GCCB_ONLINE_COLLECTION) and creates the same amount as a TIPFAC0004 "Off cycle" bill on the destination account.

Case 1 "Billed, debt not transferred": RCCS Solved, with a destination account, a TIPFAC0013 bill on the source
account created on/after the RCCS (not Cancelled ESTFAC0007 / Rebilled ESTFAC0042), amount > 0, and NO active
Credit Allocation (TIMOPA0006) collection on that bill. RJ's example: bill 11029015172609040001 (0.45) was paid
by EFT (TIMOPA0017) on the source on 2026-09-26, the Debt Transfer action is "Solved" (2026-11-25 10:06), but
nothing reached destination 1073364216 (ID_PAYMENT_FORM 879834).
Live 2026-11-25: 72 bills - 23 paid by the customer by EFT, 23 by Wire Transfer (TIMOPA0005), 23 still unpaid on
the source, the rest mixed; 3,965.90 in total. For comparison 910 bills were transferred normally; 59 TNB bills
with amount 0 had nothing to transfer (left out).
Extra columns (same rows): how the source bill was settled (payment modes), the Debt Transfer action's status,
and whether the destination has the transfer's Off-cycle bill (TIPFAC0004 / TFGEN10027, same amount, created after
the TNB bill). When it does, the transfer is HALF done: the destination was billed but the source bill was never
credited - e.g. RCALL251106477: destination 1099587352 bill 1077073448 (1.54, 2026-10-19, already paid by the
customer) while source TNB bill 1074620757 (1.54) is still pending -> charged twice. Live: 8 of the 72.

Pure logic: builds SELECT text only. Read only (Monitor).
"""
from __future__ import annotations

RCCS_TYPE_TNB = "5164COMTYP"
RCCS_SOLVED = "COMPLST054"
DEST_ATTRIBUTE = "TNBDESTACC"
BILLING_TYPE_FRAUD = "TIPFAC0013"          # Fraud Off Cycle
BILLING_TYPE_OFF_CYCLE = "TIPFAC0004"      # Off cycle (what the transfer creates on the destination)
BILL_TYPE_TRANSFER = "TFGEN10027"          # bill type of that destination Off-cycle bill (RCALL261035493 / RCALL251106477)
PAYMENT_MODE_CREDIT_ALLOCATION = "TIMOPA0006"
ACTION_DEBT_TRANSFER = "ACTI700308"
BILL_EXCLUDED_STATUSES = ("ESTFAC0007", "ESTFAC0042")   # Cancelled, Rebilled

PAYMENT_MODES = {"TIMOPA0001": "Cash", "TIMOPA0002": "Cheque", "TIMOPA0003": "Manual Credit Card",
                 "TIMOPA0005": "Wire Transfer", "TIMOPA0006": "Credit Allocation", "TIMOPA0017": "EFT"}
ACTION_STATUS = {"ACTSTA0001": "Pending", "ACTSTA0002": "Solved", "ACTSTA0003": "Cancelled", "ACTSTA0004": "Re-executable"}


def build_not_transferred_query() -> str:
    b_excl = ", ".join(f"'{s}'" for s in BILL_EXCLUDED_STATUSES)
    return f"""
WITH tc AS (
  SELECT c.ID_COMPLAINT, c.COD_REF_COMPLAINT, c.ID_ACCOUNT, c.CREATE_DATE AS RCCS_DATE, c.UPDATE_DATE AS RCCS_UPDATE_DATE,
         a.ATT_VALUE AS DEST_REFERENCE
  FROM GCCC_COMPLAINT c WITH (NOLOCK)
  JOIN OUC_ADMIN.GCCC_COMPLAINT_TYPE t ON t.ID_COMPLAINT_TYPE = c.ID_COMPLAINT_TYPE AND t.COD_DEVELOP = '{RCCS_TYPE_TNB}'
  JOIN GCCC_COMPLAINT_ATT a WITH (NOLOCK) ON a.ID_COMPLAINT = c.ID_COMPLAINT AND a.COD_MASTER_ATTRIBUTE = '{DEST_ATTRIBUTE}'
  WHERE c.COD_STATUS_COMPLAINT = '{RCCS_SOLVED}')
SELECT tc.COD_REF_COMPLAINT, tc.ID_COMPLAINT, tc.RCCS_DATE, tc.RCCS_UPDATE_DATE,
       tc.ID_ACCOUNT AS SRC_ID_PAYMENT_FORM, spf.REFERENCE AS SRC_REFERENCE,
       tc.DEST_REFERENCE, dpf.ID_PAYMENT_FORM AS DEST_ID_PAYMENT_FORM,
       b.ID_BILL, b.BILL_NUMBER, b.BILLING_DATE, b.CREATE_DATE AS BILL_CREATE_DATE, b.TOTAL_AMOUNT, b.PENDING_AMOUNT,
       x.PAYMENT_MODES, x.LAST_PAYMENT_DATE,
       dt.ACTION_STATUS AS DT_ACTION_STATUS, dt.ACTION_DATE_TS AS DT_ACTION_DATE,
       CASE WHEN EXISTS (SELECT 1 FROM GCCOM_BILL d WITH (NOLOCK)
                         WHERE d.ID_PAYMENT_FORM = dpf.ID_PAYMENT_FORM AND d.BILLING_TYPE = '{BILLING_TYPE_OFF_CYCLE}'
                           AND d.BILL_TYPE = '{BILL_TYPE_TRANSFER}' AND d.TOTAL_AMOUNT = b.TOTAL_AMOUNT AND d.CREATE_DATE >= b.CREATE_DATE
                           AND d.BILLING_STATUS NOT IN ({b_excl})) THEN 1 ELSE 0 END AS DEST_OFFCYCLE_SAME_AMOUNT
FROM tc
JOIN GCCOM_BILL b WITH (NOLOCK)
     ON b.ID_PAYMENT_FORM = tc.ID_ACCOUNT AND b.BILLING_TYPE = '{BILLING_TYPE_FRAUD}' AND b.CREATE_DATE >= tc.RCCS_DATE
    AND b.BILLING_STATUS NOT IN ({b_excl}) AND b.TOTAL_AMOUNT > 0
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM spf WITH (NOLOCK) ON spf.ID_PAYMENT_FORM = tc.ID_ACCOUNT
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM dpf WITH (NOLOCK) ON dpf.REFERENCE = tc.DEST_REFERENCE
OUTER APPLY (
  SELECT MAX(CASE WHEN oc.COD_PAYMENT_MODE_TYPE = '{PAYMENT_MODE_CREDIT_ALLOCATION}' THEN 1 ELSE 0 END) AS TRANSFERRED,
         STRING_AGG(oc.COD_PAYMENT_MODE_TYPE, ',') AS PAYMENT_MODES, MAX(n.PAYMENT_DATE) AS LAST_PAYMENT_DATE
  FROM OUC_ADMIN.GCCB_COLLECTION_NOTICE_BILL n WITH (NOLOCK)
  JOIN OUC_ADMIN.GCCB_ONLINE_COLLECTION oc WITH (NOLOCK) ON oc.ID_ONLINE_COLLECTION = n.ID_ONLINE_COLLECTION
  WHERE n.ID_BILL = b.ID_BILL AND n.ACTIVE_IND = 1) x
OUTER APPLY (
  SELECT TOP 1 p.ACTION_STATUS, p.ACTION_DATE_TS FROM GCGT_WF_PACTIVITY p WITH (NOLOCK)
  WHERE p.ID_OBJECT = tc.ID_COMPLAINT AND p.COD_ACTION = '{ACTION_DEBT_TRANSFER}'
  ORDER BY p.CREATE_DATE DESC) dt
WHERE ISNULL(x.TRANSFERRED, 0) = 0
ORDER BY tc.RCCS_UPDATE_DATE DESC, b.ID_BILL
"""


def shape_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        modes = [m for m in str(r.get("payment_modes") or "").split(",") if m]
        uniq = list(dict.fromkeys(modes))
        r["payment_modes_en"] = ", ".join(PAYMENT_MODES.get(m, m) for m in uniq)
        try:
            pending = float(r.get("pending_amount") or 0)
        except ValueError:
            pending = 0.0
        # how the source TNB bill ended up - drives the page's KPI cards / filter
        r["half_done"] = str(r.get("dest_offcycle_same_amount")) == "1"
        if pending > 0:
            r["settlement"] = "pending"          # still owed on the (terminated) source account
        elif modes:
            r["settlement"] = "paid_source"      # the customer paid it on the source account (EFT / wire / ...)
        else:
            r["settlement"] = "other"
        r["dt_action_status_en"] = ACTION_STATUS.get(r.get("dt_action_status"), r.get("dt_action_status") or "")
    return rows
