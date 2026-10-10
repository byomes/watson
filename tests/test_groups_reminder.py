import sqlite3
from datetime import date

from jobs.congregation import groups_reminder as gr

SER = "Small Groups|Remix Sunday Morning Group"


def _db(tmp_path, attended):
    p = tmp_path / f"c_{int(attended)}_{len(list(tmp_path.iterdir()))}.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE group_attendance (series TEXT, event_date TEXT, member_id INTEGER)")
    c.execute("CREATE TABLE group_counts (series TEXT, event_date TEXT, guests INTEGER, headcount INTEGER)")
    if attended:
        c.execute("INSERT INTO group_attendance VALUES (?,?,?)", (SER, "2026-10-04", 1))
    c.commit()
    c.close()
    return str(p)


def _patch(monkeypatch, tmp_path, attended):
    monkeypatch.setattr(gr, "CONGREGATION_DB", _db(tmp_path, attended))
    monkeypatch.setattr(gr, "_series_list", lambda: [{"series": SER, "title": "Remix Sunday Morning Group", "counts_only": False}])
    monkeypatch.setattr(gr, "_session_dates", lambda s: ["2026-10-04", "2026-09-27"])


def test_unrecorded_session_is_due_once(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, attended=False)
    d = gr.due(date(2026, 10, 5))                       # Monday after the Sunday session
    assert d == {470: [(SER, "Remix Sunday Morning Group", "2026-10-04")]}
    with __import__("sqlite3").connect(gr.CONGREGATION_DB) as c:
        c.execute("INSERT INTO group_reminders_sent (series, event_date, person_id) VALUES (?,?,?)", (SER, "2026-10-04", 470))
    assert gr.due(date(2026, 10, 5)) == {}               # already reminded


def test_recorded_or_stale_session_is_not_due(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, attended=True)
    assert gr.due(date(2026, 10, 5)) == {}               # attendance entered
    _patch(monkeypatch, tmp_path, attended=False)
    assert gr.due(date(2026, 10, 12)) == {}              # a week old: no more nagging
    assert gr.due(date(2026, 10, 4)) == {}               # same day as the session: the daily run waits for tomorrow
    assert gr.due(date(2026, 10, 4), include_today=True)  # the Sunday-afternoon run reminds the same day


def test_message_text():
    one = gr.message([(SER, "Shift Young Adult Group", "2026-10-04")])
    assert "Shift Young Adult Group (Sunday, October 4)" in one and one.endswith("tab=groups")
    two = gr.message([(SER, "A", "2026-10-04"), (SER, "B", "2026-10-04")])
    assert "- A (" in two and "- B (" in two and "—" not in one + two
