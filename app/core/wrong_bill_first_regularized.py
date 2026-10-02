"""
Wrong Bill - Case 4: First bill regularized (RJ, 2026-10-02).

RJ: "First bill regularized, list by billing period how many and then to have
a drill down, if the last billing date is the same as gccom_contracted service
from date and in the billing_concept contains a regularization concept
(COD_DEVELOP REGAGUA REGCC210 REGCC220 REGSANT) and the concept amount <> 0
for id_offered_service 1, 19, 190. List the account, id_bill, bill_status, and
the bills that it regularized (the bills where our ID_BILL is set to the
ID_REG_BILL), of course do not include cancelled, rebilled bill and credit
note".

- First bill: GCCOM_BILL whose LAST_BILLING_DATE (date part) equals its
  contracted service's FROM_DATE, on an Electricity (1) / Water (19) /
  Sanitary (190) contracted service.
- It carries a regularization concept (REGAGUA / REGCC210 / REGCC220 /
  REGSANT) with CONCEPT_AMOUNT <> 0.
- Not a credit note (TIPFAC0011), not Cancelled (ESTFAC0007) / Rebilled
  (ESTFAC0042) - the same exclusions apply to the regularized bills listed
  (GCCOM_BILL.ID_REG_BILL = our ID_BILL).

Live 2026-10-02: period 10000000238 -> 14 bills (~7s), 10000000236 -> 204
(~17s). Every regularized bill found sits on ANOTHER contracted service (the
previous contract), which is why a first bill should not carry them.

Performance: GCCOM_BILL has ~1.7M bills per period and the period index
alone needs a key lookup per bill (>70s for one period). So step 1
(build_bounds_query) finds, per period, the ID_BILL window holding ~98.5% of
its bills; step 2 (build_first_bills_query, per period) range-scans that
window on the primary key and picks up the remaining ~1.5% (old / late
rebilled ids) through the period index, returning only ID_BILLs; step 3
(build_detail_query) adds account / descriptions / regularized bills for
those ids.

Pure logic: builds SELECT text only.
"""
from __future__ import annotations

SCHEMA = "OUC_COMMON_ADMIN"
OFFERED_SERVICES = (1, 19, 190)
OFFERED_NAMES = {"1": "Electricity", "19": "Water", "190": "Sanitary"}
REG_CONCEPTS = ("REGAGUA", "REGCC210", "REGCC220", "REGSANT")
BILLING_TYPE_CREDIT_NOTE = "TIPFAC0011"
EXCLUDED_STATUSES = ("ESTFAC0007", "ESTFAC0042")  # Cancelled, Rebilled
MAX_PERIODS = 12
CORE_LOW_PCT = 0.005
CORE_HIGH_PCT = 0.99


def _periods(billing_periods) -> list[int]:
    periods = sorted({int(p) for p in (billing_periods or [])})
    if not periods:
        raise ValueError("Pick at least one billing period.")
    if len(periods) > MAX_PERIODS:
        raise ValueError(f"Pick at most {MAX_PERIODS} billing periods.")
    return periods


def build_bounds_query(billing_periods) -> str:
    """Step 1: one row per period (P, N, LO, HI) - LO/HI = the ID_BILL window
    holding the bulk of that period's bills (index-only, ~1-2s per period)."""
    periods = _periods(billing_periods)
    s = SCHEMA
    ids = ", ".join(str(p) for p in periods)
    return f"""
WITH x AS (
  SELECT ID_BILLING_PERIOD AS P, ID_BILL,
         ROW_NUMBER() OVER (PARTITION BY ID_BILLING_PERIOD ORDER BY ID_BILL) AS RN,
         COUNT(*) OVER (PARTITION BY ID_BILLING_PERIOD) AS N
  FROM {s}.GCCOM_BILL WITH (NOLOCK)
  WHERE ID_BILLING_PERIOD IN ({ids})
)
SELECT P, MAX(N) AS N,
       MIN(CASE WHEN RN >= N * {CORE_LOW_PCT} THEN ID_BILL END) AS LO,
       MAX(CASE WHEN RN <= N * {CORE_HIGH_PCT} THEN ID_BILL END) AS HI
FROM x
GROUP BY P
"""


def _fb_cte(source_join: str, extra_where: str = "") -> str:
    """The `fb` (first bill with a regularization concept) CTE body."""
    s = SCHEMA
    svc = ", ".join(str(x) for x in OFFERED_SERVICES)
    conc = ", ".join(f"'{c}'" for c in REG_CONCEPTS)
    excl = ", ".join(f"'{x}'" for x in EXCLUDED_STATUSES)
    return f"""fb AS (
  SELECT b.ID_BILL, b.BILL_NUMBER, b.ID_PAYMENT_FORM, b.ID_BILLING_PERIOD, b.BILLING_DATE, b.LAST_BILLING_DATE,
         b.BILLING_STATUS, b.BILLING_TYPE, b.TOTAL_AMOUNT, b.ID_CONTRACTED_SERVICE,
         cs.ID_OFFERED_SERVICE, cs.FROM_DATE AS CS_FROM_DATE, cs.ID_SECTOR_SUPPLY
  {source_join}
  JOIN {s}.GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
       ON cs.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE AND cs.ID_OFFERED_SERVICE IN ({svc})
  WHERE {extra_where}b.LAST_BILLING_DATE >= CAST(cs.FROM_DATE AS DATE)
    AND b.LAST_BILLING_DATE < DATEADD(DAY, 1, CAST(cs.FROM_DATE AS DATE))
    AND ISNULL(b.BILLING_TYPE, '') <> '{BILLING_TYPE_CREDIT_NOTE}'
    AND b.BILLING_STATUS NOT IN ({excl})
    AND EXISTS (SELECT 1 FROM GCCOM_BILLING_CONCEPT bc WITH (NOLOCK)
                WHERE bc.ID_BILL = b.ID_BILL AND bc.COD_CONCEPT IN ({conc}) AND bc.CONCEPT_AMOUNT <> 0)
)"""


def build_first_bills_query(period, lo, hi) -> str:
    """Step 2 (run once per period): the ID_BILLs of the flagged first bills.
    lo/hi from build_bounds_query(). Live: ~6s (Oct 2026) / ~17s (Aug 2026).
    Kept as a bare id list - with the descriptive joins in the same
    statement the optimizer dropped the range scan and timed out (>120s)."""
    p, lo, hi = int(period), int(lo), int(hi)
    s = SCHEMA
    fb = _fb_cte(f"FROM pb JOIN {s}.GCCOM_BILL b WITH (NOLOCK) ON b.ID_BILL = pb.ID_BILL")
    return f"""
WITH pb AS (
  SELECT ID_BILL FROM {s}.GCCOM_BILL WITH (NOLOCK) WHERE ID_BILL BETWEEN {lo} AND {hi} AND ID_BILLING_PERIOD = {p}
  UNION ALL
  SELECT ID_BILL FROM {s}.GCCOM_BILL WITH (NOLOCK) WHERE ID_BILLING_PERIOD = {p} AND (ID_BILL < {lo} OR ID_BILL > {hi})
),
{fb}
SELECT fb.ID_BILL FROM fb
"""


def build_first_bills_by_date_query(id_from, id_to, date_from: str, date_to: str, billing_periods=None) -> str:
    """Step 2, creation-date scope (RJ 2026-10-02: "I want to also see the
    cases for the latest 7 days"): flagged first bills CREATED (GCCOM_BILL.
    CREATE_DATE, inclusive dates) in the range. id_from/id_to = the ID_BILL
    window from wrong_bill_sanitary_zero.build_id_range_query() (CREATE_DATE
    has no index). Optional billing periods = AND. Live 7 days: ~1.5s."""
    from app.core.wrong_bill_sanitary_zero import validate_dates
    f, t = validate_dates(date_from, date_to)
    s = SCHEMA
    where = (f"b.ID_BILL BETWEEN {int(id_from)} AND {int(id_to)}\n"
             f"    AND b.CREATE_DATE >= '{f}' AND b.CREATE_DATE < DATEADD(DAY, 1, CAST('{t}' AS DATE))\n    AND ")
    if billing_periods:
        where += f"b.ID_BILLING_PERIOD IN ({', '.join(str(p) for p in _periods(billing_periods))})\n    AND "
    fb = _fb_cte(f"FROM {s}.GCCOM_BILL b WITH (NOLOCK)", where)
    return f"""
WITH {fb}
SELECT fb.ID_BILL FROM fb
"""


DETAIL_CHUNK = 1000


def build_detail_query(id_bills) -> str:
    """Step 3: full detail for the flagged ID_BILLs (<= DETAIL_CHUNK ids).
    One row per (first bill, regularized bill); a first bill without a
    (non-excluded) regularized bill comes back once with R_* = NULL.
    Live: 14 bills ~1s."""
    ids = sorted({int(x) for x in id_bills})
    if not ids:
        raise ValueError("No bills.")
    if len(ids) > DETAIL_CHUNK:
        raise ValueError(f"At most {DETAIL_CHUNK} bills per detail query.")
    s = SCHEMA
    conc = ", ".join(f"'{c}'" for c in REG_CONCEPTS)
    excl = ", ".join(f"'{x}'" for x in EXCLUDED_STATUSES)
    id_list = ", ".join(str(i) for i in ids)
    fb = _fb_cte(f"FROM {s}.GCCOM_BILL b WITH (NOLOCK)", "b.ID_BILL IN (" + id_list + ")\n    AND ")
    return f"""
WITH {fb}
SELECT
  fb.ID_BILLING_PERIOD AS ID_BILLING_PERIOD,
  COALESCE(dbp.TEXT, bp.DESCRIPTION) AS BILLING_PERIOD_DESC,
  pf.REFERENCE AS REFERENCE,
  ss.NISS AS NISS,
  fb.ID_OFFERED_SERVICE AS ID_OFFERED_SERVICE,
  fb.ID_CONTRACTED_SERVICE AS ID_CONTRACTED_SERVICE,
  fb.ID_BILL AS ID_BILL,
  fb.BILL_NUMBER AS BILL_NUMBER,
  fb.BILLING_STATUS AS BILLING_STATUS,
  COALESCE(dbst.TEXT, bst.NAME_TYPE) AS BILLING_STATUS_DESC,
  fb.BILLING_TYPE AS BILLING_TYPE,
  COALESCE(dbt.TEXT, bt.NAME_TYPE) AS BILLING_TYPE_DESC,
  fb.BILLING_DATE AS BILLING_DATE,
  fb.LAST_BILLING_DATE AS LAST_BILLING_DATE,
  fb.CS_FROM_DATE AS CS_FROM_DATE,
  fb.TOTAL_AMOUNT AS TOTAL_AMOUNT,
  rc.REG_CONCEPTS AS REG_CONCEPTS,
  rc.REG_AMOUNT AS REG_AMOUNT,
  r.ID_BILL AS R_ID_BILL,
  r.BILL_NUMBER AS R_BILL_NUMBER,
  r.ID_BILLING_PERIOD AS R_ID_BILLING_PERIOD,
  COALESCE(rdbp.TEXT, rbp.DESCRIPTION) AS R_BILLING_PERIOD_DESC,
  r.BILLING_DATE AS R_BILLING_DATE,
  r.BILLING_STATUS AS R_BILLING_STATUS,
  COALESCE(rdbst.TEXT, rbst.NAME_TYPE) AS R_BILLING_STATUS_DESC,
  COALESCE(rdbt.TEXT, rbt.NAME_TYPE) AS R_BILLING_TYPE_DESC,
  r.TOTAL_AMOUNT AS R_TOTAL_AMOUNT,
  r.ID_CONTRACTED_SERVICE AS R_ID_CONTRACTED_SERVICE,
  CASE WHEN r.ID_BILL IS NULL THEN NULL WHEN r.ID_CONTRACTED_SERVICE = fb.ID_CONTRACTED_SERVICE THEN 1 ELSE 0 END AS R_SAME_CS
FROM fb
CROSS APPLY (
  SELECT STRING_AGG(bc.COD_CONCEPT + ' ' + CAST(CAST(bc.CONCEPT_AMOUNT AS DECIMAL(18, 3)) AS VARCHAR(30)), '; ') AS REG_CONCEPTS,
         SUM(bc.CONCEPT_AMOUNT) AS REG_AMOUNT
  FROM GCCOM_BILLING_CONCEPT bc WITH (NOLOCK)
  WHERE bc.ID_BILL = fb.ID_BILL AND bc.COD_CONCEPT IN ({conc}) AND bc.CONCEPT_AMOUNT <> 0
) rc
LEFT JOIN {s}.GCCOM_BILL r WITH (NOLOCK)
       ON r.ID_REG_BILL = fb.ID_BILL
      AND ISNULL(r.BILLING_TYPE, '') <> '{BILLING_TYPE_CREDIT_NOTE}'
      AND r.BILLING_STATUS NOT IN ({excl})
JOIN {s}.GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = fb.ID_PAYMENT_FORM
LEFT JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = fb.ID_SECTOR_SUPPLY
LEFT JOIN {s}.GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = fb.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY dbp ON dbp.ID = bp.PERIOD_NAME_XI18N AND dbp.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCOM_BILL_STATUS bst ON bst.COD_DEVELOP = fb.BILLING_STATUS
LEFT JOIN GCTS_DICTIONARY dbst ON dbst.ID = bst.NAME_TYPE_XI18N AND dbst.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCOM_BILLING_TYPE bt ON bt.COD_DEVELOP = fb.BILLING_TYPE
LEFT JOIN GCTS_DICTIONARY dbt ON dbt.ID = bt.NAME_TYPE_XI18N AND dbt.LOCALE = 'EN'
LEFT JOIN {s}.GCCOM_BILLING_PERIOD rbp ON rbp.ID_BILLING_PERIOD = r.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY rdbp ON rdbp.ID = rbp.PERIOD_NAME_XI18N AND rdbp.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCOM_BILL_STATUS rbst ON rbst.COD_DEVELOP = r.BILLING_STATUS
LEFT JOIN GCTS_DICTIONARY rdbst ON rdbst.ID = rbst.NAME_TYPE_XI18N AND rdbst.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCOM_BILLING_TYPE rbt ON rbt.COD_DEVELOP = r.BILLING_TYPE
LEFT JOIN GCTS_DICTIONARY rdbt ON rdbt.ID = rbt.NAME_TYPE_XI18N AND rdbt.LOCALE = 'EN'
ORDER BY fb.ID_BILLING_PERIOD DESC, pf.REFERENCE, fb.ID_BILL, r.ID_BILL
"""


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def group_rows(flat: list[dict]) -> tuple[list[dict], list[dict]]:
    """flat rows (lower-case keys) -> (bills, periods).
    bills: one per first bill with `regularized` = list of regularized bills.
    periods: summary per billing period (count, accounts, per service)."""
    bills: dict[str, dict] = {}
    for r in flat:
        key = str(r.get("id_bill"))
        b = bills.get(key)
        if b is None:
            b = {k: v for k, v in r.items() if not k.startswith("r_")}
            b["offered_service"] = OFFERED_NAMES.get(str(r.get("id_offered_service")), str(r.get("id_offered_service")))
            b["regularized"] = []
            bills[key] = b
        if r.get("r_id_bill") not in (None, ""):
            b["regularized"].append({k[2:]: v for k, v in r.items() if k.startswith("r_")})
    out = list(bills.values())
    for b in out:
        b["regularized_count"] = len(b["regularized"])
        b["regularized_total"] = round(sum(_num(x.get("total_amount")) for x in b["regularized"]), 3)
        b["other_cs_count"] = sum(1 for x in b["regularized"] if str(x.get("same_cs")) == "0")
    periods: dict[str, dict] = {}
    for b in out:
        pk = str(b.get("id_billing_period"))
        p = periods.setdefault(pk, {
            "id_billing_period": pk, "billing_period_desc": b.get("billing_period_desc"),
            "count": 0, "accounts": set(), "electricity": 0, "water": 0, "sanitary": 0,
            "reg_amount": 0.0, "regularized_bills": 0, "no_regularized": 0,
        })
        p["count"] += 1
        p["accounts"].add(b.get("reference"))
        svc = b["offered_service"].lower()
        if svc in ("electricity", "water", "sanitary"):
            p[svc] += 1
        p["reg_amount"] += _num(b.get("reg_amount"))
        p["regularized_bills"] += b["regularized_count"]
        if not b["regularized_count"]:
            p["no_regularized"] += 1
    plist = sorted(periods.values(), key=lambda x: x["id_billing_period"], reverse=True)
    for p in plist:
        p["account_count"] = len(p.pop("accounts"))
        p["reg_amount"] = round(p["reg_amount"], 3)
    return out, plist
