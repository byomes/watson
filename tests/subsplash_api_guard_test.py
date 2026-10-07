#!/usr/bin/env python3
"""The Subsplash API stays OFF unless an explicit, self-expiring one-time override is set (Bill 2026-10-06). No network, no phone.
Run: python tests/subsplash_api_guard_test.py"""
import datetime
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobs.congregation import kids_checkin_client as kc  # noqa: E402

VAR = "SUBSPLASH_API_ONE_TIME_UNTIL"


class T(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.pop(VAR, None)
        self._run = kc.asyncio.run
        kc.asyncio.run = lambda coro, *a, **k: (coro.close(), "STUB-RESULT")[1]

    def tearDown(self):
        kc.asyncio.run = self._run
        os.environ.pop(VAR, None)
        if self._env is not None:
            os.environ[VAR] = self._env

    def test_off_by_default(self):
        for fn, args in ((kc.pull_full_history, ()), (kc.pull_events_by_id, (["x"],))):
            with self.assertRaises(kc.ApiAccessDisabled):
                fn(*args)

    def test_expired_or_malformed_override_does_not_work(self):
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        for v in (yesterday, "", "yes", "2026-13-45", "tomorrow"):
            os.environ[VAR] = v
            self.assertFalse(kc._one_time_override_active(), v)
            with self.assertRaises(kc.ApiAccessDisabled):
                kc.pull_full_history()

    def test_override_for_today_or_later_lets_a_run_through(self):
        for d in (datetime.date.today(), datetime.date.today() + datetime.timedelta(days=1)):
            os.environ[VAR] = d.isoformat()
            self.assertTrue(kc._one_time_override_active())
            self.assertEqual(kc.pull_full_history(), "STUB-RESULT")
            self.assertEqual(kc.pull_events_by_id(["x"]), "STUB-RESULT")

    def test_fluro_is_untouched(self):
        from jobs.congregation import fluro_client as fc
        src = Path(fc.__file__).read_text()
        self.assertNotIn(VAR, src)
        self.assertIn("raise ApiAccessDisabled", src)


if __name__ == "__main__":
    unittest.main(verbosity=1)
