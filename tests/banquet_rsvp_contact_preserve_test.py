#!/usr/bin/env python3
"""Regression: a banquet RSVP resubmission that omits contact info must not erase the stored email/phone
(2026-10-07: Cathy Brown's family submission blanked John's and Kerrigan's emails). Uses a temp DB, no LLM, no Telegram.
Run: python tests/banquet_rsvp_contact_preserve_test.py"""
import logging
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.disable(logging.CRITICAL)
import jobs.events.banquet_rsvp as b  # noqa: E402
import jobs.events.schema as schema  # noqa: E402


def make_db():
    path = Path(tempfile.mkdtemp()) / "t.db"
    with mock.patch.object(schema, "DB_PATH", str(path)):
        schema.create_tables()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def det(first, last, email="", extra=()):
    return {"first_name": first, "last_name": last, "email": email, "attending": True, "children": [],
            "additional_attendees": [{"first_name": f, "last_name": l} for f, l in extra]}


class ContactPreserve(unittest.TestCase):
    def test_resubmission_without_contact_keeps_it(self):
        conn = make_db()
        with mock.patch.object(b, "find_member_id", return_value=75):
            b._upsert_rsvp(conn, 10, det("John", "Brown", "j@x.com"), "t1")
            b._upsert_rsvp(conn, 10, det("John", "Brown", ""), "t2")
        self.assertEqual(conn.execute("SELECT email FROM event_registrations").fetchone()[0], "j@x.com")

    def test_db_trigger_blocks_direct_blanking(self):
        conn = make_db()
        conn.execute("INSERT INTO event_registrations (event_id, first_name, email, phone) VALUES (10,'A','a@x.com','3025551212')")
        conn.execute("UPDATE event_registrations SET email = NULL, phone = '' ")
        row = conn.execute("SELECT email, phone FROM event_registrations").fetchone()
        self.assertEqual((row[0], row[1]), ("a@x.com", "3025551212"))
        conn.execute("UPDATE event_registrations SET email = 'new@x.com'")
        self.assertEqual(conn.execute("SELECT email FROM event_registrations").fetchone()[0], "new@x.com")


if __name__ == "__main__":
    unittest.main()
