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
SCOPES = ("all", "in_ewa", "not_in_ewa", "with_wallet", "no_wallet", "day_no_wallet_day", "nobank_no_wallet_day",
          "cancelled_with_wallet")
# RJ 2026-10-25: "we added paymentday in the credit wallet and the rccs attribute, and the request
# in external messages in is deductionDay, so for no Bank only there will be deduction day, i need
# a filter for those with payemnt day (deductionDay on the request) and the paymentday in the
# wallet table is null". Live schema: wallet = EWA_GCCOM_SUBSIDY_WALLET.FIXED_DAY, RCCS attribute
# = GCCC_COMPLAINT_ATT 'RCCSDEDDAT' (e.g. 22), request = JSON $.deductionDay.
RCCS_DAY_ATTR = "RCCSDEDDAT"
# RJ 2026-10-25: "only messages_in and responseheader is not an error : InCMS-BL-SS000009. Invalid
# system configuration detected: Wallet already exists for that account." - a request whose
# RESPONSEHEADER carries an error (all live ones start "InCMS-BL-...": wallet already exists, DD
# process on going, deadlocks, ...) created no RCCS, so it is left out. Success = empty header.
REQUEST_OK_SQL = "NULLIF(LTRIM(RTRIM(mi.RESPONSEHEADER)), '') IS NULL"
# RJ 2026-10-28: "remove the cancelled by default but add a tick button to include them" -
# GCCC_COMPLAINT_STATUS COMPLST001 = "Canceled" (live lookup; the other *Cancellation* statuses are
# DD / OTC / PDC process steps, not a cancelled RCCS).
CANCELLED_RCCS_STATUS = "COMPLST001"
# RJ 2026-10-28: credit amount = the wallet's initial credit movement (WALMOV0001)
MOVEMENT_TABLE = "OUC_COMMON_ADMIN.EWA_GCCOM_SUBSIDY_WALLET_MOVEMENT"
CREDIT_MOVEMENT_TYPE = "WALMOV0001"
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
        # request has a deductionDay, the wallet exists but its FIXED_DAY (payment day) is null
        "day_no_wallet_day": (" AND NULLIF(LTRIM(RTRIM(m.DEDUCTION_DAY)), '') IS NOT NULL"
                              " AND w.ID_PAYMENT_FORM IS NOT NULL AND w.FIXED_DAY IS NULL"),
        # RJ 2026-10-25: "no bank with no paymentDAy in credit wallet table" - same "No Bank" rule
        # as the page's RCCS group (4103COMTYP + a No Bank Amount: the RCCS attribute when the
        # RCCS is found, else the request's noBankAmount); wallet exists, FIXED_DAY null.
        "nobank_no_wallet_day": (" AND m.COMPLAINT_TYPE = '4103COMTYP'"
                                 " AND NULLIF(LTRIM(RTRIM(CASE WHEN c.ID_COMPLAINT IS NOT NULL THEN c.ATT_NOBANK_AMT"
                                 " ELSE m.NO_BANK_AMOUNT END)), '') IS NOT NULL"
                                 " AND w.ID_PAYMENT_FORM IS NOT NULL AND w.FIXED_DAY IS NULL"),
        # RJ 2026-11-01: "i want a filter for all with cancelled rccs and with wallet"
        "cancelled_with_wallet": (" AND c.COD_STATUS_COMPLAINT = 'COMPLST001'"
                                  " AND w.ID_PAYMENT_FORM IS NOT NULL"),
    }[scope]
    # RJ 2026-11-01: "can you make it faster, using the filter it times out and never give result" -
    # a scope condition in the WHERE made SQL Server pick a far worse plan (cancelled + wallet:
    # >100s and timing out vs ~50s for "all"). The SQL now always runs the "all" shape and the
    # scope is applied to the shaped rows in Python (scope_match). `extra` kept for reference only.
    del extra
    where = f"WHERE {has_credit}"
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
           ROW_NUMBER() OVER (PARTITION BY j.ID_ACCOUNT_REF ORDER BY mi.CREATE_DATE DESC, mi.ID_SEQ DESC) AS RN,
           ROW_NUMBER() OVER (PARTITION BY j.ID_ACCOUNT_REF, j.RCCS_REF ORDER BY mi.CREATE_DATE DESC, mi.ID_SEQ DESC) AS RN_RCCS
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
      AND {REQUEST_OK_SQL}
) x
WHERE x.RN_RCCS = 1;
-- RJ 2026-10-28: "i only want to see 1 account and 1 rccs, only the successful request, create a
-- 3rd tab for those with multi rccs" - one row per (account, RCCS) = its latest successful request.
-- RJ 2026-10-29: all rows are shown; accounts with 2+ RCCS only get a "Multi RCCS" marker
-- (cancelled RCCS COMPLST001 left out of rows and count unless "Include cancelled" is ticked).
-- RJ 2026-10-25: "the main table will be the MESSAGES_IN, check this account it has multiple rccs
-- but why we only show it once? 1071736746" - every request is now a row (was: latest per
-- account only, RN = 1). RN stays as REQ_SEQ (1 = the account's latest request).

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
       MAX(CASE WHEN a.COD_MASTER_ATTRIBUTE = 'FIXAMTYP' THEN a.ATT_VALUE END) AS ATT_DEDUCTION_AMT,
       MAX(CASE WHEN a.COD_MASTER_ATTRIBUTE = '{RCCS_DAY_ATTR}' THEN a.ATT_VALUE END) AS ATT_DEDUCTION_DAY
INTO #c
FROM #m m
JOIN OUC_ADMIN.GCCC_COMPLAINT c WITH (NOLOCK) ON c.COD_REF_COMPLAINT = m.RCCS_REF
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_TYPE ct ON ct.ID_COMPLAINT_TYPE = c.ID_COMPLAINT_TYPE
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_ATT a WITH (NOLOCK)
     ON a.ID_COMPLAINT = c.ID_COMPLAINT AND a.COD_MASTER_ATTRIBUTE IN ('RCCSCRDAMT', 'RCCSNOBAMT', 'RCCSCRDUSG', 'FIXAMTYP', '{RCCS_DAY_ATTR}')
GROUP BY c.COD_REF_COMPLAINT, c.ID_ACCOUNT, c.ID_COMPLAINT, c.COD_STATUS_COMPLAINT, c.CREATE_DATE, ct.COD_DEVELOP;

-- RJ 2026-10-21: no TOP - "do not limit top 60k, we are expecting 163k customers".
-- RJ 2026-10-21 (later): base = the requests (#m), EWA_DDI_ELIGIBILITY only as info.
SELECT m.ID_ACCOUNT_REF AS ACCOUNT,
       pf.ID_PAYMENT_FORM,
       CASE WHEN e.id_payment_form IS NOT NULL THEN 'YES' ELSE 'NO' END AS IN_EWA,
       w.ID_WALLET, w.IND_ACTIVE AS WALLET_ACTIVE, w.BALANCE AS WALLET_BALANCE, w.COD_BEHAVIOUR,
       bvw.TXT AS BEHAVIOUR_DESC, w.UPDATE_DATE AS WALLET_UPDATED, w.FIXED_DAY AS WALLET_FIXED_DAY,
       wmv.AMOUNT AS WALLET_CREDIT_AMOUNT, wmv.MOVEMENT_DATE AS WALLET_CREDIT_DATE,
       m.ID_SEQ AS REQ_ID, m.REQ_DATE, m.REQ_STATUS, m.REQ_COUNT, m.RN AS REQ_SEQ,
       CASE WHEN m.RN = 1 THEN 'YES' ELSE 'NO' END AS IS_LATEST, m.COMPLAINT_TYPE AS REQ_COMPLAINT_TYPE,
       m.RCCS_REF, m.CREDIT_AMOUNT AS REQ_CREDIT_AMOUNT, m.NO_BANK_AMOUNT AS REQ_NOBANK_AMOUNT,
       m.CREDIT_USAGE AS REQ_CREDIT_USAGE, m.DEDUCTION_AMOUNT AS REQ_DEDUCTION_AMOUNT,
       m.AMOUNT_TYPE AS REQ_AMOUNT_TYPE, m.DEDUCTION_DAY AS REQ_DEDUCTION_DAY,
       c.ID_COMPLAINT, c.RCCS_DATE, c.RCCS_TYPE_CODE, c.COD_STATUS_COMPLAINT AS RCCS_STATUS,
       COALESCE(ds.TEXT, st.NAME_TYPE) AS RCCS_STATUS_EN,
       c.ATT_CREDIT_AMT, c.ATT_NOBANK_AMT, c.ATT_CREDIT_USAGE, bva.TXT AS ATT_CREDIT_USAGE_DESC, c.ATT_DEDUCTION_AMT, c.ATT_DEDUCTION_DAY,
       {ewa_cols}
FROM #m m
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.REFERENCE = m.ID_ACCOUNT_REF
LEFT JOIN {DDI_TABLE} e WITH (NOLOCK) ON e.account = m.ID_ACCOUNT_REF
LEFT JOIN {WALLET_TABLE} w WITH (NOLOCK) ON w.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM
-- RJ 2026-10-28: "for the credit amount we will get it from the wallet movement table connecting the
-- original wallet table ... MOVEMENT_TYPE = 'WALMOV0001'" (live: exactly 1 WALMOV0001 per wallet)
OUTER APPLY (SELECT SUM(mv.AMOUNT) AS AMOUNT, MIN(mv.MOVEMENT_DATE) AS MOVEMENT_DATE
             FROM {MOVEMENT_TABLE} mv WITH (NOLOCK)
             WHERE mv.ID_WALLET = w.ID_WALLET AND mv.MOVEMENT_TYPE = '{CREDIT_MOVEMENT_TYPE}') wmv
LEFT JOIN #bv bvw ON bvw.COD_DEVELOP = w.COD_BEHAVIOUR
LEFT JOIN #c c ON c.COD_REF_COMPLAINT = m.RCCS_REF AND c.ID_ACCOUNT = pf.ID_PAYMENT_FORM
LEFT JOIN #bv bva ON bva.COD_DEVELOP = c.ATT_CREDIT_USAGE
LEFT JOIN OUC_ADMIN.GCCC_COMPLAINT_STATUS st ON st.COD_DEVELOP = c.COD_STATUS_COMPLAINT
LEFT JOIN GCTS_DICTIONARY ds ON ds.ID = st.NAME_TYPE_XI18N AND ds.LOCALE = 'EN'
{where}
ORDER BY m.ID_ACCOUNT_REF, m.REQ_DATE DESC, m.ID_SEQ DESC;

DROP TABLE #m; DROP TABLE #c; DROP TABLE #bv;
"""


# ---------------------------------------------------------------------------
# Tab 2 "Subsidized - planned billing" (RJ 2026-10-23): "for all the subsidized account,
# i want to see the planned billing date on their readings for September 2026, make KPI"
# + "tell me if it has subsidy wallet and how much and the behaviour in description"
# + "use GCGT_RE_READING.reading_date +3".
#   subsidized = active contracted service (STATUS not Pending/Terminated/Cancelled) whose
#       billing service fare is a Bahraini First Account tariff (same as Temp Dashboard
#       subsidy tab): 10000000003 E-D2, 10000000004 W-D2, 10000000006 E-D2-NM
#   readings = GCGT_RE_READING of those services' sector supplies in the billing period
#   PLANNED BILLING DATE = READING_DATE + 3 days (RJ)
#   billed = READ_STATUS 7000STSRED Billed / 7001STSRED UAU - Billed
# One row per subsidized account (accounts without a reading in the period included).
# Live 2026-10-23 (period 10000000237 = September 2026): 166,833 accounts, ~52 s.
# RJ 2026-10-23 (later): "only consider cycle readings, and should only be by account, mostly
# will have water and electricity, get the highest per account to show only 1 record per
# account" - readings limited to READING_TYPE TIPTL00003 (Cycle); the account's PLANNED
# BILLING DATE = the HIGHEST planned date of its cycle readings (electricity / water dates
# are also shown separately).
# ---------------------------------------------------------------------------
SUBSIDY_FARES = {10000000003: "E-D2", 10000000004: "W-D2", 10000000006: "E-D2-NM"}
SUBSIDY_PERIOD_DEFAULT = 10000000237
PLANNED_BILLING_DAYS = 3
BILLED_READ_STATUSES = ("7000STSRED", "7001STSRED")
CYCLE_READING_TYPE = "TIPTL00003"


def build_subsidy_planned_query(billing_period: int = SUBSIDY_PERIOD_DEFAULT) -> str:
    bp = int(billing_period)
    fares = ", ".join(str(f) for f in SUBSIDY_FARES)
    fare_name = "CASE s2.ID_FARE " + " ".join(f"WHEN {f} THEN '{n}'" for f, n in SUBSIDY_FARES.items()) + " END"
    billed = ", ".join(f"'{s}'" for s in BILLED_READ_STATUSES)
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#sub') IS NOT NULL DROP TABLE #sub;
IF OBJECT_ID('tempdb..#rd') IS NOT NULL DROP TABLE #rd;
IF OBJECT_ID('tempdb..#st') IS NOT NULL DROP TABLE #st;

-- subsidized (Bahraini First Account tariff) active services
SELECT cs.ID_PAYMENT_FORM, cs.ID_CONTRACTED_SERVICE, cs.ID_SECTOR_SUPPLY, cs.ID_OFFERED_SERVICE, bs.ID_FARE
INTO #sub
FROM GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
JOIN GCCOM_BILLING_SERVICE bs WITH (NOLOCK) ON bs.ID_CONTRACTED_SERVICE = cs.ID_CONTRACTED_SERVICE
WHERE bs.ID_FARE IN ({fares}) AND cs.STATUS NOT IN ('ESTSC00001', 'ESTSC00004', 'ESTSC00005');

-- reading status names (EN)
SELECT s.COD_DEVELOP, COALESCE(d.TEXT, s.DESCRIPTION) AS TXT INTO #st
FROM OUC_COMMON_ADMIN.GCGT_RE_READ_STATUS s
LEFT JOIN GCTS_DICTIONARY d ON d.ID = s.DESCRIPTION_XI18N AND d.LOCALE = 'EN';

-- readings of the period; planned billing date = reading date + {PLANNED_BILLING_DAYS} days (RJ)
SELECT s.ID_PAYMENT_FORM, r.ID_READING, r.READ_STATUS, r.READING_TYPE,
       CAST(r.READING_DATE AS date) AS READING_DATE,
       DATEADD(day, {PLANNED_BILLING_DAYS}, CAST(r.READING_DATE AS date)) AS PLANNED_BILLING_DATE,
       CASE s.ID_OFFERED_SERVICE WHEN 1 THEN 'Electricity' WHEN 19 THEN 'Water'
            ELSE CAST(s.ID_OFFERED_SERVICE AS varchar(10)) END AS SVC
INTO #rd
FROM #sub s
JOIN GCGT_RE_READING r WITH (NOLOCK) ON r.ID_SECTOR_SUPPLY = s.ID_SECTOR_SUPPLY AND r.ID_BILLING_PERIOD = {bp}
 AND r.READING_TYPE = '{CYCLE_READING_TYPE}';

SELECT pf.REFERENCE AS ACCOUNT, a.ID_PAYMENT_FORM,
  (SELECT STRING_AGG(x.F, ', ') FROM (SELECT DISTINCT {fare_name} AS F FROM #sub s2
     WHERE s2.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM) x) AS TARIFFS,
  a.SUB_SERVICES,
  ISNULL(rs.READINGS, 0) AS READINGS,
  rs.LAST_PLANNED_BILLING_DATE AS PLANNED_BILLING_DATE,
  rs.ELEC_PLANNED_DATE, rs.WATER_PLANNED_DATE,
  rs.FIRST_PLANNED_BILLING_DATE, rs.LAST_PLANNED_BILLING_DATE, rs.UNBILLED_PLANNED_DATE,
  ISNULL(rs.BILLED, 0) AS BILLED_READINGS, ISNULL(rs.READINGS, 0) - ISNULL(rs.BILLED, 0) AS NOT_BILLED_READINGS,
  (SELECT STRING_AGG(r2.SVC + ' ' + CONVERT(varchar(10), r2.READING_DATE, 23) + ' -> '
          + CONVERT(varchar(10), r2.PLANNED_BILLING_DATE, 23) + ' ' + ISNULL(st.TXT, r2.READ_STATUS)
          + ' (' + ISNULL(rt.DESCRIPTION, r2.READING_TYPE) + ')', '; ')
     FROM #rd r2
     LEFT JOIN #st st ON st.COD_DEVELOP = r2.READ_STATUS
     LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING_TYPE rt ON rt.COD_DEVELOP = r2.READING_TYPE
     WHERE r2.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM) AS READINGS_DETAIL,
  w.ID_WALLET, w.BALANCE AS WALLET_AMOUNT, w.COD_BEHAVIOUR, bv.TXT AS BEHAVIOUR_DESC
FROM (SELECT ID_PAYMENT_FORM, COUNT(*) AS SUB_SERVICES FROM #sub GROUP BY ID_PAYMENT_FORM) a
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM
LEFT JOIN (SELECT ID_PAYMENT_FORM, COUNT(*) AS READINGS,
                  MIN(PLANNED_BILLING_DATE) AS FIRST_PLANNED_BILLING_DATE,
                  MAX(PLANNED_BILLING_DATE) AS LAST_PLANNED_BILLING_DATE,
                  MAX(CASE WHEN SVC = 'Electricity' THEN PLANNED_BILLING_DATE END) AS ELEC_PLANNED_DATE,
                  MAX(CASE WHEN SVC = 'Water' THEN PLANNED_BILLING_DATE END) AS WATER_PLANNED_DATE,
                  MIN(CASE WHEN READ_STATUS NOT IN ({billed}) THEN PLANNED_BILLING_DATE END) AS UNBILLED_PLANNED_DATE,
                  SUM(CASE WHEN READ_STATUS IN ({billed}) THEN 1 ELSE 0 END) AS BILLED
           FROM #rd GROUP BY ID_PAYMENT_FORM) rs ON rs.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM
LEFT JOIN {WALLET_TABLE} w WITH (NOLOCK) ON w.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM
LEFT JOIN (SELECT v.COD_DEVELOP, COALESCE(d.TEXT, v.NAME_TYPE) AS TXT
           FROM OUC_COMMON_ADMIN.GCGT_ATTRIBUTE_VALUES v
           LEFT JOIN GCTS_DICTIONARY d ON d.ID = v.NAME_TYPE_XI18N AND d.LOCALE = 'EN'
           WHERE v.COD_MASTER_ATTRIBUTE = '{BEHAVIOUR_MASTER}') bv ON bv.COD_DEVELOP = w.COD_BEHAVIOUR
ORDER BY rs.LAST_PLANNED_BILLING_DATE, pf.REFERENCE;

DROP TABLE #sub; DROP TABLE #rd; DROP TABLE #st;
"""


# ---------------------------------------------------------------------------
# Tab "Bahraini First Account categories" (RJ 2026-11-01): "add a new tab with 3 category
# Bahraini first account under fix billing, bahraini first account not under fix billing and,
# others that are not in the first 2 category, add a tick box if bill is generated (in status
# invoicing) for the November billing period (or the period selected)".
#   Bahraini First Account (BFA) = account with an active service on a BFA tariff
#       (SUBSIDY_FARES, same rule as the Subsidized tabs)
#   under fix billing = the account has a successful campaign request (empty RESPONSEHEADER,
#       creditAmount, after `since` - same rules as the Campaign tab)
#   categories: 1 BFA + campaign, 2 BFA without campaign,
#       3 Others = every other account (active service on any other tariff, plus campaign accounts
#       that are not BFA). RJ 2026-11-01 (later): "why others are low, i suppose other tariffs will
#       fall under this category" - was campaign-non-BFA only (59), now all other accounts (~294k).
#   bill generated = GCCOM_BILL of the account in the period with BILLING_STATUS ESTFAC0012
#       (Invoicing)
# One row per account. Category totals (CAT1..3_TOTAL / _GEN) are computed in SQL over ALL
# accounts; "Others" rows are only RETURNED when they have an Invoicing bill or a campaign request,
# unless include_all_others=True (~294k rows - heavy for the browser).
# ---------------------------------------------------------------------------
BFA_PERIOD_DEFAULT = 10000000239          # 11-November 2026 (GCCOM_BILLING_PERIOD, live)
BILL_STATUS_INVOICING = "ESTFAC0012"      # OUC_ADMIN.GCCOM_BILL_STATUS "Invoicing" (live)
BFA_CATEGORIES = ("Bahraini First Account - under fix billing",
                  "Bahraini First Account - not under fix billing",
                  "Others - other tariffs")


def build_bfa_categories_query(billing_period: int = BFA_PERIOD_DEFAULT, since: str | None = None,
                               include_all_others: bool = False) -> str:
    bp = int(billing_period)
    since = _since(since)
    fares = ", ".join(str(f) for f in SUBSIDY_FARES)
    c1, c2, c3 = BFA_CATEGORIES
    others_filter = "" if include_all_others else \
        "WHERE f.CAT_NO < 3 OR f.BILL_GENERATED = 'YES' OR f.UNDER_FIX_BILLING = 'YES'"
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#acc') IS NOT NULL DROP TABLE #acc;
IF OBJECT_ID('tempdb..#cmp') IS NOT NULL DROP TABLE #cmp;
IF OBJECT_ID('tempdb..#u') IS NOT NULL DROP TABLE #u;
IF OBJECT_ID('tempdb..#bl') IS NOT NULL DROP TABLE #bl;
IF OBJECT_ID('tempdb..#tot') IS NOT NULL DROP TABLE #tot;
DECLARE @bp_name varchar(100) = (SELECT COALESCE(d.TEXT, p.DESCRIPTION) FROM GCCOM_BILLING_PERIOD p
    LEFT JOIN GCTS_DICTIONARY d ON d.ID = p.PERIOD_NAME_XI18N AND d.LOCALE = 'EN' WHERE p.ID_BILLING_PERIOD = {bp});

-- every account with an active service: BFA flag + its tariffs (GCCOM_FARE.RATE_CODE)
SELECT x.ID_PAYMENT_FORM, MAX(x.IS_BFA) AS IS_BFA, STRING_AGG(x.RATE_CODE, ', ') AS TARIFFS
INTO #acc
FROM (SELECT DISTINCT cs.ID_PAYMENT_FORM,
             CASE WHEN bs.ID_FARE IN ({fares}) THEN 1 ELSE 0 END AS IS_BFA,
             CAST(COALESCE(f.RATE_CODE, CAST(bs.ID_FARE AS varchar(20))) AS varchar(30)) AS RATE_CODE
      FROM GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
      LEFT JOIN GCCOM_BILLING_SERVICE bs WITH (NOLOCK) ON bs.ID_CONTRACTED_SERVICE = cs.ID_CONTRACTED_SERVICE
      LEFT JOIN OUC_COMMON_ADMIN.GCCOM_FARE f ON f.ID_FARE = bs.ID_FARE
      WHERE cs.STATUS NOT IN ('ESTSC00001', 'ESTSC00004', 'ESTSC00005')) x
GROUP BY x.ID_PAYMENT_FORM;

-- fix billing campaign: successful requests with a creditAmount (Campaign tab rules).
-- Two steps (raw copy, then JSON parse) - live: ~32s + ~22s; one step was slower.
IF OBJECT_ID('tempdb..#raw') IS NOT NULL DROP TABLE #raw;
SELECT mi.CREATE_DATE, mi.REQUESTENTITY, LEFT(mi.RESPONSEENTITY, 60) AS RESPONSE_REF
INTO #raw
FROM {MESSAGES_TABLE} mi WITH (NOLOCK)
WHERE mi.REQUESTURL = '{REQUEST_URL}' AND mi.CREATE_DATE > '{since}'
  AND NULLIF(LTRIM(RTRIM(mi.RESPONSEHEADER)), '') IS NULL;

SELECT pf.ID_PAYMENT_FORM, j.ID_ACCOUNT_REF, MAX(r.CREATE_DATE) AS LAST_REQ_DATE,
       COUNT(DISTINCT j.RCCS_REF) AS RCCS_COUNT
INTO #cmp
FROM #raw r
CROSS APPLY (SELECT
    CASE WHEN ISJSON(r.REQUESTENTITY) = 1 THEN JSON_VALUE(r.REQUESTENTITY, '$.idAccount') END AS ID_ACCOUNT_REF,
    COALESCE(CASE WHEN ISJSON(r.REQUESTENTITY) = 1 THEN JSON_VALUE(r.REQUESTENTITY, '$.rccsReference') END,
             r.RESPONSE_REF) AS RCCS_REF,
    CASE WHEN ISJSON(r.REQUESTENTITY) = 1 THEN JSON_VALUE(r.REQUESTENTITY, '$.creditAmount') END AS CREDIT_AMOUNT
) j
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.REFERENCE = j.ID_ACCOUNT_REF
WHERE NULLIF(LTRIM(RTRIM(j.CREDIT_AMOUNT)), '') IS NOT NULL
GROUP BY pf.ID_PAYMENT_FORM, j.ID_ACCOUNT_REF;
DROP TABLE #raw;

-- universe = every active account + campaign accounts (FULL OUTER: campaign account without an
-- active service still listed)
SELECT COALESCE(a.ID_PAYMENT_FORM, c.ID_PAYMENT_FORM) AS ID_PAYMENT_FORM, c.ID_ACCOUNT_REF,
       a.TARIFFS, c.LAST_REQ_DATE, c.RCCS_COUNT,
       CASE WHEN a.IS_BFA = 1 AND c.ID_ACCOUNT_REF IS NOT NULL THEN 1
            WHEN a.IS_BFA = 1 THEN 2 ELSE 3 END AS CAT_NO
INTO #u
FROM #acc a
FULL OUTER JOIN #cmp c ON c.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM;

-- Invoicing bills of the period per payment form (status filter first: live ~30 ms;
-- all bills of the period with a join was ~33 s+)
SELECT b.ID_PAYMENT_FORM,
       COUNT(*) AS INVOICING_BILLS,
       SUM(b.TOTAL_AMOUNT) AS INVOICING_AMOUNT,
       MAX(b.BILLING_DATE) AS INVOICING_BILL_DATE,
       STRING_AGG(CAST(b.BILL_NUMBER AS varchar(40)), ', ') AS INVOICING_BILL_NUMBERS
INTO #bl
FROM OUC_COMMON_ADMIN.GCCOM_BILL b WITH (NOLOCK)
WHERE b.ID_BILLING_PERIOD = {bp} AND b.BILLING_STATUS = '{BILL_STATUS_INVOICING}'
GROUP BY b.ID_PAYMENT_FORM;

-- category totals over ALL accounts (returned on every row; "Others" rows may be trimmed below)
SELECT SUM(CASE WHEN u.CAT_NO = 1 THEN 1 ELSE 0 END) AS CAT1_TOTAL,
       SUM(CASE WHEN u.CAT_NO = 1 AND bl.ID_PAYMENT_FORM IS NOT NULL THEN 1 ELSE 0 END) AS CAT1_GEN,
       SUM(CASE WHEN u.CAT_NO = 2 THEN 1 ELSE 0 END) AS CAT2_TOTAL,
       SUM(CASE WHEN u.CAT_NO = 2 AND bl.ID_PAYMENT_FORM IS NOT NULL THEN 1 ELSE 0 END) AS CAT2_GEN,
       SUM(CASE WHEN u.CAT_NO = 3 THEN 1 ELSE 0 END) AS CAT3_TOTAL,
       SUM(CASE WHEN u.CAT_NO = 3 AND bl.ID_PAYMENT_FORM IS NOT NULL THEN 1 ELSE 0 END) AS CAT3_GEN
INTO #tot
FROM #u u LEFT JOIN #bl bl ON bl.ID_PAYMENT_FORM = u.ID_PAYMENT_FORM;

SELECT f.*, t.* FROM (
  SELECT COALESCE(pf.REFERENCE, u.ID_ACCOUNT_REF) AS ACCOUNT, u.ID_PAYMENT_FORM, u.CAT_NO,
         CASE u.CAT_NO WHEN 1 THEN '{c1}' WHEN 2 THEN '{c2}' ELSE '{c3}' END AS CATEGORY,
         u.TARIFFS, CASE WHEN u.ID_ACCOUNT_REF IS NOT NULL THEN 'YES' ELSE 'NO' END AS UNDER_FIX_BILLING,
         u.LAST_REQ_DATE, u.RCCS_COUNT,
         w.ID_WALLET, w.BALANCE AS WALLET_BALANCE,
         ISNULL(bl.INVOICING_BILLS, 0) AS INVOICING_BILLS, bl.INVOICING_BILL_NUMBERS,
         bl.INVOICING_AMOUNT, bl.INVOICING_BILL_DATE,
         CASE WHEN ISNULL(bl.INVOICING_BILLS, 0) > 0 THEN 'YES' ELSE 'NO' END AS BILL_GENERATED,
         {bp} AS ID_BILLING_PERIOD, @bp_name AS BILLING_PERIOD_NAME
  FROM #u u
  LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = u.ID_PAYMENT_FORM
  LEFT JOIN {WALLET_TABLE} w WITH (NOLOCK) ON w.ID_PAYMENT_FORM = u.ID_PAYMENT_FORM
  LEFT JOIN #bl bl ON bl.ID_PAYMENT_FORM = u.ID_PAYMENT_FORM
) f
CROSS JOIN #tot t
{others_filter}
ORDER BY f.CAT_NO, f.ACCOUNT;

DROP TABLE #acc; DROP TABLE #cmp; DROP TABLE #u; DROP TABLE #bl; DROP TABLE #tot;
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


def _filled(v) -> bool:
    return str(v if v is not None else "").strip() != ""


def scope_match(r: dict, scope: str) -> bool:
    """RJ 2026-11-01: Customers scope applied in Python on shaped rows (same rules the SQL had)."""
    has_wallet = _filled(r.get("id_wallet"))
    if scope == "in_ewa":
        return str(r.get("in_ewa") or "").upper() == "YES"
    if scope == "not_in_ewa":
        return str(r.get("in_ewa") or "").upper() != "YES"
    if scope == "with_wallet":
        return has_wallet
    if scope == "no_wallet":
        return not has_wallet
    if scope == "day_no_wallet_day":
        return _filled(r.get("req_deduction_day")) and has_wallet and not _filled(r.get("wallet_fixed_day"))
    if scope == "nobank_no_wallet_day":
        return r.get("category") == CATEGORY_NO_BANK and has_wallet and not _filled(r.get("wallet_fixed_day"))
    if scope == "cancelled_with_wallet":
        return str(r.get("rccs_status") or "") == CANCELLED_RCCS_STATUS and has_wallet
    return True


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
    # RJ 2026-10-28: credit = wallet movement WALMOV0001 (the balance moves with releases)
    if _differs(r.get("wallet_credit_amount"), r.get("att_credit_amt")):
        issues.append("Wallet credit (WALMOV0001) <> RCCS credit amount")
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
