"""
Turns a list of app.core.diff_engine.RowChange into a ready-to-review
T-SQL update script (text). Deliberately never executes anything - the
account this tool connects with is read-only by design, so the script
is meant to be reviewed and run by whoever owns write access.

Every generated statement (forward AND rollback) also stamps three
fixed audit columns - update_program, update_date, update_user - since
these are meant to run against a real production table, and "who/why/
when" needs to travel with the change itself, not live only in this
app's own local history. update_program is the one piece of that the
user supplies (a Jira/ticket number - the UI treats it as a required
field, defaulting to the placeholder DEFAULT_AUDIT_PROGRAM so it's
obviously not a real ticket if left unedited); update_date and
update_user are fixed by convention (GETDATE() computed by SQL Server
itself at run time, not by this app, since the actual run time is
whenever someone executes the reviewed script - not whenever it was
generated).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from .diff_engine import RowChange
from .sql_format import format_sql_literal, qualify_table, quote_ident

DEFAULT_AUDIT_PROGRAM = "JIRAXXXX"
DEFAULT_AUDIT_USER = "RMA"


@dataclass
class GeneratedScript:
    sql_text: str
    statement_count: int
    warning_count: int


def _where_clause(key_predicate: dict) -> str:
    parts = []
    for col, val in key_predicate.items():
        if val is None:
            parts.append(f"{quote_ident(col)} IS NULL")
        else:
            parts.append(f"{quote_ident(col)} = {format_sql_literal(val)}")
    return " AND ".join(parts) if parts else "1=1 /* NO KEY COLUMNS - REVIEW BEFORE RUNNING */"


def _where_predicate_with_changed_columns(key_predicate: dict, cell_changes, use_new_value: bool) -> dict:
    """
    Extends a WHERE predicate (built from the chosen key column(s)) with
    one extra equality check per CHANGED column, so the statement only
    touches a row that still has the value it was generated against - a
    lightweight optimistic-concurrency guard against the row having been
    modified by something else between "ran the query" and "ran the
    generated script". Without this, a WHERE built from key columns
    alone (e.g. just an id) will happily overwrite a value that's no
    longer what the grid showed, silently clobbering an intervening
    change - the whole point of putting the ORIGINAL value of what's
    being updated into the WHERE clause.

    key_predicate:  the column->value map already chosen for the WHERE
                     (key column(s), or every column when none were
                     picked - see key_is_full_row in diff_engine).
    cell_changes:   the row's list of CellChange (column/old_value/new_value).
    use_new_value:  False for the forward script (guard on the value
                     BEFORE this change - old_value); True for the
                     rollback script (guard on the value the forward
                     script just SET it to - new_value).

    A column already present in key_predicate is left alone - it's
    already being checked (and, for a changed key column in a rollback,
    already correctly pointed at the right value by the caller) so
    adding it again would just be a no-op duplicate.
    """
    predicate = dict(key_predicate)
    for c in cell_changes:
        if c.column in predicate:
            continue
        predicate[c.column] = c.new_value if use_new_value else c.old_value
    return predicate


def _build_set_clause(cell_changes, program: str, user: str, restore: bool) -> str:
    """
    One SET assignment per changed column, each followed by an inline
    "-- was: <the value being overwritten>" comment so a reviewer can
    see the before/after without cross-referencing the grid, plus the
    three fixed audit columns appended at the end. restore=True builds
    the ROLLBACK direction (SET = old_value, comment shows new_value);
    restore=False builds the forward direction (SET = new_value,
    comment shows old_value).
    """
    lines: list[tuple[str, str]] = []  # (assignment_sql, comment_value_sql)
    for c in cell_changes:
        set_to = c.old_value if restore else c.new_value
        was_value = c.new_value if restore else c.old_value
        lines.append((f"{quote_ident(c.column)} = {format_sql_literal(set_to)}", format_sql_literal(was_value)))

    lines.append((f"{quote_ident('update_program')} = {format_sql_literal(program)}", ""))
    lines.append((f"{quote_ident('update_date')} = GETDATE()", ""))
    lines.append((f"{quote_ident('update_user')} = {format_sql_literal(user)}", ""))

    rendered = []
    for i, (assignment, was_comment) in enumerate(lines):
        suffix = "," if i < len(lines) - 1 else ""
        piece = f"{assignment}{suffix}"
        if was_comment:
            piece += f"  -- was: {was_comment}"
        rendered.append(piece)
    return "\n    ".join(rendered)


def build_update_statements(
    schema: str | None,
    table: str,
    row_changes: list[RowChange],
    program: str = DEFAULT_AUDIT_PROGRAM,
    user: str = DEFAULT_AUDIT_USER,
) -> tuple[list[str], int]:
    """Returns (statements, warning_count)."""
    table_qualified = qualify_table(schema, table)
    statements: list[str] = []
    warnings = 0

    for change in row_changes:
        set_clause = _build_set_clause(change.cell_changes, program, user, restore=False)
        where_predicate = _where_predicate_with_changed_columns(
            change.key_predicate, change.cell_changes, use_new_value=False
        )
        where_clause = _where_clause(where_predicate)

        # row_index is 0-based (grid-data order); +1 to match the row
        # NUMBER shown in the results grid's own index column, which is
        # what someone reviewing this script alongside the app would
        # actually be looking at.
        changed_cols = ", ".join(c.column for c in change.cell_changes)
        comment = f"-- Row {change.row_index + 1}: changed column(s): {changed_cols}"
        stmt = f"{comment}\nUPDATE {table_qualified}\nSET {set_clause}\nWHERE {where_clause};"

        if change.key_is_full_row:
            warnings += 1
            stmt = (
                "-- WARNING: no primary key was selected for this table, so the WHERE\n"
                "-- clause below matches on every original column's value instead. If the\n"
                "-- table has duplicate rows this statement could update more than one row.\n"
                "-- Pick a key column in the app before trusting this script.\n" + stmt
            )

        statements.append(stmt)

    return statements, warnings


def build_rollback_statements(
    schema: str | None,
    table: str,
    row_changes: list[RowChange],
    program: str = DEFAULT_AUDIT_PROGRAM,
    user: str = DEFAULT_AUDIT_USER,
) -> tuple[list[str], int]:
    """
    The reverse of build_update_statements: restores every changed cell to
    its ORIGINAL value (and re-stamps the audit columns for the rollback
    itself - it's its own write, with its own GETDATE()). Returns
    (statements, warning_count).

    The tricky part is the WHERE clause: build_update_statements' WHERE
    matches a row by its key column(s) as they were BEFORE the forward
    script ran. A rollback script runs AFTER the forward script, so if a
    KEY column itself was one of the edited cells, the row's key value in
    the database is now the NEW value, not the original one - the
    rollback's WHERE has to match on that new value, or it'll match zero
    rows. Non-key columns that changed don't affect the WHERE clause
    either way, only the SET list (which restores them to old_value).
    """
    table_qualified = qualify_table(schema, table)
    statements: list[str] = []
    warnings = 0

    for change in row_changes:
        new_values_by_column = {c.column: c.new_value for c in change.cell_changes}
        rollback_predicate = {
            col: new_values_by_column.get(col, original_value)
            for col, original_value in change.key_predicate.items()
        }

        set_clause = _build_set_clause(change.cell_changes, program, user, restore=True)
        where_predicate = _where_predicate_with_changed_columns(
            rollback_predicate, change.cell_changes, use_new_value=True
        )
        where_clause = _where_clause(where_predicate)

        restored_cols = ", ".join(c.column for c in change.cell_changes)
        comment = f"-- Rollback for Row {change.row_index + 1}: restoring column(s): {restored_cols}"
        stmt = f"{comment}\nUPDATE {table_qualified}\nSET {set_clause}\nWHERE {where_clause};"

        if change.key_is_full_row:
            warnings += 1
            stmt = (
                "-- WARNING: no primary key was selected for this table, so the WHERE\n"
                "-- clause below matches on every column's value AFTER the forward script\n"
                "-- ran instead. If that combination isn't unique, this could restore more\n"
                "-- than one row. Pick a key column before trusting this rollback script.\n" + stmt
            )

        statements.append(stmt)

    return statements, warnings


def generate_rollback_script(
    schema: str | None,
    table: str,
    row_changes: list[RowChange],
    source_sql: str = "",
    program: str = DEFAULT_AUDIT_PROGRAM,
    user: str = DEFAULT_AUDIT_USER,
) -> GeneratedScript:
    """
    Builds the reverse of generate_update_script's output - meant to be
    generated and saved ALONGSIDE the forward script (not instead of it),
    so whoever has write access has an undo path ready before they run
    anything. Only ever generates text; like the forward script, this
    never executes against the database itself.
    """
    statements, warnings = build_rollback_statements(schema, table, row_changes, program=program, user=user)

    header_lines = [
        "-- Rollback script generated by ScriptGen",
        f"-- Generated (UTC): {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"-- Target table: {qualify_table(schema, table)}",
        f"-- Rows restored: {len(row_changes)}",
        "-- Run this ONLY after the forward UPDATE script has already been applied -",
        "-- running it first (or twice) will not do what you want.",
    ]
    if warnings:
        header_lines.append(f"-- WARNING: {warnings} statement(s) have no reliable key column - see inline notes.")
    if source_sql.strip():
        commented_source = "\n".join(f"--   {line}" for line in source_sql.strip().splitlines())
        header_lines.append("-- Original source query:")
        header_lines.append(commented_source)
    header_lines.append("")
    header_lines.append("BEGIN TRANSACTION;")
    header_lines.append("")

    footer_lines = [
        "",
        "-- Review the statements above, then COMMIT or ROLLBACK explicitly:",
        "-- COMMIT TRANSACTION;",
        "-- ROLLBACK TRANSACTION;",
    ]

    body = "\n\n".join(statements) if statements else "-- No changes to roll back."
    sql_text = "\n".join(header_lines) + body + "\n".join(footer_lines)

    return GeneratedScript(sql_text=sql_text, statement_count=len(statements), warning_count=warnings)


def generate_update_script(
    schema: str | None,
    table: str,
    row_changes: list[RowChange],
    source_sql: str = "",
    program: str = DEFAULT_AUDIT_PROGRAM,
    user: str = DEFAULT_AUDIT_USER,
) -> GeneratedScript:
    statements, warnings = build_update_statements(schema, table, row_changes, program=program, user=user)

    header_lines = [
        "-- Generated by ScriptGen",
        f"-- Generated (UTC): {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"-- Target table: {qualify_table(schema, table)}",
        f"-- Rows changed: {len(row_changes)}",
        f"-- Program/Jira: {program}",
    ]
    if warnings:
        header_lines.append(f"-- WARNING: {warnings} statement(s) have no reliable key column - see inline notes.")
    if source_sql.strip():
        commented_source = "\n".join(f"--   {line}" for line in source_sql.strip().splitlines())
        header_lines.append("-- Source query:")
        header_lines.append(commented_source)
    header_lines.append("")
    header_lines.append("BEGIN TRANSACTION;")
    header_lines.append("")

    footer_lines = [
        "",
        "-- Review the statements above, then COMMIT or ROLLBACK explicitly:",
        "-- COMMIT TRANSACTION;",
        "-- ROLLBACK TRANSACTION;",
    ]

    body = "\n\n".join(statements) if statements else "-- No changes detected - nothing to update."
    sql_text = "\n".join(header_lines) + body + "\n".join(footer_lines)

    return GeneratedScript(sql_text=sql_text, statement_count=len(statements), warning_count=warnings)


def build_preview_line(
    schema: str | None,
    table: str,
    change: RowChange,
    program: str = DEFAULT_AUDIT_PROGRAM,
    user: str = DEFAULT_AUDIT_USER,
) -> str:
    """
    A single edited row's UPDATE statement, collapsed onto one line -
    for the Workspace results grid's live "SQL Preview" column (each
    row's own generated SQL sitting right next to the row that produced
    it, instead of only appearing as one flat block of text on the
    separate Script page once you click Generate). Skips the per-column
    "-- was:" comments and row-number header the full multi-line script
    uses (build_update_statements) since a grid cell has no room for
    them - this is a live preview, not the reviewable/copyable output;
    Generate Update Script on the Script page remains that.
    """
    table_qualified = qualify_table(schema, table)
    set_parts = [
        f"{quote_ident(c.column)} = {format_sql_literal(c.new_value)}" for c in change.cell_changes
    ]
    set_parts.append(f"{quote_ident('update_program')} = {format_sql_literal(program)}")
    set_parts.append(f"{quote_ident('update_date')} = GETDATE()")
    set_parts.append(f"{quote_ident('update_user')} = {format_sql_literal(user)}")
    where_predicate = _where_predicate_with_changed_columns(
        change.key_predicate, change.cell_changes, use_new_value=False
    )
    where_clause = _where_clause(where_predicate)
    return f"UPDATE {table_qualified} SET {', '.join(set_parts)} WHERE {where_clause};"
