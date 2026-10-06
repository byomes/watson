"""jobs/church_calendar/lookup.py -- read-only queries over church_calendar_events
(filled by jobs/church_calendar/calendars.py). Public church data only.

Usage:
  python -m jobs.church_calendar.lookup "men's breakfast"   # next occurrences of matching events
  python -m jobs.church_calendar.lookup --week              # next 7 days, all calendars
  python -m jobs.church_calendar.lookup --days 14 --calendar "Small Groups"
"""
import argparse
import re
from datetime import date, datetime, timedelta

from core.database import get_connection


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("’", "'").replace("'", ""))


def upcoming(query: str | None = None, days: int | None = None, calendar: str | None = None,
             limit: int = 50) -> list[dict]:
    """Active events from today on, soonest first. `query` matches title words
    (all words must appear); `days` bounds the window."""
    sql = "SELECT * FROM church_calendar_events WHERE active=1 AND start_date >= date('now','localtime')"
    args: list = []
    if days is not None:
        sql += " AND start_date <= date('now','localtime',?)"
        args.append(f"+{days} days")
    if calendar:
        sql += " AND calendar = ?"
        args.append(calendar)
    sql += " ORDER BY start_date, time_text"
    with get_connection() as conn:
        rows = [dict(r) for r in conn.execute(sql, args)]
    if query:
        words = _norm(query).split()
        rows = [r for r in rows if all(w in _norm(r["title"]) for w in words)]
    return rows[:limit]


def next_by_series(query: str) -> dict[str, dict]:
    """{series: next upcoming occurrence} for every series matching `query`."""
    out: dict[str, dict] = {}
    for r in upcoming(query):
        out.setdefault(r["series"], r)
    return out


def weekday_date(r: dict) -> str:
    d = datetime.strptime(r["start_date"], "%Y-%m-%d")
    return f"{d.strftime('%a %b')} {d.day}"


def format_rows(rows: list[dict]) -> str:
    if not rows:
        return "Nothing found on the church calendars."
    return "\n".join(f"{weekday_date(r)}, {r['time_text'] or ''} - {r['title']} ({r['calendar']})" for r in rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--week", action="store_true")
    ap.add_argument("--days", type=int)
    ap.add_argument("--calendar")
    a = ap.parse_args()
    days = 7 if a.week else a.days
    print(format_rows(upcoming(a.query, days, a.calendar)))
