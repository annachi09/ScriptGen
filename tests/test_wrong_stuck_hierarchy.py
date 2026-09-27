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


def test_collect_ids_includes_190_only_when_also_stuck_and_dedupes():
    rows = [
        {"ID_ITEM_TO_BILL": 11, "ID_ITEM_TO_BILL_190": 21, "STATUS_190": "STTOBILL09"},
        {"ID_ITEM_TO_BILL": 12, "ID_ITEM_TO_BILL_190": 21, "STATUS_190": "STTOBILL09"},  # same 190 item
        {"ID_ITEM_TO_BILL": 13, "ID_ITEM_TO_BILL_190": 22, "STATUS_190": "STTOBILL07"},  # 190 not stuck
        {"ID_ITEM_TO_BILL": 14, "ID_ITEM_TO_BILL_190": None, "STATUS_190": None},
    ]
    assert wsh.collect_correction_item_ids(rows) == [11, 21, 12, 13, 14]


def test_correction_script_matches_rj_template():
    sql = wsh.build_correction_script([11, 21])
    assert "update GCCOM_ITEMS_TO_BILL" in sql
    assert "set STATUS         = 'STTOBILL01'," in sql
    assert "UPDATE_PROGRAM = 'WRONG_ITB_HIERARCHY_STATUS'," in sql
    assert "UPDATE_USER    = 'RMA'," in sql
    assert "UPDATE_DATE    = getdate()" in sql
    assert "where ID_ITEM_TO_BILL in" in sql
    assert "(11,\n       21)" in sql
    assert "and STATUS = 'STTOBILL09';" in sql


def test_correction_script_chunks_large_lists():
    ids = list(range(1, wsh.CORRECTION_CHUNK_SIZE + 3))
    sql = wsh.build_correction_script(ids)
    assert sql.count("update GCCOM_ITEMS_TO_BILL") == 2


def test_correction_script_clean_has_no_comments_and_custom_program():
    sql = wsh.build_correction_script([1], program="JIRA-1", user="X", clean=True)
    assert "--" not in sql
    assert "UPDATE_PROGRAM = 'JIRA-1'" in sql
    assert "UPDATE_USER    = 'X'" in sql


def test_correction_script_requires_ids():
    with pytest.raises(ValueError):
        wsh.build_correction_script([])


def test_sanitary_tab_links_sanitary_190_and_water_19_on_same_account():
    sql = wsh.build_sanitary_stuck_water_done_query()
    assert "cs.ID_OFFERED_SERVICE = 190" in sql
    assert "csw.ID_OFFERED_SERVICE = 19\n" in sql
    assert "csw.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM" in sql


def test_sanitary_tab_statuses_period_and_billing_date():
    sql = wsh.build_sanitary_stuck_water_done_query()
    assert "WHERE itb.STATUS = 'STTOBILL09'" in sql
    assert "itbw.STATUS IN ('STTOBILL07', 'STTOBILL01')" in sql
    assert "itbw.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD" in sql
    assert "CAST(itbw.BILLING_DATE AS date) = CAST(itb.BILLING_DATE AS date)" in sql
    assert "cs.STATUS IN ('ESTSC00002', 'ESTSC00003', 'ESTSC00007')" in sql


def test_period_must_be_integer():
    with pytest.raises(ValueError):
        wsh.build_wrong_stuck_query("1; DROP TABLE x")
