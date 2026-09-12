"""
Web entry point: starts the FastAPI app (web/server.py) via uvicorn and
opens it in the user's actual default web browser (stdlib `webbrowser`)
- a real browser tab, bookmarkable, with normal back/forward/zoom -
unlike desktop_launcher.py, which opens a chrome-less native pywebview
window instead. Use this one when "the web version" is genuinely what's
wanted, not a desktop-app-shaped wrapper around it.

Binds a FIXED port (PORT below) rather than an auto-picked free one, on
purpose: a fixed port means a desktop shortcut / bookmark to
http://127.0.0.1:8420/ keeps working across restarts. If that port is
already busy - most likely ScriptGen already running from an earlier
launch - this just opens a browser tab to it instead of trying (and
failing) to bind a second server on top.

Deliberately keeps its console window (see build_web.bat - no
--windowed flag) rather than running silently: closing the BROWSER TAB
does not stop the server (there's no window-close hook to catch, unlike
pywebview's), so something visible has to exist for "how do I stop
this" to have an answer - Ctrl+C in this window.

Run with:  python run_web.py
(or via the built ScriptGen-Web.exe, once packaged - see build_web.bat)
"""
from __future__ import annotations

import socket
import threading
import time
import webbrowser

HOST = "127.0.0.1"
PORT = 8420


def _port_is_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> None:
    import uvicorn
    from web.server import app

    url = f"http://{HOST}:{PORT}/"

    if _port_is_open(HOST, PORT):
        print(f"ScriptGen already appears to be running at {url} - opening it in your browser.")
        print(
            "NOTE: this is reattaching to whatever is ALREADY running on port "
            f"{PORT}, not starting a new server. If you just made code changes "
            "and they don't show up, that old process is the reason - it's "
            "still running the code it had loaded when IT started, no matter "
            "how many times you re-run this script.\n"
            "The app's login screen and sidebar show a \"Server started ...\" "
            "line - if that timestamp is old, this is what's happening.\n"
            "To fix it: close every ScriptGen/python console window (check "
            "the taskbar for ones you forgot about), or find and stop "
            f"whatever is holding port {PORT} with:\n"
            f"    netstat -ano | findstr {PORT}\n"
            "    taskkill /PID <the PID from that output> /F\n"
            "then run this again."
        )
        webbrowser.open(url)
        return

    print(f"Starting ScriptGen on {url} ...")
    server_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host=HOST, port=PORT, log_level="warning"),
        daemon=True,
    )
    server_thread.start()

    # The server needs a moment to actually bind the port before we try
    # to open it - a short poll loop instead of a fixed sleep, so this
    # doesn't race on a slow machine or stall on a fast one (same
    # pattern desktop_launcher.py uses before creating its webview window).
    deadline = time.time() + 10
    while time.time() < deadline and not _port_is_open(HOST, PORT):
        time.sleep(0.1)

    webbrowser.open(url)
    print(f"ScriptGen is running at {url}")
    print("Leave this window open while you use it. Press Ctrl+C here to stop the server.")

    try:
        server_thread.join()
    except KeyboardInterrupt:
        print("\nStopping ScriptGen...")


if __name__ == "__main__":
    main()
