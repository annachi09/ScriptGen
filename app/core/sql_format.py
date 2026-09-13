"""
Formats Python values (as returned by pytds, or typed by the user in
the grid) into T-SQL literals suitable for an UPDATE ... SET / WHERE
clause. Centralized here so the diff engine and script generator never
hand-roll quoting themselves.
"""
from __future__ import annotations

import datetime
import decimal
import re
from typing import Any

# Reserved/keyword-ish identifiers that MUST stay bracket-quoted even
# though they otherwise look like a plain name - unquoted, these would
# either fail to parse or silently mean something else to SQL Server.
# Not the full T-SQL reserved-word list (that's ~180 words); this is the
# practical subset most likely to actually collide with a real column
# name (e.g. an audit column literally called "user" or "key"), plus a
# few this app's own generated scripts already use as fixed identifiers.
_RESERVED_IDENTIFIERS = {
    "user", "key", "order", "group", "default", "select", "update", "delete",
    "insert", "table", "index", "primary", "foreign", "references", "check",
    "constraint", "column", "database", "schema", "view", "procedure",
    "function", "trigger", "transaction", "begin", "end", "if", "else",
    "while", "case", "when", "then", "null", "true", "false", "and", "or",
    "not", "in", "is", "like", "between", "exists", "all", "any", "some",
    "union", "join", "inner", "outer", "left", "right", "on", "as", "from",
    "where", "having", "distinct", "top", "into", "values", "set", "by",
    "date", "identity", "public", "system", "unique", "with",
}
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def format_sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"

    if isinstance(value, bool):
        # SQL Server bit literal
        return "1" if value else "0"

    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)

    if isinstance(value, (bytes, bytearray)):
        return "0x" + value.hex()

    if isinstance(value, datetime.datetime):
        return "'" + value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "'"

    if isinstance(value, datetime.date):
        return "'" + value.strftime("%Y-%m-%d") + "'"

    if isinstance(value, datetime.time):
        return "'" + value.strftime("%H:%M:%S") + "'"

    # Fallback: string (also covers values typed by hand in the grid,
    # which arrive as str even if the destination column is numeric -
    # see coerce_to_column_type in diff_engine for that case).
    #
    # Plain '...' (NOT N'...') on purpose - real bug found live 2026-09-17
    # while chasing Case 2 detection-query timeouts: every status/code
    # column this app filters on (GCCOM_BILL.BILLING_STATUS, GCCOM_
    # CONTRACTED_SERVICE.STATUS, etc, confirmed live via sys.columns) is
    # plain varchar, not nvarchar. Comparing a varchar column to an N'...'
    # (nvarchar) literal makes SQL Server implicitly CONVERT() the column
    # to compare them - which silently disables index seeks on every
    # index that leads with that column. A query built with N'...'
    # literals against these columns timed out at 120s three different
    # ways (OR EXISTS, UNION, direct join - the join SHAPE was never the
    # problem); the exact same query with plain '...' literals ran in
    # ~7s. A plain '...' literal is safe either way - SQL Server still
    # matches it correctly against a genuinely nvarchar column, it just
    # doesn't force a conversion (and therefore doesn't break an index)
    # on the varchar columns this app actually has. This one change
    # benefits every query builder in app/core that goes through this
    # function, not just Bill Issuance Validator.
    text = str(value)
    escaped = text.replace("'", "''")
    return "'" + escaped + "'"


def quote_ident(name: str) -> str:
    """
    Renders a SQL Server identifier (table/column name) for use in a
    generated script. Left unquoted when that's safe (plain
    letters/digits/underscore, not a reserved word) so the script reads
    like ordinary hand-written SQL - UPDATE gccom_payment_form instead
    of UPDATE [gccom_payment_form]. Falls back to bracket-quoting
    (escaping any embedded ']') when the name isn't safe unquoted: it
    contains a space or other special character, starts with a digit,
    or collides with a SQL Server reserved word (e.g. a column
    literally named "user" or "key") - any of which would otherwise
    produce a script that fails to parse or means something other than
    what was intended.
    """
    text = str(name)
    if _SAFE_IDENT_RE.match(text) and text.lower() not in _RESERVED_IDENTIFIERS:
        return text
    return "[" + text.replace("]", "]]") + "]"


def qualify_table(schema: str | None, table: str) -> str:
    if schema:
        return f"{quote_ident(schema)}.{quote_ident(table)}"
    return quote_ident(table)
