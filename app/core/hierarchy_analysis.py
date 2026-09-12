"""
Hierarchy Analysis: system-wide scan for PRIMARY meter hierarchies that
still have a NOT-YET-BILLED reading.

Background (analyst's own words): a metering "hierarchy" is a group of
GCGT_RE_MEASUREMENT_POINT rows that share one parent (child rows carry
the parent's id in ID_MAIN_MP). Within a hierarchy, ONE measuring point
is the "primary" (see PRIMARY_MP_TYPES below) - the point whose own
reading is what actually gets taken, and then distributed (via
PERC_DIST) across the rest of the hierarchy. The analyst's own day-to-
day drill-down query (kept here as build_hierarchy_detail_query, near-
verbatim) starts from "the ID_MEASURING_POINT of the primary" and pulls
every row in its hierarchy (`ID_MAIN_MP = :id OR ID_MEASURING_POINT =
:id`) alongside one billing period's worth of readings.

This module answers a different question: not "show me this one
hierarchy" but "which primaries, across the WHOLE database, still have
a reading that hasn't been billed yet" - the system-wide discovery scan
build_detect_all_anomalies_query already does for Diff Date anomalies,
applied to hierarchy/primary readings instead. build_pending_primaries_
query finds every primary with at least one reading in a "not yet
billed" READ_STATUS, for a Cycle or Distribution reading, and - since a
primary can have more than one such reading pending across different
billing periods - keeps only the EARLIEST (lowest ID_BILLING_PERIOD)
one per primary, mirroring date_anomaly.lowest_billing_period_rows'
same "oldest first" framing for the Diff Date workflow.

KNOWN ASSUMPTIONS - confirmed LIVE against the tunnel DB (2026-09-11,
via ad-hoc INFORMATION_SCHEMA / lookup-table queries run through the
app's own Workspace query runner). The "primary" and "MP status" rules
below were the analyst's own corrections to an earlier draft of this
module (which had guessed IND_DIST_PPAL = 1 and a STATUS <> '3000STAMPO'
exclusion instead) - kept here as the current, analyst-confirmed rules:

  - "Primary" = GCGT_RE_MEASUREMENT_POINT.MP_TYPE IN PRIMARY_MP_TYPES -
    pulled live from the GCGT_RE_MP_TYPE lookup table, per the analyst's
    own direction to use that lookup rather than the IND_DIST_PPAL flag
    an earlier draft of this module used (a cross-tab check had shown
    IND_DIST_PPAL = 1 isn't 1:1 with MP_TYPE = 'Principal' - the
    analyst's correction settles which one actually governs).
    PRIMARY_MP_TYPES = ('TIPEQM0003', 'TIPEQM0005') - "Principal" and
    "Principal acoplado" (coupled principal); the other three codes on
    that lookup are 'TIPEQM0001' Normal, 'TIPEQM0002' Secundario,
    'TIPEQM0004' Control - none of those are a "primary".
  - PENDING_READ_STATUSES = ('1000STSRED', '5000STSRED', '6000STSRED')
    - pulled live from GCGT_RE_READ_STATUS: 1000STSRED "Disponible"
    (Available), 5000STSRED "Consumo Anómalo" (Anomalous), 6000STSRED
    "Enviado a Facturar" (Sent to Bill) - the analyst's own three named
    states. Deliberately excludes 0500/0600STSRED (not read yet),
    1500STSRED (pending curve treatment), 3000STSRED (processing),
    4000STSRED (consumption calculated but not yet queued), 7000/
    7001STSRED (already billed), 8000STSRED (terminated not billed),
    9000STSRED (pending validation) - none of those match "Available,
    Anomalous, or Sent to Bill" as named.
  - PENDING_READING_TYPES = ('TIPTL00003', 'TIPTL00017') - pulled live
    from GCGT_RE_READING_TYPE: TIPTL00003 "Cycle", TIPTL00017
    "Distribution". Deliberately NOT the same pair as this app's other
    module, date_anomaly.CYCLE_READING_TYPES (TIPTL00003 + TIPTL00005/
    "Direct Connection") - that pair answers an unrelated question (the
    Detect All ALL_CYCLE column) and TIPTL00005 isn't "distribution", so
    reusing it here would silently answer the wrong question.
  - MP_STATUS_ALLOWED = ('1000STAMPO', '2000STAMPO') - per the
    analyst's own direction to use GCGT_RE_MEASURE_POINT_STATUS to keep
    only Active and Disconnected measuring points, replacing an earlier
    draft's "exclude only Inactive" rule. Pulled live from that lookup:
    '0000STAMPO' "Inexistente" (Nonexistent), '1000STAMPO' "Conectado"
    (Active/Connected), '2000STAMPO' "Desconectado" (Disconnected),
    '3000STAMPO' "Inactivo" (Inactive) - this allow-list keeps the first
    two and now ALSO excludes '0000STAMPO' (which the earlier exclude-
    only-Inactive rule did not).
  - build_hierarchy_detail_query (the analyst's own drill-down query,
    kept near-verbatim) still uses its ORIGINAL STATUS <> '3000STAMPO'
    exclusion, unchanged - that query is the analyst's own day-to-day
    tool as they already use it, not part of this correction.
  - A later round (analyst request) added BILLING_PERIOD_DESC (from
    GCCOM_BILLING_PERIOD.DESCRIPTION) and READING_TYPE_DESC (from
    GCGT_RE_READING_TYPE.DESCRIPTION) to BOTH build_pending_primaries_
    query's and build_hierarchy_detail_query's output, and wired the
    latter's previously-unused billing_period_filter parameter so the
    web route now scopes a hierarchy's children to the SAME billing
    period as the primary row that was clicked, instead of the child's
    full reading history across every period.
  - A further round (analyst request) added three more things to
    build_pending_primaries_query ONLY (build_hierarchy_detail_query is
    untouched by this one):
      1. MIN_BILLING_PERIOD = 10000000193 (analyst-supplied cutoff,
         confirmed live) - only readings with ID_BILLING_PERIOD >
         MIN_BILLING_PERIOD count as "pending" now. Applied inside the
         reading JOIN's ON condition (same spot as pending_statuses/
         pending_types), not the outer WHERE, so it can change WHICH
         reading is a primary's earliest pending one, not just filter
         the result afterward. The analyst's own reasoning for the
         floor wasn't stated beyond the number itself - treat it as a
         business cutoff (e.g. "don't chase pending periods this old"),
         not a schema fact, and revisit if it ever needs to move.
      2. SECONDARY_COUNT - a per-row scalar subquery, `COUNT(*) FROM
         GCGT_RE_MEASUREMENT_POINT c WHERE c.ID_MAIN_MP = mp.
         ID_MEASURING_POINT` - how many child measuring points (of any
         MP_TYPE/status) report to that primary. Confirmed live this
         counts BOTH Active and Disconnected children (not otherwise
         filtered) - a child inactive/removed since would still count;
         no request was made to exclude those, so it doesn't.
      3. CALC_MODULE_TYPE - LEFT JOIN to GCCOM_CALCULATION_MODULE
         (ID_CALCULATION_MODULE -> NAME_TYPE), giving each primary's
         raw ID_CALCULATION_MODULE code a human label. Confirmed live:
         1130 = "Hierarchy - Difference billed to the main supply",
         1150 = "Hierarchy - Percentage of association between Primary
         and Secondary" - only two values seen in the tunnel DB's
         GCGT_RE_MEASUREMENT_POINT.ID_CALCULATION_MODULE distribution
         (958808 rows have neither set), but the lookup isn't
         restricted to just those two, so any other code still
         resolves if one shows up.

Deliberately pure logic, same convention as app/core/date_anomaly.py:
this module only builds SELECT query text; it never executes anything
itself. The caller (web/server.py) runs it via app.db.mssql.run_query.
"""
from __future__ import annotations

from typing import Iterable

from .sql_format import format_sql_literal, quote_ident

# --- Schema / table names -------------------------------------------------
READING_SCHEMA = "OUC_COMMON_ADMIN"
MEASUREMENT_POINT_TABLE = "GCGT_RE_MEASUREMENT_POINT"
READING_TABLE = "GCGT_RE_READING"
# Left unqualified, matching the analyst's own query - same convention
# app.core.date_anomaly.SECTOR_SUPPLY_TABLE documents for the identical
# table used there.
SECTOR_SUPPLY_TABLE = "GCCOM_SECTOR_SUPPLY"

# --- Fixed business constants (see module docstring's "KNOWN ASSUMPTIONS") -
MAIN_MP_COLUMN = "ID_MAIN_MP"
MEASURING_POINT_COLUMN = "ID_MEASURING_POINT"

MP_TYPE_COLUMN = "MP_TYPE"
PRIMARY_MP_TYPES = ("TIPEQM0003", "TIPEQM0005")  # Principal, Principal acoplado
MP_TYPE_LOOKUP_TABLE = "GCGT_RE_MP_TYPE"
MP_TYPE_LOOKUP_KEY_COLUMN = "COD_DEVELOP"
MP_TYPE_LOOKUP_DESC_COLUMN = "DESCRIPTION"

MP_STATUS_COLUMN = "STATUS"
MP_STATUS_ALLOWED = ("1000STAMPO", "2000STAMPO")  # Conectado (Active), Desconectado (Disconnected)
# Kept for build_hierarchy_detail_query, which still uses the analyst's
# original exclude-only-Inactive rule (see module docstring) rather than
# the allow-list above.
MP_STATUS_EXCLUDED = "3000STAMPO"  # Inactivo
MP_STATUS_LOOKUP_TABLE = "GCGT_RE_MEASURE_POINT_STATUS"
MP_STATUS_LOOKUP_KEY_COLUMN = "COD_DEVELOP"
MP_STATUS_LOOKUP_DESC_COLUMN = "NAME_TYPE"

READ_STATUS_COLUMN = "READ_STATUS"
PENDING_READ_STATUSES = ("1000STSRED", "5000STSRED", "6000STSRED")  # Available, Anomalous, Sent to Bill
# "Still not sent to bill" for a SECONDARY (child of a pending primary) -
# per the analyst's own confirmation (2026-09-11, via AskUserQuestion):
# Available/Anomalous only. Deliberately a NARROWER set than PENDING_
# READ_STATUSES above - that one treats 6000STSRED "Sent to Bill" as still
# "pending" at the PRIMARY level (a primary isn't done until actually
# billed), but a SECONDARY already sent to bill counts as handled/out of
# the analyst's queue here - it's moving on its own, nothing left to do.
# Used by build_pending_primaries_query's SECONDARIES_NOT_SENT_COUNT.
SECONDARY_NOT_SENT_STATUSES = ("1000STSRED", "5000STSRED")

READING_TYPE_COLUMN = "READING_TYPE"
PENDING_READING_TYPES = ("TIPTL00003", "TIPTL00017")  # Cycle, Distribution
READING_TYPE_LOOKUP_TABLE = "GCGT_RE_READING_TYPE"
READING_TYPE_LOOKUP_KEY_COLUMN = "COD_DEVELOP"
READING_TYPE_LOOKUP_DESC_COLUMN = "DESCRIPTION"

BILLING_PERIOD_COLUMN = "ID_BILLING_PERIOD"
BILLING_PERIOD_LOOKUP_TABLE = "GCCOM_BILLING_PERIOD"
BILLING_PERIOD_LOOKUP_KEY_COLUMN = "ID_BILLING_PERIOD"
BILLING_PERIOD_LOOKUP_DESC_COLUMN = "DESCRIPTION"

# Analyst-supplied cutoff - see module docstring's "KNOWN ASSUMPTIONS" for
# where this number comes from and how it's applied (inside the reading
# JOIN, not the outer WHERE).
MIN_BILLING_PERIOD = 10000000193

CALCULATION_MODULE_COLUMN = "ID_CALCULATION_MODULE"
CALCULATION_MODULE_LOOKUP_TABLE = "GCCOM_CALCULATION_MODULE"
CALCULATION_MODULE_LOOKUP_KEY_COLUMN = "ID_CALCULATION_MODULE"
CALCULATION_MODULE_LOOKUP_DESC_COLUMN = "NAME_TYPE"

# --- Reading history popup (analyst-supplied draft query, confirmed
# 2026-09-11) - see build_reading_history_query's own docstring for the
# full rationale. Kept as separate constants from MIN_BILLING_PERIOD/
# PENDING_READING_TYPES above since this is a different, narrower
# question ("show me everything for this ONE supply") than either of
# those two system-wide scans use theirs for.
READING_HISTORY_MIN_BILLING_PERIOD = 10000000130
READING_HISTORY_EXCLUDED_READING_TYPE = "TIPTL00004"

# Same row-cap rationale as date_anomaly.DETECT_ALL_DEFAULT_LIMIT: this is
# a system-wide scan with no NISS/id to naturally bound it.
HIERARCHY_DEFAULT_LIMIT = 1000


def _qualified(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}" if schema else quote_ident(table)


def build_pending_primaries_query(
    pending_statuses: Iterable[str] = PENDING_READ_STATUSES,
    pending_types: Iterable[str] = PENDING_READING_TYPES,
    limit: int | None = HIERARCHY_DEFAULT_LIMIT,
    min_billing_period: int | None = MIN_BILLING_PERIOD,
) -> str:
    """
    System-wide scan: every PRIMARY measuring point (MP_TYPE IN
    PRIMARY_MP_TYPES, MP_STATUS IN MP_STATUS_ALLOWED) that has at least
    one reading, for a billing period AFTER min_billing_period, in a
    "not yet billed" status (pending_statuses) for a Cycle/Distribution
    reading (pending_types) - narrowed to just the EARLIEST such reading
    per measuring point via ROW_NUMBER() PARTITION BY ID_MEASURING_POINT
    ORDER BY ID_BILLING_PERIOD ASC, same "oldest pending period first"
    framing as date_anomaly.lowest_billing_period_rows. min_billing_
    period defaults to the analyst's own MIN_BILLING_PERIOD cutoff; pass
    None to see every pending period with no floor.

    JOINs (not LEFT JOINs) mp -> ss and mp -> r deliberately: a primary
    with no sector-supply row, or no matching pending reading at all,
    isn't a "pending primary" - there's nothing to report for it, unlike
    Detect All's LEFT JOINs which exist to enrich an already-anomalous
    row rather than to decide whether it belongs in the result at all.
    The MP_TYPE/MP_STATUS/billing-period/reading-type/calculation-module
    lookup JOINs ARE left, purely for the human-readable *_DESC and
    CALC_MODULE_TYPE columns - a primary whose code has no matching
    lookup row (shouldn't happen, but see Detect All's own identical
    reasoning) still surfaces rather than silently vanishing.

    SECONDARY_COUNT is a correlated scalar subquery - how many child
    measuring points (ID_MAIN_MP = this primary's ID_MEASURING_POINT)
    report to this primary, regardless of THEIR own type/status. Runs
    once per output row (post-TOP-cap, post-RN=1 filter), not once per
    scanned candidate, so its cost scales with the result size shown to
    the analyst, not the size of the underlying scan.

    SECONDARIES_NOT_SENT_COUNT (added per analyst request, 2026-09-11) -
    another correlated scalar subquery, same cost profile as SECONDARY_
    COUNT above: of THIS primary's secondaries, how many still count as
    "not sent to bill yet"? A secondary counts if EITHER (a) it has a
    Cycle/Distribution reading above min_billing_period whose READ_STATUS
    is in SECONDARY_NOT_SENT_STATUSES (Available/Anomalous - confirmed by
    the analyst over a narrower "not yet billed at all" alternative), OR
    (b) it has NO Cycle/Distribution reading above min_billing_period at
    all (per the analyst's own follow-up - a secondary with nothing
    captured for it yet is obviously not sent either, not a clean pass).
    A secondary already at 6000STSRED (Sent to Bill) or a billed-like
    status doesn't count - it's out of the analyst's queue. When this is
    0, EVERY secondary is already sent-to-bill-or-further, meaning the
    primary's own pending reading is the ONLY thing still needing
    attention in that whole hierarchy - the frontend highlights this case
    green and offers a filter for it (see app.js).

    Column list mirrors the analyst's own drill-down query (see
    build_hierarchy_detail_query) so a result row here reads the same
    way what they're already used to reviewing does.

    `limit` adds a `TOP (N)` cap for the same reason
    date_anomaly.build_detect_all_anomalies_query's does - no natural
    per-call scope here, so an unbounded scan on a large production DB
    is worth guarding against. Pass None to disable it.
    """
    mp_tbl = _qualified(READING_SCHEMA, MEASUREMENT_POINT_TABLE)
    reading_tbl = _qualified(READING_SCHEMA, READING_TABLE)
    mp_type_lookup = _qualified(READING_SCHEMA, MP_TYPE_LOOKUP_TABLE)
    mp_status_lookup = _qualified(READING_SCHEMA, MP_STATUS_LOOKUP_TABLE)
    billing_period_lookup = _qualified(READING_SCHEMA, BILLING_PERIOD_LOOKUP_TABLE)
    reading_type_lookup = _qualified(READING_SCHEMA, READING_TYPE_LOOKUP_TABLE)
    calc_module_lookup = _qualified(READING_SCHEMA, CALCULATION_MODULE_LOOKUP_TABLE)
    status_list = ", ".join(format_sql_literal(s) for s in pending_statuses)
    type_list = ", ".join(format_sql_literal(t) for t in pending_types)
    primary_type_list = ", ".join(format_sql_literal(t) for t in PRIMARY_MP_TYPES)
    allowed_status_list = ", ".join(format_sql_literal(s) for s in MP_STATUS_ALLOWED)
    not_sent_status_list = ", ".join(format_sql_literal(s) for s in SECONDARY_NOT_SENT_STATUSES)
    top_clause = f"TOP ({int(limit)}) " if limit else ""
    period_floor_clause = (
        f"       AND r.{BILLING_PERIOD_COLUMN} > {format_sql_literal(min_billing_period)}\n"
        if min_billing_period is not None
        else ""
    )
    # Reused inside SECONDARIES_NOT_SENT_COUNT's two correlated EXISTS
    # checks below - same floor as the outer primary scan (period_floor_
    # clause), just phrased as a standalone "AND ..." fragment since it's
    # embedded inside a nested subquery's own WHERE, not appended after a
    # JOIN...ON like the outer one.
    secondary_period_floor = (
        f" AND r2.{BILLING_PERIOD_COLUMN} > {format_sql_literal(min_billing_period)}"
        if min_billing_period is not None
        else ""
    )
    return (
        f"SELECT {top_clause}*\n"
        f"FROM (\n"
        f"    SELECT\n"
        f"        mp.{MAIN_MP_COLUMN},\n"
        f"        mp.{CALCULATION_MODULE_COLUMN},\n"
        f"        cm.{CALCULATION_MODULE_LOOKUP_DESC_COLUMN} AS CALC_MODULE_TYPE,\n"
        f"        mp.{MEASURING_POINT_COLUMN},\n"
        f"        (SELECT COUNT(*) FROM {mp_tbl} c "
        f"WHERE c.{MAIN_MP_COLUMN} = mp.{MEASURING_POINT_COLUMN}) AS SECONDARY_COUNT,\n"
        f"        (SELECT COUNT(*) FROM {mp_tbl} c WHERE c.{MAIN_MP_COLUMN} = mp.{MEASURING_POINT_COLUMN} "
        f"AND (\n"
        f"            EXISTS (SELECT 1 FROM {reading_tbl} r2 "
        f"WHERE r2.{MEASURING_POINT_COLUMN} = c.{MEASURING_POINT_COLUMN} "
        f"AND r2.{READ_STATUS_COLUMN} IN ({not_sent_status_list}) "
        f"AND r2.{READING_TYPE_COLUMN} IN ({type_list}){secondary_period_floor})\n"
        f"            OR NOT EXISTS (SELECT 1 FROM {reading_tbl} r3 "
        f"WHERE r3.{MEASURING_POINT_COLUMN} = c.{MEASURING_POINT_COLUMN} "
        f"AND r3.{READING_TYPE_COLUMN} IN ({type_list})"
        f"{secondary_period_floor.replace('r2.', 'r3.')})\n"
        f"        )) AS SECONDARIES_NOT_SENT_COUNT,\n"
        f"        mp.ID_SECTOR_SUPPLY,\n"
        f"        mp.ID_METER,\n"
        f"        mp.PERC_DIST,\n"
        f"        mp.{MP_TYPE_COLUMN},\n"
        f"        mt.{MP_TYPE_LOOKUP_DESC_COLUMN} AS MP_TYPE_DESC,\n"
        f"        mp.{MP_STATUS_COLUMN} AS MP_STATUS,\n"
        f"        ms.{MP_STATUS_LOOKUP_DESC_COLUMN} AS MP_STATUS_DESC,\n"
        f"        ss.NISS,\n"
        f"        r.{BILLING_PERIOD_COLUMN},\n"
        f"        bp.{BILLING_PERIOD_LOOKUP_DESC_COLUMN} AS BILLING_PERIOD_DESC,\n"
        f"        r.ID_READING,\n"
        f"        r.READING_PREV_DATE,\n"
        f"        r.READING_DATE,\n"
        f"        r.{READING_TYPE_COLUMN},\n"
        f"        rt.{READING_TYPE_LOOKUP_DESC_COLUMN} AS READING_TYPE_DESC,\n"
        f"        r.{READ_STATUS_COLUMN},\n"
        f"        r.IND_USAGE_TO_CAL,\n"
        f"        r.PREV_VALUE,\n"
        f"        r.VALUE,\n"
        f"        r.READY_USAGE,\n"
        f"        r.READING_USAGE,\n"
        f"        r.CORRECTED_USAGE,\n"
        f"        r.USAGE_TYPE,\n"
        f"        r.IND_ESTIMATE,\n"
        f"        r.UPDATE_PROGRAM,\n"
        f"        r.UPDATE_DATE,\n"
        f"        r.ID_DEVICE,\n"
        f"        ROW_NUMBER() OVER (\n"
        f"            PARTITION BY mp.{MEASURING_POINT_COLUMN}\n"
        f"            ORDER BY r.{BILLING_PERIOD_COLUMN} ASC\n"
        f"        ) AS RN\n"
        f"    FROM {mp_tbl} mp\n"
        f"    JOIN {SECTOR_SUPPLY_TABLE} ss ON ss.ID_SECTOR_SUPPLY = mp.ID_SECTOR_SUPPLY\n"
        f"    JOIN {reading_tbl} r\n"
        f"        ON r.{MEASURING_POINT_COLUMN} = mp.{MEASURING_POINT_COLUMN}\n"
        f"       AND r.{READ_STATUS_COLUMN} IN ({status_list})\n"
        f"       AND r.{READING_TYPE_COLUMN} IN ({type_list})\n"
        f"{period_floor_clause}"
        f"    LEFT JOIN {mp_type_lookup} mt "
        f"ON mt.{MP_TYPE_LOOKUP_KEY_COLUMN} = mp.{MP_TYPE_COLUMN}\n"
        f"    LEFT JOIN {mp_status_lookup} ms "
        f"ON ms.{MP_STATUS_LOOKUP_KEY_COLUMN} = mp.{MP_STATUS_COLUMN}\n"
        f"    LEFT JOIN {billing_period_lookup} bp "
        f"ON bp.{BILLING_PERIOD_LOOKUP_KEY_COLUMN} = r.{BILLING_PERIOD_COLUMN}\n"
        f"    LEFT JOIN {reading_type_lookup} rt "
        f"ON rt.{READING_TYPE_LOOKUP_KEY_COLUMN} = r.{READING_TYPE_COLUMN}\n"
        f"    LEFT JOIN {calc_module_lookup} cm "
        f"ON cm.{CALCULATION_MODULE_LOOKUP_KEY_COLUMN} = mp.{CALCULATION_MODULE_COLUMN}\n"
        f"    WHERE mp.{MP_TYPE_COLUMN} IN ({primary_type_list})\n"
        f"      AND mp.{MP_STATUS_COLUMN} IN ({allowed_status_list})\n"
        f") A\n"
        f"WHERE RN = 1\n"
        f"ORDER BY {BILLING_PERIOD_COLUMN};"
    )


def build_hierarchy_detail_query(
    id_measuring_point: int,
    billing_period_filter: int | None = None,
) -> str:
    """
    Drill-down for ONE hierarchy - the analyst's own day-to-day query,
    kept near-verbatim (their `select * from (...) A where STATUS <>
    '3000STAMPO'` shape), parameterized on the id instead of hardcoded.
    Given the ID_MEASURING_POINT of a primary (or any member of its
    hierarchy), returns every row in that hierarchy - `ID_MAIN_MP = :id
    OR ID_MEASURING_POINT = :id` catches both the primary's own row (its
    ID_MAIN_MP is typically NULL, so it wouldn't otherwise match the
    first condition) and every child whose ID_MAIN_MP points to it.

    Unlike build_pending_primaries_query, this is NOT restricted to
    pending-only reads - the analyst's own query pulls one specific
    billing period's readings (hardcoded `r.ID_BILLING_PERIOD =
    10000000236` in their original) via a LEFT JOIN so a hierarchy
    member with no reading in that period still shows up with blank
    reading columns. `billing_period_filter` reproduces that: pass the
    clicked pending-primary row's own ID_BILLING_PERIOD (the web route
    does this automatically) to scope every child's reading to the SAME
    billing period the primary was flagged pending in - the analyst's
    own request, since a child's reading from a different period isn't
    what's being reviewed for that primary. Leave it None to see the
    hierarchy's full reading history across every period instead (a
    LEFT JOIN either way, so members with no matching reading still
    surface).

    Also LEFT JOINs GCCOM_BILLING_PERIOD and GCGT_RE_READING_TYPE for
    human-readable BILLING_PERIOD_DESC / READING_TYPE_DESC columns, same
    enrichment convention as build_pending_primaries_query's MP_TYPE_DESC
    / MP_STATUS_DESC.
    """
    mp_tbl = _qualified(READING_SCHEMA, MEASUREMENT_POINT_TABLE)
    reading_tbl = _qualified(READING_SCHEMA, READING_TABLE)
    billing_period_lookup = _qualified(READING_SCHEMA, BILLING_PERIOD_LOOKUP_TABLE)
    reading_type_lookup = _qualified(READING_SCHEMA, READING_TYPE_LOOKUP_TABLE)
    mp_id = format_sql_literal(id_measuring_point)
    period_clause = (
        f"       AND r.{BILLING_PERIOD_COLUMN} = {format_sql_literal(billing_period_filter)}\n"
        if billing_period_filter is not None
        else ""
    )
    return (
        f"SELECT *\n"
        f"FROM (\n"
        f"    SELECT\n"
        f"        mp.{MAIN_MP_COLUMN},\n"
        f"        mp.ID_CALCULATION_MODULE,\n"
        f"        mp.{MEASURING_POINT_COLUMN},\n"
        f"        mp.ID_SECTOR_SUPPLY,\n"
        f"        mp.ID_METER,\n"
        f"        mp.PERC_DIST,\n"
        f"        mp.IND_DIST_PPAL,\n"
        f"        ss.NISS,\n"
        f"        r.{BILLING_PERIOD_COLUMN},\n"
        f"        bp.{BILLING_PERIOD_LOOKUP_DESC_COLUMN} AS BILLING_PERIOD_DESC,\n"
        f"        r.ID_READING,\n"
        f"        r.READING_PREV_DATE,\n"
        f"        r.READING_DATE,\n"
        f"        r.{READING_TYPE_COLUMN},\n"
        f"        rt.{READING_TYPE_LOOKUP_DESC_COLUMN} AS READING_TYPE_DESC,\n"
        f"        mp.{MP_STATUS_COLUMN},\n"
        f"        mp.MP_TYPE,\n"
        f"        r.{READ_STATUS_COLUMN},\n"
        f"        r.IND_USAGE_TO_CAL,\n"
        f"        r.PREV_VALUE,\n"
        f"        r.VALUE,\n"
        f"        r.READY_USAGE,\n"
        f"        r.READING_USAGE,\n"
        f"        r.CORRECTED_USAGE,\n"
        f"        r.USAGE_TYPE,\n"
        f"        r.IND_ESTIMATE,\n"
        f"        r.UPDATE_PROGRAM,\n"
        f"        r.UPDATE_DATE,\n"
        f"        r.ID_DEVICE\n"
        f"    FROM {mp_tbl} mp\n"
        f"    JOIN {SECTOR_SUPPLY_TABLE} ss ON ss.ID_SECTOR_SUPPLY = mp.ID_SECTOR_SUPPLY\n"
        f"    LEFT JOIN {reading_tbl} r\n"
        f"        ON mp.{MEASURING_POINT_COLUMN} = r.{MEASURING_POINT_COLUMN}\n"
        f"       AND r.{READING_TYPE_COLUMN} <> 'TIPTL00004'\n"
        f"       AND r.{READING_TYPE_COLUMN} <> 'TIPTL00001'\n"
        f"{period_clause}"
        f"    LEFT JOIN {billing_period_lookup} bp "
        f"ON bp.{BILLING_PERIOD_LOOKUP_KEY_COLUMN} = r.{BILLING_PERIOD_COLUMN}\n"
        f"    LEFT JOIN {reading_type_lookup} rt "
        f"ON rt.{READING_TYPE_LOOKUP_KEY_COLUMN} = r.{READING_TYPE_COLUMN}\n"
        f"    WHERE mp.{MAIN_MP_COLUMN} = {mp_id}\n"
        f"       OR mp.{MEASURING_POINT_COLUMN} = {mp_id}\n"
        f") A WHERE {MP_STATUS_COLUMN} <> {format_sql_literal(MP_STATUS_EXCLUDED)};"
    )


def build_reading_history_query(
    id_sector_supply: int,
    min_billing_period: int | None = READING_HISTORY_MIN_BILLING_PERIOD,
    excluded_reading_type: str | None = READING_HISTORY_EXCLUDED_READING_TYPE,
) -> str:
    """
    Reading-history popup for ONE supply - adapted from the analyst's own
    pasted draft query (task #143, confirmed 2026-09-11 after two rounds
    of review):

        select r.ID_BILLING_PERIOD, r.*
        from OUC_COMMON_ADMIN.GCGT_RE_READING r
        join GCCOM_SECTOR_SUPPLY ss on ss.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
        where ss.niss = '10008283-301'
          and ID_BILLING_PERIOD > 10000000130
          and r.READING_TYPE <> 'TIPTL00004'
        order by r.ID_BILLING_PERIOD desc;

    Two changes from the analyst's original, both per their own explicit
    follow-up direction:
      1. Filters directly on r.ID_SECTOR_SUPPLY (the FK already sits on
         GCGT_RE_READING itself, confirmed live via INFORMATION_SCHEMA.
         COLUMNS - see this module's own history/comments) instead of
         joining GCCOM_SECTOR_SUPPLY to filter on NISS. No join needed.
      2. Selects/aliases the specific columns the analyst asked to see
         (BILLING_PERIOD, ID_READING, READING_TYPE, USAGE_TYPE,
         READ_STATUS, PREV DATE, READING_DATE, PREV_VALUE, VALUE,
         READING_USAGE, READY_USAGE, IND_ESTIMATE) instead of `r.*`.
         READING_USAGE stands in for the "READING_VALUE" the analyst
         named - no such column exists on GCGT_RE_READING (confirmed
         live against all 55 of its columns); READING_USAGE was the
         closest semantic match and the analyst did not object to it.

    min_billing_period reproduces the analyst's own `ID_BILLING_PERIOD >
    10000000130` floor (READING_HISTORY_MIN_BILLING_PERIOD - a DIFFERENT
    number from MIN_BILLING_PERIOD's 10000000193 used by build_pending_
    primaries_query; don't conflate the two, they come from different
    analyst asks). excluded_reading_type reproduces `READING_TYPE <>
    'TIPTL00004'`. Both are pass-None-to-disable, same convention as this
    module's other query builders.

    Deliberately no "highlight the period we're checking" logic here -
    that's the analyst's own follow-up ask, but it's a display concern
    (compare each returned row's ID_BILLING_PERIOD to the billing period
    of whichever Hierarchy Detail row the popup was opened from), handled
    client-side in app.js, not a SQL change.

    Ordered by ID_BILLING_PERIOD DESC, matching the analyst's original.
    """
    reading_tbl = _qualified(READING_SCHEMA, READING_TABLE)
    sector_supply_id = format_sql_literal(id_sector_supply)
    where_clauses = [f"r.ID_SECTOR_SUPPLY = {sector_supply_id}"]
    if min_billing_period is not None:
        where_clauses.append(f"r.{BILLING_PERIOD_COLUMN} > {format_sql_literal(min_billing_period)}")
    if excluded_reading_type is not None:
        where_clauses.append(f"r.{READING_TYPE_COLUMN} <> {format_sql_literal(excluded_reading_type)}")
    where_sql = "\n  AND ".join(where_clauses)
    return (
        f"SELECT\n"
        f"    r.{BILLING_PERIOD_COLUMN} AS BILLING_PERIOD,\n"
        f"    r.ID_READING,\n"
        f"    r.{READING_TYPE_COLUMN},\n"
        f"    r.USAGE_TYPE,\n"
        f"    r.{READ_STATUS_COLUMN},\n"
        f"    r.READING_PREV_DATE AS [PREV DATE],\n"
        f"    r.READING_DATE,\n"
        f"    r.PREV_VALUE,\n"
        f"    r.VALUE,\n"
        f"    r.READING_USAGE,\n"
        f"    r.READY_USAGE,\n"
        f"    r.IND_ESTIMATE\n"
        f"FROM {reading_tbl} r\n"
        f"WHERE {where_sql}\n"
        f"ORDER BY r.{BILLING_PERIOD_COLUMN} DESC;"
    )
