"""
Unit tests for app/core/wrong_stuck_hierarchy.py - pure SQL-text
assertions (no real DB).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import wrong_stuck_hierarchy as wsh


def test_default_scan_has_no_period_literal():
    sql = wsh.build_wrong_stuck_query()
    assert "10000000" not in sql
    assert "itb.ID_BILLING_PERIOD = " not in sql.replace("pmp.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD", "").replace(
        "itb1.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD", "")


def test_primary_and_190_item_matched_on_same_period_as_stuck_item():
    sql = wsh.build_wrong_stuck_query()
    assert "SELECT DISTINCT mp.ID_MEASURING_POINT, itb.ID_BILLING_PERIOD" in sql
    assert "JOIN primary_mp pmp ON pmp.ID_MEASURING_POINT = mp.ID_MAIN_MP" in sql
    assert "AND pmp.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD" in sql
    assert "AND itb1.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD" in sql


def test_optional_period_narrows_both_parts():
    sql = wsh.build_wrong_stuck_query(10000000236)
    assert sql.count("itb.ID_BILLING_PERIOD = 10000000236") == 2


def test_stuck_and_billed_statuses():
    sql = wsh.build_wrong_stuck_query()
    assert "WHERE itb.STATUS = 'STTOBILL09'" in sql
    assert "WHERE itb.STATUS = 'STTOBILL07'" in sql


def test_contract_status_filter_on_both_parts():
    sql = wsh.build_wrong_stuck_query()
    assert sql.count("cs.STATUS IN ('ESTSC00002', 'ESTSC00003', 'ESTSC00007')") == 2


def test_partner_service_190_left_joins():
    sql = wsh.build_wrong_stuck_query()
    assert "cs1.ID_OFFERED_SERVICE = 190" in sql
    assert "LEFT JOIN GCCOM_ITEMS_TO_BILL itb1 ON itb1.ID_BILLING_SERVICE = bs1.ID_BILLING_SERVICE" in sql


def test_output_columns_are_aliased_and_include_period_id():
    sql = wsh.build_wrong_stuck_query()
    assert "itb.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL," in sql
    assert "itb.ID_BILLING_PERIOD AS ID_BILLING_PERIOD" in sql
    assert "itb1.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL_190" in sql
    assert "itb1.STATUS AS STATUS_190" in sql


def test_order_by_period_then_main_mp():
    assert "ORDER BY itb.ID_BILLING_PERIOD DESC, mp.ID_MAIN_MP DESC" in wsh.build_wrong_stuck_query()


def test_period_must_be_integer():
    with pytest.raises(ValueError):
        wsh.build_wrong_stuck_query("1; DROP TABLE x")
