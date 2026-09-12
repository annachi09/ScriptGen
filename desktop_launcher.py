"""
Desktop entry point for the web UI: starts the exact same FastAPI app
(web/server.py) in a background thread, then opens it in a native
window via pywebview - no browser chrome, no visible URL bar, looks and
launches like any other desktop app. This is what build.bat packages
into ScriptGen.exe going forward (see that file) - the SAME code that
runs as a real website (`uvicorn web.server:app`) also runs as the
desktop app; only the launch shell differs.

pywebview picks whatever native web-rendering engine the OS already has
- on Windows that's the WebView2 runtime, which ships with Windows 10/11
and current Edge installs, so this doesn't bundle or install a browser
of its own. If WebView2 isn't present on an older machine, pywebview
will say so; see https://developer.microsoft.com/microsoft-edge/webview2/
for the (small, standalone) runtime installer.

Run with:  python desktop_launcher.py
(or via the built ScriptGen.exe, once packaged - see build.bat)
"""
from __future__ import annotations

import socket
import threading
import time

HOST = "127.0.0.1"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _run_server(port: int) -> None:
    import uvicorn
    from web.server import app

    # log_level="warning": this window has no console the user is
    # watching - uvicorn's per-request access log would just be noise
    # (or worse, the first thing a curious user sees if one ever does
    # open a console next to it). Errors still surface normally.
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


def main() -> None:
    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "pywebview isn't installed. Run `pip install -r requirements.txt` first "
            "(see requirements.txt for why this is the desktop shell around web/server.py)."
        ) from exc

    port = _find_free_port()
    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()

    # The server thread needs a moment to actually bind the port before
    # pywebview tries to load it - a short poll loop instead of a fixed
    # sleep, so this doesn't race on a slow machine or stall on a fast one.
    url = f"http://{HOST}:{port}/"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=0.25):
                break
        except OSError:
            time.sleep(0.1)

    webview.create_window("ScriptGen", url, width=1280, height=820, min_size=(960, 640))
    webview.start()


if __name__ == "__main__":
    main()
