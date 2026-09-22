"""All SQLite access for the Telegram CRM bots.

Every function opens a fresh connection to crm.db and closes it before
returning, so the admin bot and the CRM bot (separate processes) can share
the file safely.
"""

import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "crm.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS allowed_chats (
  chat_id   INTEGER PRIMARY KEY,
  title     TEXT DEFAULT '',
  added_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leads (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  data             TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'new',
  chat_id          INTEGER,
  message_id       INTEGER,
  claimed_by       INTEGER,
  claimed_by_name  TEXT,
  claimed_at       TEXT,
  outcome          TEXT,
  outcome_at       TEXT
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_lead(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    lead = dict(row)
    lead["data"] = json.loads(lead["data"])
    return lead


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# allowlist

def add_chat(chat_id: int, title: str = "") -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO allowed_chats (chat_id, title) VALUES (?, ?)",
            (chat_id, title),
        )
        conn.commit()
    finally:
        conn.close()


def remove_chat(chat_id: int) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM allowed_chats WHERE chat_id=?", (chat_id,))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def list_chats() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT chat_id, title FROM allowed_chats ORDER BY added_at, chat_id"
        ).fetchall()
        return [{"chat_id": r["chat_id"], "title": r["title"] or ""} for r in rows]
    finally:
        conn.close()


def is_allowed(chat_id: int) -> bool:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM allowed_chats WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# leads

def add_leads(rows: list[dict]) -> int:
    conn = _connect()
    try:
        conn.executemany(
            "INSERT INTO leads (data, status) VALUES (?, 'new')",
            [(json.dumps(dict(row), ensure_ascii=False),) for row in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def next_new_lead() -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM leads WHERE status='new' ORDER BY id LIMIT 1"
        ).fetchone()
        return _row_to_lead(row)
    finally:
        conn.close()


def mark_posted(lead_id: int, chat_id: int, message_id: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE leads SET status='posted', chat_id=?, message_id=? WHERE id=?",
            (chat_id, message_id, lead_id),
        )
        conn.commit()
    finally:
        conn.close()


def claim_lead(lead_id: int, user_id: int, user_name: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE leads SET status='claimed', claimed_by=?, claimed_by_name=?, "
            "claimed_at=? WHERE id=? AND status='posted'",
            (user_id, user_name, _now(), lead_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def set_outcome(lead_id: int, user_id: int, outcome: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE leads SET outcome=?, outcome_at=? "
            "WHERE id=? AND status='claimed' AND claimed_by=?",
            (outcome, _now(), lead_id, user_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def release_lead(lead_id: int) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE leads SET status='posted', claimed_by=NULL, claimed_by_name=NULL, "
            "claimed_at=NULL, outcome=NULL, outcome_at=NULL "
            "WHERE id=? AND status='claimed'",
            (lead_id,),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def get_lead(lead_id: int) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return _row_to_lead(row)
    finally:
        conn.close()


def leads_for_user(user_id: int) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM leads WHERE claimed_by=? ORDER BY id DESC", (user_id,)
        ).fetchall()
        return [_row_to_lead(r) for r in rows]
    finally:
        conn.close()


def stats() -> dict:
    conn = _connect()
    try:
        result = {
            "new": 0,
            "posted": 0,
            "claimed": 0,
            "no_response": 0,
            "hung_up": 0,
            "on_phone": 0,
        }
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM leads GROUP BY status"
        ):
            if row["status"] in ("new", "posted", "claimed"):
                result[row["status"]] = row["n"]
        for row in conn.execute(
            "SELECT outcome, COUNT(*) AS n FROM leads "
            "WHERE outcome IS NOT NULL GROUP BY outcome"
        ):
            if row["outcome"] in ("no_response", "hung_up", "on_phone"):
                result[row["outcome"]] = row["n"]
        return result
    finally:
        conn.close()


init_db()
