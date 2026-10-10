import sqlite3
from datetime import date

from jobs.analytics import serving_schedule as ss


def _db(tmp_path):
    p = tmp_path / "w.db"
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE fluro_schedule_events (event_id TEXT PRIMARY KEY, title TEXT, start_utc TEXT, end_utc TEXT, pulled_at TEXT);
    CREATE TABLE fluro_schedule_slots (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, team TEXT, role TEXT, minimum INT, maximum INT, filled INT);
    CREATE TABLE fluro_schedule (assignment_id TEXT PRIMARY KEY, event_id TEXT, team TEXT, team_definition TEXT, role TEXT, volunteer_name TEXT,
                                 fluro_contact_id TEXT, confirmation TEXT, member_id INT);
    INSERT INTO fluro_schedule_events VALUES ('e1','Catalyst Sunday 10AM','2026-10-11T14:00:00.000Z',NULL,'2026-10-10 10:25:00');
    INSERT INTO fluro_schedule_slots (event_id,team,role,minimum,maximum,filled) VALUES
      ('e1','Worship Team','Worship Leader',1,1,1),('e1','Worship Team','Guitarist',1,1,0),('e1','Hospitality Team','Usher',2,4,1);
    INSERT INTO fluro_schedule VALUES ('a1','e1','Worship Team','worshipTeam','Worship Leader','Pat Lee','c1','confirmed',1),
                                      ('a2','e1','Hospitality Team','hospitalityTeam','Usher','Sam Roe','c2','unknown',2);
    """)
    c.commit(); c.close()
    return p


def test_answers_coming_sunday(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "DB_PATH", _db(tmp_path))
    r = ss.answer("who is serving this coming Sunday?", today=date(2026, 10, 8))
    assert "Sunday, October 11" in r and "Worship Leader: Pat Lee" in r
    assert "Guitarist: open" in r and "Usher: Sam Roe (needs 1 more)" in r and "1 of 2 confirmed" in r
    assert ss.answer("who's serving this week", today=date(2026, 10, 8)) == r


def test_missing_date(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "DB_PATH", _db(tmp_path))
    assert "don't have a volunteer schedule" in ss.answer("who is serving next Sunday", today=date(2026, 10, 8))


def test_falls_through_on_other_questions():
    for q in ["how many attended church last Sunday", "who is serving as Bill's deacon", "who served last Sunday",
              "how long has Pat served", "who is Pat Lee", "what is the sermon this Sunday", "who registered for men's fraternity",
              "how many people are serving on the worship team", "who attended Sunday"]:
        assert ss.answer(q) is None, q


def test_no_table_falls_through(tmp_path, monkeypatch):
    p = tmp_path / "empty.db"; sqlite3.connect(p).close()
    monkeypatch.setattr(ss, "DB_PATH", p)
    assert ss.answer("who is serving this Sunday", today=date(2026, 10, 8)) is None
