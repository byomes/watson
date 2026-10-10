from jobs.analytics import group_compare as gc

SERIES = [
    {"series": "Small Groups|Men's Fraternity Bible Study", "title": "Men's Fraternity Bible Study", "counts_only": False},
    {"series": "Special Events|Men's Fraternity Billiards Outing", "title": "Men's Fraternity Billiards Outing", "counts_only": False},
    {"series": "Special Events|The Names of God", "title": "The Names of God", "counts_only": False},
]


def test_pick_series(monkeypatch):
    monkeypatch.setattr(gc, "_series_list", lambda: SERIES)
    assert gc._pick_series("men's frat bible study registered")[0]["title"] == "Men's Fraternity Bible Study"
    s, cands = gc._pick_series("who registered for men's fraternity")
    assert s is None and len(cands) == 2
    assert gc._pick_series("names of god sign ups")[0]["title"] == "The Names of God"
    assert gc._pick_series("church picnic")[1] == []


def test_needs_both_words(monkeypatch):
    monkeypatch.setattr(gc, "_series_list", lambda: SERIES)
    assert gc.answer("who attended men's fraternity bible study") is None   # attendance only: other routes
    assert gc.answer("who registered for men's fraternity bible study") is None  # registration only: signup route
