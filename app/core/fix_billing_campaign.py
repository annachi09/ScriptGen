"""
FIX BILLING CAMPAIGN 2026 (RJ, 2026-10-18).

RJ: "create new menu FIX BILLING CAMPAIGN 2026: Here will be a dashboard for
customers EWA_DDI_ELIGIBILITY, connect it with the wallet
EWA_GCCOM_SUBSIDY_WALLET using payment form, I need dashboard with good details,
show me the credit amount for each customer and the cod behavior in description,
also find the linked RCCS, and request using SELECT * FROM
GCGE_EXTERNAL_MESSAGES_IN WHERE REQUESTURL = '/mobileapp/complaint' AND
CREATE_DATE > '2026-10-16'; then from complain attribute
(GCCC_COMPLAINT.COD_REF_COMPLAINT) show me 4 attributes, CREDITAMT, NOBANK AMT,
creditUSage, deduction amount".

Live schema (checked 2026-10-18):
- OUC_COMMON_ADMIN.EWA_DDI_ELIGIBILITY: one row per id_payment_form (153,236).
- OUC_COMMON_ADMIN.EWA_GCCOM_SUBSIDY_WALLET: ID_WALLET, ID_PAYMENT_FORM, IND_ACTIVE,
  BALANCE, COD_BEHAVIOUR (10,076 rows, one per payment form, all active).
  COD_BEHAVIOUR description = GCGT_ATTRIBUTE_VALUES of master attribute RCCSCRDUSG
  (Credit Usage): RCCSCRDU01 Freeze, RCCSCRDU02 Monthly Release,
  RCCSCRDU03 Oustanding + Monthly Release.
- OUC_COMMON_ADMIN.GCGE_EXTERNAL_MESSAGES_IN, REQUESTURL '/mobileapp/complaint':
  REQUESTENTITY is JSON {idAccount (= account reference), complaintType,
  rccsReference, creditAmount, noBankAmount, creditUsage, deductionAmount,
  amountType, deductionDay, ...}; RESPONSEENTITY = the RCCS reference;
  REPONSESTATUS (live: all STMESS0003). Personal fields in the JSON (cpr, IBAN,
  phone) are NOT extracted.
- OUC_ADMIN.GCCC_COMPLAINT.COD_REF_COMPLAINT = RCCS reference;
  OUC_ADMIN.GCCC_COMPLAINT_ATT (ID_COMPLAINT, COD_MASTER_ATTRIBUTE, ATT_VALUE):
  RCCSCRDAMT Credit Amount, RCCSNOBAMT No Bank Amount, RCCSCRDUSG Credit Usage,
  FIXAMTYP Fixed Amount (= the request's deductionAmount, e.g. 9.0 -> 9.000000).

RJ 2026-10-21: one row per REQUEST account (latest /mobileapp/complaint request per
idAccount since `since`, with a creditAmount). Payment form via
GCCOM_PAYMENT_FORM.REFERENCE; EWA_DDI_ELIGIBILITY is only extra info (IN_EWA + EWA_*
columns), no longer the base.
Pure logic: builds SELECT text only.
"""
from __future__ import annotations

import re

DDI_TABLE = "OUC_COMMON_ADMIN.EWA_DDI_ELIGIBILITY"
WALLET_TABLE = "OUC_COMMON_ADMIN.EWA_GCCOM_SUBSIDY_WALLET"
MESSAGES_TABLE = "OUC_COMMON_ADMIN.GCGE_EXTERNAL_MESSAGES_IN"
REQUEST_URL = "/mobileapp/complaint"
DEFAULT_SINCE = "2026-10-16"
BEHAVIOUR_MASTER = "RCCSCRDUSG"
ATTRS = {"RCCSCRDAMT": "Credit Amount", "RCCSNOBAMT": "No Bank Amount",
         "RCCSCRDUSG": "Credit Usage", "FIXAMTYP": "Deduction (Fixed) Amount"}
# RJ 2026-10-21: "let's not fully rely on EWA_DDI_ELIGIBILITY, should be mostly from
# external messages_in" - the requests (GCGE_EXTERNAL_MESSAGES_IN, latest per account)
# are now the base; EWA_DDI_ELIGIBILITY is optional extra info (LEFT JOIN) and the
# payment form comes from GCCOM_PAYMENT_FORM.REFERENCE = the request's idAccount.
SCOPES = ("all", "in_ewa", "not_in_ewa", "with_wallet", "no_wallet")
EWA_COLUMNS = ("outstanding_as_of_now", "bill_amount_may", "bill_amount_jun", "bill_amount_jul",
               "bill_amount_aug", "bill_amount_set", "bill_amount_oct", "debt_as_of_cutoff_date",
               "credit_as_of_cutoff_date", "payment_method", "calculated_ddi", "credit", "ind_processed")
ROW_CAP = 0   # RJ 2026-10-21: no row cap any more (was 60,000); 0 = unlimited
AMOUNT_TOLERANCE = 0.001


def _since(value: str | None) -> str:
    v = (value or DEFAULT_SINCE).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise ValueError("since must be YYYY-MM-DD")
    return v


def build_query(scope: str = "all", since: str | None = None) -> str:
    scope = scope if scope in SCOPES else "all"
    since = _since(since)
    # RJ 2026-10-18: "exclude all where in the request, there is no CreditAmount" -
    # only requests with a creditAmount (null / empty excluded; an explicit 0 stays).
    has_credit = "NULLIF(LTRIM(RTRIM(m.CREDIT_AMOUNT)), '') IS NOT NULL"
    extra = {
        "all": "",
        "in_ewa": " AND e.id_payment_form IS NOT NULL",
        "not_in_ewa": " AND e.id_payment_form IS NULL",
        "with_wallet": " AND w.ID_PAYMENT_FORM IS NOT NULL",
        "no_wallet": " AND w.ID_PAYMENT_FORM IS NULL",
    }[scope]
    where = f"WHERE {has_credit}{extra}"
    ewa_cols = ",\n       ".join(f"e.{c} AS EWA_{c.upper()}" for c in EWA_COLUMNS)
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#m') IS NOT NULL DROP TABLE #m;
IF OBJECT_ID('tempdb..#c') IS NOT NULL DROP TABLE #c;
IF OBJECT_ID('tempdb..#bv') IS NOT NULL DROP TABLE #bv;

SELECT v.COD_DEVELOP, COALESCE(d.TEXT, v.NAME_TYPE) AS TXT
INTO #bv
FROM OUC_COMMON_ADMIN.GCGT_ATTRIBUTE_VALUES v
LEFT JOIN GCTS_DICTIONARY d ON d.ID = v.NAME_TYPE_XI18N AND d.LOCALE = 'EN'
WHERE v.COD_MASTER_ATTRIBUTE = '{BEHAVIOUR_MASTER}';

-- requests: latest per account since the date (JSON parsed only when valid)
SELECT x.* INTO #m FROM (
    SELECT mi.ID_SEQ, mi.CREATE_DATE AS REQ_DATE, mi.REPONSESTATUS AS REQ_STATUS,
           LEFT(mi.RESPONSEENTITY, 60) AS RESPONSE_REF,
           j.ID_ACCOUNT_REF, j.COMPLAINT_TYPE, j.RCCS_REF, j.CREDIT_AMOUNT, j.NO_BANK_AMOUNT,
           j.CREDIT_USAGE, j.DEDUCTION_AMOUNT, j.AMOUNT_TYPE, j.DEDUCTION_DAY,
           COUNT(*) OVER (PARTITION BY j.ID_ACCOUNT_REF) AS REQ_COUNT,
           ROW_NUMBER() OVER (PARTITION BY j.ID_ACCOUNT_REF ORDER BY mi.CREATE_DATE DESC, mi.ID_SEQ DESC) AS RN
    FROM {MESSAGES_TABLE} mi WITH (NOLOCK)
    CROSS APPLY (SELECT
        CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.idAccount') END AS ID_ACCOUNT_REF,
        CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.complaintType') END AS COMPLAINT_TYPE,
        COALESCE(CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.rccsReference') END,
                 LEFT(mi.RESPONSEENTITY, 60)) AS RCCS_REF,
        CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.creditAmount') END AS CREDIT_AMOUNT,
        CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.noBankAmount') END AS NO_BANK_AMOUNT,
        CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.creditUsage') END AS CREDIT_USAGE,
        CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.deductionAmount') END AS DEDUCTION_AMOUNT,
        CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.amountType') END AS AMOUNT_TYPE,
        CASE WHEN ISJSON(mi.REQUESTENTITY) = 1 THEN JSON_VALUE(mi.REQUESTENTITY, '$.deductionDay') END AS DEDUCTION_DAY
    ) j
    WHERE mi.REQUESTURL = '{REQUEST_URL}' AND mi.CREATE_DATE > '{since}'
) x
WHERE x.RN = 1;

-- complaint + its 4 attributes for the RCCS of each request
-- RJ 2026-10-18 fix: one RCCS reference can exist as SEVERAL complaints, one per account
-- (live RCCS1791899179 = complaint 21451677 / account 1058800143 + 21451678 / 1052964028),
-- so the complaint is matched on reference AND account (ID_ACCOUNT = payment form) -
-- matching on the reference alone multiplied the rows (2 accounts x 2 complaints = 4).
SELECT c.COD_REF_COMPLAINT, c.ID_ACCOUNT, c.ID_COMPLAINT, c.COD_STATUS_COMPLAINT, c.CREATE_DATE AS RCCS_DATE,
       ct.COD_DEVELOP AS RCCS_TYPE_CODE,
       MAX(CASE WHEN a.COD_MASTER_ATTRIBUTE = 'RCCSCRDAMT' THEN a.ATT_VALUE END) AS ATT_CREDIT_AMT,
       MAX(CASE WHEN a.COD_MASTER_ATTRIBUTE = 'RCCSNOBAMT' THEN a.ATT_VALUE END) AS ATT_NOBANK_AMT,
       MAX(CASE WHEN a.COD_MASTER_ATTRIBUTE = 'RCCSCRDUSG' THEN a.ATT_VALUE END) AS ATT_CREDIT_USAGE,
       MAX(CASE WHEN a.COD_MASTER_ATTRIBUTE = 'FIXAMTYP' THEN a.ATT_VALUE END) AS ATT_DEDUCTION_AMT
INTO #c
FROM #m m
JOIN OUC_ADMIN.GCCC_COMPLAINT c WITH (NOLOCK) ON c.COD_REF_COMPLAINT = m.RCCS_REF
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_TYPE ct ON ct.ID_COMPLAINT_TYPE = c.ID_COMPLAINT_TYPE
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_ATT a WITH (NOLOCK)
     ON a.ID_COMPLAINT = c.ID_COMPLAINT AND a.COD_MASTER_ATTRIBUTE IN ('RCCSCRDAMT', 'RCCSNOBAMT', 'RCCSCRDUSG', 'FIXAMTYP')
GROUP BY c.COD_REF_COMPLAINT, c.ID_ACCOUNT, c.ID_COMPLAINT, c.COD_STATUS_COMPLAINT, c.CREATE_DATE, ct.COD_DEVELOP;

-- RJ 2026-10-21: no TOP - "do not limit top 60k, we are expecting 163k customers".
-- RJ 2026-10-21 (later): base = the requests (#m), EWA_DDI_ELIGIBILITY only as info.
SELECT m.ID_ACCOUNT_REF AS ACCOUNT,
       pf.ID_PAYMENT_FORM,
       CASE WHEN e.id_payment_form IS NOT NULL THEN 'YES' ELSE 'NO' END AS IN_EWA,
       w.ID_WALLET, w.IND_ACTIVE AS WALLET_ACTIVE, w.BALANCE AS WALLET_BALANCE, w.COD_BEHAVIOUR,
       bvw.TXT AS BEHAVIOUR_DESC, w.UPDATE_DATE AS WALLET_UPDATED,
       m.ID_SEQ AS REQ_ID, m.REQ_DATE, m.REQ_STATUS, m.REQ_COUNT, m.COMPLAINT_TYPE AS REQ_COMPLAINT_TYPE,
       m.RCCS_REF, m.CREDIT_AMOUNT AS REQ_CREDIT_AMOUNT, m.NO_BANK_AMOUNT AS REQ_NOBANK_AMOUNT,
       m.CREDIT_USAGE AS REQ_CREDIT_USAGE, m.DEDUCTION_AMOUNT AS REQ_DEDUCTION_AMOUNT,
       m.AMOUNT_TYPE AS REQ_AMOUNT_TYPE, m.DEDUCTION_DAY AS REQ_DEDUCTION_DAY,
       c.ID_COMPLAINT, c.RCCS_DATE, c.RCCS_TYPE_CODE, c.COD_STATUS_COMPLAINT AS RCCS_STATUS,
       COALESCE(ds.TEXT, st.NAME_TYPE) AS RCCS_STATUS_EN,
       c.ATT_CREDIT_AMT, c.ATT_NOBANK_AMT, c.ATT_CREDIT_USAGE, bva.TXT AS ATT_CREDIT_USAGE_DESC, c.ATT_DEDUCTION_AMT,
       {ewa_cols}
FROM #m m
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.REFERENCE = m.ID_ACCOUNT_REF
LEFT JOIN {DDI_TABLE} e WITH (NOLOCK) ON e.account = m.ID_ACCOUNT_REF
LEFT JOIN {WALLET_TABLE} w WITH (NOLOCK) ON w.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM
LEFT JOIN #bv bvw ON bvw.COD_DEVELOP = w.COD_BEHAVIOUR
LEFT JOIN #c c ON c.COD_REF_COMPLAINT = m.RCCS_REF AND c.ID_ACCOUNT = pf.ID_PAYMENT_FORM
LEFT JOIN #bv bva ON bva.COD_DEVELOP = c.ATT_CREDIT_USAGE
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_STATUS st ON st.COD_DEVELOP = c.COD_STATUS_COMPLAINT
LEFT JOIN GCTS_DICTIONARY ds ON ds.ID = st.NAME_TYPE_XI18N AND ds.LOCALE = 'EN'
{where}
ORDER BY m.ID_ACCOUNT_REF;

DROP TABLE #m; DROP TABLE #c; DROP TABLE #bv;
"""


def build_request_json_query(id_seq: int) -> str:
    """RJ 2026-10-18: the request group in the table shows one JSON column - the raw
    REQUESTENTITY is fetched on demand per request (keeps the scan payload small)."""
    return f"""
SELECT mi.ID_SEQ, mi.CREATE_DATE, mi.REQUESTURL, mi.REQUESTENTITY, mi.RESPONSEENTITY, mi.REPONSESTATUS
FROM {MESSAGES_TABLE} mi WITH (NOLOCK)
WHERE mi.ID_SEQ = {int(id_seq)}
"""


def _num(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _differs(a, b) -> bool:
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return False
    return abs(x - y) > AMOUNT_TOLERANCE


# RJ 2026-10-18: "it is also very important to group the RCCS, 4103 will be no bank or
# Benefit, if no bank is null it is benefit, if with nobank it is classified as no bank,
# 4102 is classified as direct debit request, this are the 3 main types".
# No-bank amount = the RCCS attribute RCCSNOBAMT; when the RCCS is not found, the
# request's noBankAmount is used.
CATEGORY_DIRECT_DEBIT = "Direct Debit Request"
CATEGORY_NO_BANK = "No Bank"
CATEGORY_BENEFIT = "Benefit"
CATEGORY_OTHER = "Other"


def category(r: dict) -> str:
    t = (r.get("req_complaint_type") or "").strip()
    if t == "4102COMTYP":
        return CATEGORY_DIRECT_DEBIT
    if t == "4103COMTYP":
        nob = r.get("att_nobank_amt") if str(r.get("id_complaint") or "").strip() else r.get("req_nobank_amount")
        return CATEGORY_BENEFIT if str(nob or "").strip() == "" else CATEGORY_NO_BANK
    return CATEGORY_OTHER


def shape_row(r: dict) -> dict:
    """Adds link flags + consistency checks (wallet vs request vs RCCS attribute)."""
    r["category"] = category(r)
    has_wallet = bool(str(r.get("id_wallet") or "").strip())
    has_req = bool(str(r.get("req_id") or "").strip())
    has_rccs = bool(str(r.get("id_complaint") or "").strip())
    r["has_wallet"], r["has_request"], r["has_rccs"] = has_wallet, has_req, has_rccs
    in_ewa = str(r.get("in_ewa") or "").upper() == "YES"
    r["in_ewa_flag"] = in_ewa
    issues = []
    # RJ 2026-10-21: rows now come from the requests - flag what the request can't be tied to.
    if not str(r.get("id_payment_form") or "").strip():
        issues.append("Account not found (no payment form)")
    if has_req and not has_rccs:
        issues.append("Request without RCCS complaint")
    if has_req and not has_wallet:
        issues.append("Request without wallet")
    if _differs(r.get("wallet_balance"), r.get("att_credit_amt")):
        issues.append("Wallet balance <> RCCS credit amount")
    if _differs(r.get("req_credit_amount"), r.get("att_credit_amt")):
        issues.append("Request credit <> RCCS credit amount")
    if _differs(r.get("req_nobank_amount"), r.get("att_nobank_amt")):
        issues.append("Request no-bank <> RCCS no-bank amount")
    if _differs(r.get("req_deduction_amount"), r.get("att_deduction_amt")):
        issues.append("Request deduction <> RCCS deduction amount")
    if has_wallet and has_rccs and r.get("cod_behaviour") and r.get("att_credit_usage") \
            and r.get("cod_behaviour") != r.get("att_credit_usage"):
        issues.append("Wallet behaviour <> RCCS credit usage")
    r["issues"] = issues
    r["issue_text"] = "; ".join(issues)
    if not has_wallet and not has_req:
        r["link_state"] = "Not in campaign"
    elif has_wallet and has_req and has_rccs:
        r["link_state"] = "Wallet + Request + RCCS"
    elif has_req and has_rccs:
        r["link_state"] = "Request + RCCS (no wallet)"
    elif has_wallet and has_req:
        r["link_state"] = "Wallet + Request (no RCCS)"
    elif has_wallet:
        r["link_state"] = "Wallet only"
    else:
        r["link_state"] = "Request only"
    return r
