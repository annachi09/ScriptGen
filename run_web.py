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

RJ, 2026-09-14: "i want to share my local to my teammate so we can work
together in the scriptgen, we are on the same network." BIND_HOST below
is 0.0.0.0 (listen on every network interface, not just loopback) so a
teammate on the same LAN can reach this same server at this machine's
own LAN IP - CHECK_HOST/the auto-opened browser tab still use 127.0.0.1
so this machine's own "is it already running" check and the tab that
pops up on launch are unaffected. Auth is unchanged - a teammate still
needs their own ScriptGen login (Users admin panel -> add one) and never
sees the SQL Server credentials, since every query runs server-side on
THIS machine. See README's "Sharing on your local network" section for
the Windows Firewall step this usually needs the first time.

Run with:  python run_web.py
(or via the built ScriptGen-Web.exe, once packaged - see build_web.bat)
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

BIND_HOST = "0.0.0.0"   # listen on every interface - allows LAN teammates in
CHECK_HOST = "127.0.0.1"  # this machine's own loopback - for the busy-port check + auto-opened tab
PORT = 8420


def _port_is_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _lan_ip() -> str | None:
    """
    Best-effort LAN IP for this machine, so main() can print a URL a
    teammate on the same network can actually use (CHECK_HOST/127.0.0.1
    only ever means "this same machine" to them). Uses the "UDP connect
    to a public IP, read back the local address the OS would have used"
    trick - no packet is actually sent (UDP connect() just resolves
    routing), so this works even offline, and it's more reliable than
    socket.gethostbyname(gethostname()) on machines with multiple
    adapters (VPN, Wi-Fi + Ethernet, etc). Returns None (never raises) if
    there's no route at all - main() just skips the LAN line in that case.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def _running_scriptgen_pid() -> int | None:
    """PID of the ScriptGen server already on PORT, read from its own /api/server-info
    (so we only ever stop ScriptGen itself, never some other program on that port)."""
    try:
        with urllib.request.urlopen(f"http://{CHECK_HOST}:{PORT}/api/server-info", timeout=3) as resp:
            pid = json.loads(resp.read().decode("utf-8")).get("pid")
            return int(pid) if pid else None
    except Exception:
        return None


def _stop_old_server() -> bool:
    """RJ 2026-10-23: "why we cannot auto close the pid now?" - restart automatically:
    stop the previous ScriptGen server (old code) and wait for the port to free up.
    Returns True when the port is free afterwards."""
    pid = _running_scriptgen_pid()
    if not pid or pid == os.getpid():
        return False
    print(f"An older ScriptGen server (PID {pid}) is running on port {PORT} - stopping it to load the latest code...")
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
        else:
            os.kill(pid, 9)
    except Exception as exc:
        print(f"Could not stop PID {pid}: {exc}")
        return False
    deadline = time.time() + 10
    while time.time() < deadline and _port_is_open(CHECK_HOST, PORT):
        time.sleep(0.2)
    return not _port_is_open(CHECK_HOST, PORT)


def main() -> None:
    import uvicorn
    from web.server import app

    url = f"http://{CHECK_HOST}:{PORT}/"
    lan_ip = _lan_ip()
    lan_url = f"http://{lan_ip}:{PORT}/" if lan_ip else None

    # RJ 2026-10-23: default = restart (stop the old ScriptGen, start with the new code).
    # `python run_web.py --attach` keeps the old behaviour (just open the running one).
    if _port_is_open(CHECK_HOST, PORT) and "--attach" not in sys.argv:
        if _stop_old_server():
            print("Old server stopped.")

    if _port_is_open(CHECK_HOST, PORT):
        print(f"ScriptGen already appears to be running at {url} - opening it in your browser.")
        if lan_url:
            print(f"A teammate on the same network can also reach it at: {lan_url}")
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
        target=lambda: uvicorn.run(app, host=BIND_HOST, port=PORT, log_level="warning"),
        daemon=True,
    )
    server_thread.start()

    # The server needs a moment to actually bind the port before we try
    # to open it - a short poll loop instead of a fixed sleep, so this
    # doesn't race on a slow machine or stall on a fast one (same
    # pattern desktop_launcher.py uses before creating its webview window).
    deadline = time.time() + 10
    while time.time() < deadline and not _port_is_open(CHECK_HOST, PORT):
        time.sleep(0.1)

    webbrowser.open(url)
    print(f"ScriptGen is running at {url}")
    if lan_url:
        print(f"A teammate on the same network can also reach it at: {lan_url}")
        print(
            "(First time only: Windows may prompt \"Windows Defender Firewall has "
            "blocked some features of this app\" - click Allow access, at least for "
            "Private networks. If nothing prompts and a teammate still can't connect, "
            "see README's \"Sharing on your local network\" section for the exact "
            "firewall-rule command.)"
        )
    else:
        print("Couldn't detect a LAN IP to share with a teammate - check your network connection.")
    print("Leave this window open while you use it. Press Ctrl+C here to stop the server.")

    try:
        server_thread.join()
    except KeyboardInterrupt:
        print("\nStopping ScriptGen...")


if __name__ == "__main__":
    main()
