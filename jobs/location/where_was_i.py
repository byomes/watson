"""jobs/location/where_was_i.py -- "where was I <day> at <time>" Telegram
Q&A over location_pings (jobs/location/api.py's OwnTracks ingestion).

Wired into bot.py's _handle_general ONLY (Bill's own chat) -- this is his
personal GPS history, not congregation data, so it deliberately carries no
allowlist-extension path the way add_child/mark_spouse/deacon-assign do for
other leaders. There's only ever one phone reporting to location_pings, so
"where was I" is inherently about Bill; no name/person argument needed.

Added 2026-09-15 after Bill asked "where was I last Wednesday at 5 PM" and
"where was I last Sunday at 7 PM" directly in chat and got manual one-off
answers (nearest ping -> saved zone match -> Nominatim reverse-geocode
fallback for the Sunday query, which didn't land in either saved zone).
This wires that same lookup into a repeatable command.
"""
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests
from dateutil import parser as _dateutil_parser

from jobs.location import get_db
from jobs.location.api import _zone_for

NY = ZoneInfo("America/New_York")

_WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# How far from the target time a ping can be and still count as an answer --
# pings arrive roughly every 10-20 min while moving and can gap wider
# overnight/stationary, so 2 hours balances "still a meaningful answer"
# against "not actually where they were."
_MAX_PING_GAP = timedelta(hours=2)


def parse_when(expr: str) -> datetime | None:
    """Parse "last Wednesday at 5 PM" / "yesterday at 3pm" / "Sunday 7pm"
    into a concrete America/New_York datetime. A bare weekday (no "last")
    is treated the same as "last <weekday>" -- always the most recent PAST
    occurrence, never today even if today is that weekday (asking "where
    was I Wednesday" on a Wednesday means a week ago, not right now).
    Requires both a day and a time -- returns None rather than guessing a
    time if one isn't given or isn't parseable."""
    expr = expr.strip().lower()
    today = datetime.now(NY).date()

    m = re.match(
        r"^(?:last\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b(.*)$",
        expr,
    )
    if m:
        target_wd = _WEEKDAY_NAMES[m.group(1)]
        days_back = (today.weekday() - target_wd) % 7
        if days_back == 0:
            days_back = 7
        target_date = today - timedelta(days=days_back)
        rest = m.group(2)
    elif expr.startswith("yesterday"):
        target_date = today - timedelta(days=1)
        rest = expr[len("yesterday"):]
    elif expr.startswith("today"):
        target_date = today
        rest = expr[len("today"):]
    else:
        return None

    rest = re.sub(r"^(?:at|around)\s+", "", rest.strip()).strip()
    if not rest:
        return None

    try:
        dt = _dateutil_parser.parse(rest, default=datetime.combine(target_date, time(0, 0)), fuzzy=True)
    except (ValueError, OverflowError):
        return None
    return dt.replace(tzinfo=NY)


def _reverse_geocode(lat: float, lon: float) -> str | None:
    """Best-effort place name for a ping outside every saved zone. Public
    Nominatim endpoint, no API key -- a descriptive User-Agent is required
    by its usage policy. Returns None (caller falls back to raw
    coordinates) on any failure; this is a nice-to-have, not load-bearing."""
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "zoom": 18, "format": "jsonv2"},
            headers={"User-Agent": "WatsonPersonalAssistant/1.0 (pastorbill@catalyst302.com)"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    address = data.get("address", {})
    name = (
        data.get("name")
        or address.get("amenity") or address.get("shop") or address.get("building")
        or address.get("office") or address.get("leisure")
    )
    road = address.get("road")
    city = address.get("city") or address.get("town") or address.get("village") or address.get("hamlet")
    parts = [p for p in (name, road, city) if p]
    return ", ".join(dict.fromkeys(parts)) if parts else data.get("display_name")


def answer(when_expr: str) -> str:
    target = parse_when(when_expr)
    if not target:
        return 'I need both a day and a time -- try something like "where was I last Wednesday at 5 PM".'
    target_ts = int(target.timestamp())
    gap_s = int(_MAX_PING_GAP.total_seconds())

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT lat, lon, tst FROM location_pings "
            "WHERE tst BETWEEN ? AND ? ORDER BY ABS(tst - ?) LIMIT 1",
            (target_ts - gap_s, target_ts + gap_s, target_ts),
        ).fetchone()
        if not row:
            return f"I don't have any location data near {target.strftime('%-I:%M %p on %A, %B %-d')}."
        zone = _zone_for(conn, row["lat"], row["lon"])
    finally:
        conn.close()

    ping_time = datetime.fromtimestamp(row["tst"], NY)
    gap_min = abs((ping_time - target).total_seconds()) / 60
    when_str = target.strftime("%-I:%M %p on %A, %B %-d")
    ping_str = ping_time.strftime("%-I:%M %p")

    place = zone or _reverse_geocode(row["lat"], row["lon"]) or f"{row['lat']:.5f}, {row['lon']:.5f}"
    gap_note = "" if gap_min < 5 else f" (closest data point was at {ping_str}, {round(gap_min)} min off)"
    return f"You were at/near {place} around {when_str}{gap_note}."
