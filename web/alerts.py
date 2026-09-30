"""
Daily e-mail alerts (RJ, 2026-09-30).

Wrong Bill CASE 1 - Unusual high Sanitary: "when it is detected i want you
to send email starting 7 am until it is sent to: dev.rj.magdurulan@gmail.com;
rjmagdurulan@indracompany.com attaching the detected case telling me to
review and apply rebilling as early as possible". Chosen behaviour (RJ's
answers): SMTP settings in the app, ONCE PER DAY.

A background thread (started with the server) wakes every minute. From
EmailConfig.send_hour (7:00 local) it runs the detection every
EmailConfig.retry_minutes (15) until that day's e-mail has been SENT
successfully; after that it is quiet until the next day's 7:00. A check
that finds no cases does not count as "sent" - it keeps checking every 15
minutes that day, so a case that appears at 10:00 is still mailed that day.
Failures (tunnel down, SMTP error) are logged and retried on the same
cadence. Every attempt is written to a small table in the internal SQLite
DB (_scriptgen_alert_log) so the page can show the last status.

The alert only runs while the ScriptGen server is running on this machine.
"""
from __future__ import annotations

import datetime
import smtplib
import sqlite3
import threading
import time
from email.message import EmailMessage
from typing import Optional

from app.config import EmailConfig, load_config
from app.core import diff_engine, unusual_sanitary
from app.db import mssql

_TABLE = "_scriptgen_alert_log"
_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key   TEXT NOT NULL,
    run_date    TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status      TEXT NOT NULL,   -- sent | no_cases | error | skipped
    row_count   INTEGER,
    detail      TEXT
)"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_SQL)
    conn.commit()
    return conn


def log_attempt(db_path: str, alert_key: str, run_date: str, status: str,
                row_count: Optional[int] = None, detail: str = "") -> None:
    with _connect(db_path) as c:
        c.execute(
            f"INSERT INTO {_TABLE} (alert_key, run_date, attempted_at, status, row_count, detail) VALUES (?,?,?,?,?,?)",
            (alert_key, run_date, datetime.datetime.now().isoformat(timespec="seconds"), status, row_count, detail[:2000]),
        )


def sent_today(db_path: str, alert_key: str, run_date: str) -> bool:
    with _connect(db_path) as c:
        row = c.execute(
            f"SELECT 1 FROM {_TABLE} WHERE alert_key = ? AND run_date = ? AND status = 'sent' LIMIT 1",
            (alert_key, run_date),
        ).fetchone()
    return row is not None


def last_attempt_at(db_path: str, alert_key: str, run_date: str) -> Optional[datetime.datetime]:
    with _connect(db_path) as c:
        row = c.execute(
            f"SELECT attempted_at FROM {_TABLE} WHERE alert_key = ? AND run_date = ? ORDER BY id DESC LIMIT 1",
            (alert_key, run_date),
        ).fetchone()
    return datetime.datetime.fromisoformat(row[0]) if row else None


def recent_log(db_path: str, alert_key: str, limit: int = 15) -> list[dict]:
    with _connect(db_path) as c:
        rows = c.execute(
            f"SELECT run_date, attempted_at, status, row_count, detail FROM {_TABLE} "
            f"WHERE alert_key = ? ORDER BY id DESC LIMIT ?", (alert_key, int(limit)),
        ).fetchall()
    return [dict(zip(("run_date", "attempted_at", "status", "row_count", "detail"), r)) for r in rows]


# ---------------------------------------------------------------- sending
def send_email(cfg: EmailConfig, subject: str, body: str,
               attachments: list[tuple[str, bytes, str]] | None = None,
               recipients: list[str] | None = None) -> None:
    """attachments: (filename, data, mime 'type/subtype'). Raises on failure."""
    if not cfg.is_configured():
        raise RuntimeError("E-mail isn't configured - set the SMTP server in Settings > Email alerts.")
    to = [r.strip() for r in (recipients or cfg.recipients) if r and r.strip()]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.from_addr or cfg.username
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    for name, data, mime in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    port = int(cfg.smtp_port or 587)
    if port == 465:
        server = smtplib.SMTP_SSL(cfg.smtp_host, port, timeout=30)
    else:
        server = smtplib.SMTP(cfg.smtp_host, port, timeout=30)
    try:
        server.ehlo()
        if cfg.use_tls and port != 465:
            server.starttls()
            server.ehlo()
        pw = cfg.get_password()
        if cfg.username and pw:
            server.login(cfg.username, pw)
        server.send_message(msg, to_addrs=to)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------- the one alert
def detect_unusual_sanitary(conn) -> list[dict]:
    res = mssql.run_query(conn, unusual_sanitary.build_query())
    return [{c.lower(): diff_engine.cell_display(v) for c, v in zip(res.columns, r)} for r in res.rows]


def run_unusual_sanitary(force: bool = False) -> dict:
    """One check. force=True ignores the time window / already-sent guard
    (the page's "Send now" button). Returns a status dict."""
    cfg = load_config()
    key = unusual_sanitary.ALERT_KEY
    today = datetime.date.today().isoformat()
    db = cfg.internal_db_path
    if not force and sent_today(db, key, today):
        return {"status": "already_sent", "run_date": today}
    conn = cfg.get_active_connection()
    if not conn:
        log_attempt(db, key, today, "error", None, "No database connection configured.")
        return {"status": "error", "detail": "No database connection configured."}
    try:
        rows = detect_unusual_sanitary(conn)
    except Exception as exc:  # noqa: BLE001
        log_attempt(db, key, today, "error", None, f"Query failed: {exc}")
        return {"status": "error", "detail": f"Query failed: {exc}"}
    if not rows:
        log_attempt(db, key, today, "no_cases", 0, "No unusual high Sanitary bills detected.")
        return {"status": "no_cases", "row_count": 0}
    subject, body = unusual_sanitary.build_email(rows, today)
    csv_bytes = unusual_sanitary.rows_to_csv(rows).encode("utf-8-sig")
    try:
        send_email(cfg.email, subject, body,
                   attachments=[(f"unusual_high_sanitary_{today}.csv", csv_bytes, "text/csv")])
    except Exception as exc:  # noqa: BLE001
        log_attempt(db, key, today, "error", len(rows), f"E-mail failed: {exc}")
        return {"status": "error", "row_count": len(rows), "detail": f"E-mail failed: {exc}"}
    log_attempt(db, key, today, "sent", len(rows), "Sent to " + ", ".join(cfg.email.recipients))
    return {"status": "sent", "row_count": len(rows)}


# -------------------------------------------------------------- scheduler
_started = False
_lock = threading.Lock()


def _tick() -> None:
    cfg = load_config()
    em = cfg.email
    if not em.alerts_enabled:
        return
    now = datetime.datetime.now()
    if now.hour < int(em.send_hour):
        return
    today = now.date().isoformat()
    db = cfg.internal_db_path
    key = unusual_sanitary.ALERT_KEY
    if sent_today(db, key, today):
        return
    last = last_attempt_at(db, key, today)
    if last and (now - last) < datetime.timedelta(minutes=max(1, int(em.retry_minutes))):
        return
    run_unusual_sanitary()


def _loop() -> None:
    while True:
        try:
            _tick()
        except Exception:  # noqa: BLE001 - never let the scheduler die
            pass
        time.sleep(60)


def start_scheduler() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="scriptgen-alerts").start()
