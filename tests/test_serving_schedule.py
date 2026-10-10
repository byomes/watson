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


TODAY = date(2026, 10, 8)  # Thursday; coming Sunday is Oct 11

POSITIVE = [
    "who is serving this coming Sunday?", "who's serving this week", "whos serving sunday", "who is serving Sunday",
    "who is volunteering this Sunday", "who's volunteering this weekend", "who is on the schedule this Sunday",
    "who's on the schedule for Sunday", "who is on the roster this week", "show me the serving roster for Sunday",
    "list everyone serving this Sunday", "tell me who is serving this weekend", "give me the volunteer lineup for Sunday",
    "what's the lineup Sunday", "what is the serving schedule for this week", "who's scheduled to serve Sunday",
    "who is scheduled this Sunday", "who all is serving on Sunday", "can you tell me who's serving this Sunday",
    "who is serving at church this Sunday", "who's working Sunday", "who's helping this Sunday", "who's on duty this Sunday",
    "who is on for Sunday", "who's on this Sunday", "Who is serving next Sunday?", "who's serving next week",
    "who is serving in two weeks", "who is serving on October 18", "who is serving on the 18th of October", "who's serving 10/18", "who is serving the Sunday after next",
    "who's serving today", "who is serving on the worship team this Sunday", "who is on the worship team Sunday",
    "who's running sound this Sunday", "who is leading worship this Sunday", "who's on slides Sunday", "who's on camera this week",
    "who is greeting this Sunday", "who's ushering Sunday", "who is in the nursery this Sunday", "who has nursery this week",
    "who's doing kids this Sunday", "who is counting the offering this Sunday", "who's singing this Sunday", "who is playing guitar Sunday",
    "who's on hospitality this Sunday", "who is on the tech team this Sunday", "who's on security this Sunday", "who's parking cars this Sunday",
    "who is serving at the 10am service", "who's serving pre-service Sunday", "what positions are open this Sunday",
    "which volunteer spots are unfilled Sunday", "where do we need volunteers this Sunday", "do we need volunteers this Sunday",
    "are there open serving spots this week", "what roles still need filling this Sunday", "who is missing from the schedule this Sunday",
    "how many people are serving this Sunday", "any open positions on Sunday", "is anyone serving in the nursery this Sunday",
    "Who’s serving this Sunday?", "WHO IS SERVING THIS SUNDAY", "hey, who is serving this sunday?", "please show who's serving this week",
]

NEGATIVE = [
    "how many attended church last Sunday", "who is serving as Bill's deacon", "how long has Pat served", "who is Pat Lee",
    "what is the sermon this Sunday", "who registered for men's fraternity", "how many people are serving on the worship team",
    "who attended Sunday", "what's scheduled this Sunday", "what time is the service Sunday", "is the office working Sunday",
    "who is preaching this Sunday", "who is speaking Sunday", "what is the sermon series this week", "who is visiting this Sunday",
    "who needs prayer this Sunday", "how many guests came last Sunday", "who is the pastor", "what is on the calendar this week",
    "who's birthday is this week", "when does the nursery open on Sunday", "who is in Bill Crook's deacon group", "who has the most attendance",
    "who is on the worship team", "who's serving in the nursery",
]


def test_many_phrasings_are_recognised(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "DB_PATH", _db(tmp_path))
    missed = [q for q in POSITIVE if ss.answer(q, today=TODAY) is None]
    assert not missed, missed


def test_collisions_fall_through(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "DB_PATH", _db(tmp_path))
    caught = [q for q in NEGATIVE if ss.answer(q, today=TODAY) is not None]
    assert not caught, caught


def test_filters_and_open_view(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "DB_PATH", _db(tmp_path))
    r = ss.answer("who's on hospitality this Sunday", today=TODAY)
    assert "Usher: Sam Roe" in r and "Worship Leader" not in r
    r = ss.answer("what positions are open this Sunday", today=TODAY)
    assert "Guitarist: open" in r and "Usher: Sam Roe (needs 1 more)" in r and "Worship Leader" not in r
    assert "don't have a volunteer schedule" in ss.answer("what positions are open next Sunday", today=TODAY)


def test_target_dates():
    t = TODAY
    d = ss._target_date
    assert d("this week", t) == date(2026, 10, 11) and d("next week", t) == date(2026, 10, 18)
    assert d("tomorrow", t) == date(2026, 10, 9) and d("in two weeks", t) == date(2026, 10, 18)
    assert d("last sunday", t) == date(2026, 10, 4) and d("10/18", t) == date(2026, 10, 18)
    assert d("this sunday", date(2026, 10, 11)) == date(2026, 10, 11)   # on Sunday itself, 'this Sunday' is today
    assert d("last sunday", date(2026, 10, 11)) == date(2026, 10, 4)
