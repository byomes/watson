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
