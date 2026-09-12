"""
Compares two independent snapshots of the same table (as exported via
app/db/internal_store.py) and reports which rows were added, removed, or
changed between them.

This is a different problem from app.core.diff_engine: diff_engine
compares the SAME grid before/after edits, so rows line up positionally
(row 3 in the original is row 3 in the edited version) and there's
nothing to "match up" first. Two snapshots are independent exports -
rows can be reordered, added, or removed between them - so rows have to
be matched by a chosen key column (or the full row, if no key is given)
before a per-column diff means anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SnapshotCellChange:
    column: str
    old_value: str  # snapshot A's value (as text - snapshots are stored as TEXT columns)
    new_value: str  # snapshot B's value


@dataclass
class SnapshotRowDiff:
    key: dict[str, str]           # key column(s) -> value, or the full row if no key was given
    status: str                   # "added" | "removed" | "changed"
    cell_changes: list[SnapshotCellChange] = field(default_factory=list)  # only set for "changed"


@dataclass
class SnapshotDiffResult:
    added: list[SnapshotRowDiff]
    removed: list[SnapshotRowDiff]
    changed: list[SnapshotRowDiff]
    unchanged_count: int
    key_is_full_row: bool


def _row_key(row: list[Any], col_index: dict[str, int], key_columns: list[str]) -> tuple:
    return tuple(row[col_index[c]] for c in key_columns)


def diff_snapshots(
    columns: list[str],
    rows_a: list[list[Any]],
    rows_b: list[list[Any]],
    key_columns: list[str],
) -> SnapshotDiffResult:
    """
    columns:      shared column list (both snapshots must have the same
                  columns - they're exports of the same table/query).
    rows_a:       the OLDER snapshot's rows.
    rows_b:       the NEWER snapshot's rows.
    key_columns:  which column(s) identify "the same row" across the two
                  snapshots. If empty, the full row is used as the key
                  instead (a row is only ever "changed" if some OTHER
                  key differs, so with no key columns nothing can be
                  detected as "changed" - a modified row just looks like
                  one row removed + one row added, which is the honest
                  answer when there's no way to know it's the "same" row).
    """
    col_index = {name: i for i, name in enumerate(columns)}
    key_is_full_row = len(key_columns) == 0
    effective_key_cols = key_columns if key_columns else columns

    by_key_a = {_row_key(row, col_index, effective_key_cols): row for row in rows_a}
    by_key_b = {_row_key(row, col_index, effective_key_cols): row for row in rows_b}

    keys_a = set(by_key_a)
    keys_b = set(by_key_b)

    def _key_dict(key_tuple: tuple) -> dict[str, str]:
        return dict(zip(effective_key_cols, key_tuple))

    added = [
        SnapshotRowDiff(key=_key_dict(k), status="added")
        for k in sorted(keys_b - keys_a, key=str)
    ]
    removed = [
        SnapshotRowDiff(key=_key_dict(k), status="removed")
        for k in sorted(keys_a - keys_b, key=str)
    ]

    changed: list[SnapshotRowDiff] = []
    unchanged_count = 0
    for k in sorted(keys_a & keys_b, key=str):
        row_a, row_b = by_key_a[k], by_key_b[k]
        cell_changes = [
            SnapshotCellChange(column=col, old_value=row_a[col_index[col]], new_value=row_b[col_index[col]])
            for col in columns
            if row_a[col_index[col]] != row_b[col_index[col]]
        ]
        if cell_changes:
            changed.append(SnapshotRowDiff(key=_key_dict(k), status="changed", cell_changes=cell_changes))
        else:
            unchanged_count += 1

    return SnapshotDiffResult(
        added=added, removed=removed, changed=changed,
        unchanged_count=unchanged_count, key_is_full_row=key_is_full_row,
    )
