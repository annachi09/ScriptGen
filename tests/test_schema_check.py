"""
Pure-logic tests for app.core.schema_check - the "Validate Target
Schema" tool. No database involved: table_columns is exactly what
app.db.mssql.get_table_columns would return.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.schema_check import validate_columns


def test_table_not_found():
    result = validate_columns(["id", "name"], ["id"], table_columns={})
    assert result.table_exists is False
    assert result.ok is False


def test_all_columns_present_is_ok():
    result = validate_columns(
        ["id", "name"], ["id"],
        table_columns={"id": "int", "name": "varchar", "created_at": "datetime"},
    )
    assert result.ok is True
    assert result.missing_columns == []
    assert result.missing_key_columns == []
    assert result.extra_table_columns == ["created_at"]


def test_missing_query_column_detected():
    result = validate_columns(
        ["id", "nickname"], ["id"],
        table_columns={"id": "int", "name": "varchar"},
    )
    assert result.ok is False
    assert result.missing_columns == ["nickname"]


def test_missing_key_column_detected():
    result = validate_columns(
        ["id", "name"], ["uuid"],
        table_columns={"id": "int", "name": "varchar"},
    )
    assert result.ok is False
    assert result.missing_key_columns == ["uuid"]


def test_case_insensitive_column_matching():
    # SQL Server identifiers are case-insensitive by default; a query
    # column cased differently from INFORMATION_SCHEMA's answer isn't a
    # real mismatch.
    result = validate_columns(
        ["ID", "Name"], ["ID"],
        table_columns={"id": "int", "name": "varchar"},
    )
    assert result.ok is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
