"""
Unit tests for app/core/wrong_billed_consumption.py - pure SQL-text
assertions (no DB).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import wrong_billed_consumption as w


def test_starts_from_bills_of_the_period_with_calculation_base():
    sql = w.build_wrong_billed_consumption_query(10000000237)
    assert "FROM OUC_COMMON_ADMIN.GCCOM_BILL b" in sql
    assert "WHERE b.ID_BILLING_PERIOD = 10000000237" in sql
    assert "SUM(bd.CALCULATION_BASE) AS CALCULATION_BASE" in sql
    assert "bc.COD_CONCEPT IN ('CONCSMO003', 'CC210')" in sql
    assert "b.BILLING_STATUS IN ('ESTFAC0005', 'ESTFAC0008')" in sql
    assert "ISNULL(b.BILLING_TYPE, '') <> 'TIPFAC0011'" in sql


def test_same_bill_to_items_to_bill_to_reading_links():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "JOIN GCCOM_ITEMS_TO_BILL itb ON itb.ID_BILL = cb.ID_BILL" in sql
    assert "JOIN OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL rit ON rit.ID_ITEM_TO_BILL = itb.ID_ITEM_TO_BILL" in sql
    assert "SUM(READY_USAGE) AS RIT_READY_USAGE" in sql
    assert "r1.USAGE_TYPE IN ('TPCONS0001', 'TPCONS0006')" in sql


def test_reading_ready_usage_over_distinct_readings():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "FROM (SELECT DISTINCT ID_BILL, ID_READING FROM lk) x" in sql
    assert "SUM(r.READY_USAGE) AS READING_READY_USAGE" in sql


def test_three_way_differences_and_filter():
    sql = w.build_wrong_billed_consumption_query(1)
    assert "AS DIFF_CALC_VS_RIT" in sql
    assert "AS DIFF_CALC_VS_READING" in sql
    assert "AS DIFF_RIT_VS_READING" in sql
    assert "WHERE ABS(cb.CALCULATION_BASE - ISNULL(rs.RIT_READY_USAGE, 0)) > 0.001" in sql
    assert "OR ABS(cb.CALCULATION_BASE - ISNULL(rr.READING_READY_USAGE, 0)) > 0.001" in sql
    assert "OR ABS(ISNULL(rs.RIT_READY_USAGE, 0) - ISNULL(rr.READING_READY_USAGE, 0)) > 0.001" in sql


def test_bill_range_chunk():
    sql = w.build_wrong_billed_consumption_query(1, 100, 199)
    assert "AND b.ID_BILL BETWEEN 100 AND 199" in sql
    assert "BETWEEN" not in w.build_wrong_billed_consumption_query(1)


def test_bill_id_chunks():
    assert w.bill_id_chunks(1, 10, 4) == [(1, 4), (5, 8), (9, 10)]
    assert w.bill_id_chunks(5, 5, 100) == [(5, 5)]
    assert w.bill_id_chunks(None, None, 10) == []


def test_bill_boundaries_query_and_chunks():
    sql = w.build_bill_boundaries_query(10000000237, 50000)
    assert "ROW_NUMBER() OVER (ORDER BY b.ID_BILL) AS RN" in sql
    assert "WHERE (x.RN - 1) % 50000 = 0" in sql
    assert w.chunks_from_boundaries([1, 50, 120], 200) == [(1, 49), (50, 119), (120, 200)]
    assert w.chunks_from_boundaries([], 10) == []


def test_bill_range_query():
    sql = w.build_bill_range_query(10000000237)
    assert "MIN(b.ID_BILL) AS MIN_ID_BILL" in sql and "COUNT(*) AS BILL_COUNT" in sql
    assert "WHERE b.ID_BILLING_PERIOD = 10000000237" in sql


def test_period_must_be_integer():
    with pytest.raises(ValueError):
        w.build_wrong_billed_consumption_query("x")


def test_billing_periods_query_flags_current():
    sql = w.build_billing_periods_query()
    assert "AS IS_CURRENT" in sql
    assert "ORDER BY bp.INITIAL_DATE DESC" in sql
