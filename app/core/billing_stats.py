"""Billing Statistics dashboard (RJ 2026-11-04).

RJ: "create a new Dashboard, Billing Statistics. Focused on accounts. counting how many by Billing
Status, add filter and KPI. lets have November 2026 (use the current date to determine which billing
period is displayed), also have a quick card for Subsidized, normal customer, and ones enrolled in
fixed billing using table ewa_gccom_fixed_billing. Will be good to have also some category per
tariff and per signal in GCCOM_PAYMENT_FORM. By default lets focus on Service bills, but there will
be filter of other bill type." + "make very good dashboard good color modern".

Live schema (checked 2026-11-04):
- OUC_COMMON_ADMIN.GCCOM_BILL: ID_PAYMENT_FORM, ID_CONTRACTED_SERVICE, ID_BILLING_PERIOD, BILL_TYPE,
  BILLING_STATUS, TOTAL_AMOUNT, PENDING_AMOUNT.
- Bill types = GCGT_ATTRIBUTE_VALUES master GENBILLTYP (TFGEN00001 = Service, default).
- Bill statuses = OUC_ADMIN.GCCOM_BILL_STATUS (COD_DEVELOP, NAME_TYPE_XI18N -> GCTS_DICTIONARY EN).
- Current period = GCCOM_BILLING_PERIOD with today BETWEEN INITIAL_DATE AND END_DATE
  (2026-11-04 -> 10000000239 "11-November 2026").
- Tariff = GCCOM_BILLING_SERVICE.ID_FARE of the bill's contracted service -> GCCOM_FARE.RATE_CODE.
- Signal = GCCOM_PAYMENT_FORM.COD_TYPE (COD00..COD14 - no description table found live).
- Subsidized = account with an active contracted service on a Bahraini First Account tariff
  (fix_billing_campaign.SUBSIDY_FARES: E-D2 / W-D2 / E-D2-NM - same rule as FIX BILLING tabs).
- Fixed billing = OUC_ADMIN.EWA_GCCOM_FIXED_BILLING with IND_ACTIVE = 1 (enrolled; overlaps the other
  two cards, it is not a third exclusive bucket).

Shape: ONE ROW PER COMBINATION of account attributes (status set, tariff set, signal, subsidized,
fixed billing) with the number of DISTINCT accounts, bills and amounts. Status / tariff sets are
"|"-joined sorted codes, so the page can filter "accounts having status X" or "tariff Y" and still
count each account once (an account with an electricity bill Invoiced and a water bill Invoicing
is in both status bars, but only once in the totals).
"""
from __future__ import annotations

from datetime import datetime

from app.core import fix_billing_campaign as fbc

SERVICE_BILL_TYPE = "TFGEN00001"
BILL_TYPE_MASTER = "GENBILLTYP"
FIXED_BILLING_TABLE = "OUC_ADMIN.EWA_GCCOM_FIXED_BILLING"
EXCLUDED_CS_STATUSES = ("ESTSC00001", "ESTSC00004", "ESTSC00005")   # Pending / Terminated / Cancelled


def _bill_types_sql(bill_types: list[str] | None) -> str:
    types = [t for t in (bill_types or [SERVICE_BILL_TYPE]) if t]
    for t in types:
        if not t.replace("_", "").isalnum() or len(t) > 20:
            raise ValueError(f"invalid bill type {t!r}")
    if "ALL" in types:
        return ""
    return "AND b.BILL_TYPE IN (" + ", ".join(f"'{t}'" for t in types) + ")"


def build_current_period_query() -> str:
    return """
SELECT TOP 1 p.ID_BILLING_PERIOD, COALESCE(d.TEXT, p.DESCRIPTION) AS NAME
FROM GCCOM_BILLING_PERIOD p
LEFT JOIN GCTS_DICTIONARY d ON d.ID = p.PERIOD_NAME_XI18N AND d.LOCALE = 'EN'
WHERE CAST(GETDATE() AS date) BETWEEN CAST(p.INITIAL_DATE AS date) AND CAST(p.END_DATE AS date)
ORDER BY p.ID_BILLING_PERIOD DESC;
"""


def build_periods_query(around: int, before: int = 12, after: int = 1) -> str:
    a = int(around)
    return f"""
SELECT p.ID_BILLING_PERIOD, COALESCE(d.TEXT, p.DESCRIPTION) AS NAME,
       CONVERT(varchar(10), p.INITIAL_DATE, 23) AS INITIAL_DATE, CONVERT(varchar(10), p.END_DATE, 23) AS END_DATE
FROM GCCOM_BILLING_PERIOD p
LEFT JOIN GCTS_DICTIONARY d ON d.ID = p.PERIOD_NAME_XI18N AND d.LOCALE = 'EN'
WHERE p.ID_BILLING_PERIOD BETWEEN {a - int(before)} AND {a + int(after)}
ORDER BY p.ID_BILLING_PERIOD DESC;
"""


def build_lookups_query() -> str:
    """Bill status + bill type names (EN) in one result set: KIND, CODE, NAME."""
    return f"""
SELECT 'STATUS' AS KIND, st.COD_DEVELOP AS CODE, COALESCE(d.TEXT, st.NAME_TYPE) AS NAME
FROM OUC_ADMIN.GCCOM_BILL_STATUS st
LEFT JOIN GCTS_DICTIONARY d ON d.ID = st.NAME_TYPE_XI18N AND d.LOCALE = 'EN'
UNION ALL
SELECT 'BILLTYPE', v.COD_DEVELOP, COALESCE(d.TEXT, v.NAME_TYPE)
FROM OUC_COMMON_ADMIN.GCGT_ATTRIBUTE_VALUES v
LEFT JOIN GCTS_DICTIONARY d ON d.ID = v.NAME_TYPE_XI18N AND d.LOCALE = 'EN'
WHERE v.COD_MASTER_ATTRIBUTE = '{BILL_TYPE_MASTER}';
"""


def _statuses_sql(statuses: list[str] | None) -> str:
    """RJ 2026-11-04 (speed): the bill-status filter runs in SQL. None = default Invoiced only;
    [] / ["ALL"] = every status."""
    sts = [s for s in (statuses if statuses is not None else [DEFAULT_STATUS]) if s]
    for s in sts:
        if not s.replace("_", "").isalnum() or len(s) > 20:
            raise ValueError(f"invalid bill status {s!r}")
    if not sts or "ALL" in sts:
        return ""
    return "AND b.BILLING_STATUS IN (" + ", ".join(f"'{s}'" for s in sts) + ")"


DEFAULT_STATUS = "ESTFAC0005"   # Invoiced


def build_stats_query(billing_period: int, bill_types: list[str] | None = None,
                      statuses: list[str] | None = None, per_account: bool = False) -> str:
    """per_account=True (RJ 2026-11-04: "add a last table to list the account and I want to download
    only the filter account") returns one row per account (ACCOUNT = GCCOM_PAYMENT_FORM.REFERENCE)
    with the same attributes instead of the grouped combinations; the route filters it."""
    # RJ 2026-11-04 (later): "the billing statistics is not working timing out, better optimize".
    #  - status filter pushed into SQL (default Invoiced only) - pulling every bill of the period
    #    was the slow part; the status chips' counts come from build_summary_query (cached);
    #  - Subsidized check only probes the accounts in #a (was: every contracted service on a
    #    BFA tariff in the whole DB);
    #  - temp tables indexed; OPTION (RECOMPILE) on the big steps.
    bp = int(billing_period)
    bt = _bill_types_sql(bill_types)
    st = _statuses_sql(statuses)
    fares = ", ".join(str(f) for f in fbc.SUBSIDY_FARES)
    cs_excl = ", ".join(f"'{s}'" for s in EXCLUDED_CS_STATUSES)
    sig = "COALESCE(NULLIF(pft.DESCRIPTION, ''), pf.COD_TYPE, '(none)')"
    sub_c = "CASE WHEN sub.ID_PAYMENT_FORM IS NOT NULL THEN 1 ELSE 0 END"
    fix_c = "CASE WHEN fb.ID_PAYMENT_FORM IS NOT NULL THEN 1 ELSE 0 END"
    if per_account:
        select_sql = (f"SELECT pf.REFERENCE AS ACCOUNT, a.STATUSES, ISNULL(a.TARIFFS, '(none)') AS TARIFFS, {sig} AS SIGNAL,\n"
                      f"       {sub_c} AS SUBSIDIZED, {fix_c} AS FIXED_BILLING, a.BILLS, a.AMOUNT, a.PENDING")
        group_sql = "ORDER BY pf.REFERENCE"
    else:
        select_sql = (f"SELECT a.STATUSES, ISNULL(a.TARIFFS, '(none)') AS TARIFFS, {sig} AS SIGNAL,\n"
                      f"       {sub_c} AS SUBSIDIZED, {fix_c} AS FIXED_BILLING,\n"
                      "       COUNT(*) AS ACCOUNTS, SUM(a.BILLS) AS BILLS, SUM(a.AMOUNT) AS AMOUNT, SUM(a.PENDING) AS PENDING")
        group_sql = f"GROUP BY a.STATUSES, ISNULL(a.TARIFFS, '(none)'), {sig}, {sub_c}, {fix_c}"
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#b') IS NOT NULL DROP TABLE #b;
IF OBJECT_ID('tempdb..#t') IS NOT NULL DROP TABLE #t;
IF OBJECT_ID('tempdb..#a') IS NOT NULL DROP TABLE #a;
IF OBJECT_ID('tempdb..#sub') IS NOT NULL DROP TABLE #sub;

-- bills of the period (selected bill types + statuses)
SELECT b.ID_PAYMENT_FORM, b.ID_CONTRACTED_SERVICE, b.BILLING_STATUS,
       b.TOTAL_AMOUNT, b.PENDING_AMOUNT
INTO #b
FROM OUC_COMMON_ADMIN.GCCOM_BILL b WITH (NOLOCK)
WHERE b.ID_BILLING_PERIOD = {bp} {bt} {st}
OPTION (RECOMPILE);
CREATE CLUSTERED INDEX cx ON #b (ID_PAYMENT_FORM);

-- tariff per contracted service of those bills - RJ 2026-11-04: "show the description only of the
-- tariff and signal" -> GCCOM_FARE.NAME_TYPE (e.g. "Domestic - Bahraini First Account"; E-D2 and
-- W-D2 share that name, so they count as one tariff)
SELECT DISTINCT bs.ID_CONTRACTED_SERVICE,
       CAST(COALESCE(NULLIF(f.NAME_TYPE, ''), NULLIF(f.DESCRIPTION, ''), f.RATE_CODE, CAST(bs.ID_FARE AS varchar(20))) AS varchar(200)) AS RATE_CODE
INTO #t
FROM (SELECT DISTINCT ID_CONTRACTED_SERVICE FROM #b) x
JOIN GCCOM_BILLING_SERVICE bs WITH (NOLOCK) ON bs.ID_CONTRACTED_SERVICE = x.ID_CONTRACTED_SERVICE
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_FARE f ON f.ID_FARE = bs.ID_FARE;

-- one row per account: status set, tariff set, counts, amounts (grouped aggregates - a correlated
-- STRING_AGG per account ran > 2 min live)
SELECT a.ID_PAYMENT_FORM, a.BILLS, a.AMOUNT, a.PENDING, s.STATUSES, t.TARIFFS
INTO #a
FROM (SELECT ID_PAYMENT_FORM, COUNT(*) AS BILLS, SUM(TOTAL_AMOUNT) AS AMOUNT, SUM(PENDING_AMOUNT) AS PENDING
      FROM #b GROUP BY ID_PAYMENT_FORM) a
JOIN (SELECT x.ID_PAYMENT_FORM, STRING_AGG(x.BILLING_STATUS, '|') WITHIN GROUP (ORDER BY x.BILLING_STATUS) AS STATUSES
      FROM (SELECT DISTINCT ID_PAYMENT_FORM, BILLING_STATUS FROM #b) x GROUP BY x.ID_PAYMENT_FORM) s
  ON s.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM
LEFT JOIN (SELECT y.ID_PAYMENT_FORM, STRING_AGG(y.RATE_CODE, '|') WITHIN GROUP (ORDER BY y.RATE_CODE) AS TARIFFS
           FROM (SELECT DISTINCT b3.ID_PAYMENT_FORM, tt.RATE_CODE FROM #b b3
                 JOIN #t tt ON tt.ID_CONTRACTED_SERVICE = b3.ID_CONTRACTED_SERVICE) y GROUP BY y.ID_PAYMENT_FORM) t
  ON t.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM;
CREATE UNIQUE CLUSTERED INDEX cx ON #a (ID_PAYMENT_FORM);

-- subsidized: only the accounts of #a are probed
SELECT DISTINCT cs.ID_PAYMENT_FORM INTO #sub
FROM #a a
JOIN GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK) ON cs.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM
JOIN GCCOM_BILLING_SERVICE bs WITH (NOLOCK) ON bs.ID_CONTRACTED_SERVICE = cs.ID_CONTRACTED_SERVICE
WHERE bs.ID_FARE IN ({fares}) AND cs.STATUS NOT IN ({cs_excl})
OPTION (RECOMPILE);

-- signal description: OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM_TYPE (COD_DEVELOP = COD_TYPE, DESCRIPTION
-- e.g. "6 - Limited Income Family") - RJ 2026-11-04
{select_sql}
FROM #a a
LEFT JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM_TYPE pft ON pft.COD_DEVELOP = pf.COD_TYPE
LEFT JOIN #sub sub ON sub.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM
LEFT JOIN (SELECT DISTINCT ID_PAYMENT_FORM FROM {FIXED_BILLING_TABLE} WITH (NOLOCK) WHERE IND_ACTIVE = 1) fb
       ON fb.ID_PAYMENT_FORM = a.ID_PAYMENT_FORM
{group_sql}
OPTION (RECOMPILE);

DROP TABLE #b; DROP TABLE #t; DROP TABLE #a; DROP TABLE #sub;
"""


def build_summary_query(billing_period: int) -> str:
    """RJ 2026-11-04 (speed): bills per bill type x status in the period - feeds the bill-type and
    status chips (one light GROUP BY, cached by the route; replaces the COUNT(DISTINCT) per bill
    type, which read every bill of the period)."""
    return f"""
SELECT b.BILL_TYPE, b.BILLING_STATUS, COUNT(*) AS BILLS
FROM OUC_COMMON_ADMIN.GCCOM_BILL b WITH (NOLOCK)
WHERE b.ID_BILLING_PERIOD = {int(billing_period)}
GROUP BY b.BILL_TYPE, b.BILLING_STATUS;
"""


# ---------------------------------------------------------------------------
# Tab 2 "Operations" (RJ 2026-11-04): daily bills invoiced, notices sent, e-mail / SMS letters
# generated + sent, payments received + processed - RJ's query, optimised:
#  - date filters are range predicates (col >= d1 AND col < d2+1) instead of CAST(col AS DATE)
#    BETWEEN, so the date indexes can be used;
#  - GCCB_EVENT_LETTERS / EWA_GCCB_EXTERNAL_COLLECTIONS have no date index -> an ID floor is found
#    first with a few primary-key probes (step back from MAX(ID) until the row date is older than
#    d1 - 3 days; RJ suggested a fixed floor ID_LETTER_EVENT > 75938612, the probe finds a much
#    tighter one: 82,296,642 for November) and the scans start there;
#  - events / send-service rows are staged into temp tables, then joined (direct join ~51 s);
#  - OPTION (RECOMPILE) so the plan uses the real floor value;
#  - RJ's event counts used COUNT(CASE ... THEN 1 ELSE 0 END), which counts every row (COUNT
#    counts non-NULL, 0 included) - now SUM(...) so generated / sent are real counts.
# Live 2026-11-04 (Nov 1-4): ~70 s (notice ~34 s and send-service ~30 s are the slow parts;
# payments 79 s -> < 1 s, events 59 s -> < 2 s with the floors).
# ---------------------------------------------------------------------------
BILL_STATUS_INVOICED = "ESTFAC0005"
NOTICE_STATUS_SENT = "ESTAVI0003"
SENDING_EMAIL = "MOENV00004"
SENDING_SMS = "MOENV00006"


def build_operations_query(date_from: str, date_to: str) -> str:
    import re
    for d in (date_from, date_to):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d or "")):
            raise ValueError("dates must be YYYY-MM-DD")
    return f"""
SET NOCOUNT ON;
DECLARE @d1 date = '{date_from}', @d2 date = DATEADD(day, 1, CAST('{date_to}' AS date));
DECLARE @margin date = DATEADD(day, -3, @d1);

-- ID floors by primary-key probes (cheap seeks)
DECLARE @e numeric(20,0) = (SELECT MAX(ID_LETTER_EVENT) FROM OUC_COMMON_ADMIN.GCCB_EVENT_LETTERS WITH (NOLOCK)), @i int = 0, @dt datetime2;
WHILE @i < 500 BEGIN
  SET @e = @e - 200000; SET @i += 1;
  SELECT @dt = MAX(GENERATION_DATE) FROM OUC_COMMON_ADMIN.GCCB_EVENT_LETTERS WITH (NOLOCK) WHERE ID_LETTER_EVENT BETWEEN @e AND @e + 500;
  IF @e <= 0 OR (@dt IS NOT NULL AND @dt < @margin) BREAK;
END
DECLARE @p numeric(20,0) = (SELECT MAX(ID_PAYMENT) FROM OUC_COMMON_ADMIN.EWA_GCCB_EXTERNAL_COLLECTIONS WITH (NOLOCK)), @j int = 0, @pd datetime2;
WHILE @j < 1000 BEGIN
  SET @p = @p - 20000; SET @j += 1;
  SELECT @pd = MAX(PAYMENT_DATE) FROM OUC_COMMON_ADMIN.EWA_GCCB_EXTERNAL_COLLECTIONS WITH (NOLOCK) WHERE ID_PAYMENT BETWEEN @p AND @p + 200;
  IF @p <= 0 OR (@pd IS NOT NULL AND @pd < @margin) BREAK;
END

IF OBJECT_ID('tempdb..#ev') IS NOT NULL DROP TABLE #ev;
IF OBJECT_ID('tempdb..#ss') IS NOT NULL DROP TABLE #ss;
IF OBJECT_ID('tempdb..#ed') IS NOT NULL DROP TABLE #ed;
IF OBJECT_ID('tempdb..#pd') IS NOT NULL DROP TABLE #pd;

-- RJ 2026-11-04 (later): bills invoiced (BILL_NUMBER) + notices sent (ACCOUNT_NUMBER) removed on
-- RJ's request - the notice step was also the slowest (~34 s).
SELECT ev.ID_LETTER_EVENT, CAST(ev.GENERATION_DATE AS date) AS d, ev.IND_COMMUNICATE INTO #ev
FROM OUC_COMMON_ADMIN.GCCB_EVENT_LETTERS ev WITH (NOLOCK)
WHERE ev.ID_LETTER_EVENT > @e AND ev.GENERATION_DATE >= @d1 AND ev.GENERATION_DATE < @d2 OPTION (RECOMPILE);
CREATE CLUSTERED INDEX cx ON #ev (ID_LETTER_EVENT);

-- exact ID window of the events in range (the probe floor has a 3-day margin)
DECLARE @emin numeric(20,0), @emax numeric(20,0);
SELECT @emin = MIN(ID_LETTER_EVENT), @emax = MAX(ID_LETTER_EVENT) FROM #ev;

SELECT ss.ID_LETTER_EVENT, ss.SENDING_WAY_TYPE INTO #ss
FROM OUC_COMMON_ADMIN.GCCB_SS_EVENT_LETTER ss WITH (NOLOCK)
WHERE ss.ID_LETTER_EVENT BETWEEN @emin AND @emax
  AND ss.SENDING_WAY_TYPE IN ('{SENDING_EMAIL}', '{SENDING_SMS}') OPTION (RECOMPILE);

SELECT ev.d,
       SUM(CASE WHEN ss.SENDING_WAY_TYPE = '{SENDING_EMAIL}' THEN 1 ELSE 0 END) AS EMAIL_GENERATED,
       SUM(CASE WHEN ss.SENDING_WAY_TYPE = '{SENDING_EMAIL}' AND ev.IND_COMMUNICATE = 0 THEN 1 ELSE 0 END) AS EMAIL_SENT,
       SUM(CASE WHEN ss.SENDING_WAY_TYPE = '{SENDING_SMS}' THEN 1 ELSE 0 END) AS SMS_GENERATED,
       SUM(CASE WHEN ss.SENDING_WAY_TYPE = '{SENDING_SMS}' AND ev.IND_COMMUNICATE = 0 THEN 1 ELSE 0 END) AS SMS_SENT
INTO #ed
FROM #ev ev JOIN #ss ss ON ss.ID_LETTER_EVENT = ev.ID_LETTER_EVENT
GROUP BY ev.d;

SELECT CAST(c.PAYMENT_DATE AS date) AS d, COUNT(*) AS PAYMENT_RECEIVED,
       SUM(CASE WHEN c.IND_PROCESSED = 1 THEN 1 ELSE 0 END) AS PAYMENT_PROCESSED
INTO #pd
FROM OUC_COMMON_ADMIN.EWA_GCCB_EXTERNAL_COLLECTIONS c WITH (NOLOCK)
WHERE c.ID_PAYMENT > @p AND c.IND_CONFIRMED = 1 AND c.PAYMENT_DATE >= @d1 AND c.PAYMENT_DATE < @d2
GROUP BY CAST(c.PAYMENT_DATE AS date) OPTION (RECOMPILE);

SELECT CONVERT(varchar(10), x.d, 23) AS [DATE],
       ISNULL(ed.EMAIL_GENERATED, 0) AS EMAIL_GENERATED, ISNULL(ed.EMAIL_SENT, 0) AS EMAIL_SENT,
       ISNULL(ed.SMS_GENERATED, 0) AS SMS_GENERATED, ISNULL(ed.SMS_SENT, 0) AS SMS_SENT,
       ISNULL(pd.PAYMENT_RECEIVED, 0) AS PAYMENT_RECEIVED, ISNULL(pd.PAYMENT_PROCESSED, 0) AS PAYMENT_PROCESSED
FROM (SELECT d FROM #ed UNION SELECT d FROM #pd) x
LEFT JOIN #ed ed ON ed.d = x.d
LEFT JOIN #pd pd ON pd.d = x.d
ORDER BY x.d;

DROP TABLE #ev; DROP TABLE #ss; DROP TABLE #ed; DROP TABLE #pd;
"""


def generated_at() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")
