"""
Main ScriptGen window. Laid out like a modern web app rather than a
stacked desktop form: a slim top bar, a left sidebar for navigating
between pages (Workspace / Script / AI Assist), and a content area
that swaps pages via grid+tkraise (not pack/Panedwindow - see the note
on _build_content for why that matters). The Dashboard and Settings
are separate windows/dialogs, opened from the sidebar.
"""
from __future__ import annotations

import getpass
import threading
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tksheet import Sheet

from ..config import AppConfig, ConnectionConfig, load_config, save_config
from ..db import mssql
from ..db import internal_store
from ..db import script_history
from ..db import date_anomaly_history
from ..core import diff_engine, script_generator, ai_assist, snapshot_diff, schema_check, sql_pretty, date_anomaly
from .config_dialog import ConfigDialog
from .dashboard_window import DashboardWindow
from .theme import PAD, PAD_SM, other_theme, is_dark, grid_colors, sql_highlight_colors

# The sidebar is a resizable Panedwindow pane, not a fixed-width Frame -
# it now holds the WHERE-clause key picker as well as nav, so a fixed
# 196px (right for a nav-only rail) was too cramped. SIDEBAR_INITIAL_WIDTH
# is only the STARTING sash position; the user can drag it wider/narrower
# from there. MIN widths keep either side from being dragged to nothing.
SIDEBAR_INITIAL_WIDTH = 300
SIDEBAR_MIN_WIDTH = 230
CONTENT_MIN_WIDTH = 520

# --- Results grid perf tuning ------------------------------------------
# tksheet's set_all_column_widths() measures EVERY cell in EVERY row via
# real Tk canvas item calls to find each column's ideal width - each call
# crosses into Tcl, so it's O(rows * cols) *canvas round-trips*, not O(1)
# Python work. On a few hundred/thousand-row result this alone can take
# hundreds of ms to a few seconds and runs synchronously on the UI thread
# (there's nothing to background - it has to happen before the grid can
# be shown), which is what made "run a query" and, right after it,
# "clicking around" feel laggy - the whole app is blocked while it runs.
# We estimate widths ourselves instead: one hidden Canvas text item,
# reconfigured and re-measured per cell (the same reuse-one-item trick
# tksheet itself uses internally) rather than a fresh tkinter.font.Font
# per measurement - font.measure() turned out to be ~130x slower per
# call than reusing a canvas item's itemconfig()+bbox() (measured in
# this sandbox: ~675us vs ~5us per call - font.measure() actually made
# things *worse* when this was first tried). We also cap the sample to
# a bounded, evenly-spaced set of rows instead of scanning all of them,
# so cost stays flat even on very large result sets. See
# _estimate_column_widths / _populate_sheet below.
_WIDTH_SAMPLE_CAP = 300
_COL_WIDTH_PAD = 18
_COL_MIN_WIDTH = 50
_COL_MAX_WIDTH = 360

NAV_ITEMS = [
    ("workspace", "🧾  Workspace"),
    ("script", "📝  Script"),
    ("ai", "🤖  AI Assist"),
    ("dateanomaly", "🩹  DIFF DATES Anomaly"),
    ("tools", "🛠  Tools"),
]


def _da_col(row: dict, name: str):
    """Case-insensitive dict lookup - SQL Server column names come back
    however the driver/table defines them, and this app shouldn't care
    whether a given install happens to report e.g. "Id_Reading" instead
    of "ID_READING"."""
    for k, v in row.items():
        if k.lower() == name.lower():
            return v
    return None

# --- SQL syntax highlighting -------------------------------------------
# Deliberately simple regex-based highlighting (not a real tokenizer) -
# good enough to make keywords/strings/numbers/comments visually
# distinct in a query editor that's realistically a few dozen lines,
# without pulling in a full lexer dependency for it.
_SQL_KEYWORDS = (
    "SELECT|FROM|WHERE|AND|OR|NOT|IN|IS|NULL|JOIN|INNER|LEFT|RIGHT|FULL|OUTER|ON|"
    "GROUP|BY|ORDER|HAVING|AS|DISTINCT|TOP|INSERT|INTO|VALUES|UPDATE|SET|DELETE|"
    "CREATE|ALTER|DROP|TABLE|VIEW|INDEX|PRIMARY|KEY|FOREIGN|REFERENCES|DEFAULT|"
    "BEGIN|END|TRANSACTION|COMMIT|ROLLBACK|CASE|WHEN|THEN|ELSE|EXISTS|BETWEEN|"
    "LIKE|UNION|ALL|ASC|DESC|COUNT|SUM|AVG|MIN|MAX|CAST|CONVERT|GETDATE|DECLARE"
)
import re as _re
_SQL_KEYWORD_RE = _re.compile(rf"\b(?:{_SQL_KEYWORDS})\b", _re.IGNORECASE)
_SQL_STRING_RE = _re.compile(r"'(?:[^']|'')*'")
_SQL_NUMBER_RE = _re.compile(r"(?<![\w])-?\d+(?:\.\d+)?\b")
_SQL_COMMENT_RE = _re.compile(r"--[^\n]*")


def _format_diff_key(key: dict) -> str:
    """Renders a snapshot_diff row key dict for the Treeview's leading
    column, e.g. {"id": 7} -> "id=7". Falls back to a fixed label when
    there's no key (full-row matching mode, or a genuinely empty key)."""
    if not key:
        return "(full row)"
    return ", ".join(f"{k}={v}" for k, v in key.items())


class MainWindow:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title("ScriptGen  ·  SQL Server Update Script Generator")
        self.root.geometry("1280x860")
        self.root.minsize(1020, 680)

        self.config: AppConfig = load_config()
        try:
            self.root.style.theme_use(self.config.theme)
        except tk.TclError:
            pass  # unknown theme saved from an older build - keep whatever main.py set

        self.query_result: mssql.QueryResult | None = None
        self.pk_columns: list[str] = []
        self.key_column_vars: dict[str, tk.BooleanVar] = {}
        self._connected_ok: bool | None = None  # None = untested
        self.nav_buttons: dict[str, tb.Button] = {}
        self.pages: dict[str, tb.Frame] = {}
        self.current_page = "workspace"
        self._sidebar_wrap_labels: list[tb.Label] = []

        # The "base" state edited cells are diffed against for grid
        # highlighting - set by _populate_sheet, read by
        # _refresh_grid_highlights. See the highlighting section below.
        self._original_columns: list[str] = []
        self._original_rows: list[list] = []

        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        self._build_menu()
        self._build_topbar()
        self._build_body()
        self._refresh_connection_badge()
        self._show_page("workspace")

        self.root.bind("<F5>", lambda e: self._run_query())

    # ------------------------------------------------------------------
    # Menu (kept as a standard desktop-native supplement to the sidebar)
    # ------------------------------------------------------------------
    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Save Script As...", command=self._save_script_as)
        file_menu.add_command(label="Export Result to Internal DB...", command=self._export_to_internal_db)
        file_menu.add_command(label="Browse Snapshots...", command=self._browse_snapshots)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        conn_menu = tk.Menu(menubar, tearoff=0)
        conn_menu.add_command(label="Configure Connection / AI...", command=self._open_config)
        conn_menu.add_command(label="Test Connection", command=self._test_connection_quick)
        menubar.add_cascade(label="Connection", menu=conn_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Toggle Light / Dark Theme", command=self._toggle_theme)
        view_menu.add_command(label="Open Dashboard...", command=self._open_dashboard)
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    # ------------------------------------------------------------------
    # Top bar - slim, neutral, actions on the right (web-app header, not
    # a big colored banner)
    # ------------------------------------------------------------------
    def _build_topbar(self):
        wrapper = tb.Frame(self.root)
        wrapper.grid(row=0, column=0, sticky="ew")

        bar = tb.Frame(wrapper, padding=(PAD, PAD_SM))
        bar.pack(fill="x")

        tb.Label(bar, text="ScriptGen", font=("TkDefaultFont", 13, "bold")).pack(side="left")
        tb.Label(bar, text="  SQL Server update-script tool", bootstyle="secondary").pack(side="left")

        self.theme_btn = tb.Button(bar, text="🌙 Dark", bootstyle="link", command=self._toggle_theme, width=8)
        self.theme_btn.pack(side="right")
        tb.Button(bar, text="Settings", bootstyle="link", command=self._open_config).pack(side="right", padx=(0, 4))
        tb.Button(
            bar, text="Test Connection", bootstyle="secondary-outline", command=self._test_connection_quick
        ).pack(side="right", padx=(0, 10))

        self.conn_badge = tb.Label(bar, text="●  checking...", font=("TkDefaultFont", 9, "bold"))
        self.conn_badge.pack(side="right", padx=(0, 16))
        self._update_theme_button_label()

        tb.Separator(wrapper, bootstyle="secondary").pack(fill="x")

    def _update_theme_button_label(self):
        self.theme_btn.configure(text="☀ Light" if is_dark(self.config.theme) else "🌙 Dark")

    def _toggle_theme(self):
        self.config.theme = other_theme(self.config.theme)
        try:
            self.root.style.theme_use(self.config.theme)
        except tk.TclError:
            pass
        self._update_theme_button_label()
        save_config(self.config)

    # ------------------------------------------------------------------
    # Body: sidebar (left) + content pages (right), split by a draggable
    # sash (ttk.Panedwindow) rather than a fixed-width Frame, since the
    # sidebar now also holds the WHERE-clause key picker - a resizable
    # split lets you widen it when column names are long, or narrow it
    # back down when they're not.
    # ------------------------------------------------------------------
    def _build_body(self):
        self.body_paned = tb.Panedwindow(self.root, orient=HORIZONTAL)
        self.body_paned.grid(row=1, column=0, sticky="nsew")

        sidebar = tb.Frame(self.body_paned, bootstyle="light")
        content = tb.Frame(self.body_paned)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        # ttk.Panedwindow panes only support the "weight" option (unlike
        # the classic tk.PanedWindow, there's no "minsize") - so the
        # min-width constants are enforced by clamping in
        # _clamp_sash_drag below instead of being pane options here.
        self.body_paned.add(sidebar, weight=0)
        self.body_paned.add(content, weight=1)
        self.body_paned.bind("<B1-Motion>", self._clamp_sash_drag, add="+")

        self._build_sidebar(sidebar)
        self._build_content(content)

        # ttk.Panedwindow's sash has no useful initial position until the
        # window has actually been laid out at least once (winfo_width()
        # is ~1px on the very first idle pass) - poll briefly rather than
        # guessing a fixed delay. Same class of issue as the old vertical
        # Panedwindow's sashpos() workaround, just the horizontal version.
        self.root.after(30, self._set_initial_sash)

    def _set_initial_sash(self, attempts: int = 0):
        self.root.update_idletasks()
        if self.body_paned.winfo_width() < 200 and attempts < 30:
            self.root.after(30, lambda: self._set_initial_sash(attempts + 1))
            return
        try:
            self.body_paned.sashpos(0, SIDEBAR_INITIAL_WIDTH)
        except tk.TclError:
            pass

    def _set_grid_pane_sash(self, grid_paned, attempts: int = 0):
        self.root.update_idletasks()
        if grid_paned.winfo_width() < 200 and attempts < 30:
            self.root.after(30, lambda: self._set_grid_pane_sash(grid_paned, attempts + 1))
            return
        try:
            grid_paned.sashpos(0, 480)
        except tk.TclError:
            pass

    def _clamp_sash_drag(self, event=None):
        # ttk.Panedwindow panes don't support a "minsize" option (see the
        # comment in _build_body), so the sash is free to drag to zero on
        # either side unless we clamp it back ourselves while dragging.
        try:
            total = self.body_paned.winfo_width()
            pos = self.body_paned.sashpos(0)
        except tk.TclError:
            return
        min_pos = SIDEBAR_MIN_WIDTH
        max_pos = max(min_pos, total - CONTENT_MIN_WIDTH)
        clamped = min(max(pos, min_pos), max_pos)
        if clamped != pos:
            self.body_paned.sashpos(0, clamped)

    def _build_sidebar(self, parent):
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        scroll = tb.ScrolledFrame(parent, autohide=True, padding=(PAD_SM, PAD))
        scroll.grid(row=0, column=0, sticky="nsew")

        for key, label in NAV_ITEMS:
            btn = tb.Button(
                scroll, text=label, bootstyle="link", command=lambda k=key: self._show_page(k)
            )
            btn.pack(fill="x", pady=2)
            self.nav_buttons[key] = btn

        tb.Separator(scroll, bootstyle="secondary").pack(fill="x", pady=PAD)

        self.dashboard_btn = tb.Button(
            scroll, text="📊  Dashboard", bootstyle="info-outline", command=self._open_dashboard, state="disabled"
        )
        self.dashboard_btn.pack(fill="x", pady=2)

        tb.Separator(scroll, bootstyle="secondary").pack(fill="x", pady=PAD)

        self._build_key_panel(scroll)

        # Bottom-anchored, outside the scroll area: connection status +
        # app version, so the sidebar reads top (nav/controls, scrollable)
        # / bottom (static system info) like a typical web app rail.
        # No bootstyle here (unlike `parent`/`scroll`) - a second nested
        # bootstyle="light" Frame rendered as a stray near-white patch
        # instead of blending in, in both themes; the plain default frame
        # style matches the surrounding "light"-bootstyle sidebar closely
        # enough to look seamless, same as the unstyled `scroll` above it.
        footer = tb.Frame(parent, padding=(PAD_SM, PAD_SM))
        footer.grid(row=1, column=0, sticky="ew")
        tb.Separator(footer, bootstyle="secondary").pack(fill="x", pady=(0, PAD_SM))
        tb.Label(footer, text="ScriptGen v1", bootstyle="secondary", font=("TkDefaultFont", 8)).pack(anchor="w")
        footer_hint = tb.Label(
            footer, text="read-only source · scripts never auto-run", bootstyle="secondary",
            font=("TkDefaultFont", 8), wraplength=SIDEBAR_INITIAL_WIDTH - 30, justify="left",
        )
        footer_hint.pack(anchor="w")
        self._sidebar_wrap_labels.append(footer_hint)

        # Keep the hint labels wrapping sensibly as the sash is dragged.
        parent.bind("<Configure>", self._on_sidebar_resize)

    def _on_sidebar_resize(self, event):
        wrap = max(140, event.width - 30)
        for lbl in self._sidebar_wrap_labels:
            lbl.configure(wraplength=wrap)

    # --- WHERE-clause key picker + target table + generate ---------------
    # Moved into the sidebar (was previously a full-width bar at the
    # bottom of the Workspace page) so it's reachable no matter which
    # page (Workspace/Script/AI Assist) is showing, and so it benefits
    # from the sidebar's own resizability.
    def _build_key_panel(self, parent):
        tb.Label(parent, text="WHERE clause key", font=("TkDefaultFont", 10, "bold")).pack(
            anchor="w", pady=(0, PAD_SM)
        )
        tb.Button(
            parent, text="🔑 Auto-detect Key", bootstyle="warning", command=self._auto_detect_key
        ).pack(fill="x", pady=(0, PAD_SM))

        btn_row = tb.Frame(parent)
        btn_row.pack(fill="x", pady=(0, PAD_SM))
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)
        tb.Button(
            btn_row, text="Select All", bootstyle="secondary-outline", command=self._select_all_keys
        ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        tb.Button(
            btn_row, text="Clear", bootstyle="secondary-outline", command=self._clear_all_keys
        ).grid(row=0, column=1, sticky="ew", padx=(3, 0))

        self.key_hint_var = tk.StringVar(value="Run a query, then pick which column(s) uniquely identify a row.")
        key_hint_label = tb.Label(
            parent, textvariable=self.key_hint_var, bootstyle="secondary",
            wraplength=SIDEBAR_INITIAL_WIDTH - 30, justify="left",
        )
        key_hint_label.pack(anchor="w", fill="x", pady=(0, PAD_SM))
        self._sidebar_wrap_labels.append(key_hint_label)

        self.key_columns_frame = tb.ScrolledFrame(parent, height=180, auto_hide=True)
        self.key_columns_frame.pack(fill="x", pady=(0, PAD))

        tb.Label(parent, text="Target schema", bootstyle="secondary", font=("TkDefaultFont", 8)).pack(anchor="w")
        self.target_schema_var = tk.StringVar(value="dbo")
        tb.Entry(parent, textvariable=self.target_schema_var).pack(fill="x", pady=(0, PAD_SM))

        tb.Label(parent, text="Target table", bootstyle="secondary", font=("TkDefaultFont", 8)).pack(anchor="w")
        self.target_table_var = tk.StringVar()
        tb.Entry(parent, textvariable=self.target_table_var).pack(fill="x", pady=(0, PAD_SM))

        # Required on every generated statement (forward AND rollback) as
        # the update_program audit column - defaults to an obviously-fake
        # placeholder so a script generated without editing this field is
        # immediately recognizable as not-ready-to-run, rather than silently
        # shipping "JIRAXXXX" as if it were a real ticket.
        tb.Label(parent, text="Jira / Program # (required)", bootstyle="secondary", font=("TkDefaultFont", 8)).pack(
            anchor="w"
        )
        self.jira_var = tk.StringVar(value=script_generator.DEFAULT_AUDIT_PROGRAM)
        tb.Entry(parent, textvariable=self.jira_var).pack(fill="x", pady=(0, PAD))

        # Target table/schema and the Jira/program # all feed the SQL
        # Preview column next to the results grid (see _refresh_sql_
        # preview) - keep it in sync as those fields change, not just
        # when a cell is actually edited.
        self.target_schema_var.trace_add("write", lambda *_: self._refresh_grid_highlights())
        self.target_table_var.trace_add("write", lambda *_: self._refresh_grid_highlights())
        self.jira_var.trace_add("write", lambda *_: self._refresh_grid_highlights())

        tb.Button(
            parent, text="📝  Generate Update Script", bootstyle=SUCCESS, command=self._generate_script
        ).pack(fill="x")

    def _show_page(self, key: str):
        self.pages[key].tkraise()
        for k, btn in self.nav_buttons.items():
            btn.configure(bootstyle=PRIMARY if k == key else "link")
        self.current_page = key
        if key == "tools" and hasattr(self, "script_history_tree"):
            self._refresh_script_history_tree()
        if key == "dateanomaly" and hasattr(self, "da_history_tree"):
            self._da_refresh_history_tree()

    # ------------------------------------------------------------------
    # Content pages
    #
    # NOTE: pages are stacked via grid()+tkraise(), not pack()/Panedwindow.
    # grid's row/column `weight` is respected regardless of call order,
    # which sidesteps an earlier bug class entirely (Tk's pack() carves
    # space out of the parent cavity in *pack-call order*, so an
    # expand=True sibling packed first can starve a later fixed-size one
    # no matter what "weight" you declare - see git history if curious).
    # ------------------------------------------------------------------
    def _build_content(self, parent):
        container = tb.Frame(parent, padding=PAD)
        container.grid(row=0, column=0, sticky="nsew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        builders = {
            "workspace": self._build_workspace_page,
            "script": self._build_script_page,
            "ai": self._build_ai_page,
            "dateanomaly": self._build_dateanomaly_page,
            "tools": self._build_tools_page,
        }
        for key, builder in builders.items():
            page = tb.Frame(container)
            page.grid(row=0, column=0, sticky="nsew")
            builder(page)
            self.pages[key] = page

    def _build_workspace_page(self, page):
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)  # results grid gets all the leftover space

        # --- Query editor -------------------------------------------------
        editor = tb.Frame(page)
        editor.grid(row=0, column=0, sticky="ew", pady=(0, PAD))
        editor.grid_columnconfigure(0, weight=1)

        editor_header = tb.Frame(editor)
        editor_header.grid(row=0, column=0, sticky="ew")
        tb.Label(editor_header, text="SQL Query", font=("TkDefaultFont", 11, "bold")).pack(side="left")
        self.run_query_btn = tb.Button(
            editor_header, text="▶  Run Query   (F5)", bootstyle=SUCCESS, command=self._run_query
        )
        self.run_query_btn.pack(side="right")
        self.format_query_btn = tb.Button(
            editor_header, text="🪄 Format", bootstyle="secondary-outline", command=self._format_query
        )
        self.format_query_btn.pack(side="right", padx=(0, PAD_SM))
        self.cancel_query_btn = tb.Button(
            editor_header, text="✕ Cancel", bootstyle="danger-outline", command=self._cancel_query, state="disabled"
        )
        self.cancel_query_btn.pack(side="right", padx=(0, PAD_SM))
        self.recent_btn = tb.Menubutton(
            editor_header, text="🕘 Recent", bootstyle="secondary-outline", direction="below"
        )
        self.recent_btn.pack(side="right", padx=(0, PAD_SM))
        self.recent_menu = tk.Menu(self.recent_btn, tearoff=0)
        self.recent_btn["menu"] = self.recent_menu
        self._rebuild_recent_menu()

        self.sql_text = tb.ScrolledText(editor, height=6, autohide=True)
        self.sql_text.grid(row=1, column=0, sticky="ew", pady=(PAD_SM, 0))
        self.sql_text.insert("1.0", "SELECT TOP 100 * FROM INFORMATION_SCHEMA.TABLES;")
        for tag, color in sql_highlight_colors(self.config.theme).items():
            self.sql_text.text.tag_configure(tag, foreground=color)
        self.sql_text.text.bind("<KeyRelease>", self._highlight_sql)
        self._highlight_sql()

        # Query cancellation is "soft": pytds has no clean mid-flight abort,
        # so Cancel just bumps a generation counter and the query WORKER
        # (still running in its background thread) checks it before acting
        # on its result - the network call itself keeps running until it
        # naturally returns/times out, but the UI stops waiting on it and
        # its result is discarded rather than clobbering whatever's on
        # screen. This also quietly fixes a pre-existing race for free:
        # hitting Run Query again before a slow previous query finished
        # could previously let the OLDER result land after the newer one.
        self._query_generation = 0
        self._query_running = False

        # --- Results grid (expands) ---------------------------------------
        results = tb.Frame(page)
        results.grid(row=1, column=0, sticky="nsew", pady=(0, PAD))
        results.grid_columnconfigure(0, weight=1)
        results.grid_rowconfigure(1, weight=1)

        results_header = tb.Frame(results)
        results_header.grid(row=0, column=0, sticky="ew")
        tb.Label(results_header, text="Results", font=("TkDefaultFont", 11, "bold")).pack(side="left")
        tb.Label(results_header, text="double-click a cell to edit", bootstyle="secondary").pack(
            side="left", padx=(PAD_SM, 0)
        )
        export_btn = tb.Menubutton(
            results_header, text="⬇ Export", bootstyle="secondary-outline", direction="below"
        )
        export_btn.pack(side="right")
        export_menu = tk.Menu(export_btn, tearoff=0)
        export_btn["menu"] = export_menu
        export_menu.add_command(label="CSV...", command=self._export_grid_csv)
        export_menu.add_command(label="Excel (.xlsx)...", command=self._export_grid_excel)
        export_menu.add_separator()
        export_menu.add_command(
            label="Internal DB (new table)...", command=self._export_to_internal_db
        )

        # Results grid + its companion "SQL Preview" column, side by side
        # in a resizable pane (same tb.Panedwindow pattern as the main
        # sidebar sash). The preview is a genuinely separate, read-only
        # Sheet - not an extra column bolted onto the data grid itself -
        # so every existing column-index-based thing (CSV/Excel/internal-
        # DB export, the key-column picker, diffing) keeps working
        # unchanged; the two stay visually row-synced via tksheet's own
        # sync_scroll(), so row N's preview always sits beside row N's
        # data no matter how you scroll either one.
        grid_paned = tb.Panedwindow(results, orient=HORIZONTAL)
        grid_paned.grid(row=1, column=0, sticky="nsew", pady=(PAD_SM, 0))

        preview_frame = tb.Frame(grid_paned)
        sheet_frame = tb.Frame(grid_paned)
        grid_paned.add(preview_frame, weight=2)
        grid_paned.add(sheet_frame, weight=3)

        self.sql_preview_sheet = Sheet(
            preview_frame, data=[[]], headers=["🔗 SQL Preview (this row's UPDATE)"]
        )
        self.sql_preview_sheet.enable_bindings("single_select", "row_select", "arrowkeys", "copy")
        self.sql_preview_sheet.grid(row=0, column=0, sticky="nsew")
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_frame.grid_rowconfigure(0, weight=1)
        self.sql_preview_sheet.set_column_widths([520])
        # Give the pane a wider starting split for the preview column than
        # a bare weight=2:3 ratio produces on first layout. Same "sash has
        # no useful position until the window's actually been laid out"
        # issue as _set_initial_sash for the main sidebar - poll briefly
        # rather than guessing a fixed delay.
        self._set_grid_pane_sash(grid_paned)

        self.sheet = Sheet(sheet_frame, data=[[]], headers=[])
        self.sheet.enable_bindings(
            "single_select", "row_select", "column_select", "arrowkeys",
            "edit_cell", "copy", "paste", "delete", "undo", "select_all",
        )
        self.sheet.grid(row=0, column=0, sticky="nsew")
        sheet_frame.grid_columnconfigure(0, weight=1)
        sheet_frame.grid_rowconfigure(0, weight=1)

        # Scroll one, the other follows - keeps row N's preview aligned
        # with row N's data regardless of which sheet you scroll.
        self.sheet.sync_scroll(self.sql_preview_sheet)

        # Re-diff and re-highlight (and refresh the SQL Preview column)
        # whenever the grid's data actually changes, not just on a raw
        # keystroke (paste/delete/undo can change cells without a
        # per-cell "edit" event of their own).
        self.sheet.extra_bindings(
            ["end_edit_cell", "end_paste", "end_delete", "end_undo", "end_cut"],
            func=self._refresh_grid_highlights,
        )

        self.status_var = tk.StringVar(value="No query run yet.")
        tb.Label(results, textvariable=self.status_var, bootstyle="secondary").grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )
        # WHERE-clause key picker / target table / Generate Update Script
        # live in the left sidebar now (_build_key_panel) - reachable from
        # any page, and resizable along with the rest of the sidebar.

    def _build_script_page(self, page):
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=3)
        page.grid_rowconfigure(4, weight=1)

        header = tb.Frame(page)
        header.grid(row=0, column=0, sticky="ew", pady=(0, PAD_SM))
        tb.Label(header, text="Generated Script", font=("TkDefaultFont", 13, "bold")).pack(side="left")
        tb.Button(
            header, text="📤 Export Result to Internal DB...", bootstyle="secondary-outline",
            command=self._export_to_internal_db,
        ).pack(side="right")
        tb.Button(header, text="💾 Save As...", bootstyle="secondary-outline", command=self._save_script_as).pack(
            side="right", padx=PAD_SM
        )
        tb.Button(header, text="📋 Copy", bootstyle=SUCCESS, command=self._copy_script).pack(side="right")

        self.script_text = tb.ScrolledText(page, autohide=True)
        self.script_text.grid(row=1, column=0, sticky="nsew")

        review_header = tb.Frame(page)
        review_header.grid(row=2, column=0, sticky="ew", pady=(PAD, 0))
        tb.Label(review_header, text="🤖 AI Pre-Flight Review", font=("TkDefaultFont", 11, "bold")).pack(side="left")
        tb.Button(
            review_header, text="Review This Script", bootstyle="info-outline", command=self._ai_review_script
        ).pack(side="right")

        self.ai_review_status_var = tk.StringVar(
            value="A second opinion on the script above before you hand it to whoever has write access - "
            "not a substitute for reviewing it yourself."
        )
        tb.Label(
            page, textvariable=self.ai_review_status_var, bootstyle="secondary", wraplength=900, justify="left"
        ).grid(row=3, column=0, sticky="w", pady=(2, PAD_SM))

        self.ai_review_output = tb.ScrolledText(page, autohide=True, height=6)
        self.ai_review_output.grid(row=4, column=0, sticky="nsew")
        self.ai_review_output.text.configure(state="disabled")

    def _build_ai_page(self, page):
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(4, weight=1)

        header = tb.Frame(page)
        header.grid(row=0, column=0, sticky="ew", pady=(0, PAD_SM))
        tb.Label(header, text="AI Assist  ·  Gemini", font=("TkDefaultFont", 13, "bold")).pack(side="left")

        # --- Inline key setup - lets the key be set/tested right here     --
        # instead of only in Settings > AI, since that's the #1 reason the
        # buttons below silently no-op the first time someone tries them.
        key_card = tb.Labelframe(page, text="Gemini API key", padding=PAD_SM, bootstyle="secondary")
        key_card.grid(row=1, column=0, sticky="ew", pady=(0, PAD))
        key_card.grid_columnconfigure(1, weight=1)

        tb.Label(key_card, text="Key", bootstyle="secondary").grid(row=0, column=0, padx=(0, PAD_SM))
        self.ai_key_var = tk.StringVar(value=self.config.ai.get_api_key())
        self.ai_key_entry = tb.Entry(key_card, textvariable=self.ai_key_var, show="*")
        self.ai_key_entry.grid(row=0, column=1, sticky="ew")

        show_key = tk.BooleanVar(value=False)

        def toggle_show():
            self.ai_key_entry.configure(show="" if show_key.get() else "*")

        tb.Checkbutton(
            key_card, text="show", variable=show_key, command=toggle_show, bootstyle="round-toggle"
        ).grid(row=0, column=2, padx=PAD_SM)
        tb.Button(key_card, text="Save", bootstyle=SUCCESS, command=self._save_ai_key).grid(row=0, column=3, padx=(0, PAD_SM))
        tb.Button(key_card, text="Test Key", bootstyle="info-outline", command=self._test_ai_key).grid(row=0, column=4)

        self.ai_key_status_var = tk.StringVar()
        tb.Label(key_card, textvariable=self.ai_key_status_var, bootstyle="secondary", wraplength=760, justify="left").grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(PAD_SM, 0)
        )
        tb.Label(
            key_card, text="Free key: aistudio.google.com/apikey  ·  used only by the buttons on this page",
            bootstyle="secondary", font=("TkDefaultFont", 8),
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(2, 0))
        self._refresh_ai_key_status()

        input_row = tb.Frame(page)
        input_row.grid(row=2, column=0, sticky="ew", pady=(0, PAD))
        input_row.grid_columnconfigure(0, weight=1)
        self.ai_intent_var = tk.StringVar()
        tb.Entry(input_row, textvariable=self.ai_intent_var).grid(row=0, column=0, sticky="ew", padx=(0, PAD_SM))
        tb.Button(input_row, text="✨ Suggest", bootstyle="info", command=self._ai_suggest).grid(row=0, column=1)
        tb.Button(
            input_row, text="⚡ Optimize Current Query", bootstyle="info-outline", command=self._ai_optimize
        ).grid(row=0, column=2, padx=(PAD_SM, 0))
        tb.Button(
            input_row, text="💬 Explain Current Query", bootstyle="info-outline", command=self._ai_explain
        ).grid(row=0, column=3, padx=(PAD_SM, 0))

        self.ai_status_var = tk.StringVar(value="Describe what you want, or optimize/explain the query from Workspace.")
        tb.Label(page, textvariable=self.ai_status_var, bootstyle="secondary").grid(
            row=3, column=0, sticky="new", pady=(0, PAD_SM)
        )

        output_block = tb.Frame(page)
        output_block.grid(row=4, column=0, sticky="nsew", pady=(PAD_SM, 0))
        output_block.grid_columnconfigure(0, weight=1)
        output_block.grid_rowconfigure(0, weight=1)

        self.ai_output = tb.ScrolledText(output_block, autohide=True)
        self.ai_output.grid(row=0, column=0, sticky="nsew")
        self.ai_output.text.configure(state="disabled")

        tb.Button(
            output_block, text="↩  Insert Into Query Editor", bootstyle=SUCCESS, command=self._ai_insert
        ).grid(row=1, column=0, sticky="e", pady=(PAD_SM, 0))

        self._build_ai_where_builder(page)

    # --- NL -> WHERE clause builder --------------------------------------
    def _build_ai_where_builder(self, page):
        card = tb.Labelframe(page, text="Build a WHERE clause from plain English", padding=PAD_SM, bootstyle="secondary")
        card.grid(row=5, column=0, sticky="ew", pady=(PAD, 0))
        card.grid_columnconfigure(0, weight=1)
        tb.Label(
            card,
            text="Describes rows to filter to, not a full query - uses the columns from your last "
            "query result as context, when there is one.",
            bootstyle="secondary", wraplength=760, justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, PAD_SM))

        row = tb.Frame(card)
        row.grid(row=1, column=0, columnspan=2, sticky="ew")
        row.grid_columnconfigure(0, weight=1)
        self.ai_where_request_var = tk.StringVar()
        tb.Entry(row, textvariable=self.ai_where_request_var).grid(row=0, column=0, sticky="ew", padx=(0, PAD_SM))
        tb.Button(row, text="🔎 Build WHERE Clause", bootstyle="info", command=self._ai_build_where).grid(
            row=0, column=1
        )

        self.ai_where_output = tb.ScrolledText(card, autohide=True, height=4)
        self.ai_where_output.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(PAD_SM, 0))
        self.ai_where_output.text.configure(state="disabled")

        where_btns = tb.Frame(card)
        where_btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(PAD_SM, 0))
        tb.Button(where_btns, text="📋 Copy", bootstyle="secondary-outline", command=self._copy_ai_where).pack(
            side="left", padx=(0, PAD_SM)
        )
        tb.Button(
            where_btns, text="↩  Append to Query as WHERE", bootstyle=SUCCESS, command=self._append_ai_where_to_query
        ).pack(side="left")

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------
    def _refresh_connection_badge(self):
        conn = self.config.get_active_connection()
        if not conn:
            self.conn_badge.configure(text="●  no connection configured")
            return
        label = f"{conn.name}  ({conn.server}:{conn.port})"
        if self._connected_ok is True:
            self.conn_badge.configure(text=f"🟢  {label}")
        elif self._connected_ok is False:
            self.conn_badge.configure(text=f"🔴  {label}")
        else:
            self.conn_badge.configure(text=f"⚪  {label}  (untested)")

    def _open_config(self):
        def on_save(new_cfg: AppConfig):
            self.config = new_cfg
            save_config(self.config)
            self._connected_ok = None
            self._refresh_connection_badge()
            # The dialog may have added/removed/renamed connections or
            # switched the active one - keep the Tools-tab Environment
            # switcher's combobox (if it's been built) in sync.
            self._refresh_env_combo()

        ConfigDialog(self.root, self.config, on_save)

    def _test_connection_quick(self):
        conn = self.config.get_active_connection()
        if not conn:
            messagebox.showwarning("No connection", "Configure a connection first.")
            return
        self.status_var.set("Testing connection...")

        def worker():
            success, message, elapsed_ms = mssql.test_connection(conn)
            self._connected_ok = success
            icon = "✓" if success else "✗"
            self.status_var.set(f"{icon} {message} ({elapsed_ms:.0f} ms)")
            self._refresh_connection_badge()

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------
    def _run_query(self):
        conn = self.config.get_active_connection()
        if not conn:
            messagebox.showwarning("No connection", "Configure a connection first (Settings).")
            return

        sql = self.sql_text.text.get("1.0", "end").strip()
        if not sql:
            return

        # Remembered even if the query below fails - a typo'd query is
        # still one you probably want to pull back up and fix, not retype.
        self.config.add_recent_query(sql)
        save_config(self.config)
        self._rebuild_recent_menu()

        self._show_page("workspace")
        self.status_var.set("Running query...")
        self._query_generation += 1
        generation = self._query_generation
        self._query_running = True
        self.run_query_btn.configure(state="disabled")
        self.cancel_query_btn.configure(state="normal")
        self.root.update_idletasks()

        def worker():
            try:
                result = mssql.run_query(conn, sql)
            except mssql.ConnectionError_ as exc:
                self.root.after(0, lambda: self._on_query_error(str(exc), generation))
                return
            self.root.after(0, lambda: self._on_query_success(result, generation))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_query(self):
        # Invalidates the in-flight generation so its result/error is
        # discarded when the background thread eventually returns - see
        # the comment above self._query_generation in _build_workspace_page.
        self._query_generation += 1
        self._query_running = False
        self.run_query_btn.configure(state="normal")
        self.cancel_query_btn.configure(state="disabled")
        self.status_var.set("Query cancelled (may still be finishing in the background).")

    def _format_query(self):
        """
        Prettifies whatever's currently in the SQL editor in place -
        reindented, one column/clause per line, keywords upper-cased -
        via sql_pretty.prettify_sql (sqlparse under the hood). Purely
        cosmetic and manually triggered (the "🪄 Format" button, not on
        every keystroke) so it never fights with what you're mid-typing;
        cursor position isn't preserved since a reformat can move
        everything around anyway.
        """
        text = self.sql_text.text
        current = text.get("1.0", "end-1c")
        if not current.strip():
            return
        formatted = sql_pretty.prettify_sql(current)
        if formatted.strip() == current.strip():
            self.status_var.set("Query already formatted.")
            return
        text.delete("1.0", "end")
        text.insert("1.0", formatted)
        self._highlight_sql()
        self.status_var.set("Query formatted.")

    def _highlight_sql(self, event=None):
        text = self.sql_text.text
        content = text.get("1.0", "end-1c")
        for tag in ("keyword", "string", "number", "comment"):
            text.tag_remove(tag, "1.0", "end")
        # Order matters: a string/comment can contain text that would
        # otherwise match the keyword/number patterns (e.g. a keyword
        # inside a quoted literal) - later tag_add calls win visually
        # where ranges overlap, so the more "this wins" patterns
        # (string, comment) go first and get overridden last by nothing,
        # while keyword/number are applied last only within what's left.
        # In practice this is simple regex highlighting, not a real
        # tokenizer, so occasional false positives inside odd literals
        # are an accepted trade-off for zero extra dependencies.
        for pattern, tag in (
            (_SQL_STRING_RE, "string"),
            (_SQL_COMMENT_RE, "comment"),
            (_SQL_KEYWORD_RE, "keyword"),
            (_SQL_NUMBER_RE, "number"),
        ):
            for match in pattern.finditer(content):
                text.tag_add(tag, f"1.0+{match.start()}c", f"1.0+{match.end()}c")

    def _rebuild_recent_menu(self):
        self.recent_menu.delete(0, "end")
        if not self.config.recent_queries:
            self.recent_menu.add_command(label="(no queries run yet)", state="disabled")
            return
        for sql in self.config.recent_queries:
            # Single-line, truncated label for the menu; the full text is
            # what actually gets loaded back into the editor.
            label = " ".join(sql.split())
            if len(label) > 80:
                label = label[:77] + "..."
            self.recent_menu.add_command(label=label, command=lambda s=sql: self._load_recent_query(s))

    def _load_recent_query(self, sql: str):
        self.sql_text.text.delete("1.0", "end")
        self.sql_text.text.insert("1.0", sql)
        self._show_page("workspace")

    def _export_grid_csv(self):
        if not self._original_columns:
            messagebox.showinfo("Nothing to export", "Run a query first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV file", "*.csv")])
        if not path:
            return
        import csv

        rows = self.sheet.get_sheet_data()
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(self._original_columns)
            writer.writerows(rows)
        self.status_var.set(f"Exported {len(rows)} row(s) to {path}")

    def _export_grid_excel(self):
        if not self._original_columns:
            messagebox.showinfo("Nothing to export", "Run a query first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel workbook", "*.xlsx")])
        if not path:
            return
        from openpyxl import Workbook
        from openpyxl.styles import Font

        rows = self.sheet.get_sheet_data()
        wb = Workbook()
        ws = wb.active
        ws.title = (self.target_table_var.get().strip() or "Results")[:31]  # Excel's 31-char sheet-name limit
        ws.append(self._original_columns)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        ws.freeze_panes = "A2"
        for row in rows:
            ws.append(row)
        # Cheap column-width pass - same "estimate from content" spirit as the
        # grid's own column sizing, just measured in characters, not pixels.
        for col_idx, col_name in enumerate(self._original_columns, start=1):
            longest = len(str(col_name))
            for row in rows:
                cell_len = len(str(row[col_idx - 1])) if row[col_idx - 1] is not None else 0
                longest = max(longest, cell_len)
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(60, max(10, longest + 2))
        wb.save(path)
        self.status_var.set(f"Exported {len(rows)} row(s) to {path}")

    def _on_query_error(self, message: str, generation: int | None = None):
        self._query_running = False
        self.run_query_btn.configure(state="normal")
        self.cancel_query_btn.configure(state="disabled")
        if generation is not None and generation != self._query_generation:
            return  # cancelled (or superseded by a newer query) - ignore
        self.status_var.set(f"✗ {message}")
        self._connected_ok = False
        self._refresh_connection_badge()
        messagebox.showerror("Query failed", message)

    # ------------------------------------------------------------------
    # Results grid population - see the _WIDTH_SAMPLE_CAP note above for
    # why this doesn't just call sheet.set_all_column_widths().
    # ------------------------------------------------------------------
    def _get_measure_canvas(self) -> tk.Canvas:
        # Built lazily, once, and never packed/gridded - it exists purely
        # as a place to hold two reusable text items for measurement.
        if not hasattr(self, "_measure_canvas"):
            self._measure_canvas = tk.Canvas(self.root)
            self._measure_table_item = self._measure_canvas.create_text(
                0, 0, text="", font=self.sheet.ops.table_font, anchor="nw"
            )
            self._measure_header_item = self._measure_canvas.create_text(
                0, 0, text="", font=self.sheet.ops.header_font, anchor="nw"
            )
        return self._measure_canvas

    def _estimate_column_widths(self, columns: list[str], display_rows: list[list[str]]) -> list[int]:
        canvas = self._get_measure_canvas()
        itemconfig = canvas.itemconfig
        bbox = canvas.bbox
        table_item = self._measure_table_item
        header_item = self._measure_header_item

        n = len(display_rows)
        if n > _WIDTH_SAMPLE_CAP:
            step = n / _WIDTH_SAMPLE_CAP
            sample_idxs = [int(i * step) for i in range(_WIDTH_SAMPLE_CAP)]
        else:
            sample_idxs = range(n)

        widths = []
        for ci, col in enumerate(columns):
            itemconfig(header_item, text=str(col))
            b = bbox(header_item)
            w = b[2] - b[0]
            for ri in sample_idxs:
                cell = display_rows[ri][ci]
                if cell is not None and cell != "":
                    itemconfig(table_item, text=str(cell))
                    b = bbox(table_item)
                    cw = b[2] - b[0]
                    if cw > w:
                        w = cw
            widths.append(max(_COL_MIN_WIDTH, min(_COL_MAX_WIDTH, w + _COL_WIDTH_PAD)))
        return widths

    def _populate_sheet(self, columns: list[str], display_rows: list[list], original_rows: list[list] | None = None):
        # redraw=False on the first two so the sheet only repaints once,
        # after widths are also in place, instead of three times.
        # reset_highlights=True: without it, tksheet leaves any highlights
        # from a PREVIOUS result sitting on whatever row/column indices
        # they were at, which would misleadingly color cells in brand new
        # data - see _refresh_grid_highlights for what sets them.
        self.sheet.headers(columns, redraw=False)
        self.sheet.set_sheet_data(display_rows, redraw=False, reset_highlights=True)
        self.sheet.set_column_widths(self._estimate_column_widths(columns, display_rows))
        self.sheet.redraw()

        # SQL Preview companion sheet: same row count, blank until an
        # edit gives a row something to preview (see _refresh_grid_
        # highlights). A fresh result means no edits yet, so blank every
        # row rather than leaving a previous result's stale previews on
        # screen at the wrong row indices. set_sheet_data() resets column
        # widths back to tksheet's default, so re-apply ours every time,
        # same as the main sheet's own _estimate_column_widths call above.
        self.sql_preview_sheet.set_sheet_data(
            [[""] for _ in display_rows], redraw=False, reset_highlights=True
        )
        self.sql_preview_sheet.set_column_widths([520])
        self.sql_preview_sheet.redraw()

        # Baseline for the "what's been edited" diff used by both script
        # generation and grid highlighting. original_rows defaults to
        # display_rows itself (the snapshot-load case: what's shown IS
        # the starting point there's nothing further upstream to compare).
        self._original_columns = columns
        self._original_rows = original_rows if original_rows is not None else display_rows

    def _current_edited_rows(self) -> list[list[str]]:
        """Current grid contents as strings, same shape as _original_rows."""
        grid_data = self.sheet.get_sheet_data()
        return [[str(v) if v is not None else "" for v in row] for row in grid_data]

    # ------------------------------------------------------------------
    # Grid highlighting - marks cells/rows that differ from the loaded
    # result so changes are visible before you even open the Script tab.
    # Also drives the companion SQL Preview sheet: every row with at
    # least one changed cell gets that row's live UPDATE statement
    # (script_generator.build_preview_line) written into the preview
    # column right beside it, using the REAL selected key columns (not
    # an empty list) so its WHERE clause matches what Generate Update
    # Script would actually produce - the same compute_row_changes call
    # drives both, so "what's highlighted"/"what's previewed" can never
    # drift from "what actually ends up in the generated script".
    # ------------------------------------------------------------------
    def _refresh_grid_highlights(self, event=None):
        if not self._original_columns:
            return
        try:
            edited_rows = self._current_edited_rows()
            changes = diff_engine.compute_row_changes(
                self._original_columns, self._original_rows, edited_rows, key_columns=self._selected_key_columns()
            )
        except ValueError:
            # Row/column count momentarily out of sync mid-operation
            # (e.g. between two steps of a paste) - just skip this tick,
            # the next edit event will settle it.
            return

        self.sheet.dehighlight_all(redraw=False)
        if changes:
            cell_bg, cell_fg, row_bg, row_fg = grid_colors(self.config.theme)
            col_index = {col: i for i, col in enumerate(self._original_columns)}
            cells = [
                (change.row_index, col_index[cc.column])
                for change in changes
                for cc in change.cell_changes
            ]
            self.sheet.highlight_cells(cells=cells, bg=cell_bg, fg=cell_fg, redraw=False)
            for change in changes:
                self.sheet.highlight_cells(row=change.row_index, canvas="index", bg=row_bg, fg=row_fg, redraw=False)
        self.sheet.redraw()

        self._refresh_sql_preview(changes, len(edited_rows))

    def _refresh_sql_preview(self, changes: list, row_count: int):
        table = self.target_table_var.get().strip() if hasattr(self, "target_table_var") else ""
        preview_rows = [[""] for _ in range(row_count)]
        if table:
            schema = self.target_schema_var.get().strip() or None
            program = (self.jira_var.get().strip() if hasattr(self, "jira_var") else "") or script_generator.DEFAULT_AUDIT_PROGRAM
            for change in changes:
                if 0 <= change.row_index < row_count:
                    preview_rows[change.row_index][0] = script_generator.build_preview_line(
                        schema, table, change, program=program
                    )
        self.sql_preview_sheet.set_sheet_data(preview_rows, redraw=False)
        self.sql_preview_sheet.set_column_widths([520])
        self.sql_preview_sheet.redraw()

    def _on_query_success(self, result: mssql.QueryResult, generation: int | None = None):
        self._query_running = False
        self.run_query_btn.configure(state="normal")
        self.cancel_query_btn.configure(state="disabled")
        if generation is not None and generation != self._query_generation:
            return  # cancelled (or superseded by a newer query) - discard this result

        self._connected_ok = True
        self._refresh_connection_badge()

        self.query_result = result
        display_rows = [[diff_engine.cell_display(v) for v in row] for row in result.rows]

        self._populate_sheet(result.columns, display_rows, original_rows=result.rows)

        if result.source_schema and result.source_table:
            table_only = result.source_table.split(".")[-1]
            self.target_schema_var.set(result.source_schema)
            self.target_table_var.set(table_only)

        self._rebuild_key_column_picker(result.columns, checked=[])
        self.dashboard_btn.configure(state="normal")

        self.status_var.set(
            f"{result.row_count} row(s) in {result.elapsed_ms:.0f} ms."
            + (f" Guessed source table: {result.source_table}." if result.source_table else "")
        )

    # ------------------------------------------------------------------
    # Key-column picker (drives the UPDATE's WHERE clause)
    # ------------------------------------------------------------------
    def _rebuild_key_column_picker(self, columns: list[str], checked: list[str]):
        for child in self.key_columns_frame.winfo_children():
            child.destroy()
        self.key_column_vars = {}

        checked_set = set(checked)
        # One chip per row, filling the available width - this now lives
        # in the (narrower, resizable) sidebar rather than a full-width
        # bottom bar, so a multi-column grid of chips doesn't fit as well
        # as a simple stacked list that grows/shrinks with the sash.
        for col in columns:
            var = tk.BooleanVar(value=col in checked_set)
            var.trace_add("write", lambda *_: (self._update_key_hint(), self._refresh_grid_highlights()))
            self.key_column_vars[col] = var
            chip = tb.Checkbutton(
                self.key_columns_frame, text=col, variable=var, bootstyle="warning-toolbutton"
            )
            chip.pack(fill="x", pady=2, anchor="w")

        self._update_key_hint()

    def _selected_key_columns(self) -> list[str]:
        return [col for col, var in self.key_column_vars.items() if var.get()]

    def _update_key_hint(self):
        selected = self._selected_key_columns()
        if not self.key_column_vars:
            self.key_hint_var.set("Run a query, then pick which column(s) uniquely identify a row.")
        elif selected:
            self.key_hint_var.set(f"WHERE will match on: {', '.join(selected)}")
        else:
            self.key_hint_var.set("⚠ No key selected - WHERE will match on ALL columns (risk of multi-row updates).")

    def _select_all_keys(self):
        for var in self.key_column_vars.values():
            var.set(True)
        self._update_key_hint()
        self._refresh_grid_highlights()

    def _clear_all_keys(self):
        for var in self.key_column_vars.values():
            var.set(False)
        self._update_key_hint()
        self._refresh_grid_highlights()

    # ------------------------------------------------------------------
    # AI assist
    # ------------------------------------------------------------------
    def _ai_call(self, mode: str):
        if not self.config.ai.enabled or not self.config.ai.get_api_key():
            messagebox.showinfo("AI not configured", "Add a Gemini API key in Settings > AI tab.")
            return

        current_sql = self.sql_text.text.get("1.0", "end").strip()
        intent = self.ai_intent_var.get().strip()
        api_key = self.config.ai.get_api_key()
        model = self.config.ai.model

        self.ai_status_var.set("Thinking...")

        def worker():
            if mode == "suggest":
                response = ai_assist.suggest_snippet(api_key, model, current_sql, intent or "improve this query")
            elif mode == "explain":
                response = ai_assist.explain_query(api_key, model, current_sql)
            else:
                response = ai_assist.optimize_query(api_key, model, current_sql)
            self.root.after(0, lambda: self._on_ai_response(response))

        threading.Thread(target=worker, daemon=True).start()

    def _ai_suggest(self):
        self._ai_call("suggest")

    def _ai_optimize(self):
        self._ai_call("optimize")

    def _ai_explain(self):
        self._ai_call("explain")

    # --- Inline API key setup (AI Assist page) --------------------------
    def _refresh_ai_key_status(self):
        key = self.config.ai.get_api_key()
        if key:
            masked = f"{key[:4]}...{key[-4:]}" if len(key) > 10 else "set"
            state = "enabled" if self.config.ai.enabled else "saved but disabled"
            self.ai_key_status_var.set(f"✓ Key {masked} ({state}), model: {self.config.ai.model}")
        else:
            self.ai_key_status_var.set("✗ No key saved yet - paste one above and click Save.")

    def _save_ai_key(self):
        key = self.ai_key_var.get().strip()
        self.config.ai.set_api_key(key)  # also flips .enabled on/off based on whether key is non-empty
        save_config(self.config)
        self._refresh_ai_key_status()
        self.ai_status_var.set("Gemini key saved." if key else "Gemini key cleared.")

    def _test_ai_key(self):
        key = self.ai_key_var.get().strip()
        model = self.config.ai.model or "gemini-3.6-flash"
        if not key:
            self.ai_key_status_var.set("✗ Enter a key first.")
            return
        self.ai_key_status_var.set("Testing key...")
        self.root.update_idletasks()

        def worker():
            response = ai_assist.test_api_key(key, model)
            self.root.after(0, lambda: self._on_ai_key_test_result(response))

        threading.Thread(target=worker, daemon=True).start()

    def _on_ai_key_test_result(self, response: ai_assist.AIResponse):
        if response.success:
            self.ai_key_status_var.set(f"✓ Key works (model: {self.config.ai.model}).")
        else:
            self.ai_key_status_var.set(f"✗ {response.text}")

    def _on_ai_response(self, response: ai_assist.AIResponse):
        self.ai_status_var.set("" if response.success else "Error")
        self.ai_output.text.configure(state="normal")
        self.ai_output.text.delete("1.0", "end")
        self.ai_output.text.insert("1.0", response.text)
        self.ai_output.text.configure(state="disabled")
        self._last_ai_response = response

    def _ai_insert(self):
        response = getattr(self, "_last_ai_response", None)
        if not response or not response.extracted_sql:
            messagebox.showinfo("Nothing to insert", "No AI-suggested SQL to insert yet.")
            return
        self.sql_text.text.delete("1.0", "end")
        self.sql_text.text.insert("1.0", response.extracted_sql)
        self._show_page("workspace")

    # --- NL -> WHERE clause builder --------------------------------------
    def _ai_build_where(self):
        if not self.config.ai.enabled or not self.config.ai.get_api_key():
            messagebox.showinfo("AI not configured", "Add a Gemini API key in Settings > AI tab.")
            return
        request = self.ai_where_request_var.get().strip()
        if not request:
            messagebox.showinfo("Describe the rows", "Enter a plain-English description of which rows to match.")
            return

        columns = list(self.query_result.columns) if self.query_result else []
        table_context = self.target_table_var.get().strip() if hasattr(self, "target_table_var") else ""
        api_key = self.config.ai.get_api_key()
        model = self.config.ai.model
        self._set_ai_where_output("Thinking...")

        def worker():
            response = ai_assist.suggest_where_clause(api_key, model, columns, request, table_context)
            self.root.after(0, lambda: self._on_ai_where_response(response))

        threading.Thread(target=worker, daemon=True).start()

    def _on_ai_where_response(self, response: ai_assist.AIResponse):
        text = response.extracted_sql or response.text
        self._set_ai_where_output(text)
        self._last_ai_where = text if response.success else ""

    def _set_ai_where_output(self, text: str):
        self.ai_where_output.text.configure(state="normal")
        self.ai_where_output.text.delete("1.0", "end")
        self.ai_where_output.text.insert("1.0", text)
        self.ai_where_output.text.configure(state="disabled")

    def _copy_ai_where(self):
        clause = getattr(self, "_last_ai_where", "")
        if not clause:
            messagebox.showinfo("Nothing to copy", "Build a WHERE clause first.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(clause)
        self.status_var.set("WHERE clause copied to clipboard.")

    def _append_ai_where_to_query(self):
        clause = getattr(self, "_last_ai_where", "")
        if not clause:
            messagebox.showinfo("Nothing to append", "Build a WHERE clause first.")
            return
        current = self.sql_text.text.get("1.0", "end").rstrip()
        separator = "\n" if current else ""
        self.sql_text.text.insert("end", f"{separator}WHERE {clause}")
        self._highlight_sql()
        self._show_page("workspace")
        self.status_var.set("WHERE clause appended to the query editor - review before running.")

    # --- AI pre-flight review of the generated script --------------------
    def _ai_review_script(self):
        if not self.config.ai.enabled or not self.config.ai.get_api_key():
            messagebox.showinfo("AI not configured", "Add a Gemini API key in Settings > AI tab (or the AI Assist page).")
            return
        script_sql = self.script_text.text.get("1.0", "end").strip()
        if not script_sql:
            messagebox.showinfo("Nothing to review", "Generate a script first.")
            return

        api_key = self.config.ai.get_api_key()
        model = self.config.ai.model
        self.ai_review_status_var.set("Reviewing...")
        self._set_ai_review_output("")

        def worker():
            response = ai_assist.review_script(api_key, model, script_sql)
            self.root.after(0, lambda: self._on_ai_review_response(response))

        threading.Thread(target=worker, daemon=True).start()

    def _on_ai_review_response(self, response: ai_assist.AIResponse):
        self.ai_review_status_var.set(
            "Second opinion below - still review the script yourself before running it."
            if response.success else "Review failed."
        )
        self._set_ai_review_output(response.text)

    def _set_ai_review_output(self, text: str):
        self.ai_review_output.text.configure(state="normal")
        self.ai_review_output.text.delete("1.0", "end")
        self.ai_review_output.text.insert("1.0", text)
        self.ai_review_output.text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Script generation
    # ------------------------------------------------------------------
    def _auto_detect_key(self):
        conn = self.config.get_active_connection()
        schema = self.target_schema_var.get().strip()
        table = self.target_table_var.get().strip()
        if not conn or not schema or not table:
            messagebox.showwarning("Missing info", "Set target schema/table first (or run a query to auto-fill).")
            return

        def worker():
            try:
                pk_cols = mssql.get_primary_key_columns(conn, schema, table)
            except mssql.ConnectionError_ as exc:
                self.root.after(0, lambda: messagebox.showerror("Key lookup failed", str(exc)))
                return
            self.root.after(0, lambda: self._on_key_detected(pk_cols))

        threading.Thread(target=worker, daemon=True).start()

    def _on_key_detected(self, pk_cols: list[str]):
        self.pk_columns = pk_cols
        if not self.key_column_vars:
            return
        for col, var in self.key_column_vars.items():
            var.set(col in pk_cols)
        self._update_key_hint()
        if pk_cols:
            self.status_var.set(f"Detected primary key: {', '.join(pk_cols)}")
        else:
            self.status_var.set("No primary key found for this table - pick key column(s) manually below.")

    def _generate_script(self):
        if not self.query_result:
            messagebox.showwarning("No results", "Run a query first.")
            return

        table = self.target_table_var.get().strip()
        if not table:
            messagebox.showwarning("Missing table", "Enter the target table name.")
            return
        schema = self.target_schema_var.get().strip() or None

        jira = self.jira_var.get().strip()
        if not jira:
            messagebox.showwarning(
                "Missing Jira / Program #",
                "Enter the Jira/ticket number this change is for - it's stamped into every "
                "statement as update_program and is required before a script can be generated.",
            )
            return

        key_columns = self._selected_key_columns()
        edited_rows = self._current_edited_rows()

        try:
            changes = diff_engine.compute_row_changes(
                self.query_result.columns,
                self.query_result.rows,
                edited_rows,
                key_columns,
            )
        except ValueError as exc:
            messagebox.showerror("Could not diff results", str(exc))
            return

        # Stashed for the Tools tab's "Generate Rollback Script" action,
        # which needs the exact same changes/target/program to produce a
        # rollback that actually undoes THIS script.
        self._last_changes = changes
        self._last_schema = schema
        self._last_table = table
        self._last_program = jira
        self._last_source_sql = self.sql_text.text.get("1.0", "end").strip()

        result = script_generator.generate_update_script(
            schema, table, changes, source_sql=self._last_source_sql, program=jira,
        )

        self.script_text.text.delete("1.0", "end")
        self.script_text.text.insert("1.0", result.sql_text)

        self._record_script_history(
            kind=script_history.KIND_UPDATE,
            schema=schema, table=table, program=jira, result=result,
        )

        msg = f"{result.statement_count} UPDATE statement(s) generated."
        if result.warning_count:
            msg += f" {result.warning_count} with no reliable key - review before running!"
        self.status_var.set(msg)
        self._show_page("script")

    def _record_script_history(self, kind, schema, table, program, result):
        """Best-effort audit-trail write (app/db/script_history.py). A
        failure here (locked/missing db file, disk full, etc.) should
        never stop the person from seeing the script they just
        generated - it's a record of the event, not a precondition for
        it - so this swallows errors rather than raising."""
        try:
            script_history.record_script(
                self.config.internal_db_path,
                username=getpass.getuser(),
                kind=kind,
                schema_name=schema or "",
                table_name=table or "",
                program=program or "",
                statement_count=result.statement_count,
                warning_count=result.warning_count,
                sql_text=result.sql_text,
                source=script_history.SOURCE_DESKTOP,
            )
        except Exception:
            pass

    def _copy_script(self):
        text = self.script_text.text.get("1.0", "end").strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("Script copied to clipboard.")

    def _save_script_as(self):
        text = self.script_text.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("Nothing to save", "Generate a script first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".sql", filetypes=[("SQL script", "*.sql")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.status_var.set(f"Script saved to {path}")

    # ------------------------------------------------------------------
    # Internal DB export / snapshots
    # ------------------------------------------------------------------
    def _export_to_internal_db(self):
        if not self.query_result:
            messagebox.showwarning("No results", "Run a query first.")
            return
        name = simpledialog.askstring("Snapshot name", "Name for this snapshot:")
        if not name:
            return

        grid_data = self.sheet.get_sheet_data()
        table_name = internal_store.export_snapshot(
            self.config.internal_db_path,
            name,
            self.query_result.columns,
            grid_data,
            source_sql=self.sql_text.text.get("1.0", "end").strip(),
            source_table=self.query_result.source_table or "",
        )
        self.status_var.set(f"Exported {len(grid_data)} row(s) to internal DB as '{table_name}'.")

    def _browse_snapshots(self):
        snapshots = internal_store.list_snapshots(self.config.internal_db_path)
        if not snapshots:
            messagebox.showinfo("No snapshots", "No snapshots have been exported yet.")
            return

        win = tb.Toplevel(self.root)
        win.title("Snapshots")
        win.geometry("640x360")
        listbox = tk.Listbox(win)
        listbox.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        for snap in snapshots:
            listbox.insert(
                "end",
                f"{snap.created_at_utc}  |  {snap.display_name}  |  {snap.row_count} rows  |  {snap.source_table}",
            )

        def load_selected():
            sel = listbox.curselection()
            if not sel:
                return
            snap = snapshots[sel[0]]
            columns, rows = internal_store.load_snapshot(self.config.internal_db_path, snap.snapshot_table)
            # A snapshot isn't a live query result - clear it so Generate
            # Script/Dashboard (which need self.query_result) correctly
            # say "run a query first" instead of silently acting on
            # whatever the PREVIOUS live query happened to be.
            self.query_result = None
            self.dashboard_btn.configure(state="disabled")
            self._populate_sheet(columns, rows, original_rows=rows)
            self._rebuild_key_column_picker(columns, checked=[])
            self.status_var.set(f"Loaded snapshot '{snap.display_name}' ({len(rows)} rows) - read-only view.")
            self._show_page("workspace")
            win.destroy()

        tb.Button(win, text="Load into grid", bootstyle=SUCCESS, command=load_selected).pack(pady=PAD_SM)

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    def _open_dashboard(self):
        if not self.query_result or not self.query_result.columns:
            messagebox.showinfo("No results", "Run a query first, then open the Dashboard.")
            return
        DashboardWindow(self.root, self.query_result, self.config.theme, self.config.internal_db_path)

    # ------------------------------------------------------------------
    # Tools page - a home for the standalone actions that don't belong to
    # any one existing page: saved queries, comparing two snapshots,
    # generating a rollback script for whatever was last generated on the
    # Workspace page, validating the target table's schema, and switching
    # which connection is active. Laid out as stacked Labelframe "cards"
    # in a scrollable column - same visual language as the Dashboard's
    # Per-Column Summary / chart panels, just vertical instead of a grid.
    # ------------------------------------------------------------------
    def _build_tools_page(self, page):
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        tb.Label(page, text="Tools", font=("TkDefaultFont", 13, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, PAD_SM)
        )

        scroll = tb.ScrolledFrame(page, autohide=True)
        scroll.grid(row=1, column=0, sticky="nsew")

        self._build_tools_environment(scroll)
        self._build_tools_saved_queries(scroll)
        self._build_tools_schema_validation(scroll)
        self._build_tools_rollback(scroll)
        self._build_tools_snapshot_diff(scroll)
        self._build_tools_script_history(scroll)

    # --- Environment quick switcher --------------------------------------
    def _build_tools_environment(self, parent):
        card = tb.Labelframe(parent, text="Environment", padding=PAD_SM, bootstyle="secondary")
        card.pack(fill="x", pady=(0, PAD))
        tb.Label(
            card, text="Switch which saved connection is active without opening Settings.",
            bootstyle="secondary", wraplength=760, justify="left",
        ).pack(anchor="w", pady=(0, PAD_SM))

        row = tb.Frame(card)
        row.pack(fill="x")
        tb.Label(row, text="Active:").pack(side="left")
        self.env_choice_var = tk.StringVar()
        self.env_combo = tb.Combobox(row, textvariable=self.env_choice_var, state="readonly", width=36)
        self.env_combo.pack(side="left", padx=PAD_SM)
        tb.Button(row, text="🔀 Switch", bootstyle="info-outline", command=self._switch_environment).pack(side="left")
        tb.Button(
            row, text="⚙ Manage Connections...", bootstyle="secondary-outline", command=self._open_config
        ).pack(side="right")
        self._env_keys: list[str] = []
        self._refresh_env_combo()

    def _refresh_env_combo(self):
        if not hasattr(self, "env_combo"):
            return
        self._env_keys = list(self.config.connections.keys())
        labels = [f"{conn.name}  ({key})" for key, conn in self.config.connections.items()]
        self.env_combo["values"] = labels
        if self.config.active_connection in self._env_keys:
            self.env_combo.current(self._env_keys.index(self.config.active_connection))
        elif labels:
            self.env_combo.current(0)

    def _switch_environment(self):
        idx = self.env_combo.current()
        if idx < 0 or idx >= len(self._env_keys):
            return
        key = self._env_keys[idx]
        if key == self.config.active_connection:
            return
        self.config.active_connection = key
        save_config(self.config)
        self._connected_ok = None
        self._refresh_connection_badge()
        self.status_var.set(f"Switched active connection to '{self.config.connections[key].name}'.")

    # --- Saved queries -----------------------------------------------------
    def _build_tools_saved_queries(self, parent):
        card = tb.Labelframe(parent, text="Saved Queries", padding=PAD_SM, bootstyle="secondary")
        card.pack(fill="x", pady=(0, PAD))
        tb.Label(
            card, text="Named on purpose, kept until you delete them - unlike Workspace's Recent "
            "menu, which is just a rolling history of the last 15 queries you ran.",
            bootstyle="secondary", wraplength=760, justify="left",
        ).pack(anchor="w", pady=(0, PAD_SM))

        btn_row = tb.Frame(card)
        btn_row.pack(fill="x", pady=(0, PAD_SM))
        tb.Button(
            btn_row, text="💾 Save Current Query As...", bootstyle="info-outline", command=self._save_current_query
        ).pack(side="left")
        tb.Button(
            btn_row, text="▶ Load Selected", bootstyle="secondary-outline", command=self._load_selected_saved_query
        ).pack(side="left", padx=PAD_SM)
        tb.Button(
            btn_row, text="🗑 Delete Selected", bootstyle="danger-outline",
            command=self._delete_selected_saved_query,
        ).pack(side="left")

        self.saved_queries_tree = tb.Treeview(card, columns=("sql",), show="tree headings", height=6)
        self.saved_queries_tree.heading("#0", text="Name")
        self.saved_queries_tree.heading("sql", text="Query")
        self.saved_queries_tree.column("#0", width=160)
        self.saved_queries_tree.column("sql", width=560)
        self.saved_queries_tree.pack(fill="x")
        self._refresh_saved_queries_tree()

    def _refresh_saved_queries_tree(self):
        self.saved_queries_tree.delete(*self.saved_queries_tree.get_children())
        for q in self.config.saved_queries:
            preview = " ".join(q.sql.split())
            if len(preview) > 90:
                preview = preview[:87] + "..."
            self.saved_queries_tree.insert("", "end", text=q.name, values=(preview,))

    def _save_current_query(self):
        sql = self.sql_text.text.get("1.0", "end").strip()
        if not sql:
            messagebox.showinfo("Nothing to save", "Type a query first.")
            return
        name = simpledialog.askstring("Save query", "Name for this saved query:")
        if not name:
            return
        self.config.save_query(name, sql)
        save_config(self.config)
        self._refresh_saved_queries_tree()
        self.status_var.set(f"Saved query '{name}'.")

    def _selected_saved_query_name(self) -> str | None:
        sel = self.saved_queries_tree.selection()
        if not sel:
            return None
        return self.saved_queries_tree.item(sel[0], "text")

    def _load_selected_saved_query(self):
        name = self._selected_saved_query_name()
        if not name:
            messagebox.showinfo("Nothing selected", "Select a saved query first.")
            return
        match = next((q for q in self.config.saved_queries if q.name == name), None)
        if not match:
            return
        self.sql_text.text.delete("1.0", "end")
        self.sql_text.text.insert("1.0", match.sql)
        self._highlight_sql()
        self._show_page("workspace")

    def _delete_selected_saved_query(self):
        name = self._selected_saved_query_name()
        if not name:
            messagebox.showinfo("Nothing selected", "Select a saved query first.")
            return
        self.config.delete_query(name)
        save_config(self.config)
        self._refresh_saved_queries_tree()

    # --- Schema validation ---------------------------------------------
    def _build_tools_schema_validation(self, parent):
        card = tb.Labelframe(parent, text="Validate Target Schema", padding=PAD_SM, bootstyle="secondary")
        card.pack(fill="x", pady=(0, PAD))
        tb.Label(
            card, text="Checks that the current query's columns (and any chosen key columns) actually "
            "exist on Target schema/table, before you find out the hard way when someone tries to run "
            "the generated script.",
            bootstyle="secondary", wraplength=760, justify="left",
        ).pack(anchor="w", pady=(0, PAD_SM))
        tb.Button(card, text="✓ Validate Now", bootstyle="info-outline", command=self._validate_target_schema).pack(
            anchor="w"
        )
        self.schema_validation_var = tk.StringVar(value="")
        tb.Label(card, textvariable=self.schema_validation_var, wraplength=760, justify="left").pack(
            anchor="w", pady=(PAD_SM, 0)
        )

    def _validate_target_schema(self):
        if not self.query_result:
            messagebox.showinfo("No results", "Run a query first.")
            return
        conn = self.config.get_active_connection()
        schema = self.target_schema_var.get().strip() or "dbo"
        table = self.target_table_var.get().strip()
        if not conn or not table:
            messagebox.showwarning("Missing info", "Set Target schema/table in the Workspace sidebar first.")
            return
        key_columns = self._selected_key_columns()
        query_columns = self.query_result.columns
        self.schema_validation_var.set("Checking...")
        self.root.update_idletasks()

        def worker():
            try:
                table_columns = mssql.get_table_columns(conn, schema, table)
            except mssql.ConnectionError_ as exc:
                self.root.after(0, lambda: self.schema_validation_var.set(f"✗ {exc}"))
                return
            result = schema_check.validate_columns(query_columns, key_columns, table_columns)
            self.root.after(0, lambda: self._on_schema_validated(result, schema, table))

        threading.Thread(target=worker, daemon=True).start()

    def _on_schema_validated(self, result: schema_check.SchemaValidationResult, schema: str, table: str):
        if not result.table_exists:
            self.schema_validation_var.set(f"✗ {schema}.{table} was not found (or has no columns).")
            return
        if result.ok:
            msg = f"✓ {schema}.{table} looks good - every query/key column exists there."
        else:
            problems = []
            if result.missing_columns:
                problems.append(f"missing column(s): {', '.join(result.missing_columns)}")
            if result.missing_key_columns:
                problems.append(f"missing key column(s): {', '.join(result.missing_key_columns)}")
            msg = f"✗ {schema}.{table} - " + "; ".join(problems)
        if result.extra_table_columns:
            shown = ", ".join(result.extra_table_columns[:8])
            more = "..." if len(result.extra_table_columns) > 8 else ""
            msg += f"  (table also has: {shown}{more})"
        self.schema_validation_var.set(msg)

    # --- Rollback script ----------------------------------------------
    def _build_tools_rollback(self, parent):
        card = tb.Labelframe(parent, text="Rollback Script", padding=PAD_SM, bootstyle="secondary")
        card.pack(fill="x", pady=(0, PAD))
        tb.Label(
            card, text="Generates the exact reverse of the last Update Script you generated on the "
            "Workspace page - restores every changed cell to its original value. Generate the forward "
            "script first (sidebar > Generate Update Script), then come back here.",
            bootstyle="secondary", wraplength=760, justify="left",
        ).pack(anchor="w", pady=(0, PAD_SM))
        tb.Button(
            card, text="↩ Generate Rollback Script", bootstyle="warning", command=self._generate_rollback_script
        ).pack(anchor="w")

        self.rollback_output = tb.ScrolledText(card, height=10, autohide=True)
        self.rollback_output.pack(fill="both", expand=True, pady=(PAD_SM, 0))
        self.rollback_output.text.configure(state="disabled")

        btn_row = tb.Frame(card)
        btn_row.pack(fill="x", pady=(PAD_SM, 0))
        tb.Button(btn_row, text="📋 Copy", bootstyle="secondary-outline", command=self._copy_rollback_script).pack(
            side="left"
        )
        tb.Button(
            btn_row, text="💾 Save As...", bootstyle="secondary-outline", command=self._save_rollback_script_as
        ).pack(side="left", padx=PAD_SM)

    def _generate_rollback_script(self):
        changes = getattr(self, "_last_changes", None)
        if changes is None:
            messagebox.showinfo(
                "Nothing to roll back",
                "Generate an Update Script first (Workspace sidebar > Generate Update Script), "
                "then come back here.",
            )
            return
        result = script_generator.generate_rollback_script(
            self._last_schema, self._last_table, changes,
            source_sql=self._last_source_sql, program=self._last_program,
        )
        self.rollback_output.text.configure(state="normal")
        self.rollback_output.text.delete("1.0", "end")
        self.rollback_output.text.insert("1.0", result.sql_text)
        self.rollback_output.text.configure(state="disabled")

        self._record_script_history(
            kind=script_history.KIND_ROLLBACK,
            schema=self._last_schema, table=self._last_table,
            program=self._last_program, result=result,
        )

        self.status_var.set(f"Rollback script generated ({result.statement_count} statement(s)).")

    def _copy_rollback_script(self):
        text = self.rollback_output.text.get("1.0", "end").strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("Rollback script copied to clipboard.")

    def _save_rollback_script_as(self):
        text = self.rollback_output.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("Nothing to save", "Generate a rollback script first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".sql", filetypes=[("SQL script", "*.sql")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.status_var.set(f"Rollback script saved to {path}")

    # --- Snapshot diff viewer -------------------------------------------
    def _build_tools_snapshot_diff(self, parent):
        card = tb.Labelframe(parent, text="Snapshot Diff Viewer", padding=PAD_SM, bootstyle="secondary")
        card.pack(fill="x", pady=(0, PAD))
        tb.Label(
            card, text="Compares two exported snapshots of the same table/query and reports which rows "
            "were added, removed, or changed between them.",
            bootstyle="secondary", wraplength=760, justify="left",
        ).pack(anchor="w", pady=(0, PAD_SM))

        pick_row = tb.Frame(card)
        pick_row.pack(fill="x", pady=(0, PAD_SM))
        pick_row.grid_columnconfigure(1, weight=1)
        tb.Label(pick_row, text="Older:").grid(row=0, column=0, sticky="w")
        self.diff_a_var = tk.StringVar()
        self.diff_a_combo = tb.Combobox(pick_row, textvariable=self.diff_a_var, state="readonly")
        self.diff_a_combo.grid(row=0, column=1, sticky="ew", padx=PAD_SM)
        tb.Label(pick_row, text="Newer:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.diff_b_var = tk.StringVar()
        self.diff_b_combo = tb.Combobox(pick_row, textvariable=self.diff_b_var, state="readonly")
        self.diff_b_combo.grid(row=1, column=1, sticky="ew", padx=PAD_SM, pady=(4, 0))
        tb.Button(
            pick_row, text="🔄 Refresh List", bootstyle="secondary-outline", command=self._refresh_diff_snapshot_list
        ).grid(row=0, column=2, rowspan=2, padx=(PAD_SM, 0))

        key_row = tb.Frame(card)
        key_row.pack(fill="x", pady=(0, PAD_SM))
        tb.Label(key_row, text="Key column(s), comma-separated (blank = full-row match):").pack(side="left")
        self.diff_key_var = tk.StringVar()
        tb.Entry(key_row, textvariable=self.diff_key_var, width=26).pack(side="left", padx=PAD_SM)
        tb.Button(key_row, text="🔍 Compare", bootstyle=SUCCESS, command=self._compare_snapshots).pack(side="left")

        self.diff_summary_var = tk.StringVar(value="")
        tb.Label(card, textvariable=self.diff_summary_var, bootstyle="secondary").pack(anchor="w")

        self.diff_tree = tb.Treeview(card, columns=("status", "detail"), show="tree headings", height=8)
        self.diff_tree.heading("#0", text="Key")
        self.diff_tree.heading("status", text="Status")
        self.diff_tree.heading("detail", text="Detail")
        self.diff_tree.column("#0", width=180)
        self.diff_tree.column("status", width=90, anchor="center")
        self.diff_tree.column("detail", width=480)
        self.diff_tree.pack(fill="x", pady=(PAD_SM, 0))

        self._diff_snapshots_cache: list[internal_store.SnapshotInfo] = []
        self._refresh_diff_snapshot_list()

    def _refresh_diff_snapshot_list(self):
        snaps = internal_store.list_snapshots(self.config.internal_db_path)
        self._diff_snapshots_cache = snaps
        labels = [
            f"{s.display_name}  ·  {s.created_at_utc[:16].replace('T', ' ')}  ·  {s.row_count} rows"
            for s in snaps
        ]
        self.diff_a_combo["values"] = labels
        self.diff_b_combo["values"] = labels

    def _compare_snapshots(self):
        idx_a = self.diff_a_combo.current()
        idx_b = self.diff_b_combo.current()
        if idx_a < 0 or idx_b < 0:
            messagebox.showinfo("Pick two snapshots", "Choose both an Older and a Newer snapshot first.")
            return
        snap_a = self._diff_snapshots_cache[idx_a]
        snap_b = self._diff_snapshots_cache[idx_b]
        cols_a, rows_a = internal_store.load_snapshot(self.config.internal_db_path, snap_a.snapshot_table)
        cols_b, rows_b = internal_store.load_snapshot(self.config.internal_db_path, snap_b.snapshot_table)
        if cols_a != cols_b:
            messagebox.showerror(
                "Column mismatch",
                "These two snapshots don't have the same columns - pick two snapshots of the same "
                "table/query.",
            )
            return
        key_columns = [c.strip() for c in self.diff_key_var.get().split(",") if c.strip()]
        unknown = [c for c in key_columns if c not in cols_a]
        if unknown:
            messagebox.showerror("Unknown key column(s)", f"Not found in these snapshots: {', '.join(unknown)}")
            return
        result = snapshot_diff.diff_snapshots(cols_a, rows_a, rows_b, key_columns)
        self._render_snapshot_diff(result)

    def _render_snapshot_diff(self, result: snapshot_diff.SnapshotDiffResult):
        self.diff_tree.delete(*self.diff_tree.get_children())
        for row in result.added:
            self.diff_tree.insert("", "end", text=_format_diff_key(row.key), values=("added", ""))
        for row in result.removed:
            self.diff_tree.insert("", "end", text=_format_diff_key(row.key), values=("removed", ""))
        for row in result.changed:
            detail = "; ".join(f"{c.column}: {c.old_value} → {c.new_value}" for c in row.cell_changes)
            self.diff_tree.insert("", "end", text=_format_diff_key(row.key), values=("changed", detail))

        summary = (
            f"{len(result.added)} added, {len(result.removed)} removed, "
            f"{len(result.changed)} changed, {result.unchanged_count} unchanged"
        )
        if result.key_is_full_row:
            summary += "  (no key column given - matched on the full row)"
        self.diff_summary_var.set(summary)

    # --- Script history ---------------------------------------------------
    def _build_tools_script_history(self, parent):
        card = tb.Labelframe(parent, text="Script History", padding=PAD_SM, bootstyle="secondary")
        card.pack(fill="x", pady=(0, PAD))
        tb.Label(
            card, text="Every UPDATE/rollback script generated here or in the web UI, on this "
            "machine - a record of what was generated, not of anything actually run.",
            bootstyle="secondary", wraplength=760, justify="left",
        ).pack(anchor="w", pady=(0, PAD_SM))

        btn_row = tb.Frame(card)
        btn_row.pack(fill="x", pady=(0, PAD_SM))
        tb.Button(
            btn_row, text="🔄 Refresh", bootstyle="secondary-outline", command=self._refresh_script_history_tree
        ).pack(side="left")
        tb.Button(
            btn_row, text="👁 View Full Script", bootstyle="info-outline", command=self._view_selected_history_entry
        ).pack(side="left", padx=PAD_SM)

        self.script_history_tree = tb.Treeview(
            card, columns=("time", "user", "kind", "table", "stmts", "source"), show="headings", height=8
        )
        for col, text, width in (
            ("time", "When (UTC)", 140), ("user", "User", 110), ("kind", "Kind", 80),
            ("table", "Table", 200), ("stmts", "Statements", 90), ("source", "From", 80),
        ):
            self.script_history_tree.heading(col, text=text)
            self.script_history_tree.column(col, width=width, anchor="center" if col != "table" else "w")
        self.script_history_tree.pack(fill="x")
        self.script_history_tree.bind("<Double-1>", lambda e: self._view_selected_history_entry())

        self._script_history_cache: list[script_history.HistoryEntry] = []
        self._refresh_script_history_tree()

    def _refresh_script_history_tree(self):
        try:
            entries = script_history.list_history(self.config.internal_db_path, limit=200)
        except Exception as exc:
            self.script_history_tree.delete(*self.script_history_tree.get_children())
            self._script_history_cache = []
            self.status_var.set(f"Could not load script history: {exc}")
            return
        self._script_history_cache = entries
        self.script_history_tree.delete(*self.script_history_tree.get_children())
        for e in entries:
            when = e.created_at_utc[:16].replace("T", " ")
            table_label = f"{e.schema_name}.{e.table_name}" if e.schema_name else e.table_name
            self.script_history_tree.insert(
                "", "end", iid=str(e.id),
                values=(when, e.username, e.kind, table_label, e.statement_count, e.source),
            )

    def _view_selected_history_entry(self):
        sel = self.script_history_tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Select a row in Script History first.")
            return
        entry = next((e for e in self._script_history_cache if str(e.id) == sel[0]), None)
        if entry is None:
            return

        win = tb.Toplevel(self.root)
        win.title(f"Script history #{entry.id} - {entry.kind} - {entry.table_name}")
        win.geometry("760x560")

        header = (
            f"{entry.created_at_utc}  ·  {entry.username}  ·  {entry.kind}  ·  "
            f"{entry.schema_name + '.' if entry.schema_name else ''}{entry.table_name}  ·  "
            f"program {entry.program or '(none)'}  ·  {entry.statement_count} statement(s)"
            + (f"  ·  {entry.warning_count} warning(s)" if entry.warning_count else "")
        )
        tb.Label(win, text=header, wraplength=740, justify="left", padding=PAD_SM).pack(fill="x")

        body = tb.ScrolledText(win, autohide=True)
        body.pack(fill="both", expand=True, padx=PAD_SM, pady=(0, PAD_SM))
        body.text.insert("1.0", entry.sql_text)
        body.text.configure(state="disabled")

        btn_row = tb.Frame(win, padding=PAD_SM)
        btn_row.pack(fill="x")

        def _copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(entry.sql_text)
            self.status_var.set(f"Script history #{entry.id} copied to clipboard.")

        tb.Button(btn_row, text="📋 Copy", bootstyle="info-outline", command=_copy).pack(side="left")
        tb.Button(btn_row, text="Close", bootstyle="secondary-outline", command=win.destroy).pack(side="right")

    # ------------------------------------------------------------------
    # Date Anomaly page - "Diff Date System" workflow: detect readings
    # stuck with the wrong READING_PREV_DATE for a given NISS, resolve
    # which item-to-bill / XML rows they cascade into, then generate the
    # three-part correction script (app.core.date_anomaly does the actual
    # logic; this is just the three-card wiring, same visual language as
    # the Tools page's stacked Labelframe cards).
    # ------------------------------------------------------------------
    def _build_dateanomaly_page(self, page):
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        tb.Label(page, text="DIFF DATES Anomaly", font=("TkDefaultFont", 13, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, PAD_SM)
        )

        scroll = tb.ScrolledFrame(page, autohide=True)
        scroll.grid(row=1, column=0, sticky="nsew")

        tb.Label(
            scroll,
            text="Finds readings stuck with the wrong READING_PREV_DATE after the date-system "
            "change (READ_STATUS 6000, unbilled) for a given NISS, and generates a script that "
            "resets them - and the downstream item-to-bill / XML rows built from them - to the "
            "last correctly billed reading's date.",
            bootstyle="secondary", wraplength=780, justify="left",
        ).pack(anchor="w", pady=(0, PAD))

        # --- Card 1: Detect --------------------------------------------
        card1 = tb.Labelframe(scroll, text="1. Detect", padding=PAD_SM, bootstyle="secondary")
        card1.pack(fill="x", pady=(0, PAD))

        input_row = tb.Frame(card1)
        input_row.pack(fill="x", pady=(0, PAD_SM))
        input_row.grid_columnconfigure(1, weight=1)
        input_row.grid_columnconfigure(3, weight=1)
        tb.Label(input_row, text="NISS:").grid(row=0, column=0, sticky="w")
        self.da_niss_var = tk.StringVar()
        tb.Entry(input_row, textvariable=self.da_niss_var).grid(row=0, column=1, sticky="ew", padx=(4, 16))
        tb.Label(input_row, text="Billing period floor:").grid(row=0, column=2, sticky="w")
        self.da_threshold_var = tk.StringVar()
        tb.Entry(input_row, textvariable=self.da_threshold_var).grid(row=0, column=3, sticky="ew", padx=(4, 0))

        self.da_detect_btn = tb.Button(
            card1, text="🔍 Detect Anomalies", bootstyle=SUCCESS, command=self._da_detect
        )
        self.da_detect_btn.pack(anchor="w")

        self.da_summary_var = tk.StringVar(value="")
        tb.Label(
            card1, textvariable=self.da_summary_var, bootstyle="secondary", wraplength=780, justify="left"
        ).pack(anchor="w", pady=(PAD_SM, 0))

        self.da_tree = tb.Treeview(
            card1, columns=("billing_period", "reading_date", "read_status"), show="tree headings", height=8
        )
        self.da_tree.heading("#0", text="ID_READING")
        self.da_tree.heading("billing_period", text="Billing Period")
        self.da_tree.heading("reading_date", text="Reading Date")
        self.da_tree.heading("read_status", text="Read Status")
        self.da_tree.column("#0", width=140)
        self.da_tree.column("billing_period", width=140, anchor="center")
        self.da_tree.column("reading_date", width=160, anchor="center")
        self.da_tree.column("read_status", width=140, anchor="center")
        self.da_tree.pack(fill="x", pady=(PAD_SM, 0))

        tb.Label(card1, text="Case explanation", bootstyle="secondary", font=("", 9, "bold")).pack(
            anchor="w", pady=(PAD_SM, 0)
        )
        self.da_explanation_var = tk.StringVar(value="")
        tb.Label(
            card1, textvariable=self.da_explanation_var, wraplength=780, justify="left"
        ).pack(anchor="w", pady=(2, 0))

        # --- Card 2: Resolve bill links ---------------------------------
        card2 = tb.Labelframe(scroll, text="2. Resolve Bill Links", padding=PAD_SM, bootstyle="secondary")
        card2.pack(fill="x", pady=(0, PAD))
        tb.Label(
            card2,
            text="Maps each anomalous reading to its GCCOM_ITEMS_TO_BILL / GCCOM_ITEMS_TO_BILL_XML "
            "rows, and checks whether those tables carry the update_program/date/user audit "
            "columns. Run Detect first.",
            bootstyle="secondary", wraplength=780, justify="left",
        ).pack(anchor="w", pady=(0, PAD_SM))
        self.da_resolve_btn = tb.Button(
            card2, text="🧩 Resolve Bill Links", bootstyle="info", command=self._da_resolve_links, state="disabled"
        )
        self.da_resolve_btn.pack(anchor="w")
        self.da_links_summary_var = tk.StringVar(value="")
        tb.Label(
            card2, textvariable=self.da_links_summary_var, bootstyle="secondary", wraplength=780, justify="left"
        ).pack(anchor="w", pady=(PAD_SM, 0))

        # --- Card 3: Generate --------------------------------------------
        card3 = tb.Labelframe(scroll, text="3. Generate Correction Script", padding=PAD_SM, bootstyle="secondary")
        card3.pack(fill="x", pady=(0, PAD))

        prow = tb.Frame(card3)
        prow.pack(fill="x", pady=(0, PAD_SM))
        tb.Label(prow, text="Jira / Program # (required):").pack(side="left")
        self.da_program_var = tk.StringVar(value=script_generator.DEFAULT_AUDIT_PROGRAM)
        tb.Entry(prow, textvariable=self.da_program_var, width=20).pack(side="left", padx=PAD_SM)
        self.da_clean_var = tk.BooleanVar(value=False)
        tb.Checkbutton(
            prow, text="Clean script (no comments)", variable=self.da_clean_var, bootstyle="round-toggle",
        ).pack(side="left", padx=PAD_SM)

        self.da_generate_btn = tb.Button(
            card3, text="📝 Generate Correction Script", bootstyle=SUCCESS,
            command=self._da_generate_script, state="disabled",
        )
        self.da_generate_btn.pack(anchor="w")

        self.da_output = tb.ScrolledText(card3, height=16, autohide=True)
        self.da_output.pack(fill="both", expand=True, pady=(PAD_SM, 0))
        self.da_output.text.configure(state="disabled")

        da_btn_row = tb.Frame(card3)
        da_btn_row.pack(fill="x", pady=(PAD_SM, 0))
        tb.Button(
            da_btn_row, text="📋 Copy", bootstyle="secondary-outline", command=self._da_copy_script
        ).pack(side="left")
        tb.Button(
            da_btn_row, text="💾 Save As...", bootstyle="secondary-outline", command=self._da_save_script_as
        ).pack(side="left", padx=PAD_SM)

        # --- Card 4: Analysis History --------------------------------------
        card4 = tb.Labelframe(scroll, text="4. Analysis History", padding=PAD_SM, bootstyle="secondary")
        card4.pack(fill="x", pady=(0, PAD))
        tb.Label(
            card4,
            text="Every NISS run through Detect, on this machine or the web UI - including ones that "
            "never made it to Generate. Double-click a row to see its full case explanation.",
            bootstyle="secondary", wraplength=780, justify="left",
        ).pack(anchor="w", pady=(0, PAD_SM))
        tb.Button(
            card4, text="🔄 Refresh", bootstyle="secondary-outline", command=self._da_refresh_history_tree
        ).pack(anchor="w")
        self.da_history_tree = tb.Treeview(
            card4, columns=("niss", "user", "source", "anomalies", "items", "status_adv", "xml", "anomalous", "generated"),
            show="headings", height=8,
        )
        for col, text, width in (
            ("niss", "NISS", 130), ("user", "User", 90), ("source", "Source", 70),
            ("anomalies", "Anomalies", 80), ("items", "Items", 60), ("status_adv", "Status adv.", 80),
            ("xml", "XML", 50), ("anomalous", "Anomalies cancelled", 130), ("generated", "Generated?", 80),
        ):
            self.da_history_tree.heading(col, text=text)
            self.da_history_tree.column(col, width=width, anchor="center")
        self.da_history_tree.pack(fill="x", pady=(PAD_SM, 0))
        self.da_history_tree.bind("<Double-1>", lambda e: self._da_view_history_explanation())
        self._da_history_entries: dict = {}  # tree item id -> AnalysisEntry

        # --- State ----------------------------------------------------
        self._da_niss: str = ""
        self._da_threshold: int = 0
        self._da_anomaly_rows: list[dict] = []
        self._da_correct_date = None
        self._da_correct_date_reading = None
        self._da_item_to_bill_map: dict = {}
        self._da_xml_rows: dict = {}
        self._da_item_to_xml_map: dict = {}
        self._da_anomalous_item_ids: list = []
        self._da_item_status_ids: list = []
        self._da_history_id = None
        self._da_reading_has_audit = False
        self._da_item_has_audit = False

    def _da_detect(self):
        conn = self.config.get_active_connection()
        if not conn:
            messagebox.showwarning("No connection", "Configure a connection first (Settings).")
            return
        niss = self.da_niss_var.get().strip()
        if not niss:
            messagebox.showwarning("Missing NISS", "Enter the sector supply (NISS) to investigate.")
            return
        threshold_raw = self.da_threshold_var.get().strip()
        try:
            threshold = int(threshold_raw) if threshold_raw else 0
        except ValueError:
            messagebox.showerror(
                "Invalid billing period floor",
                "Billing period floor must be a whole number (or blank, which means 0).",
            )
            return

        # A fresh Detect invalidates anything already resolved/generated
        # for a previous NISS - clearing it up front avoids generating a
        # script that mixes this NISS's readings with a stale link/audit
        # lookup from whatever was investigated before.
        self._da_anomaly_rows = []
        self._da_correct_date = None
        self._da_correct_date_reading = None
        self._da_item_to_bill_map = {}
        self._da_xml_rows = {}
        self._da_item_to_xml_map = {}
        self._da_anomalous_item_ids = []
        self._da_item_status_ids = []
        self._da_history_id = None
        self.da_explanation_var.set("")
        self.da_tree.delete(*self.da_tree.get_children())
        self.da_links_summary_var.set("")
        self.da_output.text.configure(state="normal")
        self.da_output.text.delete("1.0", "end")
        self.da_output.text.configure(state="disabled")
        self.da_resolve_btn.configure(state="disabled")
        self.da_generate_btn.configure(state="disabled")

        self.da_detect_btn.configure(state="disabled")
        self.status_var.set(f"Detecting date anomalies for NISS {niss}...")

        def worker():
            try:
                detect_sql = date_anomaly.build_detect_query(niss, threshold)
                detect_result = mssql.run_query(conn, detect_sql)
                correct_sql = date_anomaly.build_correct_date_query(niss, threshold)
                correct_result = mssql.run_query(conn, correct_sql)
            except mssql.ConnectionError_ as exc:
                self.root.after(0, lambda: self._da_on_detect_error(str(exc)))
                return
            self.root.after(0, lambda: self._da_on_detect_success(niss, threshold, detect_result, correct_result))

        threading.Thread(target=worker, daemon=True).start()

    def _da_on_detect_error(self, message: str):
        self.da_detect_btn.configure(state="normal")
        self.status_var.set(f"✗ {message}")
        messagebox.showerror("Detect failed", message)

    def _da_on_detect_success(self, niss, threshold, detect_result, correct_result):
        self.da_detect_btn.configure(state="normal")
        self._da_niss = niss
        self._da_threshold = threshold

        rows = [dict(zip(detect_result.columns, r)) for r in detect_result.rows]
        self._da_anomaly_rows = rows

        for row in rows:
            self.da_tree.insert(
                "", "end", text=str(_da_col(row, "ID_READING")),
                values=(
                    _da_col(row, "ID_BILLING_PERIOD"),
                    _da_col(row, "READING_DATE"),
                    _da_col(row, "READ_STATUS"),
                ),
            )

        if correct_result.rows:
            crow = dict(zip(correct_result.columns, correct_result.rows[0]))
            self._da_correct_date = _da_col(crow, "READING_DATE")
            self._da_correct_date_reading = _da_col(crow, "ID_READING")
        else:
            self._da_correct_date = None
            self._da_correct_date_reading = None

        summary = f"{len(rows)} anomalous reading(s) found."
        if self._da_correct_date is not None:
            summary += (
                f"  Correct date: {self._da_correct_date}  "
                f"(from ID_READING {self._da_correct_date_reading})."
            )
        else:
            summary += "  No correctly-billed reading found above this threshold - can't determine the correct date."
        self.da_summary_var.set(summary)
        explanation = date_anomaly.build_case_explanation(
            niss=niss, threshold=threshold, anomaly_count=len(rows),
            correct_date=self._da_correct_date, correct_date_source_reading=self._da_correct_date_reading,
        )
        self.da_explanation_var.set(explanation)
        self._da_history_id = None
        try:
            self._da_history_id = date_anomaly_history.record_analysis(
                self.config.internal_db_path,
                username=getpass.getuser(), niss=niss, threshold=threshold, anomaly_count=len(rows),
                correct_date=self._da_correct_date, correct_date_reading=self._da_correct_date_reading,
                explanation=explanation, source=date_anomaly_history.SOURCE_DESKTOP,
            )
        except Exception:
            # Same stance as _record_script_history - a record of the
            # event, never a precondition for seeing it.
            pass
        if hasattr(self, "da_history_tree"):
            self._da_refresh_history_tree()

        can_proceed = bool(rows) and self._da_correct_date is not None
        self.da_resolve_btn.configure(state="normal" if can_proceed else "disabled")
        self.da_generate_btn.configure(state="disabled")
        self.status_var.set(f"Detect complete: {len(rows)} anomalous reading(s).")

        if not rows:
            messagebox.showinfo(
                "No anomalies found",
                f"No anomalous readings found for NISS {niss} above billing period {threshold}.",
            )
        elif self._da_correct_date is None:
            messagebox.showwarning(
                "No correct date found",
                f"{len(rows)} anomalous reading(s) found, but no correctly-billed (7000STSRED) "
                f"reading exists for NISS {niss} above billing period {threshold} to source the "
                "correction date from. Try a lower billing period floor.",
            )

    def _da_resolve_links(self):
        conn = self.config.get_active_connection()
        if not conn:
            messagebox.showwarning("No connection", "Configure a connection first (Settings).")
            return
        if not self._da_anomaly_rows:
            messagebox.showinfo("Nothing to resolve", "Run Detect first.")
            return

        id_readings = [_da_col(r, "ID_READING") for r in self._da_anomaly_rows]
        self.da_resolve_btn.configure(state="disabled")
        self.status_var.set("Resolving item-to-bill / XML links...")

        def worker():
            try:
                # ID_READING -> list[ID_ITEM_TO_BILL]: a reading can map to
                # more than one item-to-bill row - accumulate, don't overwrite
                # (see app.core.date_anomaly.build_correction_script's docstring).
                item_map: dict = {}
                item_sql = date_anomaly.build_item_to_bill_query(id_readings)
                if item_sql:
                    item_result = mssql.run_query(conn, item_sql)
                    for r in item_result.rows:
                        row = dict(zip(item_result.columns, r))
                        reading_id = _da_col(row, "ID_READING")
                        item_id = _da_col(row, "ID_ITEM_TO_BILL")
                        if item_id is None:
                            continue
                        item_map.setdefault(reading_id, [])
                        if item_id not in item_map[reading_id]:
                            item_map[reading_id].append(item_id)

                item_ids = sorted({v for ids in item_map.values() for v in ids}, key=str)
                # RJ, 2026-09-13: "you are updating using id_item_to_bill,
                # you need to get first the id_xml from gccom_item_to_bill
                # and update with that id" - GCCOM_ITEMS_TO_BILL.ID_XML is
                # the real FK, not the same value as ID_ITEM_TO_BILL (see
                # date_anomaly module docstring). Look it up first.
                item_to_xml_map: dict = {}
                item_xml_id_sql = date_anomaly.build_item_xml_id_query(item_ids)
                if item_xml_id_sql:
                    item_xml_id_result = mssql.run_query(conn, item_xml_id_sql)
                    for r in item_xml_id_result.rows:
                        row = dict(zip(item_xml_id_result.columns, r))
                        id_xml_val = _da_col(row, "ID_XML")
                        if id_xml_val is not None:
                            item_to_xml_map[_da_col(row, "ID_ITEM_TO_BILL")] = id_xml_val

                xml_rows: dict = {}
                real_id_xmls = sorted({v for v in item_to_xml_map.values()}, key=str)
                xml_sql = date_anomaly.build_xml_lookup_query(real_id_xmls)
                if xml_sql:
                    xml_result = mssql.run_query(conn, xml_sql)
                    for r in xml_result.rows:
                        row = dict(zip(xml_result.columns, r))
                        xml_rows[_da_col(row, "ID_XML")] = _da_col(row, date_anomaly.XML_TO_BILL_COLUMN)

                # Part 5 candidates - item-to-bill ids with an OPEN anomaly
                # (ESTAN00009/ESTAN00001) to cancel.
                anomalous_item_ids: list = []
                anomalous_sql = date_anomaly.build_anomalous_query(item_ids)
                if anomalous_sql:
                    anomalous_result = mssql.run_query(conn, anomalous_sql)
                    seen_anomalous: set = set()
                    for r in anomalous_result.rows:
                        row = dict(zip(anomalous_result.columns, r))
                        aid = _da_col(row, "ID_ITEM_TO_BILL")
                        if aid is not None and aid not in seen_anomalous:
                            seen_anomalous.add(aid)
                            anomalous_item_ids.append(aid)

                # Part 3 candidates - item-to-bill ids still at STTOBILL00,
                # to advance to STTOBILL01.
                item_status_ids: list = []
                item_status_sql = date_anomaly.build_item_status_query(item_ids)
                if item_status_sql:
                    item_status_result = mssql.run_query(conn, item_status_sql)
                    seen_item_status: set = set()
                    for r in item_status_result.rows:
                        row = dict(zip(item_status_result.columns, r))
                        sid_val = _da_col(row, "ID_ITEM_TO_BILL")
                        if sid_val is not None and sid_val not in seen_item_status:
                            seen_item_status.add(sid_val)
                            item_status_ids.append(sid_val)

                reading_cols = mssql.get_table_columns(conn, date_anomaly.READING_SCHEMA, date_anomaly.READING_TABLE)
                item_cols = mssql.get_table_columns(conn, date_anomaly.ADMIN_SCHEMA, date_anomaly.ITEMS_TO_BILL_TABLE)
                reading_has_audit = "update_program" in {c.lower() for c in reading_cols}
                item_has_audit = "update_program" in {c.lower() for c in item_cols}
            except mssql.ConnectionError_ as exc:
                self.root.after(0, lambda: self._da_on_resolve_error(str(exc)))
                return
            self.root.after(
                0,
                lambda: self._da_on_resolve_success(
                    item_map, xml_rows, item_to_xml_map, anomalous_item_ids, item_status_ids,
                    reading_has_audit, item_has_audit
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _da_on_resolve_error(self, message: str):
        self.da_resolve_btn.configure(state="normal")
        self.status_var.set(f"✗ {message}")
        messagebox.showerror("Resolve failed", message)

    def _da_on_resolve_success(
        self, item_map, xml_rows, item_to_xml_map, anomalous_item_ids, item_status_ids,
        reading_has_audit, item_has_audit
    ):
        self.da_resolve_btn.configure(state="normal")
        self._da_item_to_bill_map = item_map
        self._da_xml_rows = xml_rows
        self._da_item_to_xml_map = item_to_xml_map
        self._da_anomalous_item_ids = anomalous_item_ids
        self._da_item_status_ids = item_status_ids
        self._da_reading_has_audit = reading_has_audit
        self._da_item_has_audit = item_has_audit

        linked = sum(1 for ids in item_map.values() if ids)
        distinct_items = len({v for ids in item_map.values() for v in ids})
        summary = (
            f"{linked}/{len(self._da_anomaly_rows)} anomalous reading(s) linked to "
            f"{distinct_items} item-to-bill id(s); {len(xml_rows)} XML row(s) found; "
            f"{len(item_status_ids)} item(s) to advance status; "
            f"{len(anomalous_item_ids)} item(s) with open anomalies to cancel."
        )
        self.da_links_summary_var.set(summary)
        explanation = date_anomaly.build_case_explanation(
            niss=self._da_niss, threshold=self._da_threshold, anomaly_count=len(self._da_anomaly_rows),
            correct_date=self._da_correct_date, correct_date_source_reading=self._da_correct_date_reading,
            item_count=distinct_items, xml_count=len(xml_rows),
            item_status_count=len(item_status_ids), anomalous_count=len(anomalous_item_ids),
        )
        self.da_explanation_var.set(explanation)
        if self._da_history_id is not None:
            try:
                date_anomaly_history.update_analysis(
                    self.config.internal_db_path, self._da_history_id,
                    item_count=distinct_items, xml_count=len(xml_rows),
                    item_status_count=len(item_status_ids), anomalous_count=len(anomalous_item_ids),
                    explanation=explanation,
                )
            except Exception:
                pass
        if hasattr(self, "da_history_tree"):
            self._da_refresh_history_tree()
        self.da_generate_btn.configure(state="normal")
        self.status_var.set("Resolve complete.")

    def _da_generate_script(self):
        if not self._da_anomaly_rows or self._da_correct_date is None:
            messagebox.showinfo("Nothing to generate", "Run Detect (and Resolve Bill Links) first.")
            return
        program = self.da_program_var.get().strip()
        if not program:
            messagebox.showwarning(
                "Missing Jira / Program #",
                "Enter the Jira/ticket number this change is for before generating the script.",
            )
            return

        id_readings = [_da_col(r, "ID_READING") for r in self._da_anomaly_rows]
        # Part 6 candidates - see date_anomaly.READING_TYPE_ORPHAN_USAGE for
        # the rule; same filter as web/server.py's date_anomaly_generate
        # route. self._da_anomaly_rows already has READING_TYPE/READ_STATUS
        # from build_detect_query, no extra query needed.
        orphan_usage_ids = [
            _da_col(r, "ID_READING") for r in self._da_anomaly_rows
            if _da_col(r, "READING_TYPE") == date_anomaly.READING_TYPE_ORPHAN_USAGE
            and _da_col(r, "READ_STATUS") == date_anomaly.READ_STATUS_ANOMALY
        ]
        billing_period_count = len({_da_col(r, "ID_BILLING_PERIOD") for r in self._da_anomaly_rows})
        result = date_anomaly.build_correction_script(
            niss=self._da_niss,
            threshold=self._da_threshold,
            correct_date=self._da_correct_date,
            correct_date_source_reading=self._da_correct_date_reading,
            anomaly_id_readings=id_readings,
            item_to_bill_map=self._da_item_to_bill_map,
            xml_rows=self._da_xml_rows,
            item_to_xml_map=self._da_item_to_xml_map,
            anomalous_item_ids=self._da_anomalous_item_ids,
            item_status_ids=self._da_item_status_ids,
            orphan_usage_id_readings=orphan_usage_ids,
            billing_period_count=billing_period_count,
            reading_has_audit_cols=self._da_reading_has_audit,
            item_has_audit_cols=self._da_item_has_audit,
            program=program,
            clean=self.da_clean_var.get(),
        )

        self.da_output.text.configure(state="normal")
        self.da_output.text.delete("1.0", "end")
        self.da_output.text.insert("1.0", result.sql_text)
        self.da_output.text.configure(state="disabled")

        self._record_script_history(
            kind=script_history.KIND_DATE_ANOMALY,
            schema=None,
            table=f"NISS {self._da_niss}",
            program=program,
            result=result,
        )

        msg = (
            f"Correction script generated: {result.reading_count} reading, {result.item_count} item, "
            f"{result.item_status_count} status-transition, {result.xml_count} XML, "
            f"{result.anomalous_count} anomaly-cancel statement(s)."
        )
        if result.warnings:
            msg += f"  {len(result.warnings)} warning(s) - see comments at the top of the script."
        self.status_var.set(msg)
        if result.explanation:
            self.da_explanation_var.set(result.explanation)
        if self._da_history_id is not None:
            try:
                date_anomaly_history.update_analysis(
                    self.config.internal_db_path, self._da_history_id,
                    item_count=result.item_count, xml_count=result.xml_count,
                    item_status_count=result.item_status_count, anomalous_count=result.anomalous_count,
                    generated=True, explanation=result.explanation,
                )
            except Exception:
                pass
        if hasattr(self, "da_history_tree"):
            self._da_refresh_history_tree()

    def _da_refresh_history_tree(self):
        try:
            entries = date_anomaly_history.list_analyses(self.config.internal_db_path, limit=200)
        except Exception:
            self.da_history_tree.delete(*self.da_history_tree.get_children())
            self._da_history_entries = {}
            return
        self.da_history_tree.delete(*self.da_history_tree.get_children())
        self._da_history_entries = {}
        for e in entries:
            item_id = self.da_history_tree.insert(
                "", "end",
                values=(
                    e.niss, e.username, e.source, e.anomaly_count,
                    e.item_count if e.item_count is not None else "",
                    e.item_status_count if e.item_status_count is not None else "",
                    e.xml_count if e.xml_count is not None else "",
                    e.anomalous_count if e.anomalous_count is not None else "",
                    "✓" if e.generated else "",
                ),
            )
            self._da_history_entries[item_id] = e

    def _da_view_history_explanation(self):
        sel = self.da_history_tree.selection()
        if not sel:
            return
        entry = self._da_history_entries.get(sel[0])
        if entry is None:
            return
        messagebox.showinfo(
            f"NISS {entry.niss} — analyzed {entry.updated_at_utc[:16].replace('T', ' ')} UTC",
            entry.explanation or "No explanation recorded for this analysis.",
        )

    def _da_copy_script(self):
        text = self.da_output.text.get("1.0", "end").strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("Correction script copied to clipboard.")

    def _da_save_script_as(self):
        text = self.da_output.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("Nothing to save", "Generate a correction script first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".sql", filetypes=[("SQL script", "*.sql")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.status_var.set(f"Correction script saved to {path}")

    # ------------------------------------------------------------------
    def _show_about(self):
        messagebox.showinfo(
            "About ScriptGen",
            "ScriptGen - connect to SQL Server, edit results, generate review-ready "
            "UPDATE scripts, export snapshots, and get AI-assisted query suggestions.\n\n"
            "This tool never executes changes against the source database itself.",
        )
