"""
Unit tests for app/core/wrong_billed_consumption.py - pure SQL-text
assertions (no DB).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import wrong_billed_consumption as w


def test_scope_billed_cycle_readings_from_feb_2023_one_period():
    sql = w.build_wrong_billed_consumption_query(10000000237)
    assert "r0.READING_TYPE = 'TIPTL00003'" in sql
    assert "r0.READING_DATE >= '2023-02-01'" in sql
    assert "r0.ID_BILLING_PERIOD = 10000000237" in sql
    assert "r0.READ_STATUS = '7000STSRED'" in sql
    assert "r0.USAGE_TYPE IN ('TPCONS0001', 'TPCONS0006')" in sql


def test_only_bills_of_the_readings_own_contracted_service():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bill.ID_CONTRACTED_SERVICE" in sql
    assert "cs.ID_SECTOR_SUPPLY = r0.ID_SECTOR_SUPPLY" in sql


def test_bill_must_be_invoiced_or_generated_and_not_credit_note():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_BILL bill ON bill.ID_BILL = itb.ID_BILL" in sql
    assert "bill.BILLING_STATUS IN ('ESTFAC0005', 'ESTFAC0008')" in sql
    assert "ISNULL(bill.BILLING_TYPE, '') <> 'TIPFAC0011'" in sql


def test_calculation_base_from_bill_concepts():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "SUM(bd.CALCULATION_BASE) AS BILLED_CONSUMPTION" in sql
    assert "bc.COD_CONCEPT IN ('CONCSMO003', 'CC210')" in sql
    assert "JOIN calc ON calc.ID_BILL = g.ID_BILL" in sql


def test_compares_readings_ready_usage_to_calculation_base():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "SUM(r.READY_USAGE) AS READY_USAGE" in sql
    assert "calc.BILLED_CONSUMPTION - ISNULL(g.READY_USAGE, 0) AS DIFFERENCE" in sql
    assert "WHERE ABS(calc.BILLED_CONSUMPTION - ISNULL(g.READY_USAGE, 0)) > 0.001" in sql
    # the old link-row usage comparison is gone
    assert "BILLED_READY_USAGE" not in sql


def test_grouped_per_supply_usage_type_and_bill():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "GROUP BY r.ID_SECTOR_SUPPLY, r.USAGE_TYPE, rb.ID_BILL" in sql
    assert "COUNT(*) AS READING_COUNT" in sql
    assert "AS ID_READINGS" in sql and "AS READING_DETAILS" in sql


def test_multiplier_and_mp_type():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "LEFT JOIN GCGT_ME_USAGE_TYPE_METER um ON um.ID_DEVICE = r.ID_DEVICE AND um.COD_USAGE_TYPE = r.USAGE_TYPE" in sql
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT mp ON mp.ID_MEASURING_POINT = r.ID_MEASURING_POINT" in sql
    assert "THEN MIN(mp.MP_TYPE) ELSE 'MIXED' END AS MP_TYPE" in sql


def test_requested_columns_in_order():
    sql = w.build_wrong_billed_consumption_query(1)
    order = ["AS NISS", "AS READ_STATUS", "AS USAGE_TYPE", "AS PREV_VALUE", "AS VALUE", "AS READING_USAGE",
             "AS CORRECTED_USAGE", "AS MULTIPLIER", "AS READY_USAGE", "AS ID_CONTRACTED_SERVICE", "AS ID_BILL",
             "AS ID_ITEM_TO_BILL", "AS BILLED_CONSUMPTION", "AS DIFFERENCE"]
    select = sql[sql.index("SELECT\n    ss.NISS"):]
    positions = [select.index(a) for a in order]
    assert positions == sorted(positions)


def test_period_must_be_integer():
    with pytest.raises(ValueError):
        w.build_wrong_billed_consumption_query("x")


def test_billing_periods_query_from_feb_2023():
    sql = w.build_billing_periods_query()
    assert "OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD bp" in sql
    assert "'2023-02-01'" in sql
    assert "ORDER BY bp.INITIAL_DATE DESC" in sql
