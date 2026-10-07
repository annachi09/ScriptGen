"""
Wrong Bill - Case 3: Sanitary not charged while water was consumed
(RJ, 2026-10-01).

RJ: "a new case for wrong bill, filter by date or billing period, a sanitary
bill which is not Billing service having tariff ... JOIN GCCOM_BILLING_SERVICE
bs ON bs.ID_BILLING_SERVICE = b1.ID_BILLING_SERVICE AND bs.ID_FARE NOT IN
(10000000021, 10000000022), but the water service has a consumption > 0 - to
get this go to the Billing_concept and check the water consumption concept and
that one should not be zero".

Confirmed with RJ (AskUserQuestion): the WRONG part is the sanitary charge
being 0 - a charging tariff + water consumed is otherwise the normal case.

- Sanitary bill: GCCOM_BILL on a Sanitary (190) contracted service whose
  billing service fare is NOT 10000000021 (W-SAN02 Sanitary Zero tariff) /
  10000000022 (W-SAN03 Sanitary Charges Subsidy) - live, that leaves
  10000000020 (W-SAN01 Sanitary Charges). Not a credit note (TIPFAC0011),
  not Cancelled (ESTFAC0007) / Rebilled (ESTFAC0042).
- Its SANITARY concept amount is 0 or the concept is missing.
- Water bill: same ID_PAYMENT_FORM, ID_BILLING_PERIOD and BILLING_DATE, on a
  Water (19) contracted service, same type/status exclusions, whose
  CONCSMO003 "Water consumption" CONCEPT_AMOUNT sum is > 0 (the concept's
  CALCULATION_BASE is empty live, so the amount is the usable value).

Filter: one or more billing periods (default = the current one) and/or a
bill CREATION date range (CREATE_DATE). Live 2026-10-01, period 10000000238 (10-October 2026): 189 bills, ~8s.

Pure logic: builds SELECT text only.
"""
from __future__ import annotations

import datetime as _dt

SCHEMA = "OUC_COMMON_ADMIN"
OFFERED_SANITARY = 190
OFFERED_WATER = 19
EXCLUDED_FARES = (10000000021, 10000000022)  # Sanitary Zero tariff, Sanitary Charges Subsidy
BILLING_TYPE_CREDIT_NOTE = "TIPFAC0011"
EXCLUDED_STATUSES = ("ESTFAC0007", "ESTFAC0042")  # Cancelled, Rebilled
SANITARY_CONCEPT = "SANITARY"
WATER_CONSUMPTION_CONCEPT = "CONCSMO003"
MAX_DATE_RANGE_DAYS = 92


def _date(s: str) -> str:
    return _dt.date.fromisoformat(str(s)[:10]).isoformat()


MAX_PERIODS = 12


def _id_range_cte(f: str, t: str) -> str:
    """CTEs ending in `idr` (ID_FROM, ID_TO): the ID_BILL window that covers
    bills created between f and t (inclusive dates)."""
    s = SCHEMA
    return f"""mm AS (SELECT MIN(ID_BILL) AS LO, MAX(ID_BILL) AS HI FROM {s}.GCCOM_BILL WITH (NOLOCK)),
dg AS (SELECT v FROM (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) x(v)),
nums AS (SELECT a.v + 10 * b.v + 100 * c.v + 1000 * e.v AS I
         FROM dg a CROSS JOIN dg b CROSS JOIN dg c CROSS JOIN (SELECT v FROM dg WHERE v < 2) e),
probe AS (
  SELECT pb.ID_BILL, pb.CREATE_DATE
  FROM nums CROSS JOIN mm
  CROSS APPLY (SELECT TOP 1 ID_BILL, CREATE_DATE FROM {s}.GCCOM_BILL WITH (NOLOCK)
               WHERE ID_BILL >= mm.LO + CAST((mm.HI - mm.LO) * nums.I / 2000.0 AS NUMERIC(15, 0))
               ORDER BY ID_BILL) pb
),
idr AS (
  SELECT ISNULL(MAX(CASE WHEN p.CREATE_DATE < '{f}' THEN p.ID_BILL END), MIN(mm.LO)) AS ID_FROM,
         ISNULL(MIN(CASE WHEN p.CREATE_DATE >= DATEADD(DAY, 1, CAST('{t}' AS DATE)) THEN p.ID_BILL END), MIN(mm.HI)) AS ID_TO
  FROM probe p CROSS JOIN mm
),
"""


def validate_dates(date_from: str, date_to: str) -> tuple[str, str]:
    f, t = _date(date_from), _date(date_to)
    if f > t:
        raise ValueError("Creation date from must be on or before date to.")
    if (_dt.date.fromisoformat(t) - _dt.date.fromisoformat(f)).days > MAX_DATE_RANGE_DAYS:
        raise ValueError(f"Creation date range is limited to {MAX_DATE_RANGE_DAYS} days.")
    return f, t


def build_id_range_query(date_from: str, date_to: str) -> str:
    """Step 1 for a creation-date scope: returns one row (ID_FROM, ID_TO) =
    the ID_BILL window covering bills created in the range. Run it first and
    pass the two numbers to build_query(id_from=..., id_to=...) - live, the
    window as a CTE inside the big query was not used as a seek once the
    lookup joins were added (>90s), while literal bounds keep it fast."""
    f, t = validate_dates(date_from, date_to)
    cte = _id_range_cte(f, t).rstrip().rstrip(",")
    return f"WITH {cte}\nSELECT ID_FROM, ID_TO FROM idr"


def build_query(*, id_billing_period: int | None = None,
                billing_periods: list[int] | None = None,
                date_from: str | None = None, date_to: str | None = None,
                id_from: int | None = None, id_to: int | None = None) -> str:
    """Scope = one or more billing periods AND/OR a bill CREATION date range
    (GCCOM_BILL.CREATE_DATE, inclusive YYYY-MM-DD) - RJ 2026-10-01: "add
    option to select multiple billing period and date range of billing
    creations date". At least one of the two is required; both = AND."""
    periods = [int(p) for p in (billing_periods or [])]
    if id_billing_period is not None:
        periods.append(int(id_billing_period))
    periods = sorted(set(periods))
    if len(periods) > MAX_PERIODS:
        raise ValueError(f"Pick at most {MAX_PERIODS} billing periods.")
    conds = []
    id_range_cte = ""
    id_range_join = ""
    if periods:
        conds.append(f"b1.ID_BILLING_PERIOD IN ({', '.join(str(p) for p in periods)})")
    if date_from or date_to:
        if not (date_from and date_to):
            raise ValueError("Give both creation dates (from and to).")
        f, t = validate_dates(date_from, date_to)
        # GCCOM_BILL.CREATE_DATE has no index (a plain filter scans the whole
        # table, >45s live). ID_BILL grows with creation time, so ~2,000
        # primary-key probes find an ID_BILL window around the dates first
        # (live: 7 days -> ~4.7s), then CREATE_DATE is filtered exactly
        # inside that window. Preferred: caller runs build_id_range_query()
        # and passes literal id_from/id_to; otherwise the window is computed
        # inline as a CTE (slower plan once the lookup joins are present).
        if id_from is not None and id_to is not None:
            conds.append(f"b1.ID_BILL BETWEEN {int(id_from)} AND {int(id_to)}")
        else:
            id_range_cte = _id_range_cte(f, t)
            id_range_join = "\n  CROSS JOIN idr"
            conds.append("b1.ID_BILL BETWEEN idr.ID_FROM AND idr.ID_TO")
        conds.append(f"b1.CREATE_DATE >= '{f}' AND b1.CREATE_DATE < DATEADD(DAY, 1, CAST('{t}' AS DATE))")
    if not conds:
        raise ValueError("Pick at least one billing period or a creation-date range.")
    scope = " AND ".join(conds)
    s = SCHEMA
    fares = ", ".join(str(int(x)) for x in EXCLUDED_FARES)
    excl = ", ".join(f"'{x}'" for x in EXCLUDED_STATUSES)
    return f"""
WITH {id_range_cte}san AS (
  SELECT b1.ID_BILL, b1.BILL_NUMBER, b1.ID_PAYMENT_FORM, b1.ID_BILLING_PERIOD, b1.BILLING_DATE, b1.CREATE_DATE, b1.BILLING_STATUS, b1.BILLING_TYPE,
         b1.ID_BILLING_SERVICE, bs.ID_FARE AS BS_FARE, b1.TOTAL_AMOUNT, cs1.ID_SECTOR_SUPPLY
  FROM {s}.GCCOM_BILL b1 WITH (NOLOCK)
  JOIN {s}.GCCOM_CONTRACTED_SERVICE cs1 WITH (NOLOCK)
       ON cs1.ID_CONTRACTED_SERVICE = b1.ID_CONTRACTED_SERVICE AND cs1.ID_OFFERED_SERVICE = {OFFERED_SANITARY}
  JOIN {s}.GCCOM_BILLING_SERVICE bs WITH (NOLOCK)
       ON bs.ID_BILLING_SERVICE = b1.ID_BILLING_SERVICE AND bs.ID_FARE NOT IN ({fares}){id_range_join}
  WHERE {scope}
    AND ISNULL(b1.BILLING_TYPE, '') <> '{BILLING_TYPE_CREDIT_NOTE}'
    AND b1.BILLING_STATUS NOT IN ({excl})
),
zero AS (
  -- RJ 2026-10-01 (bill 10683144082608010032): the tariff is also checked on
  -- the BILLING CONCEPT (bc.ID_FARE = the tariff actually used for this bill)
  -- - that bill's billing service is fare 20 today but it was billed on 22.
  -- The billing-service fare stays as a fast pre-filter (a concept-only
  -- version had to read every sanitary bill and timed out at 120s live).
  SELECT s.ID_BILL, ISNULL(SUM(bc.CONCEPT_AMOUNT), 0) AS SANITARY_AMOUNT, COUNT(bc.ID_BILLING_CONCEPT) AS SANITARY_CONCEPT_COUNT,
         MAX(bc.ID_FARE) AS CONCEPT_FARE
  FROM san s
  LEFT JOIN GCCOM_BILLING_CONCEPT bc WITH (NOLOCK) ON bc.ID_BILL = s.ID_BILL AND bc.COD_CONCEPT = '{SANITARY_CONCEPT}'
  GROUP BY s.ID_BILL
  HAVING ISNULL(SUM(bc.CONCEPT_AMOUNT), 0) = 0
     AND SUM(CASE WHEN bc.ID_FARE IN ({fares}) THEN 1 ELSE 0 END) = 0
     -- RJ 2026-10-05 (compared with the analyst query): a SANITARY concept
     -- must exist - bills with none were fee-refund bills (SCFEE only).
     AND COUNT(bc.ID_BILLING_CONCEPT) > 0
)
SELECT
  pf.REFERENCE AS REFERENCE,
  ss.NISS AS NISS,
  s.ID_BILLING_PERIOD AS ID_BILLING_PERIOD,
  COALESCE(dbp.TEXT, bp.DESCRIPTION) AS BILLING_PERIOD_DESC,
  s.BILLING_DATE AS BILLING_DATE,
  s.CREATE_DATE AS CREATE_DATE,
  s.ID_BILL AS SANITARY_ID_BILL,
  s.BILL_NUMBER AS SANITARY_BILL_NUMBER,
  s.BILLING_STATUS AS BILLING_STATUS,
  COALESCE(dbst.TEXT, bst.NAME_TYPE) AS BILLING_STATUS_DESC,
  s.BILLING_TYPE AS BILLING_TYPE,
  COALESCE(dbt.TEXT, bt.NAME_TYPE) AS BILLING_TYPE_DESC,
  s.ID_BILLING_SERVICE AS ID_BILLING_SERVICE,
  COALESCE(z.CONCEPT_FARE, s.BS_FARE) AS ID_FARE,
  COALESCE(dfa.TEXT, fa.NAME_TYPE) AS FARE_NAME,
  CASE WHEN z.CONCEPT_FARE IS NULL THEN 'Billing service' ELSE 'Billing concept' END AS FARE_SOURCE,
  s.BS_FARE AS BILLING_SERVICE_FARE,
  z.SANITARY_AMOUNT AS SANITARY_AMOUNT,
  z.SANITARY_CONCEPT_COUNT AS SANITARY_CONCEPT_COUNT,
  s.TOTAL_AMOUNT AS SANITARY_TOTAL_AMOUNT,
  wc.WATER_ID_BILL AS WATER_ID_BILL,
  wc.WATER_BILL_COUNT AS WATER_BILL_COUNT,
  wc.WATER_CONSUMPTION AS WATER_CONSUMPTION
FROM san s
JOIN zero z ON z.ID_BILL = s.ID_BILL
CROSS APPLY (
  SELECT SUM(bc.CONCEPT_AMOUNT) AS WATER_CONSUMPTION, MIN(w.ID_BILL) AS WATER_ID_BILL, COUNT(DISTINCT w.ID_BILL) AS WATER_BILL_COUNT
  FROM {s}.GCCOM_BILL w WITH (NOLOCK)
  JOIN {s}.GCCOM_CONTRACTED_SERVICE wcs WITH (NOLOCK)
       ON wcs.ID_CONTRACTED_SERVICE = w.ID_CONTRACTED_SERVICE AND wcs.ID_OFFERED_SERVICE = {OFFERED_WATER}
  JOIN GCCOM_BILLING_CONCEPT bc WITH (NOLOCK) ON bc.ID_BILL = w.ID_BILL AND bc.COD_CONCEPT = '{WATER_CONSUMPTION_CONCEPT}'
  WHERE w.ID_PAYMENT_FORM = s.ID_PAYMENT_FORM
    AND w.ID_BILLING_PERIOD = s.ID_BILLING_PERIOD
    AND w.BILLING_DATE = s.BILLING_DATE
    AND ISNULL(w.BILLING_TYPE, '') <> '{BILLING_TYPE_CREDIT_NOTE}'
    AND w.BILLING_STATUS NOT IN ({excl})
) wc
JOIN {s}.GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = s.ID_PAYMENT_FORM
LEFT JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = s.ID_SECTOR_SUPPLY
LEFT JOIN {s}.GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = s.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY dbp ON dbp.ID = bp.PERIOD_NAME_XI18N AND dbp.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCOM_BILL_STATUS bst ON bst.COD_DEVELOP = s.BILLING_STATUS
LEFT JOIN GCTS_DICTIONARY dbst ON dbst.ID = bst.NAME_TYPE_XI18N AND dbst.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCOM_BILLING_TYPE bt ON bt.COD_DEVELOP = s.BILLING_TYPE
LEFT JOIN GCTS_DICTIONARY dbt ON dbt.ID = bt.NAME_TYPE_XI18N AND dbt.LOCALE = 'EN'
LEFT JOIN {s}.GCCOM_FARE fa ON fa.ID_FARE = COALESCE(z.CONCEPT_FARE, s.BS_FARE)
LEFT JOIN GCTS_DICTIONARY dfa ON dfa.ID = fa.NAME_TYPE_XI18N AND dfa.LOCALE = 'EN'
WHERE wc.WATER_CONSUMPTION > 0
  -- RJ 2026-10-05, taken from the analyst query: the premise must still be
  -- connected to the sanitary network (a disconnected premise is a valid 0)
  AND EXISTS (
    SELECT 1 FROM GCCOM_SUPPLY sp WITH (NOLOCK)
    JOIN EWA_GCGT_NS_SANITARY_PREMISE nsp WITH (NOLOCK)
         ON nsp.ID_PREMISE = sp.ID_PREMISE AND nsp.TO_DATE IS NULL AND nsp.ACTION_TYPE = 'Connected'
    WHERE sp.ID_SUPPLY = ss.ID_SUPPLY)
  -- ... and the billing service must not have been on the Zero / Subsidy
  -- tariff up to the billing date (GCCOM_BILLING_SERVICE_HIST).
  AND NOT EXISTS (
    SELECT 1 FROM GCCOM_BILLING_SERVICE_HIST hst WITH (NOLOCK)
    WHERE hst.ID_BILLING_SERVICE = s.ID_BILLING_SERVICE AND hst.ID_FARE IN ({fares})
    GROUP BY hst.ID_FARE
    HAVING s.BILLING_DATE <= MAX(hst.END_DATE))
ORDER BY wc.WATER_CONSUMPTION DESC
"""
