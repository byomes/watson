#!/usr/bin/env python3
"""Tests for jobs/analytics/fast_path_validate.py: the gate every AUTOMATIC fast-path phrase must pass (incident: 'signed up' auto-applied to the
attendance list on 2026-10-06 because the only check was ast.parse). Uses a fixture events DB; no LLM, no Telegram, nothing is ever written to
cdb_query.py (apply_and_deploy's writer is stubbed to fail the test if reached).
Run: python tests/fastpath_validation_test.py"""
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
logging.disable(logging.CRITICAL)
import signup_routing_regression_test as fx  # noqa: E402  (fixture builder for the events DB)
from jobs.analytics import fast_path_patcher as fp  # noqa: E402
from jobs.analytics import fast_path_validate as v  # noqa: E402
from jobs.events import pattern_match as pm  # noqa: E402

TITLES = ["Men's Fraternity Billiards Outing", "Men's Fraternity Bible Study", "Men's Breakfast", "Hayride and Bonfire", "Servant Leaders Banquet",
          "5th Sunday Potluck", "Church Picnic", "Celebrate Recovery"]
BAD_EXAMPLE = "How many people are signed up for men's fraternity tomorrow night"


class T(unittest.TestCase):
    def setUp(self):
        fx.build_fixture("1")
        self._pm = pm.DB_PATH
        pm.DB_PATH = str(fx.FIX)

    def tearDown(self):
        pm.DB_PATH = self._pm

    def test_the_incident_is_rejected(self):
        r = v.evaluate("how_many_attended", "signed up", BAD_EXAMPLE, corpus_questions=[], titles=TITLES)
        self.assertFalse(r["ok"])
        text = v.format_reasons(r)
        self.assertIn("events/signup domain", text)
        self.assertIn("event/signup question", text)
        self.assertIn("too generic", text)

    def test_phrase_checks(self):
        self.assertTrue(any("too generic" in p for p in v.phrase_problems("how many", TITLES)))
        self.assertTrue(any("too generic" in p for p in v.phrase_problems("who is", TITLES)))
        self.assertTrue(any("signup domain" in p for p in v.phrase_problems("people registered for", TITLES)))
        self.assertTrue(any("names an event" in p for p in v.phrase_problems("hayride attendance", TITLES)))
        self.assertTrue(any("names an event" in p for p in v.phrase_problems("banquet headcount", TITLES)))
        self.assertEqual(v.phrase_problems("how many were in the building", TITLES), [])           # an ordinary attendance phrasing is fine
        self.assertEqual(v.phrase_problems("men attended last week", TITLES), [])                  # 'men' alone is ordinary, not an event name

    def test_event_question_detection(self):
        for q in ("how many are signed up for the frat", "who has rsvp'd for the picnic", "how many tickets for trunk or treat", "when is the hayride"):
            self.assertTrue(v.looks_like_event_question(q, TITLES), q)
        for q in ("How many people attended church today?", "how many were in the building last Sunday", "who missed church last week"):
            self.assertFalse(v.looks_like_event_question(q, TITLES), q)

    def test_a_good_phrase_is_accepted(self):
        q = "How many were in the building last Sunday?"
        r = v.evaluate("how_many_attended", "how many were in the building", q, corpus_questions=["who missed church last week", "when is the hayride"], titles=TITLES)
        self.assertTrue(r["ok"], v.format_reasons(r))
        self.assertIsNone(r["example_before"])
        self.assertTrue(r["example_after"])                                                         # it really does fix the example

    def test_phrase_must_come_from_the_question(self):
        self.assertTrue(v.phrase_in_question("how many were in the building", "How many were in the building last Sunday?"))
        self.assertTrue(v.phrase_in_question("when did [name] last attend church", "When did Kerri Brown last attend church?"))
        self.assertFalse(v.phrase_in_question("this year's attendance", "How many people have come to church this year?"))        # real audit finding (#59)
        self.assertFalse(v.phrase_in_question("what's the attendance count?", "what time is church Sunday?"))                      # real audit finding (#32)
        self.assertFalse(v.phrase_in_question("", "anything"))
        r = v.evaluate("how_many_attended", "this year's attendance", "How many people have come to church this year?", corpus_questions=[], titles=TITLES)
        self.assertFalse(r["ok"])
        self.assertIn("made it up", v.format_reasons(r))

    def test_noop_and_already_present_phrases_are_rejected(self):
        r = v.evaluate("how_many_attended", "how many attended", "how many attended church today", corpus_questions=[], titles=TITLES)
        self.assertFalse(r["ok"])
        self.assertIn("already in this category", v.format_reasons(r))
        r = v.evaluate("how_many_attended", "purple monkey dishwasher", "what is the weather", corpus_questions=[], titles=TITLES)
        self.assertFalse(r["ok"])
        self.assertIn("fixes nothing", v.format_reasons(r))

    def test_hijack_of_another_route_is_rejected_by_replay(self):
        # The phrase itself looks fine; the replay is what catches that it would also fire on an event question another route owns.
        r = v.evaluate("how_many_attended", "head count sunday", "what was the head count sunday?",
                       corpus_questions=["head count sunday for the hayride", "how many attended church today"], titles=TITLES)
        self.assertFalse(r["ok"])
        self.assertTrue(any(c["claimed_elsewhere"] for c in r["changed_others"]))
        self.assertIn("hijack", v.format_reasons(r))

    def test_too_broad_phrase_is_rejected(self):
        many = [f"what was the head count sunday number {i}" for i in range(6)]
        r = v.evaluate("how_many_attended", "head count sunday", "what was the head count sunday?", corpus_questions=many, titles=TITLES)
        self.assertFalse(r["ok"])
        self.assertIn("too broad", v.format_reasons(r))

    def test_apply_and_deploy_refuses_before_writing(self):
        orig = fp.append_cdb_phrase
        fp.append_cdb_phrase = lambda *a, **k: self.fail("a rejected phrase reached the file writer")
        try:
            ok, detail = fp.apply_and_deploy("how_many_attended", "signed up", "Watson (automatic, test)", example_question=BAD_EXAMPLE)
        finally:
            fp.append_cdb_phrase = orig
        self.assertFalse(ok)
        self.assertTrue(detail.startswith("validation rejected"), detail)

    def test_patched_text_builder_is_pure(self):
        before = fp.CDB_QUERY_PATH.read_text()
        ok, msg, new_text, old_text = fp.build_patched_text("how_many_attended", "zzz test phrase for builder")
        self.assertTrue(ok, msg)
        self.assertIn("'zzz test phrase for builder'", new_text)
        self.assertEqual(old_text, before)
        self.assertEqual(fp.CDB_QUERY_PATH.read_text(), before)                                      # nothing written
        self.assertFalse(fp.build_patched_text("nope", "x y z")[0])


if __name__ == "__main__":
    unittest.main(verbosity=1)
