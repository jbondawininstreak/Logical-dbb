"""The dashboard over HTTP: the key, the upload flow, and the look.

The upload flow is the part worth guarding. A preview must write nothing, and
the thing that turns a preview into leads is one button press that cannot be
replayed.
"""

import re

import pytest
from fastapi.testclient import TestClient

from conftest import DASHBOARD_KEY as KEY

PAGES = ("/", "/upload", "/chats", "/flags", "/attempts", "/sources", "/texts")


@pytest.fixture
def client(clean_db):
    import dashboard

    dashboard._PENDING.clear()
    return TestClient(dashboard.app)


def upload(client, name, raw, **form):
    data = {"key": KEY, "source": "a-list", "phone_column": "",
            "country": "AU", "timezone": "", "strict": ""}
    data.update(form)
    return client.post("/upload", params={"key": KEY},
                       files={"upload": (name, raw, "application/octet-stream")},
                       data=data)


class TestTheKey:
    @pytest.mark.parametrize("path", PAGES)
    def test_no_key_is_refused(self, client, path):
        assert client.get(path).status_code == 403

    @pytest.mark.parametrize("path", PAGES)
    def test_the_right_key_opens_the_page(self, client, path):
        assert client.get(path, params={"key": KEY}).status_code == 200

    def test_a_wrong_key_is_refused(self, client):
        assert client.get("/", params={"key": "nope"}).status_code == 403

    def test_every_link_on_the_home_page_carries_the_key(self, client):
        # Losing the key from a link locks the user out of their own dashboard.
        body = client.get("/", params={"key": KEY}).text
        for link in re.findall(r'href="([^"]+)"', body):
            assert f"key={KEY}" in link, link


class TestTheLook:
    @pytest.fixture
    def home(self, client):
        return client.get("/", params={"key": KEY}).text

    def test_the_home_screen_is_a_grid_of_app_icons(self, home):
        assert 'class="grid"' in home
        assert home.count('class="tile"') == 6

    def test_it_follows_the_system_appearance(self, home):
        assert "prefers-color-scheme: dark" in home
        assert "-apple-system" in home

    def test_it_fits_a_phone(self, home):
        assert "width=device-width" in home

    def test_everything_is_served_from_this_one_file(self, home):
        # No CDN: the page has to render behind a proxy with nothing to fetch.
        assert "http://" not in home and "https://" not in home

    def test_a_badge_only_appears_for_something_waiting(self, client, clean_db):
        # Both halves matter. On its own the "not in" would pass against any
        # string the page never contains, and guard nothing.
        badge = "<span class='badge'>"
        assert badge not in client.get("/", params={"key": KEY}).text
        clean_db.flag_phone("61432841955", 7, "Caller")
        after = client.get("/", params={"key": KEY}).text
        assert badge in after
        assert f"{badge}1</span>" in after


class TestUploading:
    def test_a_preview_writes_nothing(self, client, clean_db, samples):
        response = upload(client, "leads.csv", samples["leads.csv"])
        assert response.status_code == 200
        assert "Load 3 leads" in response.text
        assert clean_db.stats()["new"] == 0, "the preview must not store anything"

    def test_the_preview_says_why_each_row_was_held_back(self, client, samples):
        text = upload(client, "leads.csv", samples["leads.csv"]).text
        assert "not a real, dialable number" in text
        assert "same number as an earlier row" in text
        assert "no number in that column" in text
        assert "+61432841955" in text

    def test_loading_the_preview_stores_exactly_what_it_promised(
            self, client, clean_db, samples):
        response = upload(client, "leads.csv", samples["leads.csv"])
        token = re.search(r'name="token" value="([^"]+)"', response.text).group(1)
        client.post("/upload/commit", params={"key": KEY},
                    data={"key": KEY, "token": token})
        assert clean_db.stats()["new"] == 3

    def test_the_same_preview_cannot_be_loaded_twice(
            self, client, clean_db, samples):
        response = upload(client, "leads.csv", samples["leads.csv"])
        token = re.search(r'name="token" value="([^"]+)"', response.text).group(1)
        client.post("/upload/commit", params={"key": KEY},
                    data={"key": KEY, "token": token})
        again = client.post("/upload/commit", params={"key": KEY},
                            data={"key": KEY, "token": token})
        assert "expired" in again.text.lower()
        assert clean_db.stats()["new"] == 3, "a replay must not double the leads"

    def test_an_unknown_token_loads_nothing(self, client, clean_db):
        response = client.post("/upload/commit", params={"key": KEY},
                               data={"key": KEY, "token": "made-up"})
        assert response.status_code == 200
        assert clean_db.stats()["new"] == 0

    def test_only_this_country_offers_fewer_leads(self, client, samples):
        lenient = upload(client, "leads.csv", samples["leads.csv"])
        strict = upload(client, "leads.csv", samples["leads.csv"], strict="1")
        assert "Load 3 leads" in lenient.text
        assert "Load 2 leads" in strict.text
        assert "+44 number" in strict.text

    @pytest.mark.parametrize("name", [
        "leads.csv", "leads.tsv", "leads.xlsx", "leads.json", "leads.jsonl",
        "numbers.txt", "headerless.csv",
    ])
    def test_every_format_reaches_a_preview(self, client, samples, name):
        response = upload(client, name, samples[name], source=f"src-{name}")
        assert response.status_code == 200
        assert "Row by row" in response.text
        assert "was not loaded" not in response.text

    def test_a_file_with_no_usable_column_explains_itself(self, client):
        response = upload(client, "x.csv", b"Nickname,City\nAda,Sydney\n")
        assert "Nickname" in response.text, "say which columns there were"

    def test_an_empty_file_is_turned_away(self, client):
        assert "no rows" in upload(client, "x.csv", b"").text

    def test_a_source_name_is_required(self, client, samples):
        response = upload(client, "leads.csv", samples["leads.csv"], source="  ")
        assert "required" in response.text.lower()

    def test_the_country_picked_on_the_form_is_the_one_used(
            self, client, clean_db, samples):
        # The same file read as GB: the Australian numbers stop being real.
        response = upload(client, "leads.csv", samples["leads.csv"], country="GB")
        assert "Load 2 leads" in response.text


class TestThePagesThatCameBefore:
    def test_a_chat_can_be_added_and_removed(self, client):
        added = client.post("/chats", params={"key": KEY},
                            data={"key": KEY, "action": "add",
                                  "chat_id": "-100123", "title": "Callers"})
        assert "-100123" in added.text
        removed = client.post("/chats", params={"key": KEY},
                              data={"key": KEY, "action": "remove",
                                    "chat_id": "-100123"})
        assert "Removed" in removed.text

    def test_a_chat_id_that_is_not_a_number_is_handled(self, client):
        response = client.post("/chats", params={"key": KEY},
                               data={"key": KEY, "action": "add", "chat_id": "abc"})
        assert response.status_code == 200
        assert "integer" in response.text

    def test_a_lead_opens_by_the_digits_of_its_number(
            self, client, clean_db, samples):
        import common

        results = common.validate_rows([{"Phone": "0432841955"}], country="AU")
        clean_db.add_validated_leads(results, "a-list")
        response = client.get("/lead/+61 432 841 955", params={"key": KEY})
        assert response.status_code == 200
        assert "61432841955" in response.text

    def test_the_sources_page_shows_what_a_list_was_read_as(
            self, client, clean_db):
        import common

        results = common.validate_rows([{"Phone": "0432841955"}], country="AU")
        clean_db.add_validated_leads(results, "oct-list", country="AU",
                                     timezone="Australia/Sydney")
        text = client.get("/sources", params={"key": KEY}).text
        assert "oct-list" in text and "AU" in text and "Australia/Sydney" in text

    def test_pushing_with_nothing_queued_says_so(self, client, clean_db):
        clean_db.add_chat(-100123, "Callers")
        response = client.post("/push", params={"key": KEY},
                               data={"key": KEY, "chat_id": "-100123"})
        assert "No new leads" in response.text
