"""
Configuration dialog: SQL Server connection settings (with a Test
Connection button, and a New/Delete/Set Active switcher across multiple
named connections - e.g. "default" for the local tunnel plus a
"staging" or "prod-readonly" entry someone adds later) and the Gemini
AI API key. Styled with ttkbootstrap to match the main window.
"""
from __future__ import annotations

import threading
import tkinter as tk

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import messagebox, simpledialog

from ..config import AppConfig, ConnectionConfig
from ..db import mssql
from .theme import PAD, PAD_SM


class ConfigDialog(tb.Toplevel):
    def __init__(self, parent, config: AppConfig, on_save):
        super().__init__(parent)
        self.title("ScriptGen Configuration")
        self.geometry("560x640")
        self.resizable(False, False)
        self.config_obj = config
        self.on_save = on_save
        self.transient(parent)
        self.grab_set()

        # Which connection key's fields the form is currently showing/
        # editing. Switching the picker combobox first flushes the
        # in-progress form edits back into config_obj.connections under
        # THIS key, so browsing between connections doesn't silently
        # discard unsaved changes.
        self._editing_key = config.active_connection

        self._build_connection_tab()
        self._load_from_config()

    # ------------------------------------------------------------------
    def _build_connection_tab(self):
        notebook = tb.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=PAD, pady=PAD)

        conn_frame = tb.Frame(notebook, padding=PAD)
        ai_frame = tb.Frame(notebook, padding=PAD)
        notebook.add(conn_frame, text="  Database Connection  ")
        notebook.add(ai_frame, text="  AI (Gemini)  ")

        # --- Connection tab -------------------------------------------------
        picker = tb.Labelframe(conn_frame, text="Connections", padding=PAD_SM)
        picker.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, PAD))

        tb.Label(picker, text="Editing:").grid(row=0, column=0, sticky="w", padx=(0, PAD_SM))
        self.conn_key_var = tk.StringVar()
        self.conn_picker = tb.Combobox(picker, textvariable=self.conn_key_var, width=22, state="readonly")
        self.conn_picker.grid(row=0, column=1, sticky="w")
        self.conn_picker.bind("<<ComboboxSelected>>", self._on_connection_picked)

        self.active_conn_var = tk.StringVar()
        tb.Label(picker, textvariable=self.active_conn_var, bootstyle="success").grid(
            row=0, column=2, sticky="w", padx=(PAD, 0)
        )

        picker_btns = tb.Frame(picker)
        picker_btns.grid(row=1, column=0, columnspan=3, sticky="w", pady=(PAD_SM, 0))
        tb.Button(picker_btns, text="+ New", bootstyle="secondary-outline", command=self._new_connection).pack(
            side="left", padx=(0, PAD_SM)
        )
        tb.Button(
            picker_btns, text="Set Active", bootstyle="success-outline", command=self._set_active_connection
        ).pack(side="left", padx=(0, PAD_SM))
        tb.Button(
            picker_btns, text="Delete", bootstyle="danger-outline", command=self._delete_connection
        ).pack(side="left")

        row = 1
        self.vars = {}
        fields = [
            ("name", "Connection name"),
            ("server", "Server (e.g. localhost for your tunnel)"),
            ("port", "Port"),
            ("database", "Database (blank = login default)"),
            ("username", "Username"),
        ]
        for key, label in fields:
            tb.Label(conn_frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, PAD), pady=PAD_SM)
            var = tk.StringVar()
            entry = tb.Entry(conn_frame, textvariable=var, width=36)
            entry.grid(row=row, column=1, sticky="w", pady=PAD_SM)
            self.vars[key] = var
            row += 1

        tb.Label(conn_frame, text="Password").grid(row=row, column=0, sticky="w", padx=(0, PAD), pady=PAD_SM)
        self.vars["password"] = tk.StringVar()
        pw_entry = tb.Entry(conn_frame, textvariable=self.vars["password"], width=36, show="*")
        pw_entry.grid(row=row, column=1, sticky="w", pady=PAD_SM)
        row += 1

        show_pw = tk.BooleanVar(value=False)

        def toggle_pw():
            pw_entry.config(show="" if show_pw.get() else "*")

        tb.Checkbutton(
            conn_frame, text="Show password", variable=show_pw, command=toggle_pw, bootstyle="round-toggle"
        ).grid(row=row, column=1, sticky="w")
        row += 1

        tb.Label(conn_frame, text="Timeout (seconds)").grid(row=row, column=0, sticky="w", padx=(0, PAD), pady=PAD_SM)
        self.vars["timeout_seconds"] = tk.StringVar()
        tb.Entry(conn_frame, textvariable=self.vars["timeout_seconds"], width=10).grid(
            row=row, column=1, sticky="w", pady=PAD_SM
        )
        row += 1

        self.test_result_var = tk.StringVar(value="")
        self.test_result_label = tb.Label(
            conn_frame, textvariable=self.test_result_var, bootstyle="secondary", wraplength=480
        )
        self.test_result_label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(PAD, 4))
        row += 1

        btn_frame = tb.Frame(conn_frame)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=PAD)
        tb.Button(btn_frame, text="Test Connection", bootstyle="info-outline", command=self._test_connection).pack(
            side="left", padx=(0, PAD_SM)
        )
        tb.Button(btn_frame, text="Save", bootstyle=SUCCESS, command=self._save).pack(side="left", padx=PAD_SM)
        tb.Button(btn_frame, text="Cancel", bootstyle="secondary-outline", command=self.destroy).pack(
            side="left", padx=PAD_SM
        )

        # --- AI tab -----------------------------------------------------
        tb.Label(
            ai_frame,
            text=(
                "Get a free Gemini API key at aistudio.google.com/apikey.\n"
                "Used only for the Suggest/Optimize buttons in the query editor."
            ),
            wraplength=460,
            justify="left",
            bootstyle="secondary",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, PAD))

        tb.Label(ai_frame, text="Gemini API key").grid(row=1, column=0, sticky="w", padx=(0, PAD), pady=PAD_SM)
        self.ai_key_var = tk.StringVar()
        tb.Entry(ai_frame, textvariable=self.ai_key_var, width=40, show="*").grid(
            row=1, column=1, sticky="w", pady=PAD_SM
        )

        tb.Label(ai_frame, text="Model").grid(row=2, column=0, sticky="w", padx=(0, PAD), pady=PAD_SM)
        self.ai_model_var = tk.StringVar(value="gemini-3.6-flash")
        tb.Entry(ai_frame, textvariable=self.ai_model_var, width=40).grid(row=2, column=1, sticky="w", pady=PAD_SM)

        self.ai_enabled_var = tk.BooleanVar(value=False)
        tb.Checkbutton(
            ai_frame, text="Enable AI suggestions", variable=self.ai_enabled_var, bootstyle="round-toggle"
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=PAD)

    # ------------------------------------------------------------------
    def _load_from_config(self):
        self._refresh_connection_picker()
        self._load_connection_into_form(self._editing_key)

        self.ai_key_var.set(self.config_obj.ai.get_api_key())
        self.ai_model_var.set(self.config_obj.ai.model or "gemini-3.6-flash")
        self.ai_enabled_var.set(self.config_obj.ai.enabled)

    def _refresh_connection_picker(self):
        keys = sorted(self.config_obj.connections.keys()) or ["default"]
        self.conn_picker.configure(values=keys)
        if self._editing_key not in self.config_obj.connections:
            self._editing_key = keys[0]
        self.conn_key_var.set(self._editing_key)
        self.active_conn_var.set(f"● active: {self.config_obj.active_connection}")

    def _load_connection_into_form(self, key: str):
        conn = self.config_obj.connections.get(key) or ConnectionConfig()
        self.vars["name"].set(conn.name)
        self.vars["server"].set(conn.server)
        self.vars["port"].set(str(conn.port))
        self.vars["database"].set(conn.database)
        self.vars["username"].set(conn.username)
        self.vars["password"].set(conn.get_password())
        self.vars["timeout_seconds"].set(str(conn.timeout_seconds))
        self.test_result_var.set("")

    def _flush_form_into_editing_key(self) -> bool:
        """Writes the form's current field values into
        config_obj.connections[self._editing_key] (in memory only - a
        real file save still needs the dialog's own Save button).
        Returns False (and shows an error) if the fields don't parse,
        without touching config_obj."""
        try:
            conn_cfg = self._build_connection_from_form()
        except ValueError as exc:
            messagebox.showerror("Invalid input", f"Check port/timeout - {exc}")
            return False
        self.config_obj.add_connection(self._editing_key, conn_cfg)
        return True

    def _on_connection_picked(self, event=None):
        new_key = self.conn_key_var.get()
        if new_key == self._editing_key:
            return
        if not self._flush_form_into_editing_key():
            self.conn_key_var.set(self._editing_key)  # revert the picker
            return
        self._editing_key = new_key
        self._load_connection_into_form(new_key)

    def _new_connection(self):
        if not self._flush_form_into_editing_key():
            return
        key = simpledialog.askstring(
            "New connection",
            "Short key for this connection (e.g. staging, prod-readonly):",
            parent=self,
        )
        if not key:
            return
        key = key.strip().lower().replace(" ", "-")
        if not key:
            return
        if key in self.config_obj.connections:
            messagebox.showerror("Already exists", f"A connection named '{key}' already exists.")
            return
        self.config_obj.add_connection(key, ConnectionConfig(name=key.title()))
        self._editing_key = key
        self._refresh_connection_picker()
        self._load_connection_into_form(key)

    def _set_active_connection(self):
        if not self._flush_form_into_editing_key():
            return
        self.config_obj.active_connection = self._editing_key
        self._refresh_connection_picker()
        messagebox.showinfo("Active connection set", f"'{self._editing_key}' is now the active connection.")

    def _delete_connection(self):
        key = self._editing_key
        if not messagebox.askyesno("Delete connection", f"Delete the '{key}' connection? This can't be undone."):
            return
        try:
            self.config_obj.remove_connection(key)
        except ValueError as exc:
            messagebox.showerror("Can't delete", str(exc))
            return
        if not self.config_obj.connections:
            # Never leave the app with zero connections to pick from.
            self.config_obj.add_connection("default", ConnectionConfig())
            self.config_obj.active_connection = "default"
        self._editing_key = sorted(self.config_obj.connections.keys())[0]
        self._refresh_connection_picker()
        self._load_connection_into_form(self._editing_key)

    def _build_connection_from_form(self) -> ConnectionConfig:
        conn = ConnectionConfig(
            name=self.vars["name"].get().strip() or "Default",
            server=self.vars["server"].get().strip() or "localhost",
            port=int(self.vars["port"].get().strip() or "1433"),
            database=self.vars["database"].get().strip(),
            username=self.vars["username"].get().strip(),
            timeout_seconds=int(self.vars["timeout_seconds"].get().strip() or "10"),
        )
        conn.set_password(self.vars["password"].get())
        return conn

    def _test_connection(self):
        try:
            conn_cfg = self._build_connection_from_form()
        except ValueError as exc:
            messagebox.showerror("Invalid input", f"Check port/timeout - {exc}")
            return

        self.test_result_var.set("Testing...")
        self.test_result_label.configure(bootstyle="secondary")
        self.update_idletasks()

        def worker():
            success, message, elapsed_ms = mssql.test_connection(conn_cfg)
            prefix = "✓" if success else "✗"
            self.test_result_var.set(f"{prefix} {message} ({elapsed_ms:.0f} ms)")
            self.test_result_label.configure(bootstyle="success" if success else "danger")

        threading.Thread(target=worker, daemon=True).start()

    def _save(self):
        if not self._flush_form_into_editing_key():
            return

        self.config_obj.ai.set_api_key(self.ai_key_var.get())
        self.config_obj.ai.model = self.ai_model_var.get().strip() or "gemini-3.6-flash"
        self.config_obj.ai.enabled = self.ai_enabled_var.get()

        self.on_save(self.config_obj)
        self.destroy()
