#!/usr/bin/env python3
"""Regression: event-signup questions must reach the EVENTS fast path, never the attendance count.
Bug 2026-10-06 (5a96dc4): 'signed up' was added to the attendance-count phrase list, so "how many people are signed up for X"
answered with last Sunday's attendance. Also: an untracked event's signup question must get an honest answer, not a model-guessed
query. The model call (_generate) is stubbed to FAIL the test if reached, so this never spends an LLM call.
Run: python tests/signup_routing_regression_test.py"""
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.disable(logging.CRITICAL)
from jobs.analytics import data_chat as d  # noqa: E402

SIGNUP_QS = ["How many people are signed up for the men's billiard event", "who is signed up for the billiards event?",
             "how many men are signed up for the billiards event", "How many people are registered for the church picnic?"]


class T(unittest.TestCase):
    def test_signup_questions_never_hit_attendance_fast_path(self):
        # "who is signed up..." trips cdb_query's 'who is' NAME lookup, which finds nobody and falls through (by design,
        # see answer_data_question); the full-flow test below covers it. Every other phrasing must not match at all.
        for q in SIGNUP_QS + ["How many people are signed up for Men's Fraternity tomorrow night"]:
            if q.lower().startswith("who is"):
                continue
            self.assertIsNone(d._try_pattern_match(q), q)

    def test_who_is_signed_up_reaches_events_not_attendance(self):
        orig = d._generate
        d._generate = lambda *a, **k: self.fail("reached the LLM")
        try:
            ok, reply = d.answer_data_question("who is signed up for the billiards event?", "Bill Yomes")
        finally:
            d._generate = orig
        self.assertTrue(ok)
        self.assertNotIn("We saw a total", reply)
        self.assertNotIn("don't have signup numbers", reply)

    def test_tracked_events_hit_events_fast_path(self):
        for q in SIGNUP_QS:
            sql = d._try_pattern_match_events(q)
            self.assertTrue(sql and "event_registrations" in sql, q)

    def test_real_attendance_questions_still_work(self):
        for q in ("How many people attended church today?", "How many people came to church last Sunday?", "what was total attendance last week"):
            self.assertIsNotNone(d._try_pattern_match(q), q)

    def test_untracked_event_gets_honest_reply_without_llm(self):
        orig = d._generate
        d._generate = lambda *a, **k: self.fail("signup question reached the LLM")
        try:
            ok, reply = d.answer_data_question("How many people are signed up for Men's Fraternity tomorrow night", "Bill Yomes")
        finally:
            d._generate = orig
        self.assertTrue(ok)
        self.assertIn("don't have signup numbers", reply)
        self.assertNotIn("128", reply)                                                    # not an attendance count
        self.assertNotIn("error", reply.lower())

    def test_serving_signup_words_are_not_swallowed(self):
        self.assertIsNone(d._untracked_signup_reply("who is signed up to serve on Sunday"))
        self.assertIsNone(d._untracked_signup_reply("how many volunteers are signed up for nursery"))
        self.assertIsNone(d._untracked_signup_reply("how many people attended church today"))

    def test_tracked_event_answers_from_events_not_the_guard(self):
        orig = d._generate
        d._generate = lambda *a, **k: self.fail("tracked event reached the LLM")
        try:
            ok, reply = d.answer_data_question("how many men are signed up for the billiards event", "Bill Yomes")
        finally:
            d._generate = orig
        self.assertTrue(ok)
        self.assertNotIn("don't have signup numbers", reply)
        self.assertNotIn("128", reply)


if __name__ == "__main__":
    unittest.main(verbosity=1)
