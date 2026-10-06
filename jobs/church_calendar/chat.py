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


def answer(text: str) -> str | None:
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
