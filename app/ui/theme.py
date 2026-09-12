"""
The light/dark theme pair ScriptGen ships with, plus small style
constants shared across the UI so colors/spacing stay consistent
without hunting through every widget call.
"""
from __future__ import annotations

LIGHT_THEME = "flatly"
DARK_THEME = "sandstone-dark"
# NOTE: most ttkbootstrap dark themes (incl. the earlier "one-dark" choice)
# map their "secondary" swatch to a bright magenta/purple accent rather than
# a muted gray, which makes hint/status text (bootstyle="secondary") read as
# a garish highlight instead of quiet metadata. "sandstone-dark" is one of
# the few whose secondary swatch is an actual warm gray - verified via
# ttkbootstrap's own Style().colors for every dark theme before picking this.

PAD = 10
PAD_SM = 6


def other_theme(current: str) -> str:
    """Flips between the light and dark theme; unknown themes default to light."""
    return DARK_THEME if current == LIGHT_THEME else LIGHT_THEME


def is_dark(theme: str) -> bool:
    return theme == DARK_THEME


def grid_colors(theme: str) -> tuple[str, str, str, str]:
    """
    Colors for marking unsaved edits in the results grid, as
    (modified_cell_bg, modified_cell_fg, modified_row_index_bg, modified_row_index_fg).
    Two tiers: the specific field that was edited gets an amber tint (the
    "this exact value changed" signal), and that row's index number gets a
    blue tint (the "this row has at least one change, and will get an
    UPDATE statement" signal) - so a row with several edited cells doesn't
    turn into a single wall of one color.
    """
    if is_dark(theme):
        return "#5c4a1a", "#ffe9a8", "#1f3a52", "#bcdcff"
    return "#fff3b0", "#5c4a1a", "#dbeafe", "#1e3a5f"


def sql_highlight_colors(theme: str) -> dict[str, str]:
    """
    Foreground colors for the SQL editor's syntax highlighting, keyed by
    token kind (keyword/string/number/comment) - standard code-editor
    hues (blue/green/purple/gray), each identity-distinct from the
    others rather than a rank/intensity scale, since token KIND is what
    the color is encoding here. Separate light/dark sets rather than one
    fixed set, same reasoning as grid_colors above: a hue tuned to read
    on white washes out (or vice versa) on a dark editor background.
    """
    if is_dark(theme):
        return {"keyword": "#7dd3fc", "string": "#86efac", "number": "#c4b5fd", "comment": "#9aa0a6"}
    return {"keyword": "#1a56db", "string": "#047857", "number": "#7c3aed", "comment": "#6b7280"}
