"""
Unit tests for app/core/wrong_billed_consumption.py - pure SQL-text
assertions (no DB).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import wrong_billed_consumption as w


def test_scope_cycle_readings_from_feb_2023_one_period():
    sql = w.build_wrong_billed_consumption_query(10000000237)
    assert "r.READING_TYPE = 'TIPTL00003'" in sql
    assert "r.READING_DATE >= '2023-02-01'" in sql
    assert "r.ID_BILLING_PERIOD = 10000000237" in sql


def test_billed_usage_summed_per_reading_and_item_to_bill():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "FROM OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL rit" in sql
    assert "GROUP BY rit.ID_READING, rit.ID_ITEM_TO_BILL" in sql
    assert "SUM(rit.READY_USAGE) AS BILLED_READY_USAGE" in sql


def test_mismatch_is_billed_vs_reading_ready_usage():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "ABS(ISNULL(b.BILLED_READY_USAGE, 0) - ISNULL(r.READY_USAGE, 0)) > 0.001" in sql


def test_formula_with_multiplier_from_usage_type_meter():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "LEFT JOIN GCGT_ME_USAGE_TYPE_METER um ON um.ID_DEVICE = r.ID_DEVICE AND um.COD_USAGE_TYPE = r.USAGE_TYPE" in sql
    assert "(r.VALUE - r.PREV_VALUE) * ISNULL(um.MULTIPLIER, 1) AS EXPECTED_READY_USAGE" in sql
    assert "AS READY_USAGE_OFF_FORMULA" in sql


def test_requested_columns():
    sql = w.build_wrong_billed_consumption_query(1)
    for alias in ("ID_BILLING_PERIOD", "NISS", "READ_STATUS", "USAGE_TYPE", "PREV_VALUE", "VALUE",
                  "READING_USAGE", "CORRECTED_USAGE", "READY_USAGE", "ID_ITEM_TO_BILL", "BILLED_READY_USAGE"):
        assert f"AS {alias}" in sql


def test_bill_must_be_invoiced_or_generated_and_not_credit_note():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "JOIN GCCOM_ITEMS_TO_BILL itb ON itb.ID_ITEM_TO_BILL = b.ID_ITEM_TO_BILL" in sql
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_BILL bill ON bill.ID_BILL = itb.ID_BILL" in sql
    assert "bill.BILLING_STATUS IN ('ESTFAC0005', 'ESTFAC0008')" in sql
    assert "ISNULL(bill.BILLING_TYPE, '') <> 'TIPFAC0011'" in sql
    for alias in ("ID_BILL", "BILL_STATUS", "BILL_STATUS_DESC", "BILLING_TYPE", "BILLING_TYPE_DESC", "ITB_STATUS"):
        assert f"AS {alias}" in sql


def test_mp_type_from_measurement_point():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT mp ON mp.ID_MEASURING_POINT = r.ID_MEASURING_POINT" in sql
    assert "mp.MP_TYPE AS MP_TYPE" in sql
    assert "COALESCE(dict_mpt.text, mpt.DESCRIPTION) AS MP_TYPE_DESC" in sql


def test_read_status_billed_only():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "r.READ_STATUS = '7000STSRED'" in sql


def test_billed_consumption_from_bill_concepts():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "SUM(bd.CALCULATION_BASE) AS BILLED_CONSUMPTION" in sql
    assert "JOIN OUC_ADMIN.GCCOM_BILLING_CONCEPT_DETAIL bd ON bd.ID_BILLING_CONCEPT = bc.ID_BILLING_CONCEPT" in sql
    assert "bc.ID_BILL = bill.ID_BILL AND bc.COD_CONCEPT IN ('CONCSMO003', 'CC210')" in sql
    assert "bc_sum.BILLED_CONSUMPTION AS BILLED_CONSUMPTION" in sql


def test_period_must_be_integer():
    with pytest.raises(ValueError):
        w.build_wrong_billed_consumption_query("x")


def test_billing_periods_query_from_feb_2023():
    sql = w.build_billing_periods_query()
    assert "OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD bp" in sql
    assert "'2023-02-01'" in sql
    assert "ORDER BY bp.INITIAL_DATE DESC" in sql
