"""jobs/events/pattern_match.py — LLM-free fast path for common event-signup
Telegram questions (RSVP counts/lists, "what events are we tracking"),
mirroring jobs/skills/cdb_query.py's `_pattern_match` for the attendance
domain. Reused from jobs/analytics/data_chat.py's answer_data_question() —
same contract as that module's `_try_pattern_match`: returns a single-line
SELECT string for the "events" domain, or None if the question doesn't
match a recognized phrasing. The caller still runs whatever this returns
through data_chat.py's own _validate_sql() before trusting it, same as the
attendance fast path.

Built 2026-09-14 after Bill asked "who is registered for the church picnic?"
and "how many people are registered for the church picnic?" via Telegram —
both cost a real Claude API call (core/claude_tier.py, ~$0.011 each,
claude_tier_spend_log ids 84-85) because no events-domain fast path existed;
data_chat.py's existing pattern-match reuse only covers attendance.
"""
import json
import re
import sqlite3

from config.settings import DB_PATH

# Words that add nothing when matching a question against a tracked event's
# name — "the church picnic" should still resolve against an event literally
# named "Church Picnic" even if the asker drops or reorders these.
_STOPWORDS = {"church", "the", "our", "annual", "this", "year's", "years"}


def _active_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT id, event_name FROM church_events WHERE tracking_active = 1"
    ).fetchall()


def _norm(name_l: str) -> str:
    return " ".join(w for w in name_l.split() if w not in _STOPWORDS)


# Captures whatever a question names after "for"/"to"/"about" as the thing
# being asked about — "how many are registered FOR THE PICNIC", "who's
# coming TO the retreat". Non-greedy up to the next punctuation or end of
# string, so it doesn't swallow a trailing clause unrelated to the event.
_EVENT_REF_RE = re.compile(r"\b(?:for|to|about)\s+(?:the\s+)?([a-z0-9][a-z0-9' -]*?)(?:[?.!]|$)", re.IGNORECASE)


# Time words say WHEN, never WHICH event: "hayride tomorrow night" is the Hayride. (Found 2026-10-06 in the event-question matrix:
# every "<tracked event> tomorrow night" question was treated as an untracked event.)
_TIME_RE = re.compile(
    r"\b(?:today|tonight|tomorrow(?:\s+(?:night|morning|evening|afternoon))?|this\s+(?:week|weekend|morning|evening|coming\s+\w+)|next\s+\w+|"
    r"(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+(?:night|morning|evening))?)\b", re.IGNORECASE)


def _strip_time(phrase: str) -> str:
    return re.sub(r"\s+", " ", _TIME_RE.sub(" ", phrase)).strip(" ,.?!")


def _calendar_titles() -> list[str]:
    """Distinct event titles on the church calendars (the Subsplash import: Men's Breakfast, Men's Fraternity Bible Study, ...)."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        out = [r[0] for r in conn.execute("SELECT DISTINCT title FROM church_calendar_events WHERE title IS NOT NULL")]
        conn.close()
        return out
    except Exception:
        return []


def event_phrase(question: str) -> str:
    """The event the question names (the last for/to/about clause), minus time words. '' if there is none."""
    m = None
    for m in _EVENT_REF_RE.finditer(re.sub(r"\bfrat\b", "fraternity", question.lower())):
        pass
    return _strip_time(m.group(1)) if m else ""


def event_candidates(question: str, rows=None) -> dict:
    """Every known event (tracked in church_events OR on the church calendars) the question could mean.
    -> {"phrase", "candidates": [{"title", "tracked", "id"}]} with calendar/tracked duplicates of one title merged.
    Needed because the signup fast path only ever saw the tracked events: once the calendars were imported, "men's fraternity" matched
    the tracked Billiards Outing alone and silently skipped the weekly Bible Study."""
    if rows is None:
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
            rows = _active_events(conn)
            conn.close()
        except Exception:
            rows = []
    phrase = event_phrase(question)
    pt = _tokens(phrase)
    out: dict[str, dict] = {}
    if pt:
        for r in rows:
            if pt <= _tokens(r["event_name"]):
                out[_norm(r["event_name"].lower())] = {"title": r["event_name"], "tracked": True, "id": r["id"]}
        for t in _calendar_titles():
            if pt <= _tokens(t):
                out.setdefault(_norm(t.lower()), {"title": t, "tracked": False, "id": None})
    if phrase and not out:
        # Nothing shares all the asked words, but a whole event name may sit inside a longer phrase ("dessert for picnic",
        # "picnic on october 4 and who ..."): those are about that event (custom-form / compound questions the model must handle).
        pn = _norm(phrase.lower())
        if pn:
            for r in rows:
                n = _norm(r["event_name"].lower())
                if n and n in pn:
                    out[n] = {"title": r["event_name"], "tracked": True, "id": r["id"]}
            for t in _calendar_titles():
                n = _norm(t.lower())
                if n and n in pn:
                    out.setdefault(n, {"title": t, "tracked": False, "id": None})
    return {"phrase": phrase, "candidates": list(out.values())}


def _resolve_event_id(question: str) -> int | None:
    """Which tracking_active event the question is about.

    If the question names something after for/to/about ("...for the retreat"), that phrase MUST identify exactly one known event
    (tracked, or on the church calendars) and that event must be a tracked one; a phrase that matches nothing, or more than one
    event ("men's fraternity" = Bible Study AND Billiards Outing), returns None -- the caller then asks which one or says it has no
    numbers, instead of silently answering about a different event. Only when no such phrase is present at all does "exactly one
    active event" apply as the implicit subject (a bare "how many are registered?" while only the picnic is being tracked).
    """
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        rows = _active_events(conn)
        conn.close()
    except Exception:
        return None

    if not rows:
        return None

    q = question.lower()
    exact = [r for r in rows if r["event_name"].lower() in q]
    if len(exact) == 1:
        return exact[0]["id"]

    phrase = event_phrase(q)
    if not phrase:
        has_ref = any(_EVENT_REF_RE.finditer(q))
        return rows[0]["id"] if (len(rows) == 1 and not has_ref) else None

    cands = event_candidates(question, rows)["candidates"]
    if len(cands) == 1:
        return cands[0]["id"]            # None when the single match is a calendar-only (untracked) event
    if len(cands) > 1:
        # one candidate may be the phrase said in full ("billiards outing" vs "billiards outing party"): prefer an exact normalized name
        full = [c for c in cands if _norm(c["title"].lower()) == _norm(phrase.lower())]
        return full[0]["id"] if len(full) == 1 else None
    return None


# Words that appear in many event names and say nothing about WHICH event.
_GENERIC_WORDS = {"event", "events", "outing", "night", "party", "and", "or", "of", "a", "an", "in", "at", "day", "annual"}


def _stem(w: str) -> str:
    w = w.replace("'s", "").strip("'")
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {_stem(w) for w in words if w not in _STOPWORDS and w not in _GENERIC_WORDS and len(_stem(w)) >= 3}


def _token_match(phrase: str, rows) -> int | None:
    """Pick the single tracked event sharing the most distinctive words with
    `phrase`. Ties or zero overlap -> None (caller falls back to the LLM)."""
    pt = _tokens(phrase)
    if not pt:
        return None
    # Every distinctive word asked about must belong to the event, so "men's
    # class" can't latch onto "Men's Fraternity Billiards Outing" via "men".
    scored = sorted(((len(pt), r["id"]) for r in rows if pt <= _tokens(r["event_name"])), reverse=True)
    if not scored:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


# Checked first, independent of any specific event — a question about what's
# being tracked at all has no event to resolve against.
_TRACKED_RE = re.compile(
    r"\bwhat\s+events\b|\bevents?\s+(are\s+we|is\s+watson)\s+track|\blist\s+(the\s+)?events\b",
    re.IGNORECASE,
)

# "how many" + a signup-specific verb only — deliberately excludes
# "attend"/"coming", both dropped 2026-09-14 after a live collision: "how
# many people attended church on both campuses this past Sunday" (an
# ordinary attendance question, nothing to do with any tracked event)
# matched on "attend" and, since only one event was active at the time,
# silently answered with that event's registration count instead of falling
# through to the real attendance-domain query. "registered"/"signed up"/
# "RSVP"/"ticket" don't appear in ordinary worship-attendance phrasing, so
# they're safe; "attend"/"coming" are exactly the words that domain uses too.
_COUNT_RE = re.compile(
    r"\bhow many\b.*\b(regist|sign(ed)?[\s-]?up|rsvp|ticket)",
    re.IGNORECASE,
)

# "who" + the same signup-specific verbs — same exclusion as _COUNT_RE above.
_LIST_RE = re.compile(
    r"\bwho(?:'s|\s+is|\s+are)?\b.*\b(regist|sign(ed)?[\s-]?up|rsvp|ticket)",
    re.IGNORECASE,
)


# "how many are coming/going to X", "who's coming to X": these verbs are also ordinary worship-attendance wording, so they only count as
# a signup question when the question explicitly names a known event after for/to/about (never the implicit single-event default).
_COUNT_COMING_RE = re.compile(r"\bhow many\b.*\b(coming|going|attending|showing up|planning)\b", re.IGNORECASE)
_LIST_COMING_RE = re.compile(r"\bwho(?:'s|\s+is|\s+are)?\b.*\b(coming|going|attending|showing up)\b", re.IGNORECASE)
# "when is the banquet", "what time is trunk or treat" for tracked events that are not on the calendar cache (church_events has the date).
_INFO_RE = re.compile(r"\b(when|what\s+time|what\s+day|what\s+date)\b", re.IGNORECASE)


def _explicit_event_id(q: str) -> int | None:
    """Event id only when the question names the event after for/to/about; None for the implicit-single-event default."""
    return _resolve_event_id(q) if event_phrase(q) else None


def _mentions_extra_field(question_lower: str, event_id: int) -> bool:
    """True if the question seems to reference a specific answer to one of
    this event's custom sign-up-form questions (e.g. "dessert"/"side dish"
    for a picnic, a t-shirt size, a session choice -- whatever that
    particular event's form asked) rather than a plain headcount/list.

    Found 2026-09-20: Tara asked "how many signed up for side dish" and
    "...for dessert" about the picnic and got the SAME blind
    SUM(num_tickets)/list-everyone answer both times (11) -- this fast
    path has no idea what a "side dish" is, it just resolves the event
    name and ignores the rest of the question. Rather than hardcode a
    vocabulary of known field values (fragile, and only covers this one
    event's form), check the actual extra_fields JSON on file for this
    event and bail to the caller's LLM-generated-SQL fallback (which
    knows how to filter on extra_fields, see jobs/analytics/data_chat.py's
    _EVENTS_SCHEMA) whenever the question mentions one of that event's own
    custom question keys/answers. A false-positive bail just costs one
    LLM call instead of a wrong fast-path answer -- the safe direction."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        rows = conn.execute(
            "SELECT DISTINCT extra_fields FROM event_registrations "
            "WHERE event_id = ? AND extra_fields IS NOT NULL AND extra_fields != ''",
            (event_id,),
        ).fetchall()
        conn.close()
    except Exception:
        return False
    for (raw,) in rows:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            for token in (key, value):
                token = str(token).strip().lower().rstrip(":?")
                if len(token) >= 3 and token in question_lower:
                    return True
    return False


def pattern_match(question: str) -> str | None:
    """Return a single-line SELECT for the events domain, or None if the
    question doesn't match a recognized phrasing — bypasses both Ollama and
    the Claude budget tier entirely for the common cases."""
    q = question.strip()
    if not q:
        return None

    if _TRACKED_RE.search(q):
        return (
            "SELECT event_name, start_date, event_time FROM church_events "
            "WHERE tracking_active = 1 ORDER BY start_date"
        )

    if _INFO_RE.search(q) and not _COUNT_RE.search(q) and not _LIST_RE.search(q):
        m = re.search(r"\b(?:is|are)\s+(?:the\s+)?(.+?)\s*[?.!]?$", q, re.IGNORECASE)
        event_id = _resolve_event_id(f"{q} for {_strip_time(m.group(1))}") if m and _strip_time(m.group(1)) else None
        if event_id is not None:
            return (
                "SELECT event_name || ' is ' || COALESCE("
                "substr('SunMonTueWedThuFriSat', CAST(strftime('%w', start_date) AS INTEGER) * 3 + 1, 3) || ', ' || "
                "substr('JanFebMarAprMayJunJulAugSepOctNovDec', (CAST(strftime('%m', start_date) AS INTEGER) - 1) * 3 + 1, 3) || ' ' || "
                "CAST(strftime('%d', start_date) AS INTEGER), '(date not set)') "
                "|| COALESCE(' at ' || NULLIF(event_time, ''), '') "
                f"FROM church_events WHERE id = {event_id}"
            )
        return None

    if _COUNT_RE.search(q) or (_COUNT_COMING_RE.search(q) and _explicit_event_id(q) is not None):
        event_id = _resolve_event_id(q)
        if event_id is None:
            return None
        if _mentions_extra_field(q.lower(), event_id):
            return None
        # COALESCE to 0 -- a bare SUM() is SQL NULL when the event has zero
        # registrations so far (a real, common state right after an event is
        # created), and _format_rows/_fmt_value renders a lone NULL value as
        # a bare "—" with no surrounding sentence, which reads as a broken
        # reply instead of "0 signed up" -- confirmed live 2026-09-18 asking
        # about Hayride and Bonfire before its first registration landed.
        return (
            "SELECT COALESCE(SUM(num_tickets), 0) FROM event_registrations "
            f"WHERE event_id = {event_id}"
        )

    if _LIST_RE.search(q) or (_LIST_COMING_RE.search(q) and _explicit_event_id(q) is not None):
        event_id = _resolve_event_id(q)
        if event_id is None:
            return None
        # A plain "who's signed up" still lists everyone with ALL available
        # data per registration (tickets + whatever they answered on any
        # custom sign-up-form question, e.g. side dish vs dessert for the
        # picnic) -- Bill's 2026-09-20 standing rule: the list should never
        # hide data Watson actually has. But a question naming a SPECIFIC
        # answer ("who's bringing dessert") is asking to be filtered down to
        # just that answer, which this fast path doesn't do -- bail to the
        # LLM path for that case, same as the COUNT branch above.
        if _mentions_extra_field(q.lower(), event_id):
            return None
        return (
            "SELECT first_name || ' ' || last_name || "
            "CASE WHEN num_tickets > 1 THEN ' (' || num_tickets || ' tickets)' ELSE '' END || "
            "CASE WHEN extra_fields IS NOT NULL AND extra_fields != '' THEN "
            "' — ' || (SELECT group_concat(je.value, ', ') FROM json_each(event_registrations.extra_fields) je) "
            "ELSE '' END "
            "AS registrant FROM event_registrations "
            f"WHERE event_id = {event_id} ORDER BY first_name, last_name"
        )

    return None
