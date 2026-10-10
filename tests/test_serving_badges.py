from jobs.congregation import servants_web as sw


def test_team_matching_tokens():
    t = sw._sched_tokens
    assert t("Counter") & t("COUNTING TEAM")                       # counter ~ counting
    assert t("Responsible for Lock-up") & t("BUILDING LOCK UP")
    assert t("Nursery") & t("9AM NURSERY TEAM")
    assert t("Hospitality Team") & t("HOSPITALITY")
    assert t("Worship Team") & t("WORSHIP TEAM")
    assert not (t("Worship Team") | t("Live Sound Tech")) & t("9AM NURSERY TEAM")
    assert not t("Usher") & t("SECURITY")


def test_manual_link_beats_name_matching(tmp_path, monkeypatch):
    import sqlite3
    from jobs.congregation import fluro_schedule as fs
    p = tmp_path / "w.db"
    monkeypatch.setattr(fs, "get_connection", lambda: _conn(p))
    monkeypatch.setattr(fs, "find_member_id_by_name", lambda f, l: None)   # name matching finds nobody
    fs._bootstrap()
    with _conn(p) as c:
        c.execute("INSERT INTO fluro_member_links (fluro_contact_id, member_id) VALUES ('cX', 123)")
    ev = [{"id": "e1", "title": "T", "start": "2026-10-11T14:00:00.000Z", "end": None, "teams": [{"title": "Worship Team", "definition": "w", "slots": [
        {"title": "Vocalist", "minimum": 1, "maximum": 1, "assignments": [
            {"id": "a1", "status": "active", "confirmation": "unknown", "name": "Robert Border", "first": "Robert", "last": "Border", "contact_id": "cX"}]}]}]}]
    fs.store(ev)
    with _conn(p) as c:
        assert c.execute("SELECT member_id FROM fluro_schedule WHERE assignment_id='a1'").fetchone()[0] == 123


def _conn(path):
    import sqlite3
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c
