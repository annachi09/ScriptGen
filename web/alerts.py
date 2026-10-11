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
               recipients: list[str] | None = None, html: str | None = None) -> None:
    """attachments: (filename, data, mime 'type/subtype'). html: optional
    rich version (plain text stays as the fallback part). Raises on failure."""
    if not cfg.is_configured():
        raise RuntimeError("E-mail isn't configured - set the SMTP server in Settings > Email alerts.")
    to = [r.strip() for r in (recipients or cfg.recipients) if r and r.strip()]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.from_addr or cfg.username
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
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
    attachments = [(f"unusual_high_sanitary_{today}.csv", csv_bytes, "text/csv")]
    # RJ 2026-10-09: "when you send the email, add the script" - the rebilling
    # script for every detected bill (default user/program/creation user,
    # batch on), re-checked live like the page's Generate. Attached as .sql
    # and pasted at the end of the body. A failure here never blocks the mail.
    try:
        ids = sorted({str(r.get("id_bill")) for r in rows if str(r.get("id_bill") or "").isdigit()})
        ctx = mssql.run_query(conn, unusual_sanitary.build_rebilling_context_query(ids))
        ctx_rows = [{c.lower(): v for c, v in zip(ctx.columns, r)} for r in ctx.rows]
        script = unusual_sanitary.build_rebilling_script(ctx_rows)
        attachments.append((f"wrong_sanitary_high_rebilling_{today}.sql", script["sql_text"].encode("utf-8"), "text/plain"))
        if script.get("rollback_sql"):
            attachments.append((f"wrong_sanitary_high_rebilling_{today}_ROLLBACK.sql",
                                script["rollback_sql"].encode("utf-8"), "text/plain"))
        body += (f"\n\n==== REBILLING SCRIPT ({script['rebilling_count']} activit"
                 f"{'y' if script['rebilling_count'] == 1 else 'ies'}, {script['bill_count']} bill row(s)) - "
                 f"also attached as .sql. Review before running. ====\n\n" + script["sql_text"])
    except Exception as exc:  # noqa: BLE001
        body += f"\n\n(Rebilling script could not be generated: {exc} - use ScriptGen > Wrong Bill > Case 1 > Generate Rebilling Script.)"
    try:
        send_email(cfg.email, subject, body, attachments=attachments)
    except Exception as exc:  # noqa: BLE001
        log_attempt(db, key, today, "error", len(rows), f"E-mail failed: {exc}")
        return {"status": "error", "row_count": len(rows), "detail": f"E-mail failed: {exc}"}
    log_attempt(db, key, today, "sent", len(rows), "Sent to " + ", ".join(cfg.email.recipients))
    return {"status": "sent", "row_count": len(rows)}


# ----------------------------------------------------------- daily digest
# RJ 2026-10-09 (picked from the suggested features): one morning e-mail with
# every menu's count - only sections that have cases. The counters are the
# Overview cards' own functions, handed over by web/server.py at startup
# (register_digest_counters) because server.py imports this module.
DIGEST_KEY = "daily_digest"
_digest_counters: dict = {}   # key -> (label, fn(conn, user) -> int)


def register_digest_counters(counters: dict) -> None:
    _digest_counters.clear()
    _digest_counters.update(counters)


def _done_today(db: str, key: str, run_date: str) -> bool:
    """Digest: one run per day - 'sent' or 'no_cases' both count as done."""
    with _connect(db) as c:
        row = c.execute(f"SELECT 1 FROM {_TABLE} WHERE alert_key=? AND run_date=? AND status IN ('sent','no_cases') LIMIT 1",
                        (key, run_date)).fetchone()
    return row is not None


def build_digest(results: list[tuple[str, Optional[int], str]], run_date: str) -> tuple[str, str]:
    """results: (label, count or None, error). Returns (subject, body)."""
    hits = [(l, n) for l, n, e in results if n]
    errs = [(l, e) for l, n, e in results if n is None and e]
    total = sum(n for _, n in hits)
    subject = f"[ScriptGen] Daily digest {run_date}: {total} case(s) in {len(hits)} area(s)"
    lines = ["Hello RJ,", "", f"ScriptGen morning digest for {run_date}. Only areas with cases are listed.", ""]
    if hits:
        w = max(len(l) for l, _ in hits)
        lines += [f"  {l.ljust(w)}  {n:>6,}" for l, n in sorted(hits, key=lambda x: -x[1])]
    else:
        lines.append("  Nothing to review today.")
    if errs:
        lines += ["", "Could not be counted (check the tunnel / the page):"] + [f"  - {l}: {e[:150]}" for l, e in errs]
    lines += ["", "Open ScriptGen > Overview for details.", "", "-- Sent automatically by ScriptGen"]
    return subject, "\n".join(lines)


# RJ 2026-10-09: "email is so bad, for the daily digest, send something similar
# as our dashboard with colors and formatting". Group / icon / colour per
# counter key, same palette as the Overview cards. Email-safe HTML: tables +
# inline styles only (Outlook ignores <style>, flex and grid).
DIGEST_META = {
    "unusualsanitary": ("Wrong Bill", "🚨", "#ef4444"),
    "wrongbill_case2": ("Wrong Bill", "📊", "#ef4444"),
    "wrongbill_case3": ("Wrong Bill", "🚿", "#ef4444"),
    "wrongbill_case4": ("Wrong Bill", "🧾", "#ef4444"),
    "doubleitb": ("Billing fixes", "🔁", "#f97316"),
    "dateanomaly": ("Billing fixes", "📅", "#f97316"),
    "incorrectbillingperiod": ("Billing fixes", "📆", "#f97316"),
    "rebillingmonitor": ("Billing fixes", "♻️", "#0ea5e9"),
    "pendingsr": ("Service Requests", "🎫", "#ec4899"),
    "billissuance_case1": ("Bill Issuance Validator", "🧮", "#6366f1"),
    "billissuance_case2": ("Bill Issuance Validator", "🧮", "#6366f1"),
    "billissuance_case3": ("Bill Issuance Validator", "🧮", "#6366f1"),
    "billissuance_case4": ("Bill Issuance Validator", "🧮", "#6366f1"),
    "hierarchy": ("Hierarchy & readings", "🌳", "#8b5cf6"),
    "wrongstuckhierarchy": ("Hierarchy & readings", "🪜", "#8b5cf6"),
    "wrongstuckhierarchy_sanitary": ("Hierarchy & readings", "🚿", "#8b5cf6"),
    "tnbcycledisc": ("TNB / Disconnection", "📍", "#14b8a6"),
    "disconnectiontnb": ("TNB / Disconnection", "⛔", "#14b8a6"),
}
_GROUP_ORDER = ["Wrong Bill", "Service Requests", "Billing fixes", "Bill Issuance Validator", "Hierarchy & readings", "TNB / Disconnection", "Other"]


def _esc(s) -> str:
    import html as _h
    return _h.escape(str(s if s is not None else ""))


def build_digest_html(results: list[tuple], run_date: str, app_url: str = "http://127.0.0.1:8420/") -> str:
    """results: (key, label, count or None, error)."""
    hits = [r for r in results if r[2]]
    clear = [r for r in results if r[2] == 0]
    errs = [r for r in results if r[2] is None and r[3]]
    total = sum(r[2] for r in hits)
    top = max([r[2] for r in hits] or [1])
    font = "font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif;"

    def kpi(value, label, color):
        return (f'<td width="33%" style="padding:6px;"><table width="100%" cellpadding="0" cellspacing="0" '
                f'style="background:#ffffff;border:1px solid #e5e7eb;border-left:5px solid {color};border-radius:10px;">'
                f'<tr><td style="padding:14px 16px;{font}"><div style="font-size:28px;font-weight:800;color:{color};line-height:1;">{value}</div>'
                f'<div style="font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#64748b;margin-top:6px;">{label}</div>'
                f'</td></tr></table></td>')

    def row(key, label, n):
        group, icon, color = DIGEST_META.get(key, ("Other", "•", "#64748b"))
        pct = max(4, round(100 * n / top))
        return (f'<tr><td style="padding:5px 0;"><table width="100%" cellpadding="0" cellspacing="0" '
                f'style="background:#ffffff;border:1px solid #e5e7eb;border-left:5px solid {color};border-radius:8px;">'
                f'<tr><td width="36" style="padding:10px 0 10px 12px;font-size:20px;">{icon}</td>'
                f'<td style="padding:10px 8px;{font}"><div style="font-size:14px;font-weight:600;color:#0f172a;">{_esc(label)}</div>'
                f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px;"><tr>'
                f'<td width="{pct}%" style="background:{color};height:6px;border-radius:3px;font-size:0;line-height:0;">&nbsp;</td>'
                f'<td style="background:#f1f5f9;height:6px;font-size:0;line-height:0;">&nbsp;</td></tr></table></td>'
                f'<td width="90" align="right" style="padding:10px 16px;{font}font-size:24px;font-weight:800;color:{color};">{n:,}</td>'
                f'</tr></table></td></tr>')

    groups: dict = {}
    for key, label, n, _ in hits:
        groups.setdefault(DIGEST_META.get(key, ("Other",))[0], []).append((key, label, n))
    sections = ""
    for g in _GROUP_ORDER:
        items = sorted(groups.get(g, []), key=lambda x: -x[2])
        if not items:
            continue
        gsum = sum(i[2] for i in items)
        sections += (f'<tr><td style="padding:18px 0 4px;{font}font-size:12px;font-weight:800;letter-spacing:.08em;'
                     f'text-transform:uppercase;color:#475569;">{_esc(g)} <span style="color:#94a3b8;font-weight:600;">· {gsum:,}</span></td></tr>'
                     + "".join(row(*i) for i in items))
    if not hits:
        sections = (f'<tr><td style="padding:24px;{font}font-size:16px;color:#047857;background:#ecfdf5;border-radius:10px;'
                    f'text-align:center;font-weight:600;">✅ Nothing to review today.</td></tr>')
    clear_html = ""
    if clear:
        chips = " ".join(f'<span style="display:inline-block;margin:3px 4px 3px 0;padding:3px 10px;border-radius:999px;'
                         f'background:#ecfdf5;color:#047857;font-size:12px;">✓ {_esc(l)}</span>' for _, l, _, _ in clear)
        clear_html = (f'<tr><td style="padding:18px 0 4px;{font}font-size:12px;font-weight:800;letter-spacing:.08em;'
                      f'text-transform:uppercase;color:#475569;">All clear</td></tr><tr><td style="{font}">{chips}</td></tr>')
    err_html = ""
    if errs:
        lis = "".join(f'<li style="margin:2px 0;"><b>{_esc(l)}</b>: {_esc(str(e)[:160])}</li>' for _, l, _, e in errs)
        err_html = (f'<tr><td style="padding-top:16px;"><table width="100%" cellpadding="0" cellspacing="0" '
                    f'style="background:#fffbeb;border:1px solid #fcd34d;border-radius:10px;"><tr><td style="padding:12px 16px;{font}'
                    f'font-size:13px;color:#92400e;"><b>⚠ Could not be counted</b> (tunnel down or query timeout)<ul style="margin:6px 0 0 18px;padding:0;">{lis}</ul>'
                    f'</td></tr></table></td></tr>')
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f1f5f9;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;"><tr><td align="center" style="padding:24px 12px;">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">
  <tr><td style="background:#0f172a;background-image:linear-gradient(135deg,#1e1b4b 0%,#0f172a 55%,#0c4a6e 100%);border-radius:14px 14px 0 0;padding:26px 28px;{font}">
    <div style="font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#a5b4fc;font-weight:700;">ScriptGen · Daily digest</div>
    <div style="font-size:26px;font-weight:800;color:#ffffff;margin-top:6px;">{total:,} case(s) to review</div>
    <div style="font-size:13px;color:#cbd5e1;margin-top:4px;">{_esc(run_date)} · {len(hits)} area(s) with cases · {len(clear)} all clear</div>
  </td></tr>
  <tr><td style="background:#f8fafc;padding:14px 22px 22px;border:1px solid #e2e8f0;border-top:0;border-radius:0 0 14px 14px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      {kpi(f"{total:,}", "Total cases", "#ef4444" if total else "#10b981")}
      {kpi(len(hits), "Areas with cases", "#f59e0b" if hits else "#10b981")}
      {kpi(len(clear), "Areas all clear", "#10b981")}
    </tr></table>
    <table width="100%" cellpadding="0" cellspacing="0">{sections}{clear_html}{err_html}
      <tr><td align="center" style="padding:24px 0 6px;"><a href="{_esc(app_url)}" style="display:inline-block;background:#6366f1;color:#ffffff;
        text-decoration:none;font-weight:700;padding:11px 26px;border-radius:999px;{font}font-size:14px;">Open ScriptGen Overview →</a></td></tr>
    </table>
  </td></tr>
  <tr><td align="center" style="padding:14px;{font}font-size:11px;color:#94a3b8;">Sent automatically by ScriptGen · counts from the Overview cards · read-only checks, nothing was changed</td></tr>
</table></td></tr></table></body></html>"""


def run_daily_digest(force: bool = False) -> dict:
    cfg = load_config()
    today = datetime.date.today().isoformat()
    db = cfg.internal_db_path
    if not force and _done_today(db, DIGEST_KEY, today):
        return {"status": "already_sent", "run_date": today}
    conn = cfg.get_active_connection()
    if not conn:
        log_attempt(db, DIGEST_KEY, today, "error", None, "No database connection configured.")
        return {"status": "error", "detail": "No database connection configured."}
    if not _digest_counters:
        return {"status": "error", "detail": "Digest counters not registered (server not started?)."}
    keyed = []   # (key, label, count or None, error)
    for key, (label, fn) in _digest_counters.items():
        try:
            keyed.append((key, label, int(fn(conn, "digest") or 0), ""))
        except Exception as exc:  # noqa: BLE001 - one failing area must not stop the digest
            keyed.append((key, label, None, str(getattr(exc, "detail", exc))))
    results = [(l, n, e) for _, l, n, e in keyed]
    hits = sum(1 for _, n, _ in results if n)
    if not hits and not force:
        log_attempt(db, DIGEST_KEY, today, "no_cases", 0, "Nothing to report.")
        return {"status": "no_cases", "results": results}
    subject, body = build_digest(results, today)
    html = build_digest_html(keyed, today)
    try:
        send_email(cfg.email, subject, body, html=html)
    except Exception as exc:  # noqa: BLE001
        log_attempt(db, DIGEST_KEY, today, "error", hits, f"E-mail failed: {exc}")
        return {"status": "error", "detail": f"E-mail failed: {exc}", "results": results}
    log_attempt(db, DIGEST_KEY, today, "sent", hits, "Sent to " + ", ".join(cfg.email.recipients))
    return {"status": "sent", "areas": hits, "results": results}


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
    retry = datetime.timedelta(minutes=max(1, int(em.retry_minutes)))
    # daily digest (RJ 2026-10-09): once a day; errors retried on the same cadence
    if not _done_today(db, DIGEST_KEY, today):
        last_d = last_attempt_at(db, DIGEST_KEY, today)
        if not last_d or (now - last_d) >= retry:
            run_daily_digest()
    key = unusual_sanitary.ALERT_KEY
    if sent_today(db, key, today):
        return
    last = last_attempt_at(db, key, today)
    if last and (now - last) < retry:
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
