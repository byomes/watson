"""
Add a task from a natural-language message.
Parses title, optional due date, and optional priority.
Inserts into the tasks table in watson.db.
"""

import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config.settings import DB_PATH

NY = ZoneInfo("America/New_York")

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

_INTAKE_PREFIX_RE = re.compile(
    r'^(?:add\s+(?:a\s+)?task|new\s+task|create\s+(?:a\s+)?task'
    r'|remind\s+me\s+to|put\s+(?:this\s+)?on\s+my\s+task\s+list'
    r'|add\s+to\s+my\s+tasks)[:\s]+',
    re.IGNORECASE,
)

_PRIORITY_RE = re.compile(
    r'\b(?:(?P<prep>with\s+)?(?P<val>high|low|medium)\s+priority'
    r'|priority[:\s]+(?P<val2>high|low|medium)'
    r'|(?P<urgent>urgent|asap|critical))\b',
    re.IGNORECASE,
)

_DATE_TOMORROW_RE = re.compile(
    r'\b(?:by\s+|on\s+)?tomorrow\b', re.IGNORECASE,
)

_DATE_WEEKDAY_RE = re.compile(
    r'\b(?:by\s+|on\s+)?(?P<next>next\s+)?'
    r'(?P<day>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
    re.IGNORECASE,
)

_DATE_MONTH_DAY_RE = re.compile(
    r'\b(?:by\s+|on\s+)?'
    r'(?P<month>january|february|march|april|may|june|july|august|'
    r'september|october|november|december)'
    r'\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b',
    re.IGNORECASE,
)


def _next_weekday(today, weekday_num: int):
    days_ahead = weekday_num - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


def _extract_priority(text: str) -> tuple[str, str]:
    """Return (priority, text_with_priority_removed)."""
    m = _PRIORITY_RE.search(text)
    if not m:
        return "medium", text

    if m.group("urgent"):
        priority = "high"
    elif m.group("val2"):
        priority = m.group("val2").lower()
    else:
        priority = m.group("val").lower()

    cleaned = (text[: m.start()] + " " + text[m.end() :]).strip()
    return priority, cleaned


def _extract_date(text: str, today) -> tuple[str | None, str]:
    """Return (YYYY-MM-DD or None, text_with_date_removed)."""
    m = _DATE_TOMORROW_RE.search(text)
    if m:
        due = today + timedelta(days=1)
        cleaned = (text[: m.start()] + " " + text[m.end() :]).strip()
        return due.strftime("%Y-%m-%d"), cleaned

    m = _DATE_WEEKDAY_RE.search(text)
    if m:
        weekday_num = _WEEKDAYS[m.group("day").lower()]
        due = _next_weekday(today, weekday_num)
        if m.group("next"):
            due += timedelta(weeks=1)
        cleaned = (text[: m.start()] + " " + text[m.end() :]).strip()
        return due.strftime("%Y-%m-%d"), cleaned

    m = _DATE_MONTH_DAY_RE.search(text)
    if m:
        month = _MONTHS[m.group("month").lower()]
        day = int(m.group("day"))
        year = today.year
        try:
            candidate = datetime(year, month, day).date()
            if candidate < today:
                candidate = datetime(year + 1, month, day).date()
        except ValueError:
            return None, text
        cleaned = (text[: m.start()] + " " + text[m.end() :]).strip()
        return candidate.strftime("%Y-%m-%d"), cleaned

    return None, text


def run(message: str = None) -> str:
    if not message:
        return "Please provide a task description."
    today = datetime.now(NY).date()

    # Strip intake prefix ("add a task", "remind me to", etc.)
    text = _INTAKE_PREFIX_RE.sub("", message).strip()

    priority, text = _extract_priority(text)
    due_date, text = _extract_date(text, today)

    # Normalize whitespace for the title
    title = re.sub(r'\s+', ' ', text).strip(" ,:;.")
    if not title:
        return "Couldn't parse a task title from that message."

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO team_tasks (member_id, title, due_date, status, source, category)
               VALUES (12, ?, ?, 'open', 'personal', 'catalyst')""",
            (title, due_date),
        )

    parts = [f"Task added: {title}"]
    if due_date:
        parts.append(f"due {due_date}")
    if priority != "medium":
        parts.append(f"{priority} priority")
    return " — ".join(parts) if len(parts) > 1 else parts[0]


if __name__ == "__main__":
    import sys
    print(run(" ".join(sys.argv[1:]) or "add task test task tomorrow high priority"))
