"""Test setup: a throwaway database, and the sample files the tests upload.

The database URL is set here, before anything imports db, because db builds its
client at import time. load_dotenv does not overwrite variables that are already
set, so a developer's own .env cannot drag these tests onto the real Turso
database -- and the assert below refuses to run at all if something still did.
"""

import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime

import pytest

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="crm-tests-"))

os.environ["TURSO_DATABASE_URL"] = f"file:{_TMP}/test.db"
os.environ["TURSO_AUTH_TOKEN"] = ""
os.environ["ADMIN_BOT_TOKEN"] = "0:test-admin"
os.environ["CRM_BOT_TOKEN"] = "0:test-crm"
os.environ["ADMIN_USER_ID"] = "1"
os.environ["DASHBOARD_KEY"] = "test-dashboard-key"
os.environ["DEFAULT_COUNTRY"] = "GB"

assert os.environ["TURSO_DATABASE_URL"].startswith("file:"), (
    "The tests would be writing to a real hosted database. Refusing to run."
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

DASHBOARD_KEY = os.environ["DASHBOARD_KEY"]


@pytest.fixture(scope="session")
def samples() -> dict:
    """One small file per supported format, built rather than committed.

    Every file holds the same cast where it can: an Australian number written
    the local way, one that carries its own country code, a British one, and
    something that is not a number at all.
    """
    import openpyxl

    files = {}
    files["leads.csv"] = (
        "Name,Phone,Email,City\n"
        "Ada,0432 841 955,ada@example.com,Sydney\n"
        "Bob,+61 2 9374 4000,bob@example.com,Sydney\n"
        "Cy,61432841955,cy@example.com,Perth\n"
        "Dee,00447911123456,dee@example.com,London\n"
        "Eve,07911 123456,eve@example.com,Leeds\n"
        "Fay,not-a-number,fay@example.com,Nowhere\n"
        "Gus,,gus@example.com,Nowhere\n"
        "Hal,0432 841 955,hal@example.com,Sydney\n"
    ).encode()

    # No header row: the first line is a lead and the columns are named from it.
    files["headerless.csv"] = (
        "ada@example.com,0432841955,NS,2026-01-04\n"
        "bob@example.com,+12125551234,CA,2026-02-11\n"
    ).encode()

    # Tabs, and a phone column that is not called "phone".
    files["leads.tsv"] = (
        "Full name\tMobile\tNotes\n"
        "Ivy\t0432841955\tcalled once\n"
        "Jo\t+447911123456\t\n"
    ).encode()

    files["numbers.txt"] = b"0432841955\n+61293744000\n07911123456\n"

    files["leads.json"] = json.dumps([
        {"first": "Ken", "phone": "0432841955", "tier": "gold", "score": 7},
        {"first": "Lou", "phone": "+12125551234", "tier": "silver"},
    ]).encode()

    files["leads.jsonl"] = (
        '{"name":"Mia","contact":"0432841955"}\n'
        '{"name":"Ned","contact":"+447911123456"}\n'
    ).encode()

    # A workbook hands over real numbers and dates rather than text, so this
    # carries the float Excel turns a long number into.
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Name", "Phone", "Signed up"])
    sheet.append(["Oli", 448000000000, datetime(2026, 3, 9)])
    sheet.append(["Pat", "0432841955", datetime(2026, 3, 10)])
    path = _TMP / "leads.xlsx"
    book.save(path)
    files["leads.xlsx"] = path.read_bytes()

    return files


@pytest.fixture
def clean_db():
    """An empty database for one test, since db keeps a single client."""
    import db

    db.init_db()
    for table in ("attempts", "texts", "text_rooms", "settings", "leads",
                  "do_not_even_try", "sources", "allowed_chats"):
        db._q(f"DELETE FROM {table}")
    return db
