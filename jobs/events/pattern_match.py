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


def _resolve_event(question: str) -> tuple[int, str] | None:
    """Which tracking_active event the question is about, as (id, name).

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
        return (matches[0]["id"], matches[0]["event_name"]) if len(matches) == 1 else None

    return (rows[0]["id"], rows[0]["event_name"]) if len(rows) == 1 else None


def _resolve_event_id(question: str) -> int | None:
    found = _resolve_event(question)
    return found[0] if found else None


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

    if _COUNT_RE.search(q):
        event_id = _resolve_event_id(q)
        if event_id is None:
            return None
        return (
            "SELECT COALESCE(SUM(num_tickets), 0) FROM event_registrations "
            f"WHERE event_id = {event_id}"
        )

    if _LIST_RE.search(q):
        event_id = _resolve_event_id(q)
        if event_id is None:
            return None
        return (
            "SELECT first_name || ' ' || last_name || "
            "CASE WHEN num_tickets > 1 THEN ' (' || num_tickets || ' tickets)' ELSE '' END "
            "AS registrant FROM event_registrations "
            f"WHERE event_id = {event_id} ORDER BY first_name, last_name"
        )

    return None


def empty_reply(question: str) -> str | None:
    """Direct answer for a registration list question about a real tracked
    event that simply has no registrations yet, or None if the question
    isn't that shape.

    Found 2026-09-18: "Who's registered for the Hayride and Bonfire so
    far?" resolved to the right event, but the event had just been created
    and had zero registrations, so pattern_match()'s list query returned no
    rows. data_chat.py treats an empty fast-path result as "false positive,
    fall through to generation" (see its attendance-block comment) -- which
    here meant a paid LLM call that could only conclude the same thing.
    Zero registrations for a resolved event is a real answer, not a miss.
    """
    q = question.strip()
    if not q or _TRACKED_RE.search(q) or _COUNT_RE.search(q) or not _LIST_RE.search(q):
        return None
    found = _resolve_event(q)
    if found is None:
        return None
    return f"Nobody has registered for {found[1]} yet."
