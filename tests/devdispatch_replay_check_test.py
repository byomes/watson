#!/usr/bin/env python3
"""Tests for the replay check on the devdispatch AUTO-MERGE path (jobs/devdispatch/poller.py::_replay_check and its wiring in
_auto_merge_and_deploy). Backstory: a dispatched fix that touches only jobs/skills/cdb_query.py was auto-merged and deployed with no behavioural
check, the same hole that let 'signed up' reroute every signup question. No GitHub, no Telegram, no LLM: PR contents are faked, GitHub/Telegram/merge
are stubbed. The replay itself is REAL: a subprocess runs the real cdb_query matcher over the real logged questions.
Run: python tests/devdispatch_replay_check_test.py"""
import json
import logging
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
logging.disable(logging.CRITICAL)
import signup_routing_regression_test as fx  # noqa: E402  (fixture events DB)
from jobs.analytics import fast_path_patcher as fp  # noqa: E402
from jobs.analytics import fast_path_validate as v  # noqa: E402
from jobs.devdispatch import poller  # noqa: E402

TITLES = ["Men's Fraternity Billiards Outing", "Men's Fraternity Bible Study", "Men's Breakfast", "Hayride and Bonfire", "Servant Leaders Banquet", "Church Picnic"]
BEFORE = """
def _last_sunday(): return '2026-10-04'
def _pattern_match(q, last_sun, weeks):
    q = q.lower()
    if 'how many attended' in q: return 'SELECT 1'
    return None
"""
EX = "what was the head count sunday?"
CORPUS = ["how many attended church today", "who is coming to the hayride", "when is the servant leaders banquet"]


def after(extra: str, drop_attended=False, reroute=False) -> str:
    src = BEFORE
    if drop_attended:
        src = src.replace("    if 'how many attended' in q: return 'SELECT 1'\n", "")
    if reroute:
        src = src.replace("'SELECT 1'", "'SELECT 99'")
    return src.replace("    return None", extra + "\n    return None")


class Compare(unittest.TestCase):
    def cmp(self, new_text, example=EX):
        return v.compare_sources(BEFORE, new_text, example, corpus_questions=CORPUS, titles=TITLES)

    def test_clean_fix_passes(self):
        r = self.cmp(after("    if 'head count sunday' in q: return 'SELECT 2'"))
        self.assertTrue(r["ok"], v.format_reasons(r))
        self.assertEqual(r["changed_others"], [])

    def test_change_that_fixes_nothing(self):
        r = self.cmp(after("    if 'zebra' in q: return 'SELECT 2'"))
        self.assertIn("fixes nothing", v.format_reasons(r))

    def test_hijack_of_an_event_question(self):
        r = self.cmp(after("    if 'sunday' in q or 'hayride' in q: return 'SELECT 2'"))
        self.assertFalse(r["ok"])
        self.assertIn("hijack", v.format_reasons(r))

    def test_reroute_and_break_are_caught(self):
        self.assertIn("change the answer", v.format_reasons(self.cmp(after("    if 'head count sunday' in q: return 'SELECT 2'", reroute=True))))
        self.assertIn("STOP answering", v.format_reasons(self.cmp(after("    if 'head count sunday' in q: return 'SELECT 2'", drop_attended=True))))

    def test_file_that_does_not_load(self):
        r = self.cmp(BEFORE + "\nraise RuntimeError('boom')\n")
        self.assertFalse(r["ok"])
        self.assertIn("did not load", v.format_reasons(r))

    def test_event_question_as_example_is_refused(self):
        r = self.cmp(after("    if 'signed up' in q: return 'SELECT 2'"), example="how many are signed up for the hayride")
        self.assertIn("event/signup question", v.format_reasons(r))


class Cli(unittest.TestCase):
    def test_subprocess_entry_point_prints_one_json_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            b, a = Path(tmp) / "b.py", Path(tmp) / "a.py"
            b.write_text(BEFORE)
            a.write_text(after("    if 'head count sunday' in q: return 'SELECT 2'"))
            p = subprocess.run([sys.executable, "-m", "jobs.analytics.fast_path_validate", "--replay", str(b), str(a), "--example", EX],
                               cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True, timeout=120)
        res = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["example_after"] and not res["example_before"])


class PollerReplay(unittest.TestCase):
    """_replay_check with a faked PR file: the real subprocess, the real matcher, the real logged questions."""

    def setUp(self):
        self._o = (poller._fetch_pr_file, poller._example_for_suggestion)

    def tearDown(self):
        poller._fetch_pr_file, poller._example_for_suggestion = self._o

    def run_check(self, pr_source, example):
        poller._fetch_pr_file = lambda url, path: pr_source
        poller._example_for_suggestion = lambda sid: example
        return poller._replay_check("https://github.com/o/r/pull/1", 7)

    def test_good_phrase_pr_passes(self):
        src = fp.build_patched_text("how_many_attended", "how many were in the building")[2]
        ok, detail = self.run_check(src, "How many were in the building last Sunday?")
        self.assertTrue(ok, detail)

    def test_the_incident_pr_is_held(self):
        src = fp.build_patched_text("how_many_attended", "signed up")[2]
        ok, detail = self.run_check(src, "how many people are signed up for men's fraternity tomorrow night")
        self.assertFalse(ok)
        self.assertIn("event/signup question", detail)

    def test_pr_that_hijacks_logged_questions_is_held(self):
        # a phrase that is fine as an example but so broad it grabs other logged questions
        src = fp.build_patched_text("how_many_attended", "church")[2]
        ok, detail = self.run_check(src, "How many people were at church last Sunday?")
        self.assertFalse(ok)

    def test_pr_that_crashes_on_import_is_held(self):
        ok, detail = self.run_check(fp.CDB_QUERY_PATH.read_text() + "\nraise RuntimeError('boom')\n", "How many attended church today?")
        self.assertFalse(ok)
        self.assertIn("did not load", detail)

    def test_pr_identical_to_main_is_held(self):
        ok, detail = self.run_check(fp.CDB_QUERY_PATH.read_text(), "How many people attended church today?")
        self.assertFalse(ok)

    def test_fails_closed_without_example_or_file(self):
        poller._example_for_suggestion = lambda sid: None
        poller._fetch_pr_file = lambda url, path: "x = 1"
        self.assertFalse(poller._replay_check("u", 1)[0])
        poller._example_for_suggestion = lambda sid: "a question"
        poller._fetch_pr_file = lambda url, path: None
        ok, detail = poller._replay_check("u", 1)
        self.assertFalse(ok)
        self.assertIn("could not fetch", detail)


class MergeWiring(unittest.TestCase):
    """_auto_merge_and_deploy: a failed replay must block the merge, clear auto_merge, tell Bill and record needs_review."""

    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "t.db"
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE claude_code_jobs (id INTEGER PRIMARY KEY, auto_merge INTEGER)")
        c.execute("INSERT INTO claude_code_jobs VALUES (5, 1)")
        c.commit()
        c.close()
        self.calls = {"merge": 0, "tg": [], "outcome": []}
        names = ["_get_job_row", "_is_lookup_only_pr", "_replay_check", "_telegram", "_record_suggestion_outcome", "_merge_claude_code_job", "get_connection", "_log_auto_fix"]
        self._orig = {n: getattr(poller, n) for n in names}
        poller._get_job_row = lambda jid: {"auto_merge": 1, "source_suggestion_id": 9, "repo": "wcky", "pr_url": "https://github.com/o/r/pull/1"}
        poller._is_lookup_only_pr = lambda url: True
        poller._telegram = lambda text: self.calls["tg"].append(text)
        poller._record_suggestion_outcome = lambda sid, status, detail: self.calls["outcome"].append((status, detail))
        poller._log_auto_fix = lambda *a, **k: None

        def merge(jid):
            self.calls["merge"] += 1
            return {"status": "merged", "pr_url": "u"}
        poller._merge_claude_code_job = merge
        poller.get_connection = lambda: sqlite3.connect(self.db)

    def tearDown(self):
        for n, f in self._orig.items():
            setattr(poller, n, f)

    def test_failed_replay_blocks_the_merge(self):
        poller._replay_check = lambda url, sid: (False, "it would hijack 2 question(s)")
        poller._auto_merge_and_deploy(5)
        self.assertEqual(self.calls["merge"], 0)
        self.assertEqual(len(self.calls["tg"]), 1)
        self.assertIn("replay check", self.calls["tg"][0])
        self.assertIn("hijack", self.calls["tg"][0])
        self.assertEqual(self.calls["outcome"][0][0], "needs_review")
        self.assertEqual(sqlite3.connect(self.db).execute("SELECT auto_merge FROM claude_code_jobs WHERE id=5").fetchone()[0], 0)

    def test_passed_replay_still_merges(self):
        poller._replay_check = lambda url, sid: (True, "replay check passed")
        poller._auto_merge_and_deploy(5)
        self.assertEqual(self.calls["merge"], 1)
        self.assertEqual(self.calls["outcome"][-1][0], "applied")

    def test_not_lookup_only_is_still_held_before_any_replay(self):
        poller._is_lookup_only_pr = lambda url: False
        poller._replay_check = lambda url, sid: self.fail("replay should not even run for a non-lookup PR")
        poller._auto_merge_and_deploy(5)
        self.assertEqual(self.calls["merge"], 0)
        self.assertEqual(self.calls["outcome"][0][0], "needs_review")


if __name__ == "__main__":
    unittest.main(verbosity=1)
