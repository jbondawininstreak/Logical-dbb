"""Google Voice texts, in and out, through a mautrix-gvoice bridge on Matrix.

run.py starts main() as a fourth process when MATRIX_HOMESERVER is set. It logs
in to the homeserver as the CRM's Matrix user, follows /sync, joins the rooms
the bridge invites it to, and records every text as a row in `texts`. A text
from a number nobody has uploaded becomes a new lead under the `google-voice`
source, and inbound texts are posted to the allowed group chats so callers see
them. The dashboard's reply box goes out through send_text() below.

The bridge and homeserver themselves are set up by deploy/gvoice/setup-gvoice.sh.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from telegram import Bot

import common
import db

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
)
log = logging.getLogger("gvoice")

SYNC_TIMEOUT_MS = 30000
SYNC_FILTER = json.dumps(
    {"room": {"timeline": {"limit": 100}}, "presence": {"types": []}},
    separators=(",", ":"),
)

# Settings keys shared with the dashboard through db.settings.
KEY_TOKEN = "matrix.access_token"
KEY_USER_ID = "matrix.user_id"
KEY_SINCE = "matrix.since"
KEY_LAST_SYNC = "matrix.last_sync"
KEY_LAST_NOTICE = "gvoice.last_notice"
KEY_LAST_ERROR = "gvoice.last_error"

_PHONE_TAIL = re.compile(r"\+?(\d{6,15})$")


def configured() -> bool:
    return bool(common.MATRIX_HOMESERVER and common.MATRIX_USER and common.MATRIX_PASSWORD)


# --- reading Matrix IDs ------------------------------------------------------


def localpart(mxid: str) -> str:
    mxid = str(mxid or "")
    if not mxid.startswith("@"):
        return ""
    return mxid[1:].split(":", 1)[0]


def phone_from_mxid(mxid: str, prefix: str | None = None) -> str:
    """'@gvoice_abc.+15551234567:host' -> '+15551234567'; '' when not a ghost.

    The bridge builds a ghost's localpart from its username template
    (`gvoice_{{.}}`) and an internal ID of `<login prefix>.<E.164>`, so the
    number is whatever follows the last dot. A template without the prefix
    still works, as the number is read from the tail.
    """
    prefix = common.GVOICE_GHOST_PREFIX if prefix is None else prefix
    local = localpart(mxid)
    if not local or (prefix and not local.startswith(prefix)):
        return ""
    rest = local[len(prefix):] if prefix else local
    tail = rest.rsplit(".", 1)[-1]
    match = _PHONE_TAIL.search(tail)
    if not match:
        return ""
    return "+" + match.group(1)


def phone_from_channel_id(channel_id: str) -> str:
    """A DM portal's bridge-info channel ID is the thread ID, 't.+15551234567'."""
    channel_id = str(channel_id or "")
    if not channel_id.startswith("t."):
        return ""
    match = _PHONE_TAIL.search(channel_id[2:])
    return "+" + match.group(1) if match and channel_id[2:].lstrip("+").isdigit() else ""


def is_bridge_bot(mxid: str, bot: str | None = None) -> bool:
    bot = common.GVOICE_BOT if bot is None else bot
    return localpart(mxid) == bot


def _ts_text(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError):
        return None


def message_text(content: dict) -> tuple[str, str]:
    """(body, msgtype) for an m.room.message; media gets a short tag."""
    content = content or {}
    msgtype = str(content.get("msgtype") or "m.text")
    body = str(content.get("body") or "")
    if msgtype in ("m.image", "m.video", "m.audio", "m.file"):
        tag = msgtype.split(".", 1)[1]
        body = f"[{tag}] {body}".strip()
    return body, msgtype


# --- one /sync page -----------------------------------------------------------


@dataclass
class Announcement:
    phone: str
    lead_id: int | None
    body: str
    name: str = ""


@dataclass
class SyncOutcome:
    joins: list[str] = field(default_factory=list)
    recorded: int = 0
    new_leads: list[int] = field(default_factory=list)
    announcements: list[Announcement] = field(default_factory=list)


def handle_sync(payload: dict, me: str, announce: bool = True) -> SyncOutcome:
    """Apply one /sync response to the database; nothing here touches the network.

    Invites become joins for the caller to perform. In joined rooms, member and
    bridge-info events map the room to a phone number, messages from ghosts are
    inbound texts, messages from `me` are outbound, and the bridge bot's notices
    are kept so the dashboard can show what the bridge last said.
    """
    out = SyncOutcome()
    rooms = (payload or {}).get("rooms") or {}
    for room_id in (rooms.get("invite") or {}):
        out.joins.append(room_id)
    for room_id, room in (rooms.get("join") or {}).items():
        events = list(((room.get("state") or {}).get("events") or []))
        events += list(((room.get("timeline") or {}).get("events") or []))
        _handle_room(room_id, events, me, announce, out)
    return out


def _handle_room(room_id: str, events: list[dict], me: str, announce: bool, out: SyncOutcome) -> None:
    name = db.text_room_name(room_id)
    for ev in events:
        etype = ev.get("type")
        content = ev.get("content") or {}
        if etype == "m.room.name":
            name = str(content.get("name") or name)
            phone = db.phone_for_room(room_id)
            if phone:
                db.map_text_room(room_id, phone, name)
        elif etype == "m.room.member":
            ghost = phone_from_mxid(ev.get("state_key") or "")
            if ghost and content.get("membership") in ("join", "invite"):
                display = str(content.get("displayname") or "")
                if not name and display:
                    name = display
                if not db.phone_for_room(room_id):
                    db.map_text_room(room_id, ghost, name)
        elif etype in ("m.bridge", "uk.half-shot.bridge"):
            channel = content.get("channel") or {}
            phone = phone_from_channel_id(channel.get("id"))
            if phone:
                name = str(channel.get("displayname") or name)
                db.map_text_room(room_id, phone, name)
        elif etype == "m.room.message":
            _handle_message(room_id, ev, me, announce, out)


def _handle_message(room_id: str, ev: dict, me: str, announce: bool, out: SyncOutcome) -> None:
    sender = str(ev.get("sender") or "")
    event_id = str(ev.get("event_id") or "")
    body, msgtype = message_text(ev.get("content"))
    sent_at = _ts_text(ev.get("origin_server_ts"))

    if is_bridge_bot(sender):
        if msgtype != "m.notice" and not body:
            return
        db.setting_set(KEY_LAST_NOTICE, body)
        phone = db.phone_for_room(room_id)
        if phone and db.record_text(event_id, room_id, phone, "bridge", sender, body, msgtype, sent_at):
            out.recorded += 1
        return

    ghost_phone = phone_from_mxid(sender)
    if ghost_phone:
        if not db.phone_for_room(room_id):
            db.map_text_room(room_id, ghost_phone, "")
        name = db.text_room_name(room_id)
        lead = db.latest_lead_for_phone(ghost_phone)
        lead_id = lead["id"] if lead else None
        is_new_event = db.record_text(
            event_id, room_id, ghost_phone, "in", sender, body, msgtype, sent_at, lead_id
        )
        if not is_new_event:
            return
        out.recorded += 1
        if lead_id is None:
            lead_id = db.add_text_lead(ghost_phone, name, body, common.GVOICE_SOURCE)
            if lead_id is not None:
                out.new_leads.append(lead_id)
                db._q("UPDATE texts SET lead_id=? WHERE event_id=?", (lead_id, event_id))
        if announce:
            out.announcements.append(Announcement(ghost_phone, lead_id, body, name))
        return

    if sender == me:
        phone = db.phone_for_room(room_id)
        if not phone:
            return
        lead = db.latest_lead_for_phone(phone)
        if db.record_text(event_id, room_id, phone, "out", sender, body, msgtype, sent_at,
                          lead["id"] if lead else None):
            out.recorded += 1


# --- the homeserver ---------------------------------------------------------------


class MatrixError(RuntimeError):
    pass


class Matrix:
    """The few client-server calls the intake and the dashboard need."""

    def __init__(self, homeserver: str | None = None, token: str | None = None, user_id: str = ""):
        self.homeserver = (homeserver or common.MATRIX_HOMESERVER).rstrip("/")
        self.token = token or ""
        self.user_id = user_id
        self.http = httpx.Client(base_url=self.homeserver, timeout=httpx.Timeout(60.0, read=90.0))

    @classmethod
    def from_settings(cls) -> "Matrix":
        token = db.setting_get(KEY_TOKEN) or ""
        if not token:
            raise MatrixError("The Google Voice intake has not logged in to Matrix yet.")
        return cls(token=token, user_id=db.setting_get(KEY_USER_ID) or "")

    def close(self) -> None:
        try:
            self.http.close()
        except Exception:
            pass

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _call(self, method: str, path: str, **kwargs):
        r = self.http.request(method, path, headers=self._headers(), **kwargs)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except ValueError:
                detail = {"error": r.text[:200]}
            raise MatrixError(f"{method} {path} -> {r.status_code} {detail.get('errcode', '')} {detail.get('error', '')}".strip())
        return r.json() if r.content else {}

    def login(self, user: str, password: str) -> None:
        data = self._call(
            "POST", "/_matrix/client/v3/login",
            json={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": user},
                "password": password,
                "device_id": "crm-intake",
                "initial_device_display_name": "Telegram CRM",
            },
        )
        self.token = data["access_token"]
        self.user_id = data["user_id"]
        db.setting_set(KEY_TOKEN, self.token)
        db.setting_set(KEY_USER_ID, self.user_id)

    def whoami(self) -> str:
        return str(self._call("GET", "/_matrix/client/v3/account/whoami").get("user_id") or "")

    def sync(self, since: str | None) -> dict:
        params = {"timeout": SYNC_TIMEOUT_MS, "filter": SYNC_FILTER, "set_presence": "offline"}
        if since:
            params["since"] = since
        return self._call("GET", "/_matrix/client/v3/sync", params=params)

    def join(self, room_id: str) -> None:
        self._call("POST", f"/_matrix/client/v3/join/{quote(room_id, safe='')}")

    def send_message(self, room_id: str, body: str) -> str:
        txn = f"crm-{uuid.uuid4().hex}"
        data = self._call(
            "PUT",
            f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/send/m.room.message/{txn}",
            json={"msgtype": "m.text", "body": body},
        )
        return str(data.get("event_id") or "")


# --- sending (used by the dashboard) ----------------------------------------------


def send_text(phone: str, body: str) -> dict:
    """Write back to a number the bridge already has a conversation with."""
    if not configured():
        raise MatrixError("Google Voice is not set up: MATRIX_HOMESERVER is empty.")
    body = (body or "").strip()
    if not body:
        raise ValueError("Nothing to send.")
    key = db.normalize_phone(phone)
    room_id = db.room_for_phone(key)
    if not room_id:
        raise MatrixError(
            "No conversation with this number yet. One appears when they text the "
            "Google Voice number, or when you start a chat with them from Element."
        )
    mx = Matrix.from_settings()
    try:
        event_id = mx.send_message(room_id, body)
    finally:
        mx.close()
    lead = db.latest_lead_for_phone(key)
    db.record_text(event_id, room_id, key, "out", mx.user_id, body, "m.text", None,
                   lead["id"] if lead else None)
    db.map_text_room(room_id, key)
    return {"event_id": event_id, "room_id": room_id}


# --- telling the callers --------------------------------------------------------


def announcement_text(a: Announcement) -> str:
    who = a.phone if not a.name or db.normalize_phone(a.name) == db.normalize_phone(a.phone) else f"{a.name} {a.phone}"
    head = f"Text from {who}"
    if a.lead_id:
        head += f"  (Lead #{a.lead_id})"
    return f"{head}\n{a.body}" if a.body else head


async def _announce_all(announcements: list[Announcement]) -> None:
    chats = db.list_chats()
    if not chats:
        return
    bot = Bot(common.CRM_BOT_TOKEN)
    for a in announcements:
        text = announcement_text(a)
        for ch in chats:
            try:
                await bot.send_message(ch["chat_id"], text)
            except Exception as exc:  # a chat the bot was removed from, a network blip
                log.warning("Could not post a text to chat %s: %s", ch["chat_id"], exc)


def announce(announcements: list[Announcement]) -> None:
    if announcements and common.GVOICE_ANNOUNCE:
        asyncio.run(_announce_all(announcements))


# --- the loop ---------------------------------------------------------------------


def _connect() -> Matrix:
    mx = Matrix(token=db.setting_get(KEY_TOKEN) or "", user_id=db.setting_get(KEY_USER_ID) or "")
    if mx.token:
        try:
            mx.user_id = mx.whoami()
            db.setting_set(KEY_USER_ID, mx.user_id)
            return mx
        except MatrixError as exc:
            log.info("Stored Matrix token no longer works (%s); logging in again", exc)
    mx.login(common.MATRIX_USER, common.MATRIX_PASSWORD)
    log.info("Logged in to %s as %s", mx.homeserver, mx.user_id)
    return mx


def run_forever() -> None:
    mx = None
    backoff = 5
    while True:
        try:
            if mx is None:
                mx = _connect()
            since = db.setting_get(KEY_SINCE)
            payload = mx.sync(since)
            # The first sync carries recent history: record it, but do not
            # repost weeks of old texts into the callers' group.
            outcome = handle_sync(payload, mx.user_id, announce=bool(since))
            for room_id in outcome.joins:
                try:
                    mx.join(room_id)
                    log.info("Joined %s", room_id)
                except MatrixError as exc:
                    log.warning("Could not join %s: %s", room_id, exc)
            if outcome.new_leads:
                log.info("New leads from texts: %s", outcome.new_leads)
            announce(outcome.announcements)
            next_batch = payload.get("next_batch")
            if next_batch:
                db.setting_set(KEY_SINCE, str(next_batch))
            db.setting_set(KEY_LAST_SYNC, db._now())
            db.setting_set(KEY_LAST_ERROR, "")
            backoff = 5
        except (MatrixError, httpx.HTTPError) as exc:
            log.warning("Matrix: %s (retrying in %ss)", exc, backoff)
            db.setting_set(KEY_LAST_ERROR, str(exc))
            if isinstance(exc, MatrixError) and ("401" in str(exc) or "M_UNKNOWN_TOKEN" in str(exc)):
                if mx is not None:
                    mx.close()
                mx = None
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)


def main() -> None:
    if not configured():
        log.info("MATRIX_HOMESERVER is not set; Google Voice texts are off.")
        return
    run_forever()


if __name__ == "__main__":
    main()
