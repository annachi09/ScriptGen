"""
TNB CYCLE/DISC analysis: sector supplies that have a CYCLE reading and a
DISCONNECTION reading on the SAME reading date, where at least one of the
two is in read status "Terminated Not Billed".

RJ, 2026-09-25: "display all re_reading, which have a cycle reading and a
disconnection reading having the same reading date and one of them is in
status Terminated not billed. this will be 1 row grouped by id_sector
supply. So display NISS, BILLING PERIOD IN WORDS, details of cycle:
id_reading, Reading_type, Reading prev_date, Reading_date, Prev_value,
Value, reading_usage, corrected Usage, Ready Usage, Read_status, now same
columns for Disconnection reading. Add the filters by billing period and
niss".

Codes live-confirmed (2026-09-25) via this app's own Workspace against the
tunnel DB rather than assumed:
  - GCGT_RE_READING_TYPE: TIPTL00003 = Cycle, TIPTL00010 = Disconnection
  - GCGT_RE_READ_STATUS:  8000STSRED = Terminated Not Billed

Shape: ONE row per (cycle, disconnection) pair - the cycle reading's
columns and the disconnection reading's columns side by side (prefixed
c_ / d_), which is what "1 row grouped by id_sector_supply" asks for: the
two readings of a supply collapsed onto one line instead of two. The pair
is matched on id_sector_supply + reading_date + usage_type. usage_type is
part of the match on purpose: an electricity supply can carry several
registers (e.g. kWh and kW demand) each with its own cycle AND its own
disconnection reading on the same date - without usage_type every
register's cycle would be cross-joined with every register's
disconnection. Live check (2026-09-25): 5,567 pairs across 5,368 supplies,
so a small minority of supplies legitimately produce more than one row
(multiple registers, or the pattern recurring on another date).

Performance: the first draft (cycle JOIN disconnection, then filter on
"either is TNB") took ~46s against the tunnel DB. This version first
narrows to the (supply, date, usage_type) keys that have a TNB cycle or
disconnection reading at all (the `tnb` CTE - a small set, since TNB is a
rare status), then joins both readings from there: ~23s, same result set.

Billing period "in words" uses the same GCTS_DICTIONARY LOCALE='EN'
lookup as reading_validation.build_readings_query (COALESCE back to the
native description when there's no translation). Each side carries its
OWN billing period - live data shows ~15% of pairs have the cycle and
disconnection in different billing periods, which is itself likely
relevant to the analysis, so it's shown rather than hidden. The row's
headline billing period is the cycle's (falling back to the
disconnection's when the cycle has none).

Pure logic, same convention as every other app/core module: builds SELECT
text only; web/server.py executes it.
"""
from __future__ import annotations

from .sql_format import format_sql_literal

READING_TABLE = "gcgt_re_reading"

READING_TYPE_CYCLE = "TIPTL00003"
READING_TYPE_DISCONNECTION = "TIPTL00010"
READ_STATUS_TERMINATED_NOT_BILLED = "8000STSRED"

# "In contract" indicator (RJ, 2026-09-25): "add an indicator if the
# reading date is between a contracted service from date and end date
# which the status is not cancelled ... connect to gccom_contracted
# service using id_sector_supply". Live-confirmed: GCCOM_CONTRACTED_SERVICE
# lives in OUC_COMMON_ADMIN with FROM_DATE / END_DATE / STATUS /
# ID_SECTOR_SUPPLY, and GCCOM_CONTRACT_SERV_STATUS ESTSC00005 = Cancelled
# ("Anulado"). in_contract = 1 when ANY non-cancelled contracted service
# on the supply covers the reading date (FROM_DATE <= date, and END_DATE
# is empty or >= date - both ends inclusive).
CONTRACTED_SERVICE_TABLE = "OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE"
CONTRACT_STATUS_CANCELLED = "ESTSC00005"

# One side's column list (cycle or disconnection), as (sql expression
# template with {a} = reading alias / {p} = prefix-specific lookup
# aliases, output suffix). Built once so both sides are guaranteed to
# expose exactly the same columns in the same order.
_SIDE_COLUMNS = (
    ("{a}.id_reading", "id_reading"),
    ("COALESCE(dict_{p}rt.text, {p}rt.description)", "reading_type"),
    ("{a}.reading_prev_date", "reading_prev_date"),
    ("{a}.reading_date", "reading_date"),
    ("{a}.prev_value", "prev_value"),
    ("{a}.VALUE", "value"),
    ("{a}.reading_usage", "reading_usage"),
    ("{a}.corrected_usage", "corrected_usage"),
    ("{a}.ready_usage", "ready_usage"),
    ("COALESCE(dict_{p}st.text, {p}st.description)", "read_status"),
    ("{a}.read_status", "read_status_code"),
    ("COALESCE(dict_{p}bp.text, {p}bp.description)", "billing_period"),
    ("{a}.id_billing_period", "id_billing_period"),
)


def _side_select(alias: str, prefix: str) -> str:
    return ",\n    ".join(
        f"{expr.format(a=alias, p=prefix)} AS {prefix}_{name}" for expr, name in _SIDE_COLUMNS
    )


def _side_joins(alias: str, prefix: str) -> str:
    p = prefix
    return f"""LEFT JOIN gcgt_re_reading_type {p}rt ON {p}rt.cod_develop = {alias}.reading_type
LEFT JOIN GCTS_DICTIONARY dict_{p}rt ON dict_{p}rt.id = {p}rt.description_xi18n AND dict_{p}rt.locale = 'EN'
LEFT JOIN gcgt_re_read_status {p}st ON {p}st.cod_develop = {alias}.read_status
LEFT JOIN GCTS_DICTIONARY dict_{p}st ON dict_{p}st.id = {p}st.description_xi18n AND dict_{p}st.locale = 'EN'
LEFT JOIN gccom_billing_period {p}bp ON {p}bp.id_billing_period = {alias}.id_billing_period
LEFT JOIN GCTS_DICTIONARY dict_{p}bp ON dict_{p}bp.id = {p}bp.period_name_xi18n AND dict_{p}bp.locale = 'EN'"""


def build_tnb_cycle_disc_query() -> str:
    """Every cycle+disconnection same-date pair where either reading is
    Terminated Not Billed - see module docstring for provenance/shape."""
    cyc = format_sql_literal(READING_TYPE_CYCLE)
    disc = format_sql_literal(READING_TYPE_DISCONNECTION)
    tnb = format_sql_literal(READ_STATUS_TERMINATED_NOT_BILLED)
    cancelled = format_sql_literal(CONTRACT_STATUS_CANCELLED)
    return f"""
WITH tnb AS (
    SELECT id_sector_supply, reading_date, usage_type
    FROM {READING_TABLE}
    WHERE read_status = {tnb}
      AND reading_type IN ({cyc}, {disc})
    GROUP BY id_sector_supply, reading_date, usage_type
)
SELECT
    c.id_sector_supply AS id_sector_supply,
    ss.niss AS niss,
    c.usage_type AS usage_code,
    COALESCE(dict_consum.text, consum.name_type) AS usage_name,
    COALESCE(dict_cbp.text, cbp.description, dict_dbp.text, dbp.description) AS billing_period,
    COALESCE(c.id_billing_period, d.id_billing_period) AS id_billing_period,
    CASE WHEN EXISTS (
        SELECT 1 FROM {CONTRACTED_SERVICE_TABLE} cs
        WHERE cs.ID_SECTOR_SUPPLY = c.id_sector_supply
          AND cs.STATUS <> {cancelled}
          AND cs.FROM_DATE <= c.reading_date
          AND (cs.END_DATE IS NULL OR cs.END_DATE >= c.reading_date)
    ) THEN 1 ELSE 0 END AS in_contract,
    {_side_select("c", "c")},
    {_side_select("d", "d")}
FROM tnb
JOIN {READING_TABLE} c ON c.id_sector_supply = tnb.id_sector_supply
    AND c.reading_date = tnb.reading_date
    AND c.usage_type = tnb.usage_type
    AND c.reading_type = {cyc}
JOIN {READING_TABLE} d ON d.id_sector_supply = tnb.id_sector_supply
    AND d.reading_date = tnb.reading_date
    AND d.usage_type = tnb.usage_type
    AND d.reading_type = {disc}
LEFT JOIN gccom_sector_supply ss ON ss.id_sector_supply = c.id_sector_supply
LEFT JOIN gccom_consum_type consum ON consum.cod_develop = c.usage_type
LEFT JOIN GCTS_DICTIONARY dict_consum ON dict_consum.id = consum.name_type_xi18n AND dict_consum.locale = 'EN'
{_side_joins("c", "c")}
{_side_joins("d", "d")}
WHERE (c.read_status = {tnb} OR d.read_status = {tnb})
  -- RJ, 2026-09-25: "anything with ready usage should be included, even
  -- the partner cycle or disconnection should have zero ready usage" -
  -- at least one side must have a non-zero READY_USAGE (NULL = zero).
  AND (ISNULL(c.ready_usage, 0) <> 0 OR ISNULL(d.ready_usage, 0) <> 0)
ORDER BY COALESCE(c.id_billing_period, d.id_billing_period) DESC, ss.niss, c.reading_date DESC
"""
