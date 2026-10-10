#!/usr/bin/env python3
"""Recurring events (monthly Bible Study) are tracked one row per occurrence; the email's event date picks the row, and a verbatim
event name never fuzzy-matches a different event (2026-10-09). Run: python tests/event_matching_recurring_test.py"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobs.events.matching import _email_event_date, find_active_event  # noqa: E402

BS, BL = "Men's Fraternity Bible Study", "Men's Fraternity Billiards Outing"


def mail(name, date, reg="October 6, 2026"):
    return f"New Registration\n{name}\n\n{date} • 6:30 - 8:00 PM\nDate registered: {reg} @ 1:00PM EDT"


class T(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.execute("CREATE TABLE church_events (id INTEGER PRIMARY KEY, event_name TEXT, start_date TEXT, end_date TEXT, tracking_active INTEGER)")
        self.c.executemany("INSERT INTO church_events VALUES (?,?,?,NULL,1)",
                           [(9, BL, "2026-11-04"), (11, BS, "2026-10-07"), (12, BS, "2026-12-02")])

    def m(self, name, date):
        r = find_active_event(self.c, name, mail(name, date))
        return r and r["id"]

    def test_occurrence_picked_by_event_date(self):
        self.assertEqual(self.m(BS, "October 7, 2026"), 11)
        self.assertEqual(self.m(BS, "December 2, 2026"), 12)

    def test_unknown_occurrence_asks_instead_of_guessing(self):
        self.assertIsNone(self.m(BS, "November 4, 2026"))

    def test_bible_study_never_lands_on_billiards(self):
        self.assertNotEqual(self.m(BS, "November 4, 2026"), 9)
        self.assertEqual(self.m(BL, "November 4, 2026"), 9)

    def test_registered_date_is_not_the_event_date(self):
        self.assertEqual(_email_event_date(mail(BS, "October 7, 2026", reg="October 5, 2026")), "2026-10-07")


if __name__ == "__main__":
    unittest.main()
