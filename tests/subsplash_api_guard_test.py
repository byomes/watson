#!/usr/bin/env python3
"""Subsplash API access is permitted (2026-10-09); the dashboard must still be surfaced on screen. No network, no phone.
Run: python tests/subsplash_api_guard_test.py"""
import datetime
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobs.congregation import kids_checkin_client as kc  # noqa: E402


class T(unittest.TestCase):
    def test_api_permitted(self):
        """Subsplash confirmed Watson's dashboard/core.subsplash.com reading is allowed (2026-10-09); Fluro is not."""
        kc._require_api_permission()  # must not raise
        from jobs.congregation import fluro_client as fc
        with self.assertRaises(fc.ApiAccessDisabled):  # Fluro stays off: giving data lives there (Bill, 2026-10-09)
            fc.get_session_token()


class Surface(unittest.TestCase):
    """RULE (Bill 2026-10-06): all Subsplash work needs the dashboard surfaced to the screen."""

    def run_surface(self, visibility):
        import asyncio
        sent, evals = [], []

        class WS:
            async def send(self, data):
                sent.append(data)

        async def fake_eval(ws, js, **k):
            evals.append(js)
            return visibility(len(evals))
        orig = (kc._ws_eval, kc._select_device_serial, kc._adb, kc.asyncio.sleep)

        async def no_sleep(*a, **k):
            return None
        kc._ws_eval, kc._select_device_serial, kc._adb, kc.asyncio.sleep = fake_eval, (lambda: "SER"), (lambda *a, **k: ""), no_sleep
        try:
            asyncio.run(kc.surface_tab(WS(), tries=3))
        finally:
            kc._ws_eval, kc._select_device_serial, kc._adb, kc.asyncio.sleep = orig
        return sent, evals

    def test_brings_tab_to_front_and_waits_until_visible(self):
        sent, evals = self.run_surface(lambda n: "hidden" if n < 3 else "visible")
        self.assertIn("Page.bringToFront", sent[0])
        self.assertEqual(len(evals), 3)

    def test_refuses_to_continue_when_it_cannot_be_shown(self):
        with self.assertRaises(kc.KidsCheckinClientError) as cm:
            self.run_surface(lambda n: "hidden")
        self.assertIn("only runs while the dashboard is visible", str(cm.exception))

    def test_every_subsplash_entry_point_surfaces_first(self):
        import inspect
        from jobs.church_calendar import registrations as R
        self.assertEqual(inspect.getsource(kc).count("await surface_tab(ws)"), 3)                    # full-history pull, by-id pull and authed_eval
        self.assertIn("await pg.surface()", inspect.getsource(R._pull_inner))
        self.assertIn("await self.surface()", inspect.getsource(R._Page.go))                          # and before every navigation


if __name__ == "__main__":
    unittest.main(verbosity=1)
