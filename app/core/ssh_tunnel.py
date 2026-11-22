"""
Built-in SSH tunnel (RJ 2026-11-19: "can you do the ssh tunnel inside of you, add also the settings of ssh in
the settings").

Until now RJ opened the tunnel by hand (MobaXterm / PuTTY) so that localhost:1433 reached the SQL Server.
ScriptGen can now open it itself: a local port forward

    127.0.0.1:<local_port>  --ssh-->  <ssh_host>:<ssh_port>  --direct-tcpip-->  <remote_host>:<remote_port>

implemented with paramiko (pure Python + cryptography, installed by run_dev.bat from requirements.txt).
No external ssh.exe / plink needed. One tunnel per ScriptGen process (module-level manager):

  - start(cfg)  : connects (password or private key), opens the local listener, starts a keep-alive /
                  auto-reconnect watcher (reconnects when the SSH transport drops).
  - stop()      : closes listener, channels and the SSH connection.
  - status()    : dict for the UI (state, since, last_error, local/remote endpoints, open channels).
  - test(cfg)   : connects, checks that remote_host:remote_port can be opened through SSH, disconnects.

Secrets (SSH password, key passphrase) live encrypted in config.json (SshConfig in app/config.py).
"""
from __future__ import annotations

import select
import socket
import socketserver
import threading
import time
from datetime import datetime

_LOCK = threading.RLock()
_STATE: dict = {"state": "stopped", "since": None, "last_error": "", "channels": 0, "reconnects": 0}
_CLIENT = None          # paramiko.SSHClient
_SERVER = None          # _ForwardServer
_SERVER_THREAD = None
_WATCHER = None
_STOP = threading.Event()
_CFG = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _set(**kw) -> None:
    with _LOCK:
        _STATE.update(kw)


def _paramiko():
    try:
        import paramiko  # noqa: WPS433 - optional dependency, imported lazily
        return paramiko
    except ImportError as exc:   # pragma: no cover - only when requirements were not installed
        raise RuntimeError("The 'paramiko' package is missing - run run_dev.bat once to install it "
                           "(it is in requirements.txt).") from exc


def _load_key(paramiko, path: str, passphrase: str | None):
    errors = []
    for cls_name in ("RSAKey", "Ed25519Key", "ECDSAKey"):
        cls = getattr(paramiko, cls_name, None)
        if cls is None:
            continue
        try:
            return cls.from_private_key_file(path, password=passphrase or None)
        except Exception as exc:   # wrong key type or bad passphrase - try the next type
            errors.append(f"{cls_name}: {exc}")
    raise RuntimeError("Could not read the private key (OpenSSH / PEM format expected; PuTTY .ppk must be "
                       "exported to OpenSSH first). " + " | ".join(errors[-2:]))


def _connect(cfg):
    paramiko = _paramiko()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())   # internal jump host, same as Moba's first-connect accept
    kwargs = dict(hostname=cfg.ssh_host, port=int(cfg.ssh_port or 22), username=cfg.ssh_user,
                  timeout=15, banner_timeout=15, auth_timeout=20, allow_agent=False, look_for_keys=False)
    if cfg.auth == "key":
        if not cfg.key_path:
            raise RuntimeError("Private key file is empty - set it in Settings > SSH tunnel.")
        kwargs["pkey"] = _load_key(paramiko, cfg.key_path, cfg.get_key_passphrase())
    else:
        kwargs["password"] = cfg.get_password()
    client.connect(**kwargs)
    tr = client.get_transport()
    if tr is not None:
        tr.set_keepalive(int(cfg.keepalive_seconds or 30))
    return client


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        cfg = _CFG
        client = _CLIENT
        tr = client.get_transport() if client else None
        if tr is None or not tr.is_active():
            return
        try:
            chan = tr.open_channel("direct-tcpip", (cfg.remote_host, int(cfg.remote_port)), self.request.getpeername())
        except Exception as exc:
            _set(last_error=f"{_now()} cannot open {cfg.remote_host}:{cfg.remote_port} through SSH: {exc}")
            return
        if chan is None:
            return
        with _LOCK:
            _STATE["channels"] += 1
        try:
            while True:
                r, _, _ = select.select([self.request, chan], [], [], 1.0)
                if self.request in r:
                    data = self.request.recv(32768)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(32768)
                    if not data:
                        break
                    self.request.sendall(data)
                if _STOP.is_set():
                    break
        except Exception:
            pass
        finally:
            try:
                chan.close()
            except Exception:
                pass
            try:
                self.request.close()
            except Exception:
                pass
            with _LOCK:
                _STATE["channels"] = max(0, _STATE["channels"] - 1)


class _ForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False   # fail loudly if MobaXterm already listens on the port


def _port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _watch():
    """Keep-alive / auto-reconnect: if the SSH transport drops, reconnect with back-off."""
    global _CLIENT
    delay = 5
    while not _STOP.wait(5):
        client = _CLIENT
        tr = client.get_transport() if client else None
        if tr is not None and tr.is_active():
            delay = 5
            continue
        _set(state="reconnecting", last_error=f"{_now()} SSH connection lost - reconnecting")
        try:
            new = _connect(_CFG)
            with _LOCK:
                old, _CLIENT = _CLIENT, new
                _STATE["reconnects"] += 1
            try:
                old and old.close()
            except Exception:
                pass
            _set(state="connected", since=_now())
            delay = 5
        except Exception as exc:
            _set(state="reconnecting", last_error=f"{_now()} reconnect failed: {exc}")
            if _STOP.wait(delay):
                break
            delay = min(delay * 2, 60)


def start(cfg) -> dict:
    """Open the tunnel with the given SshConfig. Raises RuntimeError with a readable message."""
    global _CLIENT, _SERVER, _SERVER_THREAD, _WATCHER, _CFG
    with _LOCK:
        if _STATE["state"] in ("connected", "reconnecting", "connecting"):
            stop()   # restart, not a manual stop
        if not (cfg.ssh_host and cfg.ssh_user and cfg.remote_host):
            raise RuntimeError("SSH host, SSH user and DB host (as seen from the SSH server) are required.")
        lp = int(cfg.local_port or 1433)
        if not _port_free(lp):
            raise RuntimeError(f"Local port {lp} is already in use - another tunnel (MobaXterm / PuTTY) or SQL "
                               f"Server is listening on it. Close it, or pick another local port and point the "
                               f"DB connection to localhost:<that port>.")
        _set(state="connecting", last_error="", manual_stop=False, external=False, note="")
        _STOP.clear()
        _CFG = cfg
    try:   # outside the lock so status() keeps answering while the SSH login runs
        client = _connect(cfg)
    except Exception as exc:
        _set(state="error", last_error=f"{_now()} {exc}")
        raise RuntimeError(f"SSH connection failed: {exc}") from exc
    with _LOCK:
        _CLIENT = client
        try:
            _SERVER = _ForwardServer(("127.0.0.1", lp), _Handler)
        except OSError as exc:
            client.close(); _CLIENT = None
            _set(state="error", last_error=f"{_now()} cannot listen on 127.0.0.1:{lp}: {exc}")
            raise RuntimeError(f"Cannot listen on 127.0.0.1:{lp}: {exc}") from exc
        _SERVER_THREAD = threading.Thread(target=_SERVER.serve_forever, name="ssh-tunnel-listener", daemon=True)
        _SERVER_THREAD.start()
        _WATCHER = threading.Thread(target=_watch, name="ssh-tunnel-watch", daemon=True)
        _WATCHER.start()
        _set(state="connected", since=_now(), channels=0, reconnects=0)
        return status()


def port_open(port: int, timeout: float = 2.0) -> bool:
    """Is something already listening on 127.0.0.1:<port> (an external MobaXterm / PuTTY tunnel, or ours)?"""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _ours_up() -> bool:
    with _LOCK:
        client = _CLIENT
        return _SERVER is not None and client is not None and client.get_transport() is not None \
            and client.get_transport().is_active()


_SUPERVISOR = None


def supervise(get_cfg, probe_db, interval: int = 15) -> None:
    """RJ 2026-11-19: "there is a check first if tunnel is running already then if not it will do its own
    tunnel, same thing if it lost tunnel connection, it will trigger a retry on its own".
    One background loop (started once at ScriptGen startup):
      - our tunnel is up                      -> nothing to do (the watcher reconnects it if SSH drops)
      - local port already open (not ours)    -> external tunnel: probe the DB through it;
            OK  -> state "external" (use it, do nothing)
            KO  -> state "external_down" (port held by another program but the DB does not answer -
                   close it so ScriptGen can open its own; checked again next round)
      - local port closed                     -> open our own tunnel (back-off 15 s .. 60 s on failure)
    Stops trying while the setting is off or after the Stop button (manual_stop)."""
    global _SUPERVISOR
    if _SUPERVISOR is not None and _SUPERVISOR.is_alive():
        return

    def loop():
        delay = interval
        last_probe = 0.0
        while True:
            try:
                cfg = get_cfg()
                if not (cfg.enabled and cfg.auto_start) or _STATE.get("manual_stop"):
                    time.sleep(interval)
                    continue
                if _ours_up():
                    delay = interval
                elif port_open(cfg.local_port):
                    with _LOCK:
                        ours_listening = _SERVER is not None
                    if ours_listening:          # our listener is there but SSH is down -> watcher is on it
                        pass
                    elif _STATE.get("state") == "external" and time.time() - last_probe < 60:
                        pass                    # healthy external tunnel - re-check the DB once a minute only
                    else:
                        last_probe = time.time()
                        ok, msg = probe_db()
                        if ok:
                            _set(state="external", since=_STATE.get("since") if _STATE.get("state") == "external" else _now(),
                                 last_error="", external=True,
                                 note=f"External tunnel already open on 127.0.0.1:{cfg.local_port} - using it.")
                        else:
                            _set(state="external_down", external=True,
                                 last_error=f"{_now()} port {cfg.local_port} is held by another program (MobaXterm / PuTTY?) "
                                            f"but the database does not answer through it: {msg}. Close that tunnel so "
                                            f"ScriptGen can open its own.")
                    delay = interval
                else:
                    _set(external=False, note="")
                    try:
                        start(cfg)
                        delay = interval
                    except Exception as exc:  # noqa: BLE001
                        note_retry(f"own tunnel failed, next try in {delay}s: {exc}")
                        time.sleep(delay)
                        delay = min(delay * 2, 60)
                        continue
            except Exception as exc:  # noqa: BLE001 - never let the supervisor die
                _set(last_error=f"{_now()} supervisor: {exc}")
            time.sleep(interval)

    _SUPERVISOR = threading.Thread(target=loop, name="ssh-tunnel-supervisor", daemon=True)
    _SUPERVISOR.start()


def note_retry(msg: str) -> None:
    """Startup auto-retry (web/server.py) - shows the pending retry in Settings > SSH tunnel."""
    _set(state="retrying", last_error=f"{_now()} {msg}")


def stop(manual: bool = False) -> dict:
    """manual=True (Stop button) also cancels the startup auto-retry loop."""
    global _CLIENT, _SERVER
    with _LOCK:
        _STATE["manual_stop"] = manual
        _STOP.set()
        if _SERVER is not None:
            try:
                _SERVER.shutdown()
                _SERVER.server_close()
            except Exception:
                pass
            _SERVER = None
        if _CLIENT is not None:
            try:
                _CLIENT.close()
            except Exception:
                pass
            _CLIENT = None
        _set(state="stopped", since=_now(), channels=0)
        return status()


def status() -> dict:
    with _LOCK:
        cfg = _CFG
        out = dict(_STATE)
        if cfg is not None:
            out.update(local=f"127.0.0.1:{cfg.local_port}", ssh=f"{cfg.ssh_user}@{cfg.ssh_host}:{cfg.ssh_port}",
                       remote=f"{cfg.remote_host}:{cfg.remote_port}")
        return out


def test(cfg) -> dict:
    """Connect, check the DB endpoint is reachable THROUGH ssh, disconnect. Does not touch a running tunnel."""
    t0 = time.time()
    client = _connect(cfg)
    try:
        tr = client.get_transport()
        chan = tr.open_channel("direct-tcpip", (cfg.remote_host, int(cfg.remote_port)), ("127.0.0.1", 0), timeout=10)
        chan.close()
        return {"ok": True, "ms": int((time.time() - t0) * 1000),
                "message": f"SSH login OK and {cfg.remote_host}:{cfg.remote_port} is reachable through {cfg.ssh_host}."}
    finally:
        client.close()
