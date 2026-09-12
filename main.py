"""
ScriptGen entry point.

Dev usage:   python main.py
Built usage: ScriptGen.exe  (see build.bat)
"""
import sys
import tkinter as tk

import ttkbootstrap as tb

from app.ui.theme import LIGHT_THEME


def main():
    root = tb.Window(themename=LIGHT_THEME)
    try:
        from app.ui.main_window import MainWindow

        MainWindow(root)
    except Exception as exc:  # noqa: BLE001 - show a dialog instead of a bare traceback on Windows
        try:
            from tkinter import messagebox

            messagebox.showerror("ScriptGen failed to start", str(exc))
        finally:
            raise
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main() or 0)
