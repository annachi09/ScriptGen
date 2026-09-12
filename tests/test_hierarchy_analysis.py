"""
Pure-logic tests for app/core/hierarchy_analysis.py's query builders -
same convention as tests/test_date_anomaly.py: these only check the
generated SQL TEXT (clauses, literals, table/column names), never touch
a real database. Live correctness against the real tunnel DB was
verified by hand this round (see the module's own "KNOWN ASSUMPTIONS"
docstring) - these tests guard against the SQL TEXT regressing, not
against the assumptions themselves being wrong.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import hierarchy_analysis as ha


# ---------------------------------------------------------------------
# build_pending_primaries_query
# ---------------------------------------------------------------------

def test_build_pending_primaries_query_filters_on_primary_mp_type():
    sql = ha.build_pending_primaries_query()
    assert "mp.MP_TYPE IN ('TIPEQM0003', 'TIPEQM0005')" in sql


def test_build_pending_primaries_query_filters_allowed_mp_status():
    sql = ha.build_pending_primaries_query()
    assert "mp.STATUS IN ('1000STAMPO', '2000STAMPO')" in sql


def test_build_pending_primaries_query_filters_pending_read_statuses():
    sql = ha.build_pending_primaries_query()
    assert "r.READ_STATUS IN ('1000STSRED', '5000STSRED', '6000STSRED')" in sql


def test_build_pending_primaries_query_filters_pending_reading_types():
    sql = ha.build_pending_primaries_query()
    assert "r.READING_TYPE IN ('TIPTL00003', 'TIPTL00017')" in sql


def test_build_pending_primaries_query_uses_custom_statuses_and_types():
    sql = ha.build_pending_primaries_query(pending_statuses=["9999FAKE"], pending_types=["TIPTL09999"])
    assert "r.READ_STATUS IN ('9999FAKE')" in sql
    assert "r.READING_TYPE IN ('TIPTL09999')" in sql


def test_build_pending_primaries_query_partitions_by_measuring_point_lowest_period():
    sql = ha.build_pending_primaries_query()
    assert "PARTITION BY mp.ID_MEASURING_POINT" in sql
    assert "ORDER BY r.ID_BILLING_PERIOD ASC" in sql
    assert "WHERE RN = 1" in sql


def test_build_pending_primaries_query_joins_sector_supply_unqualified():
    sql = ha.build_pending_primaries_query()
    # Matches the analyst's own query convention - see module docstring
    # and app.core.date_anomaly.SECTOR_SUPPLY_TABLE's identical comment.
    assert "JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = mp.ID_SECTOR_SUPPLY" in sql
    assert "OUC_COMMON_ADMIN.GCCOM_SECTOR_SUPPLY" not in sql


def test_build_pending_primaries_query_qualifies_measurement_point_and_reading():
    sql = ha.build_pending_primaries_query()
    assert "OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT" in sql
    assert "OUC_COMMON_ADMIN.GCGT_RE_READING" in sql


def test_build_pending_primaries_query_default_limit_adds_top_clause():
    sql = ha.build_pending_primaries_query()
    assert f"TOP ({ha.HIERARCHY_DEFAULT_LIMIT})" in sql


def test_build_pending_primaries_query_limit_none_omits_top_clause():
    sql = ha.build_pending_primaries_query(limit=None)
    assert "TOP (" not in sql


def test_build_pending_primaries_query_custom_limit():
    sql = ha.build_pending_primaries_query(limit=50)
    assert "TOP (50)" in sql


def test_build_pending_primaries_query_uses_inner_joins_for_scope():
    # A primary with no matching pending reading, or no sector-supply
    # row, isn't a "pending primary" - nothing to report. Those two
    # joins stay INNER; only the MP_TYPE/MP_STATUS description lookups
    # (added for MP_TYPE_DESC/MP_STATUS_DESC) are LEFT JOINs.
    sql = ha.build_pending_primaries_query()
    assert "JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = mp.ID_SECTOR_SUPPLY" in sql
    assert "JOIN " + ha._qualified(ha.READING_SCHEMA, ha.READING_TABLE) + " r\n" in sql


def test_build_pending_primaries_query_left_joins_type_and_status_lookups():
    sql = ha.build_pending_primaries_query()
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_MP_TYPE mt ON mt.COD_DEVELOP = mp.MP_TYPE" in sql
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_MEASURE_POINT_STATUS ms ON ms.COD_DEVELOP = mp.STATUS" in sql
    assert "mt.DESCRIPTION AS MP_TYPE_DESC" in sql
    assert "ms.NAME_TYPE AS MP_STATUS_DESC" in sql


def test_build_pending_primaries_query_custom_primary_types_and_statuses_from_constants():
    # PRIMARY_MP_TYPES / MP_STATUS_ALLOWED aren't function parameters
    # (unlike pending_statuses/pending_types) - confirm the module
    # constants are what's embedded.
    sql = ha.build_pending_primaries_query()
    for t in ha.PRIMARY_MP_TYPES:
        assert t in sql
    for s in ha.MP_STATUS_ALLOWED:
        assert s in sql


def test_build_pending_primaries_query_left_joins_billing_period_and_reading_type_lookups():
    sql = ha.build_pending_primaries_query()
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = r.ID_BILLING_PERIOD" in sql
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING_TYPE rt ON rt.COD_DEVELOP = r.READING_TYPE" in sql
    assert "bp.DESCRIPTION AS BILLING_PERIOD_DESC" in sql
    assert "rt.DESCRIPTION AS READING_TYPE_DESC" in sql


def test_build_pending_primaries_query_applies_default_min_billing_period_floor():
    sql = ha.build_pending_primaries_query()
    assert f"AND r.ID_BILLING_PERIOD > {ha.MIN_BILLING_PERIOD}" in sql
    # Floor has to land inside the reading JOIN's ON condition (with the
    # status/type filters), not the outer WHERE - it can change which
    # reading is a primary's EARLIEST pending one, not just filter the
    # already-picked result afterward.
    join_idx = sql.index("JOIN " + ha._qualified(ha.READING_SCHEMA, ha.READING_TABLE) + " r")
    floor_idx = sql.index(f"AND r.ID_BILLING_PERIOD > {ha.MIN_BILLING_PERIOD}")
    outer_where_idx = sql.index("WHERE mp.MP_TYPE IN")
    assert join_idx < floor_idx < outer_where_idx


def test_build_pending_primaries_query_custom_min_billing_period():
    sql = ha.build_pending_primaries_query(min_billing_period=10000000200)
    assert "AND r.ID_BILLING_PERIOD > 10000000200" in sql
    assert f"AND r.ID_BILLING_PERIOD > {ha.MIN_BILLING_PERIOD}" not in sql


def test_build_pending_primaries_query_min_billing_period_none_omits_floor():
    sql = ha.build_pending_primaries_query(min_billing_period=None)
    assert "ID_BILLING_PERIOD >" not in sql


def test_build_pending_primaries_query_secondary_count_subquery():
    sql = ha.build_pending_primaries_query()
    assert (
        "(SELECT COUNT(*) FROM OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT c "
        "WHERE c.ID_MAIN_MP = mp.ID_MEASURING_POINT) AS SECONDARY_COUNT" in sql
    )


def test_build_pending_primaries_query_left_joins_calculation_module_lookup():
    sql = ha.build_pending_primaries_query()
    assert (
        "LEFT JOIN OUC_COMMON_ADMIN.GCCOM_CALCULATION_MODULE cm "
        "ON cm.ID_CALCULATION_MODULE = mp.ID_CALCULATION_MODULE" in sql
    )
    assert "cm.NAME_TYPE AS CALC_MODULE_TYPE" in sql


# ---------------------------------------------------------------------
# build_hierarchy_detail_query
# ---------------------------------------------------------------------

def test_build_hierarchy_detail_query_matches_main_mp_or_self():
    sql = ha.build_hierarchy_detail_query(11527)
    assert "WHERE mp.ID_MAIN_MP = 11527" in sql
    assert "OR mp.ID_MEASURING_POINT = 11527" in sql


def test_build_hierarchy_detail_query_excludes_inactive_status_outer():
    sql = ha.build_hierarchy_detail_query(11527)
    assert sql.strip().endswith("A WHERE STATUS <> '3000STAMPO';")


def test_build_hierarchy_detail_query_left_joins_reading_excluding_install_and_control():
    sql = ha.build_hierarchy_detail_query(11527)
    assert "LEFT JOIN" in sql
    assert "r.READING_TYPE <> 'TIPTL00004'" in sql
    assert "r.READING_TYPE <> 'TIPTL00001'" in sql


def test_build_hierarchy_detail_query_embeds_id_as_literal_not_placeholder():
    sql = ha.build_hierarchy_detail_query(999888777)
    assert "999888777" in sql
    assert ":id" not in sql
    assert "?" not in sql


def test_build_hierarchy_detail_query_left_joins_billing_period_and_reading_type_lookups():
    sql = ha.build_hierarchy_detail_query(11527)
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = r.ID_BILLING_PERIOD" in sql
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING_TYPE rt ON rt.COD_DEVELOP = r.READING_TYPE" in sql
    assert "bp.DESCRIPTION AS BILLING_PERIOD_DESC" in sql
    assert "rt.DESCRIPTION AS READING_TYPE_DESC" in sql


def test_build_hierarchy_detail_query_no_billing_period_filter_by_default():
    # Without billing_period_filter, the child join keeps its full
    # reading history - no extra AND r.ID_BILLING_PERIOD = ... clause.
    sql = ha.build_hierarchy_detail_query(11527)
    assert "AND r.ID_BILLING_PERIOD =" not in sql


def test_build_hierarchy_detail_query_scopes_children_to_given_billing_period():
    sql = ha.build_hierarchy_detail_query(11527, billing_period_filter=10000000236)
    assert "AND r.ID_BILLING_PERIOD = 10000000236" in sql
    # The scoping clause has to land inside the reading LEFT JOIN's ON
    # condition (alongside the READING_TYPE exclusions), not the outer
    # WHERE - a child hierarchy member with no reading in that period
    # must still surface with blank reading columns.
    join_idx = sql.index("LEFT JOIN")
    period_idx = sql.index("AND r.ID_BILLING_PERIOD = 10000000236")
    where_idx = sql.index("WHERE mp.ID_MAIN_MP")
    assert join_idx < period_idx < where_idx


def test_build_hierarchy_detail_query_billing_period_filter_is_optional_second_positional_arg():
    sql_with = ha.build_hierarchy_detail_query(11527, 10000000236)
    sql_without = ha.build_hierarchy_detail_query(11527)
    assert sql_with != sql_without
    assert "10000000236" in sql_with
    assert "10000000236" not in sql_without
