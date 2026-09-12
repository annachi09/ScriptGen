import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.stats import (
    compute_column_stats,
    numeric_columns,
    categorical_columns,
    numeric_bucket_histogram,
    summary_counts,
    pearson_correlation,
    duplicate_row_count,
    data_quality_flags,
)


def test_numeric_column_stats():
    columns = ["id", "amount"]
    rows = [[1, 10.0], [2, 20.0], [3, 30.0]]
    stats = compute_column_stats(columns, rows)

    assert stats["amount"].kind == "numeric"
    assert stats["amount"].non_null_count == 3
    assert stats["amount"].null_count == 0
    assert stats["amount"].min_value == 10.0
    assert stats["amount"].max_value == 30.0
    assert stats["amount"].mean_value == 20.0
    assert stats["amount"].sum_value == 60.0


def test_categorical_column_stats():
    columns = ["status"]
    rows = [["active"], ["active"], ["inactive"], [None]]
    stats = compute_column_stats(columns, rows)

    s = stats["status"]
    assert s.kind == "categorical"
    assert s.non_null_count == 3
    assert s.null_count == 1
    assert s.distinct_count == 2
    assert s.top_values[0] == ("active", 2)


def test_empty_column_all_null():
    columns = ["notes"]
    rows = [[None], [None]]
    stats = compute_column_stats(columns, rows)
    assert stats["notes"].kind == "empty"
    assert stats["notes"].null_count == 2


def test_mixed_types_treated_as_categorical():
    columns = ["mixed"]
    rows = [[1], ["two"], [3]]
    stats = compute_column_stats(columns, rows)
    assert stats["mixed"].kind == "categorical"


def test_bool_column_treated_as_categorical_not_numeric():
    columns = ["active"]
    rows = [[True], [False], [True]]
    stats = compute_column_stats(columns, rows)
    assert stats["active"].kind == "categorical"
    assert stats["active"].top_values[0] == ("True", 2)


def test_numeric_and_categorical_column_helpers():
    columns = ["id", "name", "amount"]
    rows = [[1, "a", 10.0], [2, "b", 20.0]]
    stats = compute_column_stats(columns, rows)
    assert set(numeric_columns(stats)) == {"id", "amount"}
    assert set(categorical_columns(stats)) == {"name"}


def test_stdev_single_value_is_zero_not_error():
    columns = ["x"]
    rows = [[5]]
    stats = compute_column_stats(columns, rows)
    assert stats["x"].stdev_value == 0.0


def test_histogram_basic_bucketing():
    rows = [[0], [1], [2], [9], [10]]
    labels, counts = numeric_bucket_histogram(rows, col_index=0, bins=5)
    assert len(labels) == 5
    assert sum(counts) == 5


def test_histogram_all_same_value():
    rows = [[7], [7], [7]]
    labels, counts = numeric_bucket_histogram(rows, col_index=0, bins=5)
    assert labels == ["7"]
    assert counts == [3]


def test_histogram_empty_rows():
    labels, counts = numeric_bucket_histogram([], col_index=0)
    assert labels == []
    assert counts == []


def test_distinct_count_ignores_nulls():
    columns = ["x"]
    rows = [[1], [1], [None], [2]]
    stats = compute_column_stats(columns, rows)
    assert stats["x"].distinct_count == 2


def test_summary_counts_rollup():
    columns = ["id", "name", "notes", "amount"]
    rows = [[1, "a", None, 10.0], [2, "b", None, 20.0]]
    stats = compute_column_stats(columns, rows)
    counts = summary_counts(stats)
    assert counts == {
        "numeric_columns": 2,
        "categorical_columns": 1,
        "empty_columns": 1,
        "total_nulls": 2,  # both `notes` cells are NULL
    }


# ---------- pearson_correlation ----------

def test_pearson_perfect_positive_correlation():
    rows = [[1, 10], [2, 20], [3, 30], [4, 40]]
    r = pearson_correlation(rows, 0, 1)
    assert r == pytest_approx(1.0)


def test_pearson_perfect_negative_correlation():
    rows = [[1, 40], [2, 30], [3, 20], [4, 10]]
    r = pearson_correlation(rows, 0, 1)
    assert r == pytest_approx(-1.0)


def test_pearson_no_correlation_returns_none_for_zero_variance():
    rows = [[1, 5], [2, 5], [3, 5]]  # column 1 never varies
    assert pearson_correlation(rows, 0, 1) is None


def test_pearson_ignores_rows_with_non_numeric_or_missing_values():
    rows = [[1, 10], [2, None], ["x", 30], [4, 40]]
    r = pearson_correlation(rows, 0, 1)
    # Only (1,10) and (4,40) are usable pairs - still a valid (perfect) correlation.
    assert r == pytest_approx(1.0)


def test_pearson_too_few_pairs_returns_none():
    rows = [[1, 10]]
    assert pearson_correlation(rows, 0, 1) is None


# ---------- median / percentiles / outliers / unique_ratio ----------

def test_numeric_median_and_percentiles():
    columns = ["x"]
    rows = [[i] for i in range(1, 11)]  # 1..10
    stats = compute_column_stats(columns, rows)
    s = stats["x"]
    assert s.median_value == pytest_approx(5.5)
    assert s.p25_value == pytest_approx(3.25)
    assert s.p75_value == pytest_approx(7.75)
    assert s.outlier_count == 0


def test_numeric_single_value_median_and_percentiles_equal_that_value():
    stats = compute_column_stats(["x"], [[5]])
    s = stats["x"]
    assert s.median_value == 5.0
    assert s.p25_value == 5.0
    assert s.p75_value == 5.0
    assert s.outlier_count == 0


def test_outlier_count_flags_value_outside_tukey_fence():
    columns = ["x"]
    rows = [[1], [2], [3], [4], [5], [100]]  # 100 is a clear outlier
    stats = compute_column_stats(columns, rows)
    assert stats["x"].outlier_count == 1


def test_unique_ratio_numeric_column():
    columns = ["x"]
    rows = [[1], [1], [2], [3]]  # 3 distinct of 4 non-null
    stats = compute_column_stats(columns, rows)
    assert stats["x"].unique_ratio == pytest_approx(0.75)


def test_unique_ratio_categorical_column():
    columns = ["status"]
    rows = [["a"], ["a"], ["b"]]  # 2 distinct of 3 non-null
    stats = compute_column_stats(columns, rows)
    assert stats["status"].unique_ratio == pytest_approx(2 / 3)


def test_unique_ratio_none_for_empty_column():
    stats = compute_column_stats(["notes"], [[None], [None]])
    assert stats["notes"].unique_ratio is None


# ---------- duplicate_row_count ----------

def test_duplicate_row_count_no_duplicates():
    rows = [[1, "a"], [2, "b"], [3, "c"]]
    assert duplicate_row_count(rows) == 0


def test_duplicate_row_count_counts_repeats_beyond_first_occurrence():
    rows = [[1], [1], [1]]
    assert duplicate_row_count(rows) == 2  # 2nd and 3rd copies are duplicates


def test_duplicate_row_count_mixed():
    rows = [[1, "a"], [2, "b"], [1, "a"], [3, "c"]]
    assert duplicate_row_count(rows) == 1


def test_duplicate_row_count_empty():
    assert duplicate_row_count([]) == 0


# ---------- data_quality_flags ----------

def test_data_quality_flags_likely_key():
    stats = compute_column_stats(["id"], [[1], [2], [3]])
    flags = data_quality_flags(stats)
    assert any(f["kind"] == "likely_key" and f["column"] == "id" for f in flags)


def test_data_quality_flags_constant_column():
    stats = compute_column_stats(["status"], [["A"], ["A"], ["A"]])
    flags = data_quality_flags(stats)
    assert any(f["kind"] == "constant" and f["column"] == "status" for f in flags)


def test_data_quality_flags_single_row_result_has_no_flags():
    # A one-row result makes every column trivially "100% unique" - not a
    # meaningful signal, so likely_key/constant should both be suppressed.
    stats = compute_column_stats(["id", "status"], [[1, "A"]])
    assert data_quality_flags(stats) == []


def test_data_quality_flags_high_nulls():
    columns = ["notes"]
    rows = [[None], [None], [None], ["x"], ["y"]]  # 3/5 = 60% NULL
    stats = compute_column_stats(columns, rows)
    flags = data_quality_flags(stats)
    assert any(f["kind"] == "high_nulls" and f["column"] == "notes" for f in flags)


def test_data_quality_flags_has_outliers():
    columns = ["x"]
    rows = [[1], [2], [3], [4], [5], [100]]
    stats = compute_column_stats(columns, rows)
    flags = data_quality_flags(stats)
    assert any(f["kind"] == "has_outliers" and f["column"] == "x" for f in flags)


def pytest_approx(value, tol=1e-9):
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) < tol
    return _Approx()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
