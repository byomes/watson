#!/usr/bin/env python3
"""Regression for the Connection page dropping every tracked-event signup (2026-10-06): event_registrations.submitted_at arrives as an email
header date or an ISO timestamp with a +0000 suffix, neither parsed by SQLite's date(). _reg_date handles every format in the live data.
Run: python tests/connection_reg_dates_test.py"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobs.congregation.connection_web import _reg_date  # noqa: E402


class T(unittest.TestCase):
    def test_every_format_seen_in_the_data(self):
        self.assertEqual(_reg_date("Sat, 26 Sep 2026 00:21:33 +0000", "2026-09-26 00:22:24"), "2026-09-26")      # email header
        self.assertEqual(_reg_date("Tue, 6 Oct 2026 13:55:07 -0400", None), "2026-10-06")                        # single-digit day, offset
        self.assertEqual(_reg_date("2026-08-30 15:30:30 +0000", "2026-09-07 00:51:14"), "2026-08-30")            # csv import
        self.assertEqual(_reg_date("2026-09-02", None), "2026-09-02")
        self.assertEqual(_reg_date(None, "2026-09-07 00:51:14"), "2026-09-07")                                    # no submitted_at: created_at
        self.assertEqual(_reg_date("", "2026-09-07 00:51:14"), "2026-09-07")
        self.assertEqual(_reg_date("garbage", "2026-09-07 00:51:14"), "2026-09-07")                               # unparseable: fall back
        self.assertIsNone(_reg_date("garbage", "also garbage"))
        self.assertIsNone(_reg_date(None, None))

    def test_live_rows_all_parse(self):
        db = Path(__file__).resolve().parent.parent / "data" / "watson.db"
        if not db.exists():
            self.skipTest("no live db")
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = c.execute("SELECT submitted_at, created_at FROM event_registrations").fetchall()
        bad = [r for r in rows if _reg_date(*r) is None]
        self.assertEqual(bad, [], f"{len(bad)} live registration rows have no parseable date")


if __name__ == "__main__":
    unittest.main(verbosity=1)
