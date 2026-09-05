"""jobs/gcal/calendar.py — "what's on my calendar" style queries.

Registered as the calendar_query skill. jobs/skillbuilder/router.py's
_SKILL_PRE_CHECKS and jobs/intent/classifier.py both already routed to
this slug, but it had no matching entry in memory/skills.json and no
implementation module (the old jobs/gcal/calendar.py had been deleted
without updating either) — every query failed with
"Skill 'calendar_query' not found."
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jobs.gcal.gcal_service import get_events

NY = ZoneInfo("America/New_York")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _resolve_day(message: str) -> tuple[datetime, str]:
    text = (message or "").lower()
    now = datetime.now(NY)
    if "tomorrow" in text:
        return now + timedelta(days=1), "tomorrow"
    for i, weekday in enumerate(_WEEKDAYS):
        if weekday in text:
            days_ahead = (i - now.weekday()) % 7
            target = now + timedelta(days=days_ahead)
            label = "today" if days_ahead == 0 else weekday.capitalize()
            return target, label
    return now, "today"


def run(message: str = None) -> str:
    target, label = _resolve_day(message)
    start = target.replace(hour=0, minute=0, second=0, microsecond=0)
    end = target.replace(hour=23, minute=59, second=59, microsecond=0)

    try:
        events = get_events(start, end)
    except Exception as exc:
        return f"Could not reach Google Calendar: {exc}"

    if not events:
        return f"Nothing on your calendar for {label}."

    lines = [f"Your calendar for {label}:"]
    for event in events:
        summary = event.get("summary") or "(no title)"
        start_raw = event.get("start", "")
        try:
            when = datetime.fromisoformat(start_raw).astimezone(NY).strftime("%-I:%M %p")
        except Exception:
            when = "All day"
        lines.append(f"• {when} — {summary}")
    return "\n".join(lines)
