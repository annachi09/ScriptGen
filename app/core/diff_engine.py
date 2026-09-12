"""
Tracks the difference between the original query result and whatever
the user has typed into the results grid, and turns that into a list
of per-row changes ready for the script generator.

Grid editing note: tksheet (like any plain text grid) hands us edited
cells back as strings. To keep generated UPDATE statements correctly
typed (numbers unquoted, strings quoted, etc.) we try to coerce each
edited string back to the ORIGINAL value's Python type before handing
it to sql_format.format_sql_literal. See coerce_edited_value below for
the exact rules, including the NULL convention.
"""
from __future__ import annotations

import datetime
import decimal
from dataclasses import dataclass, field
from typing import Any, Optional


NULL_SENTINEL = "NULL"  # what the user types in a cell to mean SQL NULL


def cell_display(value: Any) -> str:
    """How an original DB value is first rendered into an editable grid cell."""
    if value is None:
        return ""
    return str(value)


def coerce_edited_value(edited_str: str, original_value: Any) -> Any:
    """
    Best-effort coercion of a hand-typed grid string back to the type
    of the original value, so the generated SQL literal is well-typed.
    Falls back to the raw string if coercion fails - the resulting SQL
    will then just be a quoted string literal, which is always valid
    syntactically even if not the "ideal" type.
    """
    stripped = edited_str.strip()

    if stripped.upper() == NULL_SENTINEL:
        return None

    if original_value is None:
        # No type hint available from the original value (it was NULL).
        # Try the obvious numeric/bool guesses, else keep as text.
        for caster in (int, float):
            try:
                return caster(stripped)
            except (ValueError, TypeError):
                continue
        return edited_str

    if isinstance(original_value, bool):
        lowered = stripped.lower()
        if lowered in ("1", "true", "t", "yes", "y"):
            return True
        if lowered in ("0", "false", "f", "no", "n"):
            return False
        return edited_str  # let it fall through as text; caller can fix

    if isinstance(original_value, int):
        try:
            return int(stripped)
        except ValueError:
            try:
                return float(stripped)
            except ValueError:
                return edited_str

    if isinstance(original_value, float):
        try:
            return float(stripped)
        except ValueError:
            return edited_str

    if isinstance(original_value, decimal.Decimal):
        try:
            return decimal.Decimal(stripped)
        except decimal.InvalidOperation:
            return edited_str

    if isinstance(original_value, datetime.datetime):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(stripped, fmt)
            except ValueError:
                continue
        return edited_str

    if isinstance(original_value, datetime.date):
        try:
            return datetime.datetime.strptime(stripped, "%Y-%m-%d").date()
        except ValueError:
            return edited_str

    # Original was already a string (or something else) - keep as-is.
    return edited_str


@dataclass
class CellChange:
    column: str
    old_value: Any
    new_value: Any


@dataclass
class RowChange:
    row_index: int
    key_predicate: dict[str, Any]   # column -> ORIGINAL value, used in WHERE
    key_is_full_row: bool           # True when no key columns were available/chosen
    cell_changes: list[CellChange] = field(default_factory=list)


def compute_row_changes(
    columns: list[str],
    original_rows: list[list[Any]],
    edited_rows: list[list[str]],
    key_columns: list[str],
) -> list[RowChange]:
    """
    columns:       column names, in grid order
    original_rows: python-typed values as returned by the query
    edited_rows:   current grid contents (strings) - same shape as original_rows
    key_columns:   which columns uniquely identify a row (usually the PK).
                   If empty, every non-key column is used to build the WHERE
                   clause instead (full-row match) and key_is_full_row=True
                   is set on every result so the UI/generator can warn about
                   the risk of matching more than one row.
    """
    if len(original_rows) != len(edited_rows):
        raise ValueError("original_rows and edited_rows must have the same length")

    col_index = {name: i for i, name in enumerate(columns)}
    key_is_full_row = len(key_columns) == 0
    effective_key_cols = key_columns if key_columns else columns

    changes: list[RowChange] = []

    for row_idx, (orig_row, edit_row) in enumerate(zip(original_rows, edited_rows)):
        if len(orig_row) != len(edit_row):
            raise ValueError(f"Row {row_idx}: column count mismatch between original and edited data")

        key_predicate = {col: orig_row[col_index[col]] for col in effective_key_cols}

        cell_changes: list[CellChange] = []
        for col in columns:
            idx = col_index[col]
            original_value = orig_row[idx]
            edited_str = edit_row[idx]
            # Unedited cells arrive back exactly as cell_display() produced them.
            if edited_str == cell_display(original_value):
                continue
            new_value = coerce_edited_value(edited_str, original_value)
            # Guard against false positives, e.g. "3" vs 3 both display "3".
            if new_value == original_value:
                continue
            cell_changes.append(CellChange(column=col, old_value=original_value, new_value=new_value))

        if cell_changes:
            changes.append(
                RowChange(
                    row_index=row_idx,
                    key_predicate=key_predicate,
                    key_is_full_row=key_is_full_row,
                    cell_changes=cell_changes,
                )
            )

    return changes
