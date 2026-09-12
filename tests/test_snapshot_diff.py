"""
Pure-logic tests for app.core.snapshot_diff - comparing two independent
snapshot exports of the same table (as opposed to diff_engine, which
compares the same grid before/after edits).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.snapshot_diff import diff_snapshots


def test_no_changes():
    columns = ["id", "name"]
    rows_a = [["1", "Alice"], ["2", "Bob"]]
    rows_b = [["1", "Alice"], ["2", "Bob"]]
    result = diff_snapshots(columns, rows_a, rows_b, key_columns=["id"])
    assert result.added == []
    assert result.removed == []
    assert result.changed == []
    assert result.unchanged_count == 2


def test_added_and_removed_rows():
    columns = ["id", "name"]
    rows_a = [["1", "Alice"], ["2", "Bob"]]
    rows_b = [["1", "Alice"], ["3", "Carla"]]
    result = diff_snapshots(columns, rows_a, rows_b, key_columns=["id"])
    assert len(result.added) == 1 and result.added[0].key == {"id": "3"}
    assert len(result.removed) == 1 and result.removed[0].key == {"id": "2"}
    assert result.unchanged_count == 1


def test_changed_row_reports_cell_diff():
    columns = ["id", "name", "amount"]
    rows_a = [["1", "Alice", "10"]]
    rows_b = [["1", "Alice", "20"]]
    result = diff_snapshots(columns, rows_a, rows_b, key_columns=["id"])
    assert len(result.changed) == 1
    change = result.changed[0]
    assert change.key == {"id": "1"}
    assert len(change.cell_changes) == 1
    assert change.cell_changes[0].column == "amount"
    assert change.cell_changes[0].old_value == "10"
    assert change.cell_changes[0].new_value == "20"


def test_no_key_columns_falls_back_to_full_row():
    columns = ["name", "amount"]
    rows_a = [["Alice", "10"]]
    rows_b = [["Alice", "20"]]
    result = diff_snapshots(columns, rows_a, rows_b, key_columns=[])
    # With no key, a modified row can't be recognized as "the same row" -
    # it just looks like one row removed and a different one added.
    assert result.key_is_full_row is True
    assert result.changed == []
    assert len(result.removed) == 1
    assert len(result.added) == 1


def test_row_order_does_not_matter():
    columns = ["id", "name"]
    rows_a = [["1", "Alice"], ["2", "Bob"]]
    rows_b = [["2", "Bob"], ["1", "Alice"]]
    result = diff_snapshots(columns, rows_a, rows_b, key_columns=["id"])
    assert result.added == []
    assert result.removed == []
    assert result.unchanged_count == 2


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
