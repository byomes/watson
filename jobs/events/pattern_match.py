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
_EVENT_REF_RE = re.compile(r"\b(?:for|to|about)\s+(?:the\s+)?([a-z][a-z0-9' -]*?)(?:[?.!]|$)", re.IGNORECASE)


def _resolve_event_id(question: str) -> int | None:
    """Which tracking_active event the question is about.

    If the question names something after for/to/about ("...for the
    retreat"), that phrase MUST match a tracked event's name (verbatim, or
    with stopwords like "church"/"the" stripped) — a name that matches
    nothing returns None even if exactly one event happens to be active,
    since defaulting there would silently answer a question about an
    untracked event with a different event's numbers. Only when no such
    phrase is present at all does "exactly one active event" apply as the
    implicit subject (the common case: a bare "how many are registered?"
    while only the picnic is being tracked).
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
    m = None
    for m in _EVENT_REF_RE.finditer(q):
        pass  # take the last for/to/about clause — closest to the actual object
    phrase = (m.group(1).strip().strip("?.!,") if m else None) or None

    if phrase:
        phrase_norm = _norm(phrase)
        matches = [
            r for r in rows
            if r["event_name"].lower() in q
            or (_norm(r["event_name"].lower()) and (
                _norm(r["event_name"].lower()) in phrase_norm or phrase_norm in _norm(r["event_name"].lower())
            ))
        ]
        return matches[0]["id"] if len(matches) == 1 else None

    return rows[0]["id"] if len(rows) == 1 else None


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


# A leading filler interjection ("Mmm", "Hmm", "Umm", "uh,") — sometimes
# glued straight onto the first real word by voice dictation or a fast
# typist. Found 2026-09-22: a Team Chat leader asked "Mmmwho has signed up
# for the church picnic" and it fell through to a paid LLM call only
# because "Mmmwho" has no word boundary before "who", so _LIST_RE's \bwho
# never matched. The glued form requires a doubled "m" ("mm", "umm",
# "hmm") so it can't eat the start of a real word like "umbrella"; the
# short forms ("um", "uh", "hm", "er", "erm") only strip as whole words.
_FILLER_RE = re.compile(r"^\s*(?:(?:u+|h+)?m{2,}|(?:um|uh|hm|erm?)\b)[\s,.…-]*", re.IGNORECASE)


def pattern_match(question: str) -> str | None:
    """Return a single-line SELECT for the events domain, or None if the
    question doesn't match a recognized phrasing — bypasses both Ollama and
    the Claude budget tier entirely for the common cases."""
    q = _FILLER_RE.sub("", question).strip()
    if not q:
        return None

    if _TRACKED_RE.search(q):
        return (
            "SELECT event_name, start_date, event_time FROM church_events "
            "WHERE tracking_active = 1 ORDER BY start_date"
        )

    if _COUNT_RE.search(q):
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

    if _LIST_RE.search(q):
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
