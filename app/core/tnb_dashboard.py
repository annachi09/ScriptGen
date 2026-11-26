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

from .search_inputs import sql_list

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


# ---------------------------------------------------------------------------------------------------------------
# RJ 2026-11-26 rework: "for the 1st tab, if the source and destination is the same, remove it form the list as it is
# ok. also the case with half done = true, looks like they are ok and transferred can you check RCALL260121978".
# Checked: the Debt Transfer moves the account's pending balance in PAIRS created at the same moment - a negative
# TFGEN10027 Off-cycle bill on the source and +the same amount (TFGEN10027) on the receiving account, right after it
# (next ID_BILLs). RCALL260121978 (2026-08-18 13:08): -457.08 / +457.08 and -5.58 / +5.58 to 1079637029 - the debt
# WAS moved; TNB bill 1073566945 (5.58) only looks pending because the source credit was allocated to older debt.
# So a TNB bill counts as transferred when ANY of:
#   - it has a Credit Allocation (TIMOPA0006) collection, or
#   - its RCCS's Debt Transfer (ACTI700308, +-10 min) created such a pair to another account, or
#   - the destination has a TFGEN10027 of the same amount after it (the old "half done" - now treated as done).
# Source = destination (SRC_REFERENCE = TNBDESTACC) is OK and left out.
# Live 2026-11-26: 2,351 TNB bills of solved RCCS with a destination -> 95 same account, 2,193 credit-allocated,
# 25 transferred at the RCCS's Debt Transfer, 38 NOT transferred (35 RCCS, 3,538.35) = this case.
# Built as one batch with temp tables (one staging step shared with the wrong-destination case): the old single
# SELECT took 54-120 s for the pair lookups; staged it is ~10-14 s.
# ---------------------------------------------------------------------------------------------------------------
def _stage_sql(inputs: list[str] | None = None) -> str:
    """Temp tables #tc (TNB RCCS), #tb (TNB bills -> their RCCS), #dt (Debt Transfer action), #ca (collections on
    the TNB bills), #px (every transfer credit on those accounts + the account its counterpart landed on).
    inputs (RJ 2026-11-26 Search screen): RCCS codes, account references (source or destination) or bill numbers -
    keeps the whole account(s) they belong to, so the transfer-pair logic stays the same."""
    L = sql_list(inputs) if inputs else ""
    search = f"""
-- Search: only the accounts of the RCCS / accounts / bills entered
DELETE FROM #tc WHERE ID_ACCOUNT NOT IN (
    SELECT ID_ACCOUNT FROM #tc WHERE COD_REF_COMPLAINT IN ({L}) OR DEST_REFERENCE IN ({L})
    UNION SELECT ID_PAYMENT_FORM FROM GCCOM_PAYMENT_FORM WITH (NOLOCK) WHERE REFERENCE IN ({L})
    UNION SELECT ID_PAYMENT_FORM FROM GCCOM_BILL WITH (NOLOCK) WHERE BILL_NUMBER IN ({L}));
""" if inputs else ""
    return """
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#tc') IS NOT NULL DROP TABLE #tc;
IF OBJECT_ID('tempdb..#tb') IS NOT NULL DROP TABLE #tb;
IF OBJECT_ID('tempdb..#ca') IS NOT NULL DROP TABLE #ca;
IF OBJECT_ID('tempdb..#px') IS NOT NULL DROP TABLE #px;
IF OBJECT_ID('tempdb..#dt') IS NOT NULL DROP TABLE #dt;

-- every TNB RCCS (any status) of the accounts that have a Solved TNB RCCS with a destination
SELECT c.ID_COMPLAINT, c.COD_REF_COMPLAINT, c.ID_ACCOUNT, c.CREATE_DATE AS RCCS_DATE, c.UPDATE_DATE AS RCCS_UPDATE_DATE,
       c.COD_STATUS_COMPLAINT, a.ATT_VALUE AS DEST_REFERENCE, t.COD_DEVELOP AS RCCS_TYPE
INTO #tc
FROM GCCC_COMPLAINT c WITH (NOLOCK)
JOIN OUC_ADMIN.GCCC_COMPLAINT_TYPE t ON t.ID_COMPLAINT_TYPE = c.ID_COMPLAINT_TYPE AND t.COD_DEVELOP IN ('5164COMTYP', '5163COMTYP')
LEFT JOIN GCCC_COMPLAINT_ATT a WITH (NOLOCK) ON a.ID_COMPLAINT = c.ID_COMPLAINT AND a.COD_MASTER_ATTRIBUTE = 'TNBDESTACC'
WHERE c.ID_ACCOUNT IN (SELECT c2.ID_ACCOUNT FROM GCCC_COMPLAINT c2 WITH (NOLOCK)
                       JOIN OUC_ADMIN.GCCC_COMPLAINT_TYPE t2 ON t2.ID_COMPLAINT_TYPE = c2.ID_COMPLAINT_TYPE AND t2.COD_DEVELOP IN ('5164COMTYP', '5163COMTYP')
                       JOIN GCCC_COMPLAINT_ATT a2 WITH (NOLOCK) ON a2.ID_COMPLAINT = c2.ID_COMPLAINT AND a2.COD_MASTER_ATTRIBUTE = 'TNBDESTACC'
                       WHERE c2.COD_STATUS_COMPLAINT = 'COMPLST054');
""" + search + """
-- TNB bills -> the latest TNB RCCS opened before them; kept when that RCCS is Solved with a destination
SELECT x.* INTO #tb FROM (
  SELECT tc.*, b.ID_BILL, b.BILL_NUMBER, b.BILLING_DATE, b.CREATE_DATE AS BILL_CREATE_DATE, b.TOTAL_AMOUNT, b.PENDING_AMOUNT,
         ROW_NUMBER() OVER (PARTITION BY b.ID_BILL ORDER BY tc.RCCS_DATE DESC, tc.ID_COMPLAINT DESC) AS RN
  FROM #tc tc
  JOIN GCCOM_BILL b WITH (NOLOCK) ON b.ID_PAYMENT_FORM = tc.ID_ACCOUNT AND b.BILLING_TYPE = 'TIPFAC0013' AND b.CREATE_DATE >= tc.RCCS_DATE
   AND b.BILLING_STATUS NOT IN ('ESTFAC0007', 'ESTFAC0042') AND b.TOTAL_AMOUNT > 0) x
WHERE x.RN = 1 AND x.COD_STATUS_COMPLAINT = 'COMPLST054' AND x.DEST_REFERENCE IS NOT NULL;

-- the Debt Transfer action of those RCCS
SELECT d.ID_COMPLAINT, p.ACTION_STATUS AS DT_ACTION_STATUS, p.ACTION_DATE_TS AS DT_AT INTO #dt
FROM (SELECT DISTINCT ID_COMPLAINT FROM #tb) d
OUTER APPLY (SELECT TOP 1 p.ACTION_STATUS, p.ACTION_DATE_TS FROM GCGT_WF_PACTIVITY p WITH (NOLOCK)
             WHERE p.ID_OBJECT = d.ID_COMPLAINT AND p.COD_ACTION = 'ACTI700308' ORDER BY p.CREATE_DATE DESC) p;

-- active collections on the TNB bills
SELECT tb.ID_BILL, oc.COD_PAYMENT_MODE_TYPE, oc.CREATE_DATE AS COLL_AT, n.PAYMENT_DATE, n.COLLECTION_AMOUNT INTO #ca
FROM #tb tb
JOIN OUC_ADMIN.GCCB_COLLECTION_NOTICE_BILL n WITH (NOLOCK) ON n.ID_BILL = tb.ID_BILL AND n.ACTIVE_IND = 1
JOIN OUC_ADMIN.GCCB_ONLINE_COLLECTION oc WITH (NOLOCK) ON oc.ID_ONLINE_COLLECTION = n.ID_ONLINE_COLLECTION;

-- every transfer credit (negative TFGEN10027) on those accounts + where its counterpart (+same amount, created right
-- after it -> next ID_BILLs) landed
SELECT s.ID_PAYMENT_FORM AS ID_ACCOUNT, s.ID_BILL AS CR_BILL, s.TOTAL_AMOUNT AS CR_AMT, s.CREATE_DATE AS CR_AT,
       d.ID_BILL AS TO_BILL, d.ID_PAYMENT_FORM AS TO_PF INTO #px
FROM (SELECT DISTINCT ID_ACCOUNT FROM #tb) acc
JOIN GCCOM_BILL s WITH (NOLOCK) ON s.ID_PAYMENT_FORM = acc.ID_ACCOUNT AND s.BILL_TYPE = 'TFGEN10027' AND s.TOTAL_AMOUNT < 0
OUTER APPLY (SELECT TOP 1 d.ID_BILL, d.ID_PAYMENT_FORM FROM GCCOM_BILL d WITH (NOLOCK)
             WHERE d.ID_BILL > s.ID_BILL AND d.ID_BILL <= s.ID_BILL + 50 AND d.BILL_TYPE = 'TFGEN10027'
               AND d.TOTAL_AMOUNT = -s.TOTAL_AMOUNT AND d.ID_PAYMENT_FORM <> s.ID_PAYMENT_FORM ORDER BY d.ID_BILL) d;
"""


def build_not_transferred_query(inputs: list[str] | None = None) -> str:
    return _stage_sql(inputs) + """
SELECT tb.COD_REF_COMPLAINT, tb.RCCS_TYPE, tb.ID_COMPLAINT, tb.RCCS_DATE, tb.RCCS_UPDATE_DATE,
       tb.ID_ACCOUNT AS SRC_ID_PAYMENT_FORM, spf.REFERENCE AS SRC_REFERENCE, tb.DEST_REFERENCE, dpf.ID_PAYMENT_FORM AS DEST_ID_PAYMENT_FORM,
       tb.ID_BILL, tb.BILL_NUMBER, tb.BILLING_DATE, tb.BILL_CREATE_DATE, tb.TOTAL_AMOUNT, tb.PENDING_AMOUNT,
       x.PAYMENT_MODES, x.LAST_PAYMENT_DATE, dt.DT_ACTION_STATUS, dt.DT_AT AS DT_ACTION_DATE,
       ISNULL(x.TRANSFERRED, 0) AS BILL_CREDIT_ALLOCATED, ISNULL(rt.N, 0) AS RCCS_TRANSFER_PAIRS,
       CASE WHEN EXISTS (SELECT 1 FROM GCCOM_BILL d WITH (NOLOCK) WHERE d.ID_PAYMENT_FORM = dpf.ID_PAYMENT_FORM AND d.BILL_TYPE = 'TFGEN10027'
                         AND d.TOTAL_AMOUNT = tb.TOTAL_AMOUNT AND d.CREATE_DATE >= tb.BILL_CREATE_DATE
                         AND d.BILLING_STATUS NOT IN ('ESTFAC0007', 'ESTFAC0042')) THEN 1 ELSE 0 END AS DEST_SAME_AMOUNT,
       CASE WHEN spf.REFERENCE = tb.DEST_REFERENCE THEN 1 ELSE 0 END AS SAME_ACCOUNT
INTO #c1
FROM #tb tb
LEFT JOIN #dt dt ON dt.ID_COMPLAINT = tb.ID_COMPLAINT
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM spf WITH (NOLOCK) ON spf.ID_PAYMENT_FORM = tb.ID_ACCOUNT
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM dpf WITH (NOLOCK) ON dpf.REFERENCE = tb.DEST_REFERENCE
OUTER APPLY (SELECT MAX(CASE WHEN c.COD_PAYMENT_MODE_TYPE = 'TIMOPA0006' THEN 1 ELSE 0 END) AS TRANSFERRED,
                    STRING_AGG(c.COD_PAYMENT_MODE_TYPE, ',') AS PAYMENT_MODES, MAX(c.PAYMENT_DATE) AS LAST_PAYMENT_DATE
             FROM #ca c WHERE c.ID_BILL = tb.ID_BILL) x
OUTER APPLY (SELECT COUNT(*) AS N FROM #px p WHERE p.ID_ACCOUNT = tb.ID_ACCOUNT AND p.TO_PF IS NOT NULL
               AND p.CR_AT BETWEEN DATEADD(MINUTE, -10, dt.DT_AT) AND DATEADD(MINUTE, 10, dt.DT_AT)) rt;

SELECT COD_REF_COMPLAINT, RCCS_TYPE, ID_COMPLAINT, RCCS_DATE, RCCS_UPDATE_DATE, SRC_ID_PAYMENT_FORM, SRC_REFERENCE,
       DEST_REFERENCE, DEST_ID_PAYMENT_FORM, ID_BILL, BILL_NUMBER, BILLING_DATE, BILL_CREATE_DATE, TOTAL_AMOUNT, PENDING_AMOUNT,
       PAYMENT_MODES, LAST_PAYMENT_DATE, DT_ACTION_STATUS, DT_ACTION_DATE, DEST_SAME_AMOUNT AS DEST_OFFCYCLE_SAME_AMOUNT
FROM #c1
WHERE SAME_ACCOUNT = 0 AND BILL_CREDIT_ALLOCATED = 0 AND RCCS_TRANSFER_PAIRS = 0 AND DEST_SAME_AMOUNT = 0
ORDER BY RCCS_UPDATE_DATE DESC, ID_BILL;
"""


# ---------------------------------------------------------------------------------------------------------------
# Tab "Transferred to wrong account" (RJ 2026-11-26): "TNB billed and Transferred but not on the selected account,
# so this are cases where we billed and transferred the tnb bill, but the account where it debt transferred is not
# correct selected destination account (the one in RCCS ATT)".
# A TNB bill settled by Credit Allocation; its transfer pair (credit on the source within +-10 min of that
# collection -> +same amount on the next ID_BILLs) landed on an account whose REFERENCE is not the RCCS's TNBDESTACC.
# One row per bill (several pairs of one transfer are aggregated). Live 2026-11-26: 9 bills. Two kinds: the money
# went to ANOTHER TNB RCCS's destination on the same account (e.g. RCALL251107004: selected 1092638251, sent to
# 1080187808 = RCALL251107002's destination), or to an account no TNB RCCS selected (RCALL251105900: 613.5 sent to
# 1052122015 on 2026-02-18; 1064631219 was only selected on 2026-08-17). In all of them the destination was
# selected AFTER the transfer - shown per row.
# ---------------------------------------------------------------------------------------------------------------
def build_wrong_destination_query(inputs: list[str] | None = None) -> str:
    return _stage_sql(inputs) + """
SELECT tb.ID_BILL, MIN(c.COLL_AT) AS TRANSFER_AT, SUM(DISTINCT c.COLLECTION_AMOUNT) AS ALLOCATED_AMOUNT,
       STRING_AGG(CAST(p.CR_BILL AS varchar(20)) + ':' + CAST(-p.CR_AMT AS varchar(30)) + ':' + CAST(p.TO_BILL AS varchar(20))
                  + ':' + ISNULL(apf.REFERENCE, ''), ';') AS PAIRS
INTO #w
FROM #tb tb
JOIN #ca c ON c.ID_BILL = tb.ID_BILL AND c.COD_PAYMENT_MODE_TYPE = 'TIMOPA0006'
JOIN #px p ON p.ID_ACCOUNT = tb.ID_ACCOUNT AND p.TO_PF IS NOT NULL
          AND p.CR_AT BETWEEN DATEADD(MINUTE, -10, c.COLL_AT) AND DATEADD(MINUTE, 10, c.COLL_AT)
JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM apf WITH (NOLOCK) ON apf.ID_PAYMENT_FORM = p.TO_PF
WHERE apf.REFERENCE <> tb.DEST_REFERENCE
GROUP BY tb.ID_BILL;

SELECT tb.COD_REF_COMPLAINT, tb.RCCS_TYPE, tb.ID_COMPLAINT, tb.RCCS_DATE, tb.RCCS_UPDATE_DATE,
       tb.ID_ACCOUNT AS SRC_ID_PAYMENT_FORM, spf.REFERENCE AS SRC_REFERENCE,
       tb.DEST_REFERENCE, dpf.ID_PAYMENT_FORM AS DEST_ID_PAYMENT_FORM, att.CREATE_DATE AS DEST_SELECTED_AT,
       tb.ID_BILL, tb.BILL_NUMBER, tb.BILL_CREATE_DATE, tb.TOTAL_AMOUNT, tb.PENDING_AMOUNT,
       w.TRANSFER_AT, w.ALLOCATED_AMOUNT, w.PAIRS,
       (SELECT TOP 1 t2.COD_REF_COMPLAINT FROM #tc t2 WHERE t2.ID_ACCOUNT = tb.ID_ACCOUNT AND t2.ID_COMPLAINT <> tb.ID_COMPLAINT
          AND w.PAIRS LIKE '%:' + t2.DEST_REFERENCE + '%' ORDER BY t2.RCCS_DATE DESC) AS ACTUAL_VIA_RCCS
FROM #w w
JOIN #tb tb ON tb.ID_BILL = w.ID_BILL
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM spf WITH (NOLOCK) ON spf.ID_PAYMENT_FORM = tb.ID_ACCOUNT
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM dpf WITH (NOLOCK) ON dpf.REFERENCE = tb.DEST_REFERENCE
LEFT JOIN GCCC_COMPLAINT_ATT att WITH (NOLOCK) ON att.ID_COMPLAINT = tb.ID_COMPLAINT AND att.COD_MASTER_ATTRIBUTE = 'TNBDESTACC'
ORDER BY w.TRANSFER_AT DESC, tb.ID_BILL;
"""


def shape_wrong_destination_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["rccs_type_en"] = RCCS_TYPES_TNB.get(r.get("rccs_type"), r.get("rccs_type") or "")
        pairs, actual = [], []
        for part in str(r.get("pairs") or "").split(";"):
            f = part.split(":")
            if len(f) >= 4:
                pairs.append({"credit_bill": f[0], "amount": f[1], "to_bill": f[2], "to_reference": f[3]})
                if f[3] and f[3] not in actual:
                    actual.append(f[3])
        r["pair_list"] = pairs
        r["actual_references"] = actual
        r["transferred_amount"] = round(sum(float(p["amount"] or 0) for p in pairs), 3)
        r["kind"] = "other_rccs" if r.get("actual_via_rccs") else "unselected"
        sel, at = str(r.get("dest_selected_at") or ""), str(r.get("transfer_at") or "")
        r["selected_after_transfer"] = bool(sel and at and sel > at)
    return rows


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
        r["proposed_dest"] = None   # RJ 2026-11-26: "proposed by the system - i will give you the query later"
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
