"""jobs/church_calendar/chat.py -- LLM-free team-chat answers about church
events from the unified calendar cache (church_calendar_events).

answer(text) returns a reply string, or None when the message is not a
church-event question (so bot.py's other routes handle it unchanged).
Public church data only. Deliberately conservative: a question must both
sound like an event question AND name a known event/series or a time window.
Anything about attendance, a person, or Dr. Bill's own calendar is skipped.
"""
import re
from datetime import date, datetime, timedelta

from jobs.church_calendar.lookup import _norm, format_rows, upcoming, weekday_date

_TRIGGER = re.compile(
    r"\b(when is|when's|when are|what time|what day|where is|where's|where are|what is happening|"
    r"what's happening|whats happening|what is going on|what's going on|whats going on|going on|"
    r"events?|on the (?:church )?calendar|is there (?:a|any|an|anything|something)|schedule|next)\b", re.I)
# Messages that belong to other routes (attendance, people, Bill's own calendar).
_EXCLUDE = re.compile(
    r"\b(attend\w*|miss\w*|absent|present|check-?ins?|how many|headcount|"
    r"dr\.? bill|bill'?s calendar|my calendar|available|availability|free|busy|meeting with|"
    r"phone|email|address|birthday|last (?:seen|visit))\b", re.I)
_STOP = set(
    "when is are was the a an of for at on in to what time day where whats what's next do does we our "
    "there any about tell me us church calendar event events happening going schedule this coming upcoming "
    "next date and or with".split())
_SERVICE_WORDS = {"service", "services", "worship", "sunday", "church"}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _window(low: str) -> tuple[int, int] | None:
    """(start_offset_days, end_offset_days) from today, or None if no window named."""
    today = date.today()
    if "today" in low or "tonight" in low:
        return 0, 0
    if "tomorrow" in low:
        return 1, 1
    if "weekend" in low:
        to_sat = (5 - today.weekday()) % 7
        return to_sat, to_sat + 1
    if "next week" in low:
        return 7, 13
    if re.search(r"this week|next 7 days|coming week|rest of the week", low):
        return 0, 6
    for i, wd in enumerate(_WEEKDAYS):
        if re.search(rf"\b{wd}\b", low):
            return ((i - today.weekday()) % 7,) * 2
    return None


def _series_matches(text: str) -> list[dict]:
    """Next occurrence of every series whose title contains all the question's content words."""
    words = [w for w in _norm(text).split() if w not in _STOP and not w.isdigit()]
    if not words:
        return []
    out: dict[str, dict] = {}
    for r in upcoming():
        title = _norm(r["title"]).split()
        if all(w in title for w in words) or (
            set(words) <= _SERVICE_WORDS | {"time"} and r["calendar"] == "Services"
            and set(words) & _SERVICE_WORDS):
            out.setdefault(r["series"], r)
    return list(out.values())


def _detail_line(r: dict) -> str:
    line = f"{r['title']}: {weekday_date(r)}, {r['time_text'] or 'time TBD'}"
    if r.get("register_url"):
        line += f"\n  Register: {r['register_url']}"
    return line


_ALIASES = {"frat": "fraternity"}
_REG_FILLER = set(
    "signed up sign ups signup signups registered registration registrations registering rsvp rsvpd rsvps "
    "how many who whos coming going people tomorrow tonight night morning today week weekend monday tuesday "
    "wednesday thursday friday saturday sunday names name list".split())
_REG_TRIGGER = re.compile(
    r"\b(signed up|sign(?:ed)? ?ups?|registered|registrations?|registering|rsvp'?d?|rsvps|who(?:'s| is) coming|"
    r"how many (?:are )?(?:coming|going))\b", re.I)


def registrations_answer(text: str) -> str | None:
    """"How many are signed up for Men's Fraternity tomorrow night?" answered from the local
    copy of Subsplash registrations (jobs/church_calendar/registrations.py). Returns None
    unless the question asks about signups AND names a known upcoming event."""
    from core.database import get_connection
    low = text.lower().replace("’", "'")
    if not _REG_TRIGGER.search(low) or re.search(r"\b(attend\w*|missed|present)\b", low):
        return None
    words = [_ALIASES.get(w, w) for w in _norm(low).split() if w not in _STOP and w not in _REG_FILLER]
    if not words:
        return None
    win = _window(low)
    with get_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT event_uuid, title, start_date, registered FROM subsplash_event_regs "
            "WHERE has_form = 1 AND start_date >= date('now','localtime') ORDER BY start_date")]
        rows = [r for r in rows if all(w in _norm(r["title"]).split() for w in words)]

        def local_date(r):
            return date.fromisoformat(r["start_date"])

        if win is not None:
            lo, hi = date.today() + timedelta(days=win[0]), date.today() + timedelta(days=win[1])
            rows = [r for r in rows if lo <= local_date(r) <= hi] or rows
        if not rows:
            return None
        picked: dict[str, dict] = {}
        for r in rows:  # next occurrence of each distinct event title
            picked.setdefault(r["title"], r)
        lines = []
        for r in picked.values():
            d = local_date(r)
            n = r["registered"] or 0
            line = f"{r['title']} ({d.strftime('%a %b')} {d.day}): {n} signed up"
            if n and re.search(r"\bwho\b|names?|list", low):
                names = [f"{x['first_name'] or ''} {x['last_name'] or ''}".strip() for x in conn.execute(
                    "SELECT first_name, last_name FROM subsplash_registrations WHERE event_uuid=? "
                    "ORDER BY last_name, first_name", (r["event_uuid"],))]
                line += ": " + ", ".join(names)
            lines.append(line)
    return "\n".join(lines)


def answer(text: str) -> str | None:
    reg = registrations_answer(text)
    if reg:
        return reg
    low = text.lower().replace("’", "'")
    if not _TRIGGER.search(low) or _EXCLUDE.search(low):
        return None
    win = _window(low)
    series = _series_matches(low)
    # Strip the weekday/window words so "when is men's fraternity on wednesday" still names the event.
    if not series and win is None:
        return None
    if series and len(series) <= 4:
        if win is not None:
            d0, d1 = win
            lo, hi = date.today() + timedelta(days=d0), date.today() + timedelta(days=d1)
            hits = [r for r in upcoming() if r["series"] in {s["series"] for s in series}
                    and lo <= datetime.strptime(r["start_date"], "%Y-%m-%d").date() <= hi]
            if hits:
                return "\n".join(_detail_line(r) for r in hits)
        return "\n".join(_detail_line(r) for r in series)
    if win is not None and not series:
        d0, d1 = win
        rows = [r for r in upcoming(days=d1)
                if datetime.strptime(r["start_date"], "%Y-%m-%d").date() >= date.today() + timedelta(days=d0)]
        return format_rows(rows) if rows else "Nothing on the church calendars for that time."
    return None
