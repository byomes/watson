import sqlite3

from jobs.events import matching as m


def _db(tmp_path, monkeypatch):
    p = tmp_path / "c.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE members (id INTEGER PRIMARY KEY, name TEXT, active TEXT)")
    c.executemany("INSERT INTO members VALUES (?,?,?)", [
        (1, "Dorothy Johnson", "active"), (2, "Eric Johnson", "active"), (3, "Rob Border", "active"), (4, "Robbie Border", "active"),
        (5, "Fred Palmer", "active"), (6, "Letha Palmer", "active"), (7, "Dr. Bill Yomes", "active"), (8, "Dorothy Gone", "deceased"),
        (9, "William Smith", "active"), (10, "Willa Smith", "active")])
    c.commit(); c.close()
    monkeypatch.setattr(m, "CONG_DB", str(p))


def test_nickname_fallback(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    assert m.find_member_id_by_name("Dottie", "Johnson") == 1       # nickname of Dorothy
    assert m.find_member_id_by_name("Dot", "Johnson") == 1
    assert m.find_member_id_by_name("Bill", "Yomes") == 7           # via Dr. + nickname table
    assert m.find_member_id_by_name("Thee Fred", "Palmer") == 5      # multi-word first name
    assert m.find_member_id_by_name("Bill", "Smith") == 9


def test_stays_strict(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    assert m.find_member_id_by_name("Robert", "Border") is None      # Rob and Robbie both fit: ambiguous, no guess
    assert m.find_member_id_by_name("Dottie", "Gone") is None        # deceased members are not nickname-matched
    assert m.find_member_id_by_name("Dottie", "Nobody") is None
    assert m.find_member_id_by_name("Eric", "Johnson") == 2          # exact path unchanged
