"""All database access for the Telegram CRM, on Turso (hosted SQLite) via libsql-client.

Plain SQL strings, one function per operation. A module-level client is created
at import and init_db() runs every CREATE TABLE IF NOT EXISTS at import.

Timestamps are UTC text "YYYY-MM-DD HH:MM:SS" computed in Python.
A lead dict is one leads row (every column) with `data` parsed from JSON plus
`source` (the source name). An attempt dict is one attempts row (every column).
"""

import atexit
import json
import os
import re
import threading
from datetime import datetime, timezone

import libsql_client
from dotenv import load_dotenv

load_dotenv()
TURSO_DATABASE_URL = os.environ["TURSO_DATABASE_URL"]
TURSO_AUTH_TOKEN = os.environ["TURSO_AUTH_TOKEN"]

# Turso shows the database URL as libsql://..., but libsql-client turns that
# scheme into a hrana WebSocket connection, and Turso's edge answers the wss
# handshake with HTTP 400. The https:// form of the same host speaks the HTTP
# API and works, so accept whichever one is pasted into .env and send that.
if TURSO_DATABASE_URL.startswith("libsql://"):
    TURSO_DATABASE_URL = "https://" + TURSO_DATABASE_URL[len("libsql://"):]

OUTCOME_KEYS = ("no_response", "hung_up", "on_phone")

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS allowed_chats (
         chat_id  INTEGER PRIMARY KEY,
         title    TEXT DEFAULT '',
         added_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS sources (
         id         INTEGER PRIMARY KEY AUTOINCREMENT,
         name       TEXT UNIQUE NOT NULL,
         created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS leads (
         id         INTEGER PRIMARY KEY AUTOINCREMENT,
         phone      TEXT NOT NULL,
         data       TEXT NOT NULL,
         source_id  INTEGER NOT NULL REFERENCES sources(id),
         status     TEXT NOT NULL DEFAULT 'new',
         chat_id    INTEGER,
         message_id INTEGER,
         created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS attempts (
         id           INTEGER PRIMARY KEY AUTOINCREMENT,
         lead_id      INTEGER NOT NULL REFERENCES leads(id),
         phone        TEXT NOT NULL,
         caller_id    INTEGER NOT NULL,
         caller_name  TEXT,
         claimed_at   TEXT,
         answered_at  TEXT,
         ended_at     TEXT,
         talk_seconds INTEGER,
         outcome      TEXT,
         notes        TEXT)""",
    """CREATE TABLE IF NOT EXISTS do_not_even_try (
         phone           TEXT PRIMARY KEY,
         flagged_by      INTEGER,
         flagged_by_name TEXT,
         flagged_at      TEXT DEFAULT CURRENT_TIMESTAMP,
         confirmed       INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status, id)",
    "CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads (phone)",
    "CREATE INDEX IF NOT EXISTS idx_attempts_phone ON attempts (phone)",
    "CREATE INDEX IF NOT EXISTS idx_attempts_caller ON attempts (caller_id, ended_at)",
]

_LEAD_SELECT = (
    "SELECT leads.*, sources.name AS source FROM leads "
    "JOIN sources ON sources.id = leads.source_id "
)


# --- client -----------------------------------------------------------------


def _create_client():
    # libsql-client rejects/ignores auth tokens for local file: URLs, so only
    # send one to a remote database.
    if TURSO_DATABASE_URL.startswith("file:") or not TURSO_AUTH_TOKEN:
        return libsql_client.create_client_sync(url=TURSO_DATABASE_URL)
    return libsql_client.create_client_sync(
        url=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN
    )


client = _create_client()
_client_pid = os.getpid()


def _c():
    """The module-level client, recreated after a fork.

    The sync client runs its statements on a private background thread, which a
    multiprocessing fork does not carry over, so a child process that inherits
    the parent's client would hang forever on its first call.
    """
    global client, _client_pid
    if os.getpid() != _client_pid:
        client = _create_client()
        _client_pid = os.getpid()
    return client


def _close_client_at_exit() -> None:
    # The client's background thread is not a daemon, so the interpreter would
    # wait on it forever at exit unless the client is closed first. Python joins
    # threads before atexit handlers run, hence threading's own hook when present.
    if os.getpid() != _client_pid:
        return  # a forked child that never used the inherited (dead) client
    try:
        client.close()
    except Exception:
        pass


(getattr(threading, "_register_atexit", None) or atexit.register)(_close_client_at_exit)


def _q(sql: str, args=()):
    return _c().execute(sql, list(args))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _rows(rs) -> list[dict]:
    return [dict(zip(rs.columns, row)) for row in rs.rows]


def _one(rs) -> dict | None:
    rows = _rows(rs)
    return rows[0] if rows else None


def _lead(row: dict | None) -> dict | None:
    if row is None:
        return None
    try:
        row["data"] = json.loads(row["data"]) if row.get("data") else {}
    except (TypeError, ValueError):
        row["data"] = {}
    return row


# Columns added after v2 shipped. SQLite has no "ADD COLUMN IF NOT EXISTS", and
# init_db runs at import in each of run.py's three processes, so the existing
# columns are read first and a lost race is swallowed.
_ADDED_COLUMNS = {
    "sources": (
        ("country", "TEXT DEFAULT ''"),
        ("timezone", "TEXT DEFAULT ''"),
    ),
}


def _add_missing_columns() -> None:
    for table, columns in _ADDED_COLUMNS.items():
        have = {str(r.get("name")) for r in _rows(_q(f"PRAGMA table_info({table})"))}
        for name, decl in columns:
            if name in have:
                continue
            try:
                _q(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            except Exception as exc:  # another process added it first
                if "duplicate column" not in str(exc).lower():
                    raise


def init_db() -> None:
    _c().batch(_SCHEMA)
    _add_missing_columns()


def normalize_phone(raw: str) -> str:
    """Digits only: '+447911 123456' -> '447911123456'."""
    return re.sub(r"\D", "", str(raw or ""))


# --- allowlist (unchanged from v1) -------------------------------------------


def add_chat(chat_id: int, title: str = "") -> None:
    _q(
        "INSERT OR REPLACE INTO allowed_chats (chat_id, title) VALUES (?, ?)",
        (chat_id, title or ""),
    )


def remove_chat(chat_id: int) -> bool:
    rs = _q("DELETE FROM allowed_chats WHERE chat_id=?", (chat_id,))
    return rs.rows_affected == 1


def list_chats() -> list[dict]:
    rs = _q("SELECT chat_id, title FROM allowed_chats ORDER BY added_at, chat_id")
    return [{"chat_id": r["chat_id"], "title": r["title"] or ""} for r in _rows(rs)]


def is_allowed(chat_id: int) -> bool:
    rs = _q("SELECT 1 FROM allowed_chats WHERE chat_id=?", (chat_id,))
    return len(rs.rows) > 0


# --- sources ------------------------------------------------------------------


def get_or_create_source(name: str, country: str = "", timezone: str = "") -> int:
    name = (name or "").strip() or "unknown"
    _q("INSERT OR IGNORE INTO sources (name) VALUES (?)", (name,))
    if country or timezone:
        # Re-uploading under the same source name updates what it was read as.
        _q(
            "UPDATE sources SET country=?, timezone=? WHERE name=?",
            ((country or "").upper(), timezone or "", name),
        )
    row = _one(_q("SELECT id FROM sources WHERE name=?", (name,)))
    return int(row["id"])


def list_sources() -> list[dict]:
    rs = _q(
        "SELECT s.id, s.name, s.created_at, s.country, s.timezone, "
        "(SELECT COUNT(*) FROM leads l WHERE l.source_id = s.id) AS leads "
        "FROM sources s ORDER BY s.name"
    )
    return _rows(rs)


# --- leads --------------------------------------------------------------------


def pick_phone_column(rows: list[dict], phone_column: str) -> str:
    headers: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in headers:
                headers.append(key)
    wanted = (phone_column or "").strip()
    if wanted:
        if wanted in headers:
            return wanted
        for h in headers:
            if h.strip().lower() == wanted.lower():
                return h
        raise ValueError(
            f"Phone column '{wanted}' not found. Columns are: {', '.join(headers)}"
        )
    for h in headers:
        if "phone" in h.lower():
            return h
    raise ValueError(
        "No phone column found. Name one in the upload, or use a header that "
        f"contains 'phone'. Columns are: {', '.join(headers)}"
    )


def add_leads(rows: list[dict], source_name: str, phone_column: str) -> dict:
    """Store rows as new leads under source_name.

    The lead key is normalize_phone(row[phone_column]); rows whose phone is in
    do_not_even_try are skipped and counted; rows with no phone digits are
    skipped silently. Returns {"added": n, "skipped_blocked": n}.
    """
    result = {"added": 0, "skipped_blocked": 0}
    rows = [dict(r) for r in rows if r]
    if not rows:
        return result
    column = pick_phone_column(rows, phone_column)
    source_id = get_or_create_source(source_name)
    blocked = {r["phone"] for r in _rows(_q("SELECT phone FROM do_not_even_try"))}

    statements = []
    for row in rows:
        phone = normalize_phone(row.get(column) or "")
        if not phone:
            continue
        if phone in blocked:
            result["skipped_blocked"] += 1
            continue
        statements.append(
            libsql_client.Statement(
                "INSERT INTO leads (phone, data, source_id, status) VALUES (?, ?, ?, 'new')",
                [phone, json.dumps(row, ensure_ascii=False), source_id],
            )
        )
    for i in range(0, len(statements), 200):
        _c().batch(statements[i : i + 200])
    result["added"] = len(statements)
    return result


def blocked_phones() -> set[str]:
    """Every do-not-even-try number, as lead keys, for checks before an upload."""
    return {r["phone"] for r in _rows(_q("SELECT phone FROM do_not_even_try"))}


def add_validated_leads(
    results: list[dict], source_name: str, country: str = "", timezone: str = ""
) -> dict:
    """Write the rows common.validate_rows marked valid; skip and count the rest.

    The stored key stays digits only, so a row uploaded as +61432841955 is keyed
    as 61432841955 and still matches the leads, flags and call history of the
    same number from an earlier upload in any other shape. Returns
    {"added": n, "skipped": {status: n}}.
    """
    skipped = {}
    for r in results:
        status = r.get("status")
        if status != "valid":
            skipped[status] = skipped.get(status, 0) + 1
    valid = [r for r in results if r.get("status") == "valid"]
    if not valid:
        return {"added": 0, "skipped": skipped}
    source_id = get_or_create_source(source_name, country, timezone)
    statements = []
    for r in valid:
        key = r.get("key") or normalize_phone(r.get("phone") or "")
        if not key:
            continue
        statements.append(
            libsql_client.Statement(
                "INSERT INTO leads (phone, data, source_id, status) VALUES (?, ?, ?, 'new')",
                [key, json.dumps(r.get("row") or {}, ensure_ascii=False), source_id],
            )
        )
    for i in range(0, len(statements), 200):
        _c().batch(statements[i : i + 200])
    return {"added": len(statements), "skipped": skipped}


def next_new_lead() -> dict | None:
    rs = _q(
        _LEAD_SELECT
        + "WHERE leads.status='new' "
        "AND leads.phone NOT IN (SELECT phone FROM do_not_even_try) "
        "ORDER BY leads.id LIMIT 1"
    )
    return _lead(_one(rs))


def mark_posted(lead_id: int, chat_id: int, message_id: int) -> None:
    _q(
        "UPDATE leads SET status='posted', chat_id=?, message_id=? WHERE id=?",
        (chat_id, message_id, lead_id),
    )


def get_lead(lead_id: int) -> dict | None:
    return _lead(_one(_q(_LEAD_SELECT + "WHERE leads.id=?", (lead_id,))))


def leads_for_phone(phone: str) -> list[dict]:
    """Every lead row ever stored for this phone, newest first."""
    rs = _q(_LEAD_SELECT + "WHERE leads.phone=? ORDER BY leads.id DESC", (normalize_phone(phone),))
    return [_lead(r) for r in _rows(rs)]


def history_for_phone(phone: str) -> list[dict]:
    """Past (ended) attempts on this phone, newest first; the open one is excluded."""
    rs = _q(
        "SELECT * FROM attempts WHERE phone=? AND ended_at IS NOT NULL "
        "ORDER BY ended_at DESC, id DESC",
        (normalize_phone(phone),),
    )
    return _rows(rs)


def attempts_for_phone(phone: str) -> list[dict]:
    """Every attempt on this phone, open ones included, newest first."""
    rs = _q(
        "SELECT * FROM attempts WHERE phone=? ORDER BY id DESC",
        (normalize_phone(phone),),
    )
    return _rows(rs)


# --- attempts (the call lifecycle) --------------------------------------------


def _get_attempt(attempt_id: int) -> dict | None:
    return _one(_q("SELECT * FROM attempts WHERE id=?", (attempt_id,)))


def claim_lead(lead_id: int, user_id: int, user_name: str) -> int | None:
    """Atomically move the lead posted->claimed; insert the attempt; return its id."""
    rs = _q(
        "UPDATE leads SET status='claimed' WHERE id=? AND status='posted'", (lead_id,)
    )
    if rs.rows_affected != 1:
        return None
    rs = _q(
        "INSERT INTO attempts (lead_id, phone, caller_id, caller_name, claimed_at) "
        "SELECT id, phone, ?, ?, ? FROM leads WHERE id=?",
        (user_id, user_name or "", _now(), lead_id),
    )
    return int(rs.last_insert_rowid) if rs.rows_affected == 1 else None


def mark_answered(attempt_id: int, user_id: int) -> bool:
    rs = _q(
        "UPDATE attempts SET answered_at=? "
        "WHERE id=? AND caller_id=? AND answered_at IS NULL AND ended_at IS NULL",
        (_now(), attempt_id, user_id),
    )
    return rs.rows_affected == 1


def end_attempt(attempt_id: int, user_id: int, outcome: str) -> dict | None:
    """Close the caller's own open attempt; talk_seconds = ended_at - answered_at."""
    if outcome not in OUTCOME_KEYS:
        raise ValueError(f"Unknown outcome '{outcome}'. Use one of {', '.join(OUTCOME_KEYS)}")
    now = _now()
    rs = _q(
        "UPDATE attempts SET ended_at=?, outcome=?, "
        "talk_seconds = CASE WHEN answered_at IS NULL THEN NULL "
        "ELSE CAST(strftime('%s', ?) AS INTEGER) - CAST(strftime('%s', answered_at) AS INTEGER) END "
        "WHERE id=? AND caller_id=? AND ended_at IS NULL",
        (now, outcome, now, attempt_id, user_id),
    )
    if rs.rows_affected != 1:
        return None
    _q(
        "UPDATE leads SET status='closed' "
        "WHERE id=(SELECT lead_id FROM attempts WHERE id=?) AND status='claimed'",
        (attempt_id,),
    )
    return _get_attempt(attempt_id)


def add_notes(attempt_id: int, user_id: int, notes: str) -> bool:
    rs = _q(
        "UPDATE attempts SET notes=? WHERE id=? AND caller_id=?",
        ((notes or "").strip(), attempt_id, user_id),
    )
    return rs.rows_affected == 1


def open_attempt_for_user(user_id: int) -> dict | None:
    return _one(
        _q(
            "SELECT * FROM attempts WHERE caller_id=? AND ended_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
    )


def attempt_needing_notes(user_id: int) -> dict | None:
    return _one(
        _q(
            "SELECT * FROM attempts WHERE caller_id=? AND ended_at IS NOT NULL "
            "AND talk_seconds > 300 AND (notes IS NULL OR notes='') "
            "ORDER BY ended_at DESC, id DESC LIMIT 1",
            (user_id,),
        )
    )


def attempts_for_user(user_id: int, limit: int = 20) -> list[dict]:
    """The caller's attempts, newest first (for /mine)."""
    rs = _q(
        "SELECT * FROM attempts WHERE caller_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )
    return _rows(rs)


def recent_attempts(limit: int = 200) -> list[dict]:
    """Newest attempts with the lead's source name attached (for the dashboard)."""
    rs = _q(
        "SELECT a.*, s.name AS source FROM attempts a "
        "JOIN leads l ON l.id = a.lead_id "
        "JOIN sources s ON s.id = l.source_id "
        "ORDER BY a.id DESC LIMIT ?",
        (limit,),
    )
    return _rows(rs)


def release_lead(lead_id: int) -> bool:
    """Un-claim a lead: end its open attempt with no outcome, set it back to posted."""
    rs = _q("UPDATE leads SET status='posted' WHERE id=? AND status='claimed'", (lead_id,))
    if rs.rows_affected != 1:
        return False
    _q(
        "UPDATE attempts SET ended_at=? WHERE lead_id=? AND ended_at IS NULL",
        (_now(), lead_id),
    )
    return True


# --- do not even try ------------------------------------------------------------


def flag_phone(phone: str, user_id: int, user_name: str) -> None:
    """Insert a pending flag; block that phone's open leads; end any open attempt on them."""
    phone = normalize_phone(phone)
    if not phone:
        return
    now = _now()
    _c().batch(
        [
            libsql_client.Statement(
                "INSERT OR IGNORE INTO do_not_even_try "
                "(phone, flagged_by, flagged_by_name, flagged_at) VALUES (?, ?, ?, ?)",
                [phone, user_id, user_name or "", now],
            ),
            libsql_client.Statement(
                "UPDATE attempts SET ended_at=? WHERE ended_at IS NULL AND lead_id IN "
                "(SELECT id FROM leads WHERE phone=? AND status IN ('new','posted','claimed'))",
                [now, phone],
            ),
            libsql_client.Statement(
                "UPDATE leads SET status='blocked' "
                "WHERE phone=? AND status IN ('new','posted','claimed')",
                [phone],
            ),
        ]
    )


def confirm_flag(phone: str) -> None:
    _q("UPDATE do_not_even_try SET confirmed=1 WHERE phone=?", (normalize_phone(phone),))


def unflag_phone(phone: str) -> None:
    phone = normalize_phone(phone)
    _c().batch(
        [
            libsql_client.Statement("DELETE FROM do_not_even_try WHERE phone=?", [phone]),
            libsql_client.Statement(
                "UPDATE leads SET status='new', chat_id=NULL, message_id=NULL "
                "WHERE phone=? AND status='blocked'",
                [phone],
            ),
        ]
    )


def list_flags(pending_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM do_not_even_try "
    if pending_only:
        sql += "WHERE confirmed=0 "
    sql += "ORDER BY confirmed ASC, flagged_at DESC, phone"
    return _rows(_q(sql))


# --- analytics ------------------------------------------------------------------


def _avg_int(value) -> int | None:
    return None if value is None else int(round(float(value)))


def stats_by_source() -> list[dict]:
    rs = _q(
        "SELECT s.id, s.name, "
        "COUNT(DISTINCT l.id) AS leads, "
        "COUNT(a.id) AS attempts, "
        "COALESCE(SUM(a.answered_at IS NOT NULL), 0) AS answered, "
        "COALESCE(SUM(a.outcome='no_response'), 0) AS no_response, "
        "COALESCE(SUM(a.outcome='hung_up'), 0) AS hung_up, "
        "COALESCE(SUM(a.outcome='on_phone'), 0) AS on_phone, "
        "AVG(CASE WHEN a.answered_at IS NOT NULL THEN a.talk_seconds END) AS avg_talk_seconds "
        "FROM sources s "
        "LEFT JOIN leads l ON l.source_id = s.id "
        "LEFT JOIN attempts a ON a.lead_id = l.id "
        "GROUP BY s.id ORDER BY s.name"
    )
    out = []
    for r in _rows(rs):
        r["avg_talk_seconds"] = _avg_int(r["avg_talk_seconds"])
        out.append(r)
    return out


def stats_by_caller() -> list[dict]:
    rs = _q(
        "SELECT a.caller_id, "
        "(SELECT a2.caller_name FROM attempts a2 WHERE a2.caller_id = a.caller_id "
        " ORDER BY a2.id DESC LIMIT 1) AS caller_name, "
        "COUNT(*) AS attempts, "
        "COALESCE(SUM(a.answered_at IS NOT NULL), 0) AS answered, "
        "COALESCE(SUM(a.outcome='no_response'), 0) AS no_response, "
        "COALESCE(SUM(a.outcome='hung_up'), 0) AS hung_up, "
        "COALESCE(SUM(a.outcome='on_phone'), 0) AS on_phone, "
        "AVG(CASE WHEN a.answered_at IS NOT NULL THEN a.talk_seconds END) AS avg_talk_seconds, "
        "COALESCE(SUM(a.notes IS NOT NULL AND a.notes <> ''), 0) AS notes_written "
        "FROM attempts a GROUP BY a.caller_id ORDER BY attempts DESC, a.caller_id"
    )
    out = []
    for r in _rows(rs):
        r["avg_talk_seconds"] = _avg_int(r["avg_talk_seconds"])
        out.append(r)
    return out


def stats() -> dict:
    """Queue counts from leads.status plus outcome counts from attempts.outcome (admin bot fallback)."""
    result = {
        "new": 0,
        "posted": 0,
        "claimed": 0,
        "closed": 0,
        "blocked": 0,
        "no_response": 0,
        "hung_up": 0,
        "on_phone": 0,
    }
    for r in _rows(_q("SELECT status, COUNT(*) AS n FROM leads GROUP BY status")):
        if r["status"] in ("new", "posted", "claimed", "closed", "blocked"):
            result[r["status"]] = r["n"]
    for r in _rows(
        _q("SELECT outcome, COUNT(*) AS n FROM attempts WHERE outcome IS NOT NULL GROUP BY outcome")
    ):
        if r["outcome"] in OUTCOME_KEYS:
            result[r["outcome"]] = r["n"]
    return result


init_db()
