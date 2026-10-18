"""Config, CSV parsing, outcome labels, lead-card formatting, keyboards, post_next_lead.

Shared by admin_bot.py, crm_bot.py and dashboard.py. Plain text only.
"""

import csv
import io
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

import db

load_dotenv()
ADMIN_BOT_TOKEN = os.environ["ADMIN_BOT_TOKEN"]
CRM_BOT_TOKEN = os.environ["CRM_BOT_TOKEN"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
TURSO_DATABASE_URL = os.environ["TURSO_DATABASE_URL"]
TURSO_AUTH_TOKEN = os.environ["TURSO_AUTH_TOKEN"]
DASHBOARD_KEY = os.environ["DASHBOARD_KEY"]

OUTCOMES = {  # callback key -> button label
    "no_response": "No R",
    "hung_up": "Hung up",
    "on_phone": "On the phone",
}


# --- CSV parsing (moved from v1 admin_bot.py, behaviour unchanged) -------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DATE_RE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}")
_PHONE_HEADER_WORDS = ("phone", "number", "mobile", "tel", "cell", "contact")


def _digits_and_plus(value: str) -> str:
    """Keep a leading + and digits only. Also unwraps Excel's 4.48E+11 form."""
    value = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?[eE]\+?\d+", value):
        try:
            value = str(int(float(value)))
        except (ValueError, OverflowError):
            pass
    plus = value.startswith("+")
    digits = re.sub(r"\D", "", value)
    return ("+" if plus else "") + digits


def _looks_like_phone(value: str) -> bool:
    if _DATE_RE.match(value.strip()) or _EMAIL_RE.match(value.strip()):
        return False
    d = _digits_and_plus(value)
    return 8 <= len(d.lstrip("+")) <= 15 and d.lstrip("+").isdigit()


def _normalize_phone(value: str) -> str:
    """Return the number in international form with a leading +.

    44xxxxxxxxxx -> +44xxxxxxxxxx, 0044... -> +44..., 07xxx -> +447xxx.
    Anything else that already has + is kept; other shapes are left as digits.
    """
    d = _digits_and_plus(value)
    if not d or not _looks_like_phone(d):
        return value.strip()
    if d.startswith("+"):
        return d
    if d.startswith("00"):
        return "+" + d[2:]
    if d.startswith("44"):
        return "+" + d
    if d.startswith("0") and len(d) in (10, 11):
        return "+44" + d[1:]
    return d


def _first_row_is_header(cells: list[str]) -> bool:
    """A row holding an email, a phone number or a date is data, not a header."""
    for cell in cells:
        cell = cell.strip()
        if _EMAIL_RE.match(cell) or _DATE_RE.match(cell) or _looks_like_phone(cell):
            return False
    return True


def _infer_headers(cells: list[str]) -> list[str]:
    """Name columns from the shape of the first data row."""
    names, seen = [], {}
    for i, cell in enumerate(cells, start=1):
        cell = cell.strip()
        if _EMAIL_RE.match(cell):
            name = "Email"
        elif _looks_like_phone(cell):
            name = "Phone"
        elif _DATE_RE.match(cell):
            name = "Date"
        elif re.fullmatch(r"[A-Z]{2}", cell):
            name = "Region"
        else:
            name = f"Column {i}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name} {seen[name]}")
    return names


def _clean_row(row: dict, fieldnames: list[str]) -> dict:
    clean = {}
    for key, value in row.items():
        if key is None:
            # Values past the last header: keep them as Column N.
            for i, extra in enumerate(value or [], start=len(fieldnames) + 1):
                extra = (extra or "").strip()
                if extra:
                    clean[f"Column {i}"] = extra
            continue
        key = str(key).strip()
        value = (value or "").strip()
        if value and _looks_like_phone(value) and (
            any(w in key.lower() for w in _PHONE_HEADER_WORDS) or key.startswith("Phone")
            or not any(ch.isalpha() for ch in value)
        ):
            value = _normalize_phone(value)
        clean[key] = value
    return clean


def parse_csv(text: str) -> list[dict]:
    """CSV text -> list of cleaned row dicts.

    Detects whether the first row is a header (a row holding an email, phone or
    date is data, and the headers are then inferred from its shape), normalises
    phone values to +44 form, keeps values past the last header as "Column N",
    and drops blank rows. Bytes are accepted and decoded as utf-8-sig/latin-1.
    """
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8-sig")
        except UnicodeDecodeError:
            text = bytes(text).decode("latin-1")
    text = (text or "").lstrip("﻿")
    first = next(csv.reader(io.StringIO(text)), None)
    if first is None:
        rows = []
    elif _first_row_is_header(first):
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = list(reader.fieldnames or [])
        rows = [_clean_row(r, fieldnames) for r in reader]
    else:
        # No header row: name the columns from the data and keep the first row.
        fieldnames = _infer_headers(first)
        reader = csv.DictReader(io.StringIO(text), fieldnames=fieldnames)
        rows = [_clean_row(r, fieldnames) for r in reader]
    return [r for r in rows if any(r.values())]


# --- formatting -----------------------------------------------------------------


def fmt_talk(seconds: int | None) -> str:
    """Seconds -> 'm:ss', or 'not answered' when None."""
    if seconds is None:
        return "not answered"
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _local_hhmm(utc_text: str) -> str:
    """'YYYY-MM-DD HH:MM:SS' (UTC) -> 'HH:MM' in the server's local time."""
    try:
        dt = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        return str(utc_text)[11:16]


def _history_line(past: dict) -> str:
    date = str(past.get("ended_at") or past.get("claimed_at") or "")[:10]
    caller = past.get("caller_name") or str(past.get("caller_id") or "")
    outcome = past.get("outcome")
    label = OUTCOMES.get(outcome, outcome) if outcome else "no outcome"
    talk = fmt_talk(past.get("talk_seconds"))
    notes = " ".join(str(past.get("notes") or "").split())[:80]
    return f"{date}  {caller}  {label}  {talk}  {notes}".rstrip()


def format_lead(lead: dict, attempt: dict | None = None, history: list[dict] | None = None) -> str:
    """The lead card.

    Lead #<id>  [source: <name>], the CSV fields one per line, a 'Seen before:'
    block when history is non-empty, then the attempt's state: 'Claimed by <name>',
    'On the line since <HH:MM>' while answered, or 'Outcome: <label>  Talk: <m:ss>'
    once ended ('Outcome: <label>  not answered' when talk_seconds is NULL).
    """
    source = lead.get("source") or ""
    head = f"Lead #{lead['id']}"
    if source:
        head += f"  [source: {source}]"
    lines = [head]
    for header, value in (lead.get("data") or {}).items():
        if value is None:
            continue
        value = str(value).strip()
        if not value:
            continue
        lines.append(f"{header}: {value}")

    if history:
        lines.append("")
        lines.append("Seen before:")
        for past in history:
            lines.append(_history_line(past))

    if attempt:
        lines.append("")
        lines.append(f"Claimed by {attempt.get('caller_name') or attempt.get('caller_id')}")
        if attempt.get("ended_at"):
            outcome = attempt.get("outcome")
            label = OUTCOMES.get(outcome, outcome) if outcome else "no outcome"
            if attempt.get("talk_seconds") is None:
                lines.append(f"Outcome: {label}  not answered")
            else:
                lines.append(f"Outcome: {label}  Talk: {fmt_talk(attempt['talk_seconds'])}")
        elif attempt.get("answered_at"):
            lines.append(f"On the line since {_local_hhmm(attempt['answered_at'])}")
    return "\n".join(lines)


# --- keyboards ------------------------------------------------------------------


def posted_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("I'm calling it", callback_data=f"claim:{lead_id}"),
                InlineKeyboardButton("Do not even try", callback_data=f"flag:{lead_id}"),
            ]
        ]
    )


def claimed_keyboard(attempt_id: int, lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Answered", callback_data=f"answered:{attempt_id}"),
                InlineKeyboardButton(
                    OUTCOMES["no_response"], callback_data=f"outcome:{attempt_id}:no_response"
                ),
                InlineKeyboardButton("Do not even try", callback_data=f"flag:{lead_id}"),
            ]
        ]
    )


def answered_keyboard(attempt_id: int) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(OUTCOMES[key], callback_data=f"outcome:{attempt_id}:{key}")
        for key in ("hung_up", "no_response", "on_phone")
    ]
    return InlineKeyboardMarkup([row])


# --- posting --------------------------------------------------------------------


async def post_next_lead(chat_id: int) -> bool:
    """Post the next new lead (with its history) to chat_id; False when none is left."""
    lead = db.next_new_lead()
    if lead is None:
        return False
    history = db.history_for_phone(lead["phone"])
    sent = await Bot(CRM_BOT_TOKEN).send_message(
        chat_id,
        format_lead(lead, None, history),
        reply_markup=posted_keyboard(lead["id"]),
    )
    db.mark_posted(lead["id"], chat_id, sent.message_id)
    return True
