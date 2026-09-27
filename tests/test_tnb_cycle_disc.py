"""
Unit tests for app/core/tnb_cycle_disc.py - pure SQL-text assertions,
same style as test_reading_validation.py (no real DB).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import tnb_cycle_disc as tcd


def test_codes_are_the_live_confirmed_ones():
    assert tcd.READING_TYPE_CYCLE == "TIPTL00003"
    assert tcd.READING_TYPE_DISCONNECTION == "TIPTL00010"
    assert tcd.READ_STATUS_TERMINATED_NOT_BILLED == "8000STSRED"


def test_tnb_cte_narrows_to_tnb_cycle_or_disc_keys():
    sql = tcd.build_tnb_cycle_disc_query()
    assert "WITH tnb AS (" in sql
    assert "WHERE read_status = '8000STSRED'" in sql
    assert "reading_type IN ('TIPTL00003', 'TIPTL00010')" in sql
    assert "GROUP BY id_sector_supply, reading_date, usage_type" in sql


def test_pairs_cycle_and_disc_on_supply_date_and_usage_type():
    sql = tcd.build_tnb_cycle_disc_query()
    for alias, code in (("c", "TIPTL00003"), ("d", "TIPTL00010")):
        assert f"{alias}.id_sector_supply = tnb.id_sector_supply" in sql
        assert f"{alias}.reading_date = tnb.reading_date" in sql
        assert f"{alias}.usage_type = tnb.usage_type" in sql
        assert f"{alias}.reading_type = '{code}'" in sql


def test_requires_at_least_one_side_tnb():
    sql = tcd.build_tnb_cycle_disc_query()
    assert "WHERE (c.read_status = '8000STSRED' OR d.read_status = '8000STSRED')" in sql


def test_keeps_pair_when_at_least_one_side_has_ready_usage():
    sql = tcd.build_tnb_cycle_disc_query()
    assert "AND (ISNULL(c.ready_usage, 0) <> 0 OR ISNULL(d.ready_usage, 0) <> 0)" in sql


def test_in_contract_indicator_uses_non_cancelled_contract_covering_reading_date():
    sql = tcd.build_tnb_cycle_disc_query()
    assert "FROM OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs" in sql
    assert "cs.ID_SECTOR_SUPPLY = c.id_sector_supply" in sql
    assert "cs.STATUS <> 'ESTSC00005'" in sql
    assert "cs.FROM_DATE <= c.reading_date" in sql
    assert "(cs.END_DATE IS NULL OR cs.END_DATE >= c.reading_date)" in sql
    assert "THEN 1 ELSE 0 END AS in_contract" in sql


def test_both_sides_expose_identical_column_sets():
    sql = tcd.build_tnb_cycle_disc_query()
    for name in ("id_reading", "reading_type", "reading_prev_date", "reading_date", "prev_value",
                 "value", "reading_usage", "corrected_usage", "ready_usage", "read_status",
                 "read_status_code", "billing_period", "id_billing_period"):
        assert f"AS c_{name}" in sql
        assert f"AS d_{name}" in sql


def test_value_column_comes_from_reading_value():
    sql = tcd.build_tnb_cycle_disc_query()
    assert "c.VALUE AS c_value" in sql
    assert "d.VALUE AS d_value" in sql


def test_headline_columns_niss_and_billing_period_in_words():
    sql = tcd.build_tnb_cycle_disc_query()
    assert "ss.niss AS niss" in sql
    assert "COALESCE(dict_cbp.text, cbp.description, dict_dbp.text, dbp.description) AS billing_period" in sql
    assert "dict_cbp.id = cbp.period_name_xi18n AND dict_cbp.locale = 'EN'" in sql


def test_english_descriptions_for_type_and_status():
    sql = tcd.build_tnb_cycle_disc_query()
    assert "COALESCE(dict_crt.text, crt.description) AS c_reading_type" in sql
    assert "COALESCE(dict_dst.text, dst.description) AS d_read_status" in sql
