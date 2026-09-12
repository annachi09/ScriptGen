"""
Pure-logic summary statistics for a query result grid, used by the
Dashboard window. No UI, no database - takes the same (columns, rows)
shape the rest of the app already passes around, so it's easy to unit
test and easy to reuse (e.g. for a future "stats on a snapshot" view).
"""
from __future__ import annotations

import decimal
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

NUMERIC_TYPES = (int, float, decimal.Decimal)
TOP_N_DEFAULT = 10


@dataclass
class ColumnStats:
    name: str
    kind: str  # "numeric" | "categorical" | "empty"
    row_count: int
    non_null_count: int
    null_count: int
    distinct_count: int
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    mean_value: Optional[float] = None
    sum_value: Optional[float] = None
    stdev_value: Optional[float] = None
    median_value: Optional[float] = None
    p25_value: Optional[float] = None
    p75_value: Optional[float] = None
    # Count of numeric values outside [Q1 - 1.5*IQR, Q3 + 1.5*IQR] (the
    # standard Tukey fence) - a cheap, well-known "does this column have
    # weird outliers" signal for the Dashboard's data-quality section.
    # None (not 0) for a non-numeric column, same "don't guess" convention
    # every other Optional field in this dataclass already follows.
    outlier_count: Optional[int] = None
    # distinct_count / non_null_count, i.e. "of the values actually
    # present, what fraction are unique" - 1.0 means every non-null value
    # is distinct (a likely id/key column), close to 0 means the column is
    # closer to constant. None only for an all-null "empty" column, where
    # the ratio is undefined (0/0) rather than meaningfully 0.
    unique_ratio: Optional[float] = None
    top_values: list[tuple[str, int]] = field(default_factory=list)


def _is_numeric(value: Any) -> bool:
    return isinstance(value, NUMERIC_TYPES) and not isinstance(value, bool)


def _percentile(sorted_values: list[float], pct: float) -> float:
    """
    Linear-interpolation percentile (the numpy-default / Excel
    PERCENTILE.INC method) over an already-sorted list - pure Python, no
    numpy, consistent with the rest of this module. `pct` is 0..1 (0.25
    for P25, etc). Callers guarantee a non-empty list.
    """
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def compute_column_stats(
    columns: list[str],
    rows: list[list[Any]],
    top_n: int = TOP_N_DEFAULT,
) -> dict[str, ColumnStats]:
    result: dict[str, ColumnStats] = {}
    row_count = len(rows)

    for idx, col in enumerate(columns):
        values = [row[idx] for row in rows]
        non_null = [v for v in values if v is not None]
        null_count = row_count - len(non_null)

        try:
            distinct_count = len({v if not isinstance(v, (list, dict)) else str(v) for v in non_null})
        except TypeError:
            distinct_count = len({str(v) for v in non_null})

        if not non_null:
            result[col] = ColumnStats(
                name=col, kind="empty", row_count=row_count, non_null_count=0,
                null_count=null_count, distinct_count=0,
            )
            continue

        if all(_is_numeric(v) for v in non_null):
            numeric_values = [float(v) for v in non_null]
            sorted_values = sorted(numeric_values)
            p25 = _percentile(sorted_values, 0.25)
            p75 = _percentile(sorted_values, 0.75)
            iqr = p75 - p25
            # Standard Tukey fence. When iqr == 0 (every value in the
            # middle 50% is identical) the fence collapses to that single
            # value, so anything different counts as an outlier - a
            # mathematically consistent, if aggressive, reading for a
            # near-constant column with a few stray values.
            lower_fence = p25 - 1.5 * iqr
            upper_fence = p75 + 1.5 * iqr
            outlier_count = sum(1 for v in numeric_values if v < lower_fence or v > upper_fence)
            result[col] = ColumnStats(
                name=col,
                kind="numeric",
                row_count=row_count,
                non_null_count=len(non_null),
                null_count=null_count,
                distinct_count=distinct_count,
                min_value=min(numeric_values),
                max_value=max(numeric_values),
                mean_value=statistics.mean(numeric_values),
                sum_value=sum(numeric_values),
                stdev_value=statistics.pstdev(numeric_values) if len(numeric_values) > 1 else 0.0,
                median_value=statistics.median(numeric_values),
                p25_value=p25,
                p75_value=p75,
                outlier_count=outlier_count,
                unique_ratio=distinct_count / len(non_null),
            )
            continue

        counts = Counter(str(v) for v in non_null)
        top_values = counts.most_common(top_n)
        result[col] = ColumnStats(
            name=col,
            kind="categorical",
            row_count=row_count,
            non_null_count=len(non_null),
            null_count=null_count,
            distinct_count=distinct_count,
            unique_ratio=distinct_count / len(non_null),
            top_values=top_values,
        )

    return result


def numeric_columns(stats: dict[str, ColumnStats]) -> list[str]:
    return [name for name, s in stats.items() if s.kind == "numeric"]


def categorical_columns(stats: dict[str, ColumnStats]) -> list[str]:
    return [name for name, s in stats.items() if s.kind == "categorical"]


def pearson_correlation(rows: list[list[Any]], col_index_a: int, col_index_b: int) -> Optional[float]:
    """
    Pearson correlation coefficient between two numeric columns, in plain
    Python (no numpy) - consistent with the rest of this module. Only
    rows where BOTH values are present and numeric are used; returns
    None if fewer than 2 such rows exist or either column has zero
    variance (correlation is undefined, not zero, in that case).
    """
    pairs = []
    for row in rows:
        a, b = row[col_index_a], row[col_index_b]
        if _is_numeric(a) and _is_numeric(b):
            pairs.append((float(a), float(b)))

    if len(pairs) < 2:
        return None

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)

    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)

    if var_x == 0 or var_y == 0:
        return None

    return cov / (var_x ** 0.5 * var_y ** 0.5)


def duplicate_row_count(rows: list[list[Any]]) -> int:
    """
    Number of rows that are exact duplicates of an earlier row in the
    result set (i.e. total rows minus distinct rows) - a row-level data-
    quality signal alongside the per-column stats above. Cells are
    converted to str() before hashing so an unhashable cell value (a list/
    dict from a JSON/XML column, say) can't blow this up, mirroring
    compute_column_stats' own distinct_count fallback for the same reason.
    A single-row or empty result always returns 0, never divides by
    anything, so this is safe to call unconditionally.
    """
    distinct_rows: set[tuple] = set()
    duplicates = 0
    for row in rows:
        try:
            key = tuple(row)
            if key in distinct_rows:
                duplicates += 1
            else:
                distinct_rows.add(key)
        except TypeError:
            key = tuple(str(v) for v in row)
            if key in distinct_rows:
                duplicates += 1
            else:
                distinct_rows.add(key)
    return duplicates


def summary_counts(stats: dict[str, ColumnStats]) -> dict[str, int]:
    """
    Small pure-logic rollup used to feed the Dashboard's KPI card row -
    kept here (not computed inline in the UI) so it's cheaply unit
    testable without constructing any Tk/matplotlib widgets.
    """
    return {
        "numeric_columns": sum(1 for s in stats.values() if s.kind == "numeric"),
        "categorical_columns": sum(1 for s in stats.values() if s.kind == "categorical"),
        "empty_columns": sum(1 for s in stats.values() if s.kind == "empty"),
        "total_nulls": sum(s.null_count for s in stats.values()),
    }


HIGH_NULL_RATE_THRESHOLD = 0.3  # a column more than 30% NULL is worth flagging


def data_quality_flags(stats: dict[str, ColumnStats]) -> list[dict[str, Any]]:
    """
    Synthesizes plain-language data-quality observations purely from the
    already-computed per-column stats above (no new query, no new pass
    over the rows) - powers the Dashboard's "Data Quality" card. Each flag
    is {"kind": ..., "column": ..., "detail": ...}; `kind` is one of
    "likely_key" (unique_ratio == 1.0 on a result with more than one row -
    a single-row result makes every column trivially "100% unique" and
    isn't a meaningful signal), "constant" (exactly 1 distinct non-null
    value across more than one row), "high_nulls" (null rate above
    HIGH_NULL_RATE_THRESHOLD), or "has_outliers" (outlier_count > 0 on a
    numeric column). Returns [] for an empty/degenerate result rather than
    raising - this is advisory, not a correctness check.
    """
    flags: list[dict[str, Any]] = []
    for s in stats.values():
        if s.row_count <= 1 or s.non_null_count == 0:
            continue
        if s.unique_ratio == 1.0:
            flags.append({"kind": "likely_key", "column": s.name, "detail": "every non-null value is unique"})
        elif s.distinct_count == 1:
            flags.append({
                "kind": "constant", "column": s.name,
                "detail": f"only one distinct value across {s.non_null_count} row(s)",
            })
        if s.row_count and (s.null_count / s.row_count) > HIGH_NULL_RATE_THRESHOLD:
            pct = round(100 * s.null_count / s.row_count)
            flags.append({"kind": "high_nulls", "column": s.name, "detail": f"{pct}% NULL"})
        if s.kind == "numeric" and s.outlier_count:
            flags.append({
                "kind": "has_outliers", "column": s.name,
                "detail": f"{s.outlier_count} value(s) outside the normal IQR range",
            })
    return flags


def numeric_bucket_histogram(rows: list[list[Any]], col_index: int, bins: int = 12) -> tuple[list[str], list[int]]:
    """
    Returns (bucket_labels, counts) for a simple equal-width histogram of
    a numeric column - pure Python, no numpy/matplotlib dependency, so
    it's cheaply unit-testable. The dashboard just plots whatever this
    returns.
    """
    values = [float(row[col_index]) for row in rows if row[col_index] is not None]
    if not values:
        return [], []

    lo, hi = min(values), max(values)
    if lo == hi:
        return [f"{lo:g}"], [len(values)]

    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        bucket = int((v - lo) / width)
        if bucket >= bins:
            bucket = bins - 1
        counts[bucket] += 1

    labels = [f"{lo + i * width:.2g}" for i in range(bins)]
    return labels, counts
