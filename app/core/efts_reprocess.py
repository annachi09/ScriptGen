"""
Reprocess EFTS (RJ, 2026-11-23).

RJ: "create a new temp menu, reprocess efts ... here we need to execute in an ssh. and the ssh is the tunnel
itself ... then can be 1 or many id_efts i need to see the result also". His query (kept as given, two fixes
below) turns a row of EWA_GCCB_EFTS_INPUT into a curl call that re-posts the message to the EFTS service:

    SELECT 'curl --location ''http://localhost:7005/ewaouc-ss-server/services/efts/messageReceived''
            --header ''Content-Type: application/json'' --data ''{"message":"' + <base64 of message>
            + '","receiver":"EWABBHBMXXXX","sender":"BMAGBHBMAEFT","messageType":"996","priority": "N",
            "reference":"' + msg_ref + '"}'''
    FROM (select reference as msg_ref, CAST(message as VARBINARY(1000)) as bin
          from ewa_gccb_efts_input e where e.id_efts_input = 18988289) x

The service listens on localhost:7005 OF THE SSH SERVER (RJ confirmed: the curl works from the MobaXterm SSH
session, i.e. on the same jump host as the DB tunnel, Settings > SSH tunnel). ScriptGen sends the same POST
through that SSH login (app/core/ssh_tunnel.http_post) - no curl / shell needed - and falls back to running the
exact curl on the SSH server only if the SSH server refuses the port forward.

Two fixes to the query, same rows and same payload otherwise:
  1. VARBINARY(1000) -> VARBINARY(MAX): a message over 1,000 bytes was silently cut.
  2. .value(..., 'varchar(1000)') -> 'varchar(max)': base64 is 4/3 the size, so 1,000 bytes become 1,336
     characters - anything over ~750 bytes was cut and a broken payload would have been re-sent.
  DATALENGTH(message bytes) is returned so the page shows the size.
The JSON body is built in Python (json.dumps) from the same fields instead of string concatenation, so a quote
in the reference can't break it; the curl text is still shown per row exactly in RJ's format.
receiver / sender / messageType / priority default to RJ's values and are editable on the page ("maybe can be
more but format is like that").

Pure logic: builds SQL text, payloads and curl text only. Admin-only menu (it re-sends real bank messages).
"""
from __future__ import annotations

import base64
import json
import re
import shlex

SERVICE_HOST = "localhost"          # as seen FROM the SSH server
SERVICE_PORT = 7005
SERVICE_PATH = "/ewaouc-ss-server/services/efts/messageReceived"
SERVICE_URL = f"http://{SERVICE_HOST}:{SERVICE_PORT}{SERVICE_PATH}"

DEFAULT_RECEIVER = "EWABBHBMXXXX"
DEFAULT_SENDER = "BMAGBHBMAEFT"
DEFAULT_MESSAGE_TYPE = "996"
DEFAULT_PRIORITY = "N"

MAX_IDS = 500            # per Find
MAX_SEND = 200           # per Reprocess click
PREVIEW_BYTES = 160

_FIELD_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")


def parse_ids(text: str) -> list[int]:
    """'18988289, 18988290\\n18988291' -> [18988289, 18988290, 18988291] (digits only, de-duplicated)."""
    out: list[int] = []
    for tok in re.split(r"[\s,;]+", str(text or "")):
        tok = tok.strip()
        if tok.isdigit():
            v = int(tok)
            if v not in out:
                out.append(v)
    return out


def check_field(name: str, value: str) -> str:
    """receiver / sender / messageType / priority: letters, digits, _ . - only (they go into JSON and curl)."""
    v = str(value or "").strip()
    if not _FIELD_RE.match(v):
        raise ValueError(f"{name} must be 1-40 letters, digits, '_', '.' or '-' (got '{value}').")
    return v


def build_query(ids: list[int]) -> str:
    """RJ's query (2026-11-23) for many ids, with VARBINARY(MAX) / varchar(max) (see module docstring)."""
    if not ids:
        raise ValueError("Enter at least one ID_EFTS_INPUT.")
    id_list = ", ".join(str(int(i)) for i in ids)
    return f"""
SELECT x.id_efts_input,
       x.msg_ref,
       DATALENGTH(x.bin) AS message_bytes,
       CAST('' AS XML).value('xs:base64Binary(xs:hexBinary(sql:column("bin")))', 'varchar(max)') AS message_b64
FROM (SELECT e.id_efts_input,
             e.reference AS msg_ref,
             CAST(e.message AS VARBINARY(MAX)) AS bin
      FROM ewa_gccb_efts_input e
      WHERE e.id_efts_input IN ({id_list})) x
ORDER BY x.id_efts_input
"""


def build_body(row: dict, receiver: str, sender: str, message_type: str, priority: str) -> dict:
    """Same JSON as RJ's curl --data, key order kept."""
    return {
        "message": str(row.get("message_b64") or ""),
        "receiver": receiver,
        "sender": sender,
        "messageType": message_type,
        "priority": priority,
        "reference": str(row.get("msg_ref") or ""),
    }


def body_json(body: dict) -> str:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def curl_text(body: dict) -> str:
    """The curl RJ runs by hand, for the page and for the SSH fallback (single-quoted for bash)."""
    return (f"curl --location {shlex.quote(SERVICE_URL)} --header 'Content-Type: application/json' "
            f"--data {shlex.quote(body_json(body))}")


def curl_exec_command(body: dict, timeout_s: int = 60) -> str:
    """SSH fallback: RJ's curl + the HTTP status on the last line (parsed by ssh_tunnel.exec_curl)."""
    return (f"curl -sS -m {int(timeout_s)} -w '\\n__SG_HTTP_STATUS__:%{{http_code}}' --location "
            f"{shlex.quote(SERVICE_URL)} --header 'Content-Type: application/json' "
            f"--data {shlex.quote(body_json(body))}")


def preview(message_b64: str) -> str:
    """First bytes of the decoded message, printable only - so the row can be recognised on the page."""
    try:
        raw = base64.b64decode(message_b64 or "", validate=False)[:PREVIEW_BYTES]
    except Exception:  # noqa: BLE001
        return ""
    txt = raw.decode("utf-8", errors="replace")
    return "".join(ch if ch.isprintable() else " " for ch in txt).strip()


def shape_rows(rows: list[dict], receiver: str, sender: str, message_type: str, priority: str) -> list[dict]:
    out = []
    for r in rows:
        body = build_body(r, receiver, sender, message_type, priority)
        b64 = body["message"]
        out.append({
            "id_efts_input": str(r.get("id_efts_input")),
            "msg_ref": body["reference"],
            "message_bytes": int(r.get("message_bytes") or 0) if str(r.get("message_bytes") or "").isdigit() else r.get("message_bytes"),
            "b64_chars": len(b64),
            "empty": not b64,
            "preview": preview(b64),
            "curl": curl_text(body),
        })
    return out
