"""
Pure-logic half of "Validate Target Schema": given the query's column
list and the target table's actual columns (as looked up from
INFORMATION_SCHEMA by app.db.mssql.get_table_columns), reports whether
the table looks like a sane target for the generated UPDATE script -
before the user finds out the hard way when someone tries to run it.

Deliberately just existence checks (does the table exist, does every
result column exist on it, do the chosen key columns exist on it) - not
a full type-compatibility checker. Catching "target table doesn't
exist" or "you renamed a column and forgot to update Target Table"
covers the most common real mistakes without pretending to validate
data types SQL Server itself would happily coerce anyway.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SchemaValidationResult:
    table_exists: bool
    missing_columns: list[str] = field(default_factory=list)      # query columns not on the table
    missing_key_columns: list[str] = field(default_factory=list)  # chosen key columns not on the table
    extra_table_columns: list[str] = field(default_factory=list)  # on the table but not in the query (informational)

    @property
    def ok(self) -> bool:
        return self.table_exists and not self.missing_columns and not self.missing_key_columns


def validate_columns(
    query_columns: list[str],
    key_columns: list[str],
    table_columns: dict[str, str],
) -> SchemaValidationResult:
    if not table_columns:
        return SchemaValidationResult(table_exists=False)

    # SQL Server identifiers are case-insensitive by default (and this
    # tool has no way to know the target's actual collation), so compare
    # case-insensitively rather than risk a false "missing" on a table
    # whose columns just happen to be cased differently than the query.
    table_cols_lower = {c.lower() for c in table_columns}

    missing_columns = [c for c in query_columns if c.lower() not in table_cols_lower]
    missing_key_columns = [c for c in key_columns if c.lower() not in table_cols_lower]
    query_cols_lower = {c.lower() for c in query_columns}
    extra_table_columns = [c for c in table_columns if c.lower() not in query_cols_lower]

    return SchemaValidationResult(
        table_exists=True,
        missing_columns=missing_columns,
        missing_key_columns=missing_key_columns,
        extra_table_columns=extra_table_columns,
    )
