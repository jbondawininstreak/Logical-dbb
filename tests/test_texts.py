"""Google Voice texts: what one /sync page does to the database, and the pages
that show it.

The contract these protect: a text is keyed on the same digits as a lead, so
it lands on the lead page; a number nobody uploaded becomes a lead once and
only once however many times the same page is replayed; and nothing a caller
reads in the group is announced from history the first time the intake runs.
"""

import pytest
from fastapi.testclient import TestClient

import common
import gvoice
from conftest import DASHBOARD_KEY as KEY

ME = "@crm:matrix.example.com"
GHOST = "@gvoice_abc123.+15551234567:matrix.example.com"
BOT = "@gvoicebot:matrix.example.com"
ROOM = "!portal:matrix.example.com"
PHONE = "15551234567"
BODY = "hi, still interested?"


def message(sender, body, event_id, msgtype="m.text", ts=1700000000000):
    return {
        "type": "m.room.message",
        "sender": sender,
        "event_id": event_id,
        "origin_server_ts": ts,
        "content": {"msgtype": msgtype, "body": body},
    }


def member(mxid, displayname=""):
    return {
        "type": "m.room.member",
        "sender": BOT,
        "state_key": mxid,
        "content": {"membership": "join", "displayname": displayname},
    }


def sync_page(state=(), timeline=(), invites=(), room=ROOM):
    return {
        "next_batch": "s1",
        "rooms": {
            "invite": {r: {} for r in invites},
            "join": {
                room: {
                    "state": {"events": list(state)},
                    "timeline": {"events": list(timeline)},
                }
            },
        },
    }


def stranger_texts():
    """The page every scenario starts from: Ada's ghost joins, Ada writes."""
    return sync_page(state=[member(GHOST, "Ada")], timeline=[message(GHOST, BODY, "$e1")])


# --- reading Matrix IDs ------------------------------------------------------


@pytest.mark.parametrize("mxid, phone", [
    (GHOST, "+15551234567"),
    ("@gvoice_+15551234567:x", "+15551234567"),
    (BOT, ""),
    (ME, ""),
    ("@gvoice_abc:x", ""),
])
def test_a_ghost_carries_its_number_and_nobody_else_does(mxid, phone):
    assert gvoice.phone_from_mxid(mxid) == phone


@pytest.mark.parametrize("channel, phone", [
    ("t.+15551234567", "+15551234567"),
    ("g.abc", ""),
    ("", ""),
])
def test_only_a_dm_thread_names_a_number(channel, phone):
    assert gvoice.phone_from_channel_id(channel) == phone


def test_media_is_tagged_rather_than_lost():
    assert gvoice.message_text({"msgtype": "m.image", "body": "photo.jpg"}) == (
        "[image] photo.jpg", "m.image"
    )


# --- one /sync page -----------------------------------------------------------


def test_an_invite_is_a_join_and_nothing_more(clean_db):
    outcome = gvoice.handle_sync(sync_page(invites=["!new:matrix.example.com"]), ME)
    assert outcome.joins == ["!new:matrix.example.com"]
    assert outcome.recorded == 0
    assert clean_db.recent_texts() == []


class TestAStrangerTexting:
    def test_the_text_becomes_a_lead(self, clean_db):
        outcome = gvoice.handle_sync(stranger_texts(), ME)
        assert outcome.recorded == 1
        assert len(outcome.new_leads) == 1
        assert clean_db.stats()["new"] == 1

        lead = clean_db.get_lead(outcome.new_leads[0])
        assert lead["source"] == "google-voice"
        assert lead["data"] == {
            "Phone": "+15551234567",
            "Name": "Ada",
            "First text": BODY,
        }

    def test_the_room_and_the_text_are_keyed_on_the_number(self, clean_db):
        outcome = gvoice.handle_sync(stranger_texts(), ME)
        assert clean_db.room_for_phone(PHONE) == ROOM

        text = clean_db.texts_for_phone(PHONE)[0]
        assert text["direction"] == "in"
        assert text["body"] == BODY
        assert text["lead_id"] == outcome.new_leads[0]
        assert text["sent_at"] == "2023-11-14 22:13:20"

    def test_the_callers_hear_about_it(self, clean_db):
        outcome = gvoice.handle_sync(stranger_texts(), ME)
        assert len(outcome.announcements) == 1
        a = outcome.announcements[0]
        assert a.phone == "+15551234567"
        assert a.lead_id == outcome.new_leads[0]
        assert a.body == BODY
        assert a.name == "Ada"

        text = gvoice.announcement_text(a)
        assert text.startswith(f"Text from Ada +15551234567  (Lead #{a.lead_id})")
        assert text.endswith(BODY)

    def test_replaying_the_page_changes_nothing(self, clean_db):
        gvoice.handle_sync(stranger_texts(), ME)
        again = gvoice.handle_sync(stranger_texts(), ME)
        assert again.recorded == 0
        assert again.new_leads == []
        assert clean_db.stats()["new"] == 1
        assert len(clean_db.texts_for_phone(PHONE)) == 1

    def test_the_first_sync_is_recorded_but_not_announced(self, clean_db):
        # The first page carries history; reposting weeks of old texts into the
        # group would bury the callers.
        outcome = gvoice.handle_sync(stranger_texts(), ME, announce=False)
        assert outcome.announcements == []
        assert outcome.recorded == 1
        assert len(outcome.new_leads) == 1


def test_a_known_number_joins_its_latest_lead(clean_db):
    clean_db.add_leads([{"Phone": "+15551234567", "Name": "Known"}], "a-list", "Phone")
    known_id = clean_db.leads_for_phone(PHONE)[0]["id"]

    outcome = gvoice.handle_sync(stranger_texts(), ME)
    assert outcome.new_leads == []
    assert clean_db.texts_for_phone(PHONE)[0]["lead_id"] == known_id


def test_a_blocked_number_is_kept_but_not_queued(clean_db):
    clean_db.flag_phone(PHONE, 7, "Caller")

    outcome = gvoice.handle_sync(stranger_texts(), ME)
    assert outcome.recorded == 1
    assert outcome.new_leads == []
    assert clean_db.stats()["new"] == 0


def test_my_own_message_is_a_reply(clean_db):
    gvoice.handle_sync(stranger_texts(), ME)
    assert clean_db.phones_awaiting_reply() == 1

    gvoice.handle_sync(
        sync_page(state=[member(GHOST, "Ada")], timeline=[message(ME, "on my way", "$e2")]), ME
    )
    out = [t for t in clean_db.texts_for_phone(PHONE) if t["direction"] == "out"]
    assert len(out) == 1
    assert out[0]["body"] == "on my way"
    assert clean_db.phones_awaiting_reply() == 0


class TestTheBridgeBot:
    def test_its_notice_in_a_conversation_joins_the_thread(self, clean_db):
        gvoice.handle_sync(stranger_texts(), ME)
        gvoice.handle_sync(
            sync_page(timeline=[message(BOT, "Failed to send", "$e3", msgtype="m.notice")]), ME
        )
        notes = [t for t in clean_db.texts_for_phone(PHONE) if t["direction"] == "bridge"]
        assert len(notes) == 1
        assert notes[0]["body"] == "Failed to send"
        assert clean_db.setting_get(gvoice.KEY_LAST_NOTICE) == "Failed to send"

    def test_its_notice_elsewhere_is_only_remembered(self, clean_db):
        gvoice.handle_sync(
            sync_page(
                timeline=[message(BOT, "Failed to send", "$e4", msgtype="m.notice")],
                room="!management:matrix.example.com",
            ),
            ME,
        )
        assert clean_db.recent_texts() == []
        assert clean_db.setting_get(gvoice.KEY_LAST_NOTICE) == "Failed to send"


# --- the dashboard ------------------------------------------------------------


@pytest.fixture
def client(clean_db):
    import dashboard

    dashboard._PENDING.clear()
    return TestClient(dashboard.app)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(common, "MATRIX_HOMESERVER", "http://matrix.test")
    monkeypatch.setattr(common, "MATRIX_PASSWORD", "pw")


class TestTheTextsPages:
    def test_the_texts_page_lists_the_text_and_links_the_lead(self, client):
        gvoice.handle_sync(stranger_texts(), ME)
        body = client.get("/texts", params={"key": KEY}).text
        assert BODY in body
        assert f"/lead/{PHONE}" in body

    def test_the_lead_page_shows_the_thread(self, client):
        gvoice.handle_sync(stranger_texts(), ME)
        body = client.get(f"/lead/{PHONE}", params={"key": KEY}).text
        assert BODY in body

    def test_no_reply_box_until_google_voice_is_set_up(self, client):
        gvoice.handle_sync(stranger_texts(), ME)
        body = client.get(f"/lead/{PHONE}", params={"key": KEY}).text
        assert "Google Voice is not set up." in body
        assert "/texts/send" not in body

    def test_the_reply_box_appears_once_set_up(self, client, configured):
        gvoice.handle_sync(stranger_texts(), ME)
        body = client.get(f"/lead/{PHONE}", params={"key": KEY}).text
        assert 'action="/texts/send?key=' in body
        assert 'name="body"' in body

    def test_an_unanswered_text_puts_a_badge_on_the_tile(self, client):
        badge = "<span class='badge'>1</span>"
        assert badge not in client.get("/", params={"key": KEY}).text
        gvoice.handle_sync(stranger_texts(), ME)
        assert badge in client.get("/", params={"key": KEY}).text


class TestReplying:
    def send(self, client, body="thanks"):
        return client.post(
            "/texts/send", params={"key": KEY},
            data={"key": KEY, "phone": PHONE, "body": body},
            follow_redirects=False,
        )

    def test_nothing_goes_out_before_the_intake_has_logged_in(self, client, clean_db, configured):
        gvoice.handle_sync(stranger_texts(), ME)
        response = self.send(client)
        assert response.status_code == 200
        assert "has not logged in" in response.text
        assert [t for t in clean_db.texts_for_phone(PHONE) if t["direction"] == "out"] == []

    def test_a_reply_is_sent_recorded_and_returns_to_the_lead(
            self, client, clean_db, configured, monkeypatch):
        gvoice.handle_sync(stranger_texts(), ME)
        clean_db.setting_set(gvoice.KEY_TOKEN, "tok")
        clean_db.setting_set(gvoice.KEY_USER_ID, ME)
        monkeypatch.setattr(gvoice.Matrix, "send_message", lambda self, room_id, body: "$sent1")

        response = self.send(client)
        assert response.status_code == 303
        assert f"/lead/{PHONE}" in response.headers["location"]

        last = clean_db.texts_for_phone(PHONE)[-1]
        assert last["direction"] == "out"
        assert last["body"] == "thanks"
        assert last["event_id"] == "$sent1"

    def test_a_blank_reply_goes_nowhere(self, client, clean_db, configured, monkeypatch):
        gvoice.handle_sync(stranger_texts(), ME)
        clean_db.setting_set(gvoice.KEY_TOKEN, "tok")
        clean_db.setting_set(gvoice.KEY_USER_ID, ME)
        monkeypatch.setattr(gvoice.Matrix, "send_message", lambda self, room_id, body: "$sent2")

        response = self.send(client, body="   ")
        assert response.status_code == 200
        assert "Nothing to send" in response.text
