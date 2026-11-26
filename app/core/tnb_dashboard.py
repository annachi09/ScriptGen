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
account (not Cancelled ESTFAC0007 / Rebilled ESTFAC0042), amount > 0, and NO active Credit Allocation (TIMOPA0006)
collection on that bill.

RJ 2026-11-25 (later) "this is the example of debt transfer 10543488322611030004 bill number, please verify if your
condition is correct" - verified, and two fixes came out of it:
  - That bill is the transfer's CREDIT on the source: a TFGEN10027 Off-cycle bill of -44.95 on account 216421,
    applied as the TIMOPA0006 Credit Allocation on TNB bill 10543488322611040004 (44.95); +44.95 (TFGEN10027, bill
    1078914019) went to destination 1087412712 - all wfLaunchAction at 2026-11-24 10:32. The "TIMOPA0006 on the
    TNB bill = transferred" check is correct.
  - Its RCCS (RCALL261103957) is type 5163COMTYP "Water - Billing of Termination not billed" - only 5164 was in
    scope. Both TNB types carry TNBDESTACC (5164: 933 RCCS, 5163: 982) -> both are in scope now.
  - Account 216421 has 4 TNB RCCS in a row, so "bill created after the RCCS" matched one bill to several RCCS.
    Now a bill belongs to the LATEST TNB RCCS (any status) opened before it, and is listed only when that RCCS is
    Solved with a destination. Checked: 216421's 4 RCCS -> exactly their own 4 bills, all transferred; a bill
    under a later unsolved RCCS (1075210146) drops out; RCALL260904564's bills billed a day after the solve stay.
  Live after the fix: 123 bills / 107 RCCS / 6,994.37 (66 Electricity, 57 Water) not transferred - 47 still
  pending on the source (3,154.56), 76 paid on the source by the customer, 19 half done (402.22); 2,127
  transferred normally. 1.7 s. RJ's example: bill 11029015172609040001 (0.45) was paid
by EFT (TIMOPA0017) on the source on 2026-09-26, the Debt Transfer action is "Solved" (2026-11-25 10:06), but
nothing reached destination 1073364216 (ID_PAYMENT_FORM 879834).
First version (Electricity only, 2026-11-25 morning): 72 bills / 3,965.90; 59 TNB bills with amount 0 had nothing
to transfer (left out).
Extra columns (same rows): how the source bill was settled (payment modes), the Debt Transfer action's status,
and whether the destination has the transfer's Off-cycle bill (TIPFAC0004 / TFGEN10027, same amount, created after
the TNB bill). When it does, the transfer is HALF done: the destination was billed but the source bill was never
credited - e.g. RCALL251106477: destination 1099587352 bill 1077073448 (1.54, 2026-10-19, already paid by the
customer) while source TNB bill 1074620757 (1.54) is still pending -> charged twice. Live: 19 of the 123.

Pure logic: builds SELECT text only. Read only (Monitor).
"""
from __future__ import annotations

RCCS_TYPES_TNB = {"5164COMTYP": "Electricity", "5163COMTYP": "Water"}   # "Billing of Termination not billed" (both carry TNBDESTACC)
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
    types = ", ".join(f"'{s}'" for s in RCCS_TYPES_TNB)
    return f"""
WITH tc AS (
  SELECT c.ID_COMPLAINT, c.COD_REF_COMPLAINT, c.ID_ACCOUNT, c.CREATE_DATE AS RCCS_DATE, c.UPDATE_DATE AS RCCS_UPDATE_DATE,
         c.COD_STATUS_COMPLAINT, a.ATT_VALUE AS DEST_REFERENCE, t.COD_DEVELOP AS RCCS_TYPE
  FROM GCCC_COMPLAINT c WITH (NOLOCK)
  JOIN OUC_ADMIN.GCCC_COMPLAINT_TYPE t ON t.ID_COMPLAINT_TYPE = c.ID_COMPLAINT_TYPE AND t.COD_DEVELOP IN ({types})
  LEFT JOIN GCCC_COMPLAINT_ATT a WITH (NOLOCK) ON a.ID_COMPLAINT = c.ID_COMPLAINT AND a.COD_MASTER_ATTRIBUTE = '{DEST_ATTRIBUTE}'
  WHERE c.ID_ACCOUNT IN (SELECT c2.ID_ACCOUNT FROM GCCC_COMPLAINT c2 WITH (NOLOCK)
                         JOIN OUC_ADMIN.GCCC_COMPLAINT_TYPE t2 ON t2.ID_COMPLAINT_TYPE = c2.ID_COMPLAINT_TYPE AND t2.COD_DEVELOP IN ({types})
                         JOIN GCCC_COMPLAINT_ATT a2 WITH (NOLOCK) ON a2.ID_COMPLAINT = c2.ID_COMPLAINT AND a2.COD_MASTER_ATTRIBUTE = '{DEST_ATTRIBUTE}'
                         WHERE c2.COD_STATUS_COMPLAINT = '{RCCS_SOLVED}')),
-- RJ 2026-11-25 (verified on bill 10543488322611030004 / account 216421, 4 TNB RCCS in a row): a TNB bill belongs to
-- the LATEST TNB RCCS (any status) opened before it; it is only listed when that RCCS is Solved with a destination.
tb AS (
  SELECT tc.*, b.ID_BILL, b.BILL_NUMBER, b.BILLING_DATE, b.CREATE_DATE AS BILL_CREATE_DATE, b.TOTAL_AMOUNT, b.PENDING_AMOUNT,
         ROW_NUMBER() OVER (PARTITION BY b.ID_BILL ORDER BY tc.RCCS_DATE DESC, tc.ID_COMPLAINT DESC) AS RN
  FROM tc
  JOIN GCCOM_BILL b WITH (NOLOCK)
       ON b.ID_PAYMENT_FORM = tc.ID_ACCOUNT AND b.BILLING_TYPE = '{BILLING_TYPE_FRAUD}' AND b.CREATE_DATE >= tc.RCCS_DATE
      AND b.BILLING_STATUS NOT IN ({b_excl}) AND b.TOTAL_AMOUNT > 0)
SELECT tc.COD_REF_COMPLAINT, tc.RCCS_TYPE, tc.ID_COMPLAINT, tc.RCCS_DATE, tc.RCCS_UPDATE_DATE,
       tc.ID_ACCOUNT AS SRC_ID_PAYMENT_FORM, spf.REFERENCE AS SRC_REFERENCE,
       tc.DEST_REFERENCE, dpf.ID_PAYMENT_FORM AS DEST_ID_PAYMENT_FORM,
       tc.ID_BILL, tc.BILL_NUMBER, tc.BILLING_DATE, tc.BILL_CREATE_DATE, tc.TOTAL_AMOUNT, tc.PENDING_AMOUNT,
       x.PAYMENT_MODES, x.LAST_PAYMENT_DATE,
       dt.ACTION_STATUS AS DT_ACTION_STATUS, dt.ACTION_DATE_TS AS DT_ACTION_DATE,
       CASE WHEN EXISTS (SELECT 1 FROM GCCOM_BILL d WITH (NOLOCK)
                         WHERE d.ID_PAYMENT_FORM = dpf.ID_PAYMENT_FORM AND d.BILLING_TYPE = '{BILLING_TYPE_OFF_CYCLE}'
                           AND d.BILL_TYPE = '{BILL_TYPE_TRANSFER}' AND d.TOTAL_AMOUNT = tc.TOTAL_AMOUNT AND d.CREATE_DATE >= tc.BILL_CREATE_DATE
                           AND d.BILLING_STATUS NOT IN ({b_excl})) THEN 1 ELSE 0 END AS DEST_OFFCYCLE_SAME_AMOUNT
FROM tb tc
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM spf WITH (NOLOCK) ON spf.ID_PAYMENT_FORM = tc.ID_ACCOUNT
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM dpf WITH (NOLOCK) ON dpf.REFERENCE = tc.DEST_REFERENCE
OUTER APPLY (
  SELECT MAX(CASE WHEN oc.COD_PAYMENT_MODE_TYPE = '{PAYMENT_MODE_CREDIT_ALLOCATION}' THEN 1 ELSE 0 END) AS TRANSFERRED,
         STRING_AGG(oc.COD_PAYMENT_MODE_TYPE, ',') AS PAYMENT_MODES, MAX(n.PAYMENT_DATE) AS LAST_PAYMENT_DATE
  FROM OUC_ADMIN.GCCB_COLLECTION_NOTICE_BILL n WITH (NOLOCK)
  JOIN OUC_ADMIN.GCCB_ONLINE_COLLECTION oc WITH (NOLOCK) ON oc.ID_ONLINE_COLLECTION = n.ID_ONLINE_COLLECTION
  WHERE n.ID_BILL = tc.ID_BILL AND n.ACTIVE_IND = 1) x
OUTER APPLY (
  SELECT TOP 1 p.ACTION_STATUS, p.ACTION_DATE_TS FROM GCGT_WF_PACTIVITY p WITH (NOLOCK)
  WHERE p.ID_OBJECT = tc.ID_COMPLAINT AND p.COD_ACTION = '{ACTION_DEBT_TRANSFER}'
  ORDER BY p.CREATE_DATE DESC) dt
WHERE tc.RN = 1 AND tc.COD_STATUS_COMPLAINT = '{RCCS_SOLVED}' AND tc.DEST_REFERENCE IS NOT NULL
  AND ISNULL(x.TRANSFERRED, 0) = 0
ORDER BY tc.RCCS_UPDATE_DATE DESC, tc.ID_BILL
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
        r["rccs_type_en"] = RCCS_TYPES_TNB.get(r.get("rccs_type"), r.get("rccs_type") or "")
        r["dt_action_status_en"] = ACTION_STATUS.get(r.get("dt_action_status"), r.get("dt_action_status") or "")
    return rows


# ---------------------------------------------------------------------------------------------------------------
# Tab 2 - "TNB on cancelled contract" (RJ 2026-11-25): "create 2nd tab, TNB on Cancelled contract, so you check the
# RCCS id payment form and id offered service, here you will show if there is an RCCS TNB where its linked to a
# cancelled contract. show the status of the TNB".
# Link (RJ's rule): GCCC_COMPLAINT.ID_ACCOUNT = GCCOM_CONTRACTED_SERVICE.ID_PAYMENT_FORM and the offered service of
# the TNB type - GCCC_COMPLAINT has no offered-service column, so 5164COMTYP (Electricity) -> ID_OFFERED_SERVICE 1,
# 5163COMTYP (Water) -> 19. Listed: every TNB RCCS (any status) with at least one Cancelled (ESTSC00005) contract
# on that payment form + service. "Status of the TNB" = COD_STATUS_COMPLAINT, which for TNB IS the workflow step
# (COMPLST155 Billing of TNB Usage, 142 Assignation, 157 Validate Owner Account, 158 Debt Transfer, 054 Solved,
# 001 Canceled, 061 Rejected, 002 In Resolution).
# Live 2026-11-25: 87 RCCS whose ONLY matching contracts are Cancelled (70 stuck at Billing of TNB Usage, 5 Solved -
# billed on the cancelled contract, 6 Canceled, 3 Rejected, 2 Assignation, 1 In Resolution) + 115 where a Cancelled
# contract sits next to an Active / Terminated one (no TNB bill on the cancelled one there - shown with its own KPI
# so they can be told apart). Headline (page + Overview card) = the 87 "cancelled contract only". Extra columns: the cancelled contract(s) with dates / drop reason, the other contracts
# on the same service, and the latest TNB (TIPFAC0013) bill since the RCCS with the contract it was issued on.
# ---------------------------------------------------------------------------------------------------------------
OFFERED_BY_TNB_TYPE = {"5164COMTYP": 1, "5163COMTYP": 19}
CS_CANCELLED = "ESTSC00005"
RCCS_STATUS_NAMES = {"COMPLST155": "Billing of TNB Usage", "COMPLST142": "Assignation", "COMPLST157": "Validate Owner Account",
                     "COMPLST158": "Debt Transfer (TNB billing)", "COMPLST054": "Solved", "COMPLST001": "Canceled",
                     "COMPLST061": "Rejected", "COMPLST002": "In Resolution"}
RCCS_STATUS_GROUP = {"COMPLST054": "solved", "COMPLST001": "closed", "COMPLST061": "closed"}   # everything else = open
CS_STATUS_NAMES = {"ESTSC00001": "Pending", "ESTSC00002": "Active", "ESTSC00003": "Pending termination",
                   "ESTSC00004": "Terminated", "ESTSC00005": "Cancelled", "ESTSC00007": "Suspended"}


def build_cancelled_contract_query() -> str:
    types = ", ".join(f"'{k}'" for k in OFFERED_BY_TNB_TYPE)
    os_case = "CASE t.COD_DEVELOP " + " ".join(f"WHEN '{k}' THEN {v}" for k, v in OFFERED_BY_TNB_TYPE.items()) + " END"
    b_excl = ", ".join(f"'{s}'" for s in BILL_EXCLUDED_STATUSES)
    return f"""
WITH r AS (
  SELECT c.ID_COMPLAINT, c.COD_REF_COMPLAINT, c.ID_ACCOUNT, c.COD_STATUS_COMPLAINT, c.CREATE_DATE AS RCCS_DATE,
         c.UPDATE_DATE AS RCCS_UPDATE_DATE, c.MOTIVE, t.COD_DEVELOP AS RCCS_TYPE, {os_case} AS ID_OFFERED_SERVICE
  FROM GCCC_COMPLAINT c WITH (NOLOCK)
  JOIN OUC_ADMIN.GCCC_COMPLAINT_TYPE t ON t.ID_COMPLAINT_TYPE = c.ID_COMPLAINT_TYPE AND t.COD_DEVELOP IN ({types})),
k AS (
  SELECT r.ID_COMPLAINT,
         SUM(CASE WHEN cs.STATUS = '{CS_CANCELLED}' THEN 1 ELSE 0 END) AS N_CANCELLED, COUNT(*) AS N_CONTRACTS,
         STRING_AGG(CASE WHEN cs.STATUS = '{CS_CANCELLED}' THEN CAST(cs.ID_CONTRACTED_SERVICE AS varchar(20)) + '|'
                    + ISNULL(CONVERT(varchar(10), cs.FROM_DATE, 120), '') + '|' + ISNULL(CONVERT(varchar(10), cs.DROP_DATE, 120), '')
                    + '|' + ISNULL(CONVERT(varchar(10), cs.UPDATE_DATE, 120), '') + '|' + ISNULL(cs.DROP_REASON, '') END, ';') AS CANCELLED_CS,
         STRING_AGG(CASE WHEN cs.STATUS <> '{CS_CANCELLED}' THEN CAST(cs.ID_CONTRACTED_SERVICE AS varchar(20)) + '|' + cs.STATUS END, ';') AS OTHER_CS
  FROM r JOIN GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
       ON cs.ID_PAYMENT_FORM = r.ID_ACCOUNT AND cs.ID_OFFERED_SERVICE = r.ID_OFFERED_SERVICE
  GROUP BY r.ID_COMPLAINT
  HAVING SUM(CASE WHEN cs.STATUS = '{CS_CANCELLED}' THEN 1 ELSE 0 END) > 0)
SELECT r.COD_REF_COMPLAINT, r.ID_COMPLAINT, r.RCCS_TYPE, r.ID_OFFERED_SERVICE, r.COD_STATUS_COMPLAINT, r.RCCS_DATE,
       r.RCCS_UPDATE_DATE, r.MOTIVE, r.ID_ACCOUNT AS ID_PAYMENT_FORM, pf.REFERENCE,
       k.N_CANCELLED, k.N_CONTRACTS, k.CANCELLED_CS, k.OTHER_CS,
       tb.ID_BILL AS TNB_ID_BILL, tb.BILL_NUMBER AS TNB_BILL_NUMBER, tb.TOTAL_AMOUNT AS TNB_AMOUNT, tb.PENDING_AMOUNT AS TNB_PENDING,
       tb.ID_CONTRACTED_SERVICE AS TNB_BILL_CS, tb.CS_STATUS AS TNB_BILL_CS_STATUS
FROM k
JOIN r ON r.ID_COMPLAINT = k.ID_COMPLAINT
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = r.ID_ACCOUNT
OUTER APPLY (
  SELECT TOP 1 b.ID_BILL, b.BILL_NUMBER, b.TOTAL_AMOUNT, b.PENDING_AMOUNT, b.ID_CONTRACTED_SERVICE, bcs.STATUS AS CS_STATUS
  FROM GCCOM_BILL b WITH (NOLOCK)
  JOIN GCCOM_CONTRACTED_SERVICE bcs WITH (NOLOCK) ON bcs.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE
  WHERE b.ID_PAYMENT_FORM = r.ID_ACCOUNT AND b.BILLING_TYPE = '{BILLING_TYPE_FRAUD}' AND b.CREATE_DATE >= r.RCCS_DATE
    AND bcs.ID_OFFERED_SERVICE = r.ID_OFFERED_SERVICE AND b.BILLING_STATUS NOT IN ({b_excl})
  ORDER BY b.CREATE_DATE) tb
ORDER BY CASE WHEN k.N_CANCELLED = k.N_CONTRACTS THEN 0 ELSE 1 END, r.RCCS_DATE DESC
"""


def shape_cancelled_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["rccs_type_en"] = RCCS_TYPES_TNB.get(r.get("rccs_type"), r.get("rccs_type") or "")
        st = r.get("cod_status_complaint") or ""
        r["status_en"] = RCCS_STATUS_NAMES.get(st, st)
        r["status_group"] = RCCS_STATUS_GROUP.get(st, "open")
        r["only_cancelled"] = str(r.get("n_cancelled")) == str(r.get("n_contracts"))
        canc = []
        for part in str(r.get("cancelled_cs") or "").split(";"):
            if not part:
                continue
            f = (part.split("|") + ["", "", "", "", ""])[:5]
            canc.append({"id": f[0], "from": f[1], "drop": f[2], "updated": f[3], "reason": f[4]})
        r["cancelled_list"] = canc
        others = []
        for part in str(r.get("other_cs") or "").split(";"):
            if part:
                cid, _, code = part.partition("|")
                others.append({"id": cid, "status": code, "status_en": CS_STATUS_NAMES.get(code, code)})
        r["other_list"] = others
        r["tnb_bill_cs_status_en"] = CS_STATUS_NAMES.get(r.get("tnb_bill_cs_status"), r.get("tnb_bill_cs_status") or "")
        r["tnb_on_cancelled"] = r.get("tnb_bill_cs_status") == CS_CANCELLED
    return rows
