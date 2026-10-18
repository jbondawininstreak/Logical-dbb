"""Reading files: every format has to land on the same list of row dicts.

Parsing deliberately does not touch phone numbers. A number written the local
way means nothing until a country is chosen, and that happens in validation, so
these tests check that the raw value survives the trip.
"""

import common


def test_every_format_gives_row_dicts_of_text(samples):
    for name, raw in samples.items():
        rows = common.parse_upload(name, raw)
        assert rows, f"{name} parsed to nothing"
        for row in rows:
            assert isinstance(row, dict)
            assert all(isinstance(value, str) for value in row.values()), name


def test_a_file_keeps_its_own_columns(samples):
    rows = common.parse_upload("leads.csv", samples["leads.csv"])
    assert list(rows[0]) == ["Name", "Phone", "Email", "City"]
    assert len(rows) == 8


def test_parsing_leaves_the_phone_exactly_as_written(samples):
    rows = common.parse_upload("leads.csv", samples["leads.csv"])
    assert rows[0]["Phone"] == "0432 841 955"


def test_a_file_with_no_header_row_keeps_its_first_line(samples):
    rows = common.parse_upload("headerless.csv", samples["headerless.csv"])
    assert len(rows) == 2
    assert rows[0]["Email"] == "ada@example.com"
    assert rows[0]["Phone"] == "0432841955"


def test_tabs_are_sniffed(samples):
    rows = common.parse_upload("leads.tsv", samples["leads.tsv"])
    assert list(rows[0]) == ["Full name", "Mobile", "Notes"]


def test_one_value_per_line_is_a_single_column(samples):
    rows = common.parse_upload("numbers.txt", samples["numbers.txt"])
    assert len(rows) == 3
    assert list(rows[0]) == ["Phone"]


def test_json_objects_bring_their_own_schema(samples):
    rows = common.parse_upload("leads.json", samples["leads.json"])
    assert set(rows[0]) == {"first", "phone", "tier", "score"}
    assert rows[0]["score"] == "7", "a JSON number has to arrive as text"


def test_jsonl_is_read_line_by_line(samples):
    rows = common.parse_upload("leads.jsonl", samples["leads.jsonl"])
    assert [r["name"] for r in rows] == ["Mia", "Ned"]


def test_a_spreadsheet_number_does_not_keep_its_decimal_point(samples):
    rows = common.parse_upload("leads.xlsx", samples["leads.xlsx"])
    assert rows[0]["Phone"] == "448000000000", "448000000000.0 is not dialable"
    assert rows[0]["Signed up"] == "2026-03-09", "a date cell is not a datetime"


def test_format_is_worked_out_from_the_bytes_when_the_name_says_nothing(samples):
    # An upload saved without an extension still has to be readable.
    assert common.parse_upload("upload", samples["leads.xlsx"])
    assert common.parse_upload("upload", samples["leads.json"])
    assert common.parse_upload("upload", samples["leads.csv"])


def test_an_empty_file_is_no_rows_rather_than_an_error():
    assert common.parse_upload("empty.csv", b"") == []
    assert common.parse_upload("empty.json", b"") == []


def test_latin1_is_read_when_it_is_not_utf8():
    rows = common.parse_upload("x.csv", b"Name,Phone\nJos\xe9,0432841955\n")
    assert rows[0]["Name"].startswith("Jos")


class TestE164:
    """Turning a written number into one that can be dialled."""

    def test_a_local_number_uses_the_country_it_was_read_as(self):
        assert common.to_e164("0432 841 955", "AU")[0] == "+61432841955"
        assert common.to_e164("2125551234", "US")[0] == "+12125551234"

    def test_a_number_that_carries_its_own_code_ignores_the_country(self):
        assert common.to_e164("00447911123456", "AU")[0] == "+447911123456"
        assert common.to_e164("+61432841955", "US")[0] == "+61432841955"

    def test_the_wrong_country_is_a_rejection_not_a_guess(self):
        # The bug this replaced: an Australian number read as British used to
        # come out as +44432841955, a real number belonging to someone else.
        assert common.to_e164("0432841955", "GB")[0] == ""

    def test_nothing_in_means_nothing_out(self):
        assert common.to_e164("", "AU")[0] == ""
        assert common.to_e164("not-a-number", "AU")[0] == ""

    def test_a_number_knows_its_own_country_and_time_zones(self):
        e164, region, zones = common.to_e164("0432 841 955", "AU")
        assert (e164, region) == ("+61432841955", "AU")
        assert any(z.startswith("Australia/") for z in zones)

    def test_a_uk_mobile_may_report_itself_as_guernsey(self):
        # Not a bug: +44 covers GB, GG, JE and IM. Anything comparing country
        # letters rather than calling codes breaks on this.
        _e164, region, _zones = common.to_e164("07911 123456", "GB")
        assert region in {"GB", "GG", "JE", "IM"}
