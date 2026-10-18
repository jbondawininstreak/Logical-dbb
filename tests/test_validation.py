"""Checking rows before they are written, and what reaches the database.

The contract these protect: only rows marked valid are stored, and a stored
lead is keyed on digits alone so the same number matches its own earlier
uploads, flags and call history whatever shape it arrived in.
"""

import common
import pytest


def statuses(results):
    return [r["status"] for r in results]


def test_the_cast_is_sorted_the_way_it_reads(samples):
    rows = common.parse_upload("leads.csv", samples["leads.csv"])
    results = common.validate_rows(rows, country="AU")
    assert statuses(results) == [
        "valid",       # Ada: a local AU number
        "valid",       # Bob: already carries +61
        "duplicate",   # Cy: 61432841955 is Ada's number again
        "valid",       # Dee: 0044 carries its own country code
        "invalid",     # Eve: a UK local number cannot be read as AU
        "no_phone",    # Fay: no digits at all
        "no_phone",    # Gus: the cell is empty
        "duplicate",   # Hal: Ada's number a third time
    ]


def test_a_local_number_becomes_e164_but_the_key_stays_digits(samples):
    rows = common.parse_upload("leads.csv", samples["leads.csv"])
    first = common.validate_rows(rows, country="AU")[0]
    assert first["phone"] == "+61432841955"
    assert first["key"] == "61432841955", "the key must not grow a plus"


def test_the_row_keeps_everything_else_it_came_with(samples):
    rows = common.parse_upload("leads.csv", samples["leads.csv"])
    row = common.validate_rows(rows, country="AU")[0]["row"]
    assert row["Phone"] == "+61432841955", "the phone cell is rewritten"
    assert row["Email"] == "ada@example.com"
    assert row["City"] == "Sydney"


def test_nothing_without_digits_is_called_invalid():
    # An empty cell is nothing to fix; digits that do not dial are a data
    # problem somebody may want to chase. They are not the same report.
    results = common.validate_rows(
        [{"Phone": ""}, {"Phone": "not-a-number"}, {"Phone": "12345678"}],
        country="AU",
    )
    assert statuses(results) == ["no_phone", "no_phone", "invalid"]


class TestStrict:
    """The "only this country" toggle."""

    def test_a_number_from_elsewhere_is_held_back(self, samples):
        rows = common.parse_upload("leads.csv", samples["leads.csv"])
        results = common.validate_rows(rows, country="AU", strict=True)
        assert results[3]["status"] == "wrong_country"   # Dee, a +44 number

    def test_the_same_number_is_welcome_when_strict_is_off(self, samples):
        rows = common.parse_upload("leads.csv", samples["leads.csv"])
        results = common.validate_rows(rows, country="AU", strict=False)
        assert results[3]["status"] == "valid"

    def test_the_reason_names_calling_codes_not_territories(self, samples):
        rows = common.parse_upload("leads.csv", samples["leads.csv"])
        reason = common.validate_rows(rows, country="AU", strict=True)[3]["reason"]
        assert "+44" in reason and "+61" in reason
        assert "GG" not in reason, "a British number must not be called Guernsey"

    def test_a_uk_mobile_is_not_punished_for_reporting_guernsey(self):
        # +44 covers GB, GG, JE and IM. Comparing country letters would hold
        # back perfectly good UK numbers on a UK list.
        results = common.validate_rows(
            [{"Phone": "07911 123456"}, {"Phone": "+447911123457"}],
            country="GB", strict=True,
        )
        assert statuses(results) == ["valid", "valid"]


class TestPrecedence:
    """Which reason a row is reported under when several apply."""

    def test_a_block_outranks_a_duplicate(self):
        # Reporting the duplicate first would write a flagged number off as a
        # repeat, and the flag somebody set would never be seen.
        results = common.validate_rows(
            [{"Phone": "0432841955"}, {"Phone": "0432841955"}],
            country="AU", blocked={"61432841955"},
        )
        assert statuses(results) == ["blocked", "blocked"]

    def test_a_block_outranks_the_wrong_country(self):
        results = common.validate_rows(
            [{"Phone": "+447911123456"}], country="AU", strict=True,
            blocked={"447911123456"},
        )
        assert statuses(results) == ["blocked"]

    def test_an_unreadable_number_is_reported_before_anything_else(self):
        results = common.validate_rows(
            [{"Phone": "zzz"}], country="AU", strict=True, blocked=set())
        assert statuses(results) == ["no_phone"]


class TestPhoneColumn:
    """Working out which column holds the number."""

    def test_a_column_named_in_the_form_wins(self):
        rows = [{"a": "0432841955", "b": "+61293744000"}]
        assert common.validate_rows(rows, country="AU", phone_column="b")[0][
            "phone"] == "+61293744000"

    def test_the_name_is_matched_whatever_its_case(self):
        rows = [{"Mobile Number": "0432841955"}]
        assert common.validate_rows(rows, country="AU",
                                    phone_column="mobile number")[0]["status"] == "valid"

    def test_any_word_that_means_phone_is_enough(self):
        # This is the gap that let a file with a Mobile column parse fine and
        # then be rejected for having no phone column.
        for header in ("Phone", "Mobile", "Cell", "Tel", "Contact", "Number"):
            rows = [{"Name": "Ada", header: "0432841955"}]
            assert common.validate_rows(rows, country="AU")[0]["status"] == "valid", header

    def test_a_phone_word_beats_a_column_that_merely_holds_digits(self):
        # Both columns hold 8-15 digits, so the fallback that reads values
        # would take whichever came first. Only the header word settles it,
        # which is what makes this the test that notices if that list is
        # narrowed back to "phone" alone.
        import db

        rows = [{"Account": "123456789012", "Mobile": "0432841955"}]
        assert db.pick_phone_column(rows, "") == "Mobile"

    def test_headers_that_say_nothing_fall_back_to_the_values(self):
        rows = [{"a": "Ada", "b": "0432841955"}, {"a": "Bob", "b": "0432841956"}]
        assert common.validate_rows(rows, country="AU")[0]["phone"] == "+61432841955"

    def test_a_missing_column_says_which_ones_there_are(self):
        with pytest.raises(ValueError, match="Nickname"):
            common.validate_rows([{"Nickname": "Ada"}], country="AU")


class TestWritingToTheDatabase:
    def test_only_valid_rows_are_written_and_the_rest_are_counted(
            self, clean_db, samples):
        db = clean_db
        rows = common.parse_upload("leads.csv", samples["leads.csv"])
        results = common.validate_rows(rows, country="AU",
                                       blocked=db.blocked_phones())
        out = db.add_validated_leads(results, "oct-list", country="AU",
                                     timezone="Australia/Sydney")
        assert out["added"] == 3
        assert out["skipped"] == {"duplicate": 2, "invalid": 1, "no_phone": 2}
        assert db.stats()["new"] == 3

    def test_a_stored_lead_is_found_by_the_digits_of_its_number(
            self, clean_db, samples):
        db = clean_db
        rows = common.parse_upload("leads.csv", samples["leads.csv"])
        results = common.validate_rows(rows, country="AU")
        db.add_validated_leads(results, "oct-list", country="AU")
        found = db.leads_for_phone("61432841955")
        assert len(found) == 1
        assert found[0]["data"]["Phone"] == "+61432841955"

    def test_the_same_number_uploaded_in_another_shape_shares_its_key(
            self, clean_db):
        db = clean_db
        for raw, country in (("0432 841 955", "AU"), ("+61 432 841 955", "AU"),
                             ("0061432841955", "GB")):
            results = common.validate_rows([{"Phone": raw}], country=country)
            db.add_validated_leads(results, f"list-{country}-{raw[:4]}")
        assert len(db.leads_for_phone("61432841955")) == 3

    def test_a_blocked_number_never_reaches_the_database(self, clean_db):
        db = clean_db
        db.flag_phone("61432841955", 7, "Caller")
        results = common.validate_rows([{"Phone": "0432841955"}], country="AU",
                                       blocked=db.blocked_phones())
        out = db.add_validated_leads(results, "oct-list")
        assert out["added"] == 0
        assert db.stats()["new"] == 0

    def test_a_source_remembers_what_it_was_read_as(self, clean_db):
        db = clean_db
        results = common.validate_rows([{"Phone": "0432841955"}], country="AU")
        db.add_validated_leads(results, "oct-list", country="AU",
                               timezone="Australia/Sydney")
        source = [s for s in db.list_sources() if s["name"] == "oct-list"][0]
        assert source["country"] == "AU"
        assert source["timezone"] == "Australia/Sydney"

    def test_the_migration_can_run_again(self, clean_db):
        # init_db runs at import in each of run.py's three processes.
        clean_db.init_db()
        clean_db.init_db()
