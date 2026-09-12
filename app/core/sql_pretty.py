"""
Reformats a hand-typed SQL query for readability - keyword
capitalization, one clause/column per line, consistent indentation -
via sqlparse (pure Python, no compiled extensions, same reasoning as
every other dependency in this project: nothing here should ever need
a C build toolchain on the machine building the .exe).

Purely cosmetic: sqlparse re-lays-out tokens, it doesn't parse SQL
Server's dialect or validate anything, so this never changes what a
query MEANS - only how it looks. It's meant for the query editor on
the Workspace page ("Format" button next to Run Query), not for the
generated UPDATE/rollback scripts, which already lay themselves out
deliberately in script_generator.py.
"""
from __future__ import annotations

import sqlparse


def prettify_sql(sql: str) -> str:
    """
    Returns a reformatted copy of sql. Blank/whitespace-only input is
    returned unchanged. sqlparse doesn't validate SQL, it just re-lays-
    out tokens - text that isn't valid SQL comes back re-indented, not
    rejected, so this is always safe to call speculatively.
    """
    text = sql.strip()
    if not text:
        return sql
    formatted = sqlparse.format(
        text,
        reindent=True,
        keyword_case="upper",
        identifier_case=None,
        indent_width=4,
        use_space_around_operators=True,
    )
    return formatted.strip() + "\n"
