#!/usr/bin/env python3
"""Regression tests for event-signup routing in team chat. Uses a FIXTURE watson.db, so it does not depend on which events are live, and
the model call (_generate) is stubbed to FAIL the test if reached, so it never spends an LLM call. No Telegram, no Subsplash.
Run: python tests/signup_routing_regression_test.py

History these guard:
  * 2026-10-06 (5a96dc4): 'signed up' added to the attendance-count phrases -> every signup question answered with last Sunday's attendance.
  * 2026-10-06 after the calendar/Subsplash import: "men's fraternity" silently answered about the tracked Billiards Outing and skipped the
    weekly Bible Study; "<event> tomorrow night" made tracked events look untracked; "Servant Leaders Banquet" tripped the 'serv...' exclusion;
    digit-leading names ("5th Sunday Potluck") and "coming to" fell through to the model; the Subsplash route said Billiards had 0 signed up
    while the tracked record had 5."""
import logging
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.disable(logging.CRITICAL)
import core.database as cdb  # noqa: E402
from jobs.analytics import data_chat as d  # noqa: E402
from jobs.church_calendar import chat as cc  # noqa: E402
from jobs.events import pattern_match as pm  # noqa: E402

FIX = Path(tempfile.mkdtemp()) / "fixture.db"


def build_fixture(paused="1"):
    c = sqlite3.connect(FIX)
    c.executescript("""
    DROP TABLE IF EXISTS church_events; DROP TABLE IF EXISTS event_registrations; DROP TABLE IF EXISTS church_calendar_events;
    DROP TABLE IF EXISTS subsplash_event_regs; DROP TABLE IF EXISTS subsplash_registrations; DROP TABLE IF EXISTS system_settings;
    CREATE TABLE church_events (id INTEGER PRIMARY KEY, event_name TEXT, start_date TEXT, event_time TEXT, tracking_active INTEGER DEFAULT 1);
    CREATE TABLE event_registrations (id INTEGER PRIMARY KEY, event_id INTEGER, first_name TEXT, last_name TEXT, num_tickets INTEGER DEFAULT 1, extra_fields TEXT);
    CREATE TABLE church_calendar_events (title TEXT, start_date TEXT);
    CREATE TABLE subsplash_event_regs (event_uuid TEXT PRIMARY KEY, title TEXT, start_date TEXT, calendar TEXT, has_form INTEGER, registered INTEGER);
    CREATE TABLE subsplash_registrations (event_uuid TEXT, first_name TEXT, last_name TEXT, tickets INTEGER DEFAULT 1);
    CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT);
    INSERT INTO church_events VALUES (1, 'Men''s Fraternity Billiards Outing', '2099-11-04', NULL, 1), (2, 'Hayride and Bonfire', NULL, NULL, 1),
                                     (3, 'Servant Leaders Banquet', '2099-11-07', NULL, 1), (4, 'Church Picnic', '2020-10-04', NULL, 1);
    INSERT INTO event_registrations VALUES (1,1,'Aaron','Harper',1,NULL),(2,1,'Tom','Thomas',1,NULL),(3,2,'Mel','Yomes',2,NULL),(4,4,'Pat','Lee',1,NULL);
    INSERT INTO church_calendar_events VALUES ('Men''s Fraternity Billiards Outing','2099-11-04'),('Men''s Fraternity Bible Study','2099-10-07'),
        ('Men''s Breakfast','2099-10-17'),('Hayride and Bonfire','2099-10-10'),('5th Sunday Potluck','2099-11-29'),('Remix Youth Group','2099-10-11');
    INSERT INTO subsplash_event_regs VALUES ('u1','Men''s Fraternity Billiards Outing','2099-11-04','Special Events',1,0),
        ('u2','Men''s Fraternity Bible Study','2099-10-07','Small Groups',1,4);
    INSERT INTO subsplash_registrations VALUES ('u2','Zed','Zimmer', 4);
    """)
    c.execute("INSERT INTO system_settings VALUES ('subsplash_registrations_paused', ?)", (paused,))
    c.commit()
    c.close()


def conn_factory():
    c = sqlite3.connect(FIX)
    c.row_factory = sqlite3.Row
    return c


class Base(unittest.TestCase):
    paused = "1"

    def setUp(self):
        build_fixture(self.paused)
        self._orig = (pm.DB_PATH, d.WATSON_DB_PATH, dict(d._DB_PATH), cdb.get_connection, d._generate)
        pm.DB_PATH = str(FIX)
        d.WATSON_DB_PATH = str(FIX)
        d._DB_PATH["events"] = str(FIX)
        cdb.get_connection = conn_factory
        d._generate = lambda *a, **k: self.fail("a question reached the LLM")
        d._pending_clarifications.clear()

    def tearDown(self):
        pm.DB_PATH, d.WATSON_DB_PATH, d._DB_PATH, cdb.get_connection, d._generate = self._orig

    def ask(self, q, who="Bill Yomes"):
        """Route like bot.py: church-event route first, then the data chat."""
        return cc.answer(q) or d.answer_data_question(q, who)[1]


class Paused(Base):
    paused = "1"

    def test_no_signup_question_ever_reaches_attendance(self):
        for q in ("How many people are signed up for the men's billiard event", "how many men are signed up for the billiards event",
                  "How many people are registered for the church picnic?", "How many people are signed up for Men's Fraternity tomorrow night"):
            self.assertIsNone(d._try_pattern_match(q), q)
            self.assertNotIn("We saw a total", self.ask(q))

    def test_tracked_events_answer_with_numbers(self):
        self.assertEqual(self.ask("how many men are signed up for the billiards event"), "2")
        self.assertEqual(self.ask("How many people are signed up for the men's billiard event"), "2")
        self.assertEqual(self.ask("how many signed up for hayride tomorrow night"), "2")           # time words do not hide a tracked event
        self.assertEqual(self.ask("how many are going to hayride tomorrow night"), "2")            # coming/going with an explicit event
        r = self.ask("who is signed up for the billiards event?")
        self.assertIn("Aaron Harper", r)
        self.assertIn("Tom Thomas", r)

    def test_ambiguous_name_asks_which_instead_of_picking(self):
        for q in ("how many are signed up for men's fraternity", "How many people are signed up for Men's Fraternity tomorrow night", "how many signed up for the frat"):
            r = self.ask(q)
            self.assertIn("Which one do you mean", r, q)
            self.assertIn("Bible Study", r)
            self.assertIn("Billiards Outing", r)
            self.assertIn("I have signup numbers for Men's Fraternity Billiards Outing", r)

    def test_untracked_calendar_event_is_named_honestly(self):
        r = self.ask("How many are coming to the 5th Sunday Potluck")                              # digit-leading name
        self.assertIn("I don't have signup numbers for 5th Sunday Potluck", r)
        self.assertIn("Hayride and Bonfire", r)                                                    # lists what IS tracked
        self.assertIn("Men's Breakfast", self.ask("how many are signed up for men's breakfast"))

    def test_banquet_is_an_event_not_a_serving_question(self):
        self.assertEqual(self.ask("who is signed up for the servant leaders banquet"), "Nobody has signed up for Servant Leaders Banquet yet.")
        self.assertEqual(self.ask("how many are signed up for the servant leaders banquet"), "0")
        self.assertIsNone(d._untracked_signup_reply("who is signed up to serve on Sunday"))
        self.assertIsNone(d._untracked_signup_reply("how many volunteers are signed up for nursery"))

    def test_when_is_a_tracked_event_not_on_the_calendar(self):
        import datetime
        wd = datetime.date(2099, 11, 7).strftime("%a")
        self.assertEqual(self.ask("when is the servant leaders banquet"), f"Servant Leaders Banquet is {wd}, Nov 7")
        self.assertEqual(self.ask("what time is the servant leaders banquet"), f"Servant Leaders Banquet is {wd}, Nov 7")
        self.assertEqual(pm_info("when is hayride and bonfire"), "Hayride and Bonfire is (date not set)")   # no date on file: say so


    def test_statements_are_not_questions(self):
        # Real messages from the log: introductions/announcements that merely CONTAIN "registrations" must never get a signup-numbers reply.
        for q in ("Hi Watson, this is Kaci. I handle digital communications and event registrations for Catalyst.",
                  "Hi Watson. I just created an event called Hayride and Bonfire for you to track registrations. I will be sending the link."):
            self.assertIsNone(d._untracked_signup_reply(q), q)

    def test_custom_form_questions_still_go_to_the_model(self):
        # "dessert"/"side dish" are answers on the picnic's own sign-up form: the fast path bails and the MODEL must filter (Tara, 2026-09-20).
        for q in ("How many people are signed up for dessert for picnic", "How many people are signed up for the picnic on October 4 and who many of those are signed up for side dish"):
            self.assertIsNone(d._untracked_signup_reply(q), q)
            info = pm.event_candidates(q)
            self.assertEqual([c["title"] for c in info["candidates"]], ["Church Picnic"], q)

    def test_plain_wording_when_signups_are_stored_but_paused(self):
        q = "Who is registered for Men's Fraternity Bible Study tomorrow night"
        bill = self.ask(q, "Bill Yomes")
        self.assertIn("I have a copy of the Subsplash signups for Men's Fraternity Bible Study", bill)
        self.assertIn("paused", bill)
        self.assertNotIn("4", bill.replace("Men's", ""))                                           # the stored number is NOT given while paused
        jim = self.ask(q, "Jim Bouchat")
        self.assertIn("I can't give you signup numbers for Men's Fraternity Bible Study right now", jim)
        self.assertNotIn("paused", jim)                                                            # internal reason stays with Bill
        self.assertNotIn("Subsplash", jim)
        self.assertNotIn("Bill Crook", d._BILL_NAMES)                                              # Bill Crook is not Bill Yomes
        self.assertEqual((d._untracked_signup_reply(q, "Bill Crook") or "").count("paused"), 0)
        self.assertIn("I don't have signup numbers for 5th Sunday Potluck", self.ask("how many are signed up for the 5th sunday potluck"))   # no stored copy

    def test_which_one_follow_ups(self):
        ask = "Who is signed up for men's fraternity tomorrow night"
        for pick, expect in (("Men's Frat Bible Study", "copy of the Subsplash signups for Men's Fraternity Bible Study"), ("bible study", "Bible Study"),
                             ("billiards", "Aaron Harper"), ("the billiards one", "Aaron Harper"), ("the second one", "Men's Fraternity Bible Study"),
                             ("first", "Aaron Harper"), ("2", "Men's Fraternity Bible Study")):
            d._pending_clarifications.clear()
            self.assertIn("Which one do you mean", self.ask(ask))
            ok, reply = d.answer_data_question(pick, "Bill Yomes")
            self.assertTrue(ok, pick)
            self.assertIn(expect, reply, pick)
            self.assertNotIn("didn't turn up", reply)
            self.assertNotIn("Bill Yomes", d._pending_clarifications)                              # consumed

    def test_still_ambiguous_keeps_waiting_then_resolves(self):
        self.ask("how many are signed up for men's fraternity")
        ok, reply = d.answer_data_question("men's fraternity", "Bill Yomes")
        self.assertIn("Still more than one", reply)
        ok, reply = d.answer_data_question("billiards", "Bill Yomes")
        self.assertEqual(reply, "2")

    def test_follow_up_is_per_asker_and_expires(self):
        self.ask("how many are signed up for men's fraternity", "Bill Yomes")
        self.assertIsNone(d._try_resolve_pending_clarification("Jim Bouchat", "billiards"))        # someone else's reply never resolves Bill's question
        self.assertIn("Bill Yomes", d._pending_clarifications)
        d._pending_clarifications["Bill Yomes"]["asked_at"] -= d._PENDING_CLARIFICATION_TTL_SECONDS + 1
        self.assertIsNone(d._try_resolve_pending_clarification("Bill Yomes", "billiards"))         # expired
        self.assertNotIn("Bill Yomes", d._pending_clarifications)

    def test_a_new_question_or_cancel_clears_the_pending_choice(self):
        self.ask("how many are signed up for men's fraternity")
        self.assertIsNone(d._try_resolve_pending_clarification("Bill Yomes", "How many people attended church today?"))
        self.assertNotIn("Bill Yomes", d._pending_clarifications)
        self.ask("how many are signed up for men's fraternity")
        self.assertEqual(d.answer_data_question("never mind", "Bill Yomes"), (True, "Okay, never mind."))
        self.assertNotIn("Bill Yomes", d._pending_clarifications)

    def test_pending_choices_are_size_capped(self):
        for i in range(d._PENDING_CLARIFICATION_MAX_ENTRIES + 25):
            d._remember_pending_event_choice(f"asker{i}", "q", "p", ["A", "B"])
        self.assertLessEqual(len(d._pending_clarifications), d._PENDING_CLARIFICATION_MAX_ENTRIES)

    def test_pick_and_rewrite_helpers(self):
        titles = ["Men's Fraternity Billiards Outing", "Men's Fraternity Bible Study"]
        self.assertEqual(d._event_pick("Men's Frat Bible Study", titles), [1])
        self.assertEqual(d._event_pick("billiard", titles), [0])
        self.assertEqual(d._event_pick("men's fraternity", titles), [0, 1])
        self.assertEqual(d._event_pick("how many attended church today", titles), [])
        self.assertEqual(d._event_pick("third", titles), [])                                       # only two were offered
        self.assertEqual(d._question_with_event("Who is signed up for men's fraternity tomorrow night", "men's fraternity", titles[1]),
                         "Who is signed up for Men's Fraternity Bible Study tomorrow night")
        self.assertEqual(d._question_with_event("Who is signed up for the frat", "men's fraternity", titles[0]),
                         "Who is signed up for the frat for Men's Fraternity Billiards Outing")  # phrase not found: title appended as the subject

    def test_attendance_wording_untouched(self):
        for q in ("How many people attended church today?", "How many people came to church last Sunday?"):
            self.assertIsNotNone(d._try_pattern_match(q), q)
        self.assertIsNone(d._untracked_signup_reply("how many are coming to church on Sunday"))   # attendance wording, not an event
        self.assertIsNone(pm.pattern_match("how many are coming to church on Sunday"))
        self.assertIsNone(pm._resolve_event_id("how many are registered for church"))             # empty phrase never matches everything


class Unpaused(Base):
    paused = "0"

    def test_tracked_numbers_beat_the_subsplash_copy(self):
        r = self.ask("who is signed up for the billiards event")
        self.assertIn("2 people signed up", r)                                                            # tracked record, not Subsplash's 0
        self.assertIn("Aaron Harper", r)

    def test_men_fraternity_lists_both_events(self):
        r = self.ask("how many are signed up for men's fraternity")
        self.assertIn("Men's Fraternity Bible Study (", r)
        self.assertIn("4 people signed up", r)
        self.assertIn("Billiards Outing", r)
        self.assertIn("2 people signed up", r)


def pm_info(q):
    sql = pm.pattern_match(q)
    c = sqlite3.connect(FIX)
    return c.execute(sql).fetchone()[0]


if __name__ == "__main__":
    unittest.main(verbosity=1)
