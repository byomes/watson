from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from jobs.gcal.calendar import get_events
from jobs.gcal.availability import BOOKING_WINDOWS

NY = ZoneInfo("America/New_York")

_WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def parse_day(day_str: str) -> date:
    s = day_str.lower().strip()
    today = datetime.now(NY).date()

    if s == "today":
        return today
    if s == "tomorrow":
        return today + timedelta(days=1)

    for prefix in ("next ", "this "):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break

    if s in _WEEKDAY_MAP:
        target_wd = _WEEKDAY_MAP[s]
        days_ahead = (target_wd - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # never book same weekday — always the next occurrence
        return today + timedelta(days=days_ahead)

    try:
        from dateutil import parser as dparser
        dt = dparser.parse(day_str, default=datetime.now(NY))
        parsed = dt.date()
        if parsed <= today:
            parsed += timedelta(days=7)
        return parsed
    except Exception:
        pass

    return today + timedelta(days=1)


def _overlaps(slot_start: datetime, slot_end: datetime, events: list) -> bool:
    for e in events:
        ev_start_str = e.get("start", "")
        ev_end_str = e.get("end", "")
        if not ev_start_str or not ev_end_str:
            continue
        if "T" not in ev_start_str:
            continue
        try:
            ev_start = datetime.fromisoformat(ev_start_str).astimezone(NY)
            ev_end = datetime.fromisoformat(ev_end_str).astimezone(NY)
        except Exception:
            continue
        if slot_start < ev_end and slot_end > ev_start:
            return True
    return False


def find_best_slot(day_str: str, duration_minutes: int) -> dict:
    try:
        d = parse_day(day_str)
    except Exception:
        return {"available": False, "message": "Could not parse that day."}

    windows = BOOKING_WINDOWS.get(d.weekday())
    if not windows:
        return {"available": False, "message": "That day is outside booking windows (Wed/Thu/Sat only)."}

    day_start = datetime(d.year, d.month, d.day, 0, 0, tzinfo=NY)
    day_end = datetime(d.year, d.month, d.day, 23, 59, tzinfo=NY)
    events = get_events(day_start, day_end)

    for h_start, m_start, h_end, m_end in windows:
        window_start = datetime(d.year, d.month, d.day, h_start, m_start, tzinfo=NY)
        window_end = datetime(d.year, d.month, d.day, h_end, m_end, tzinfo=NY)
        slot_start = window_start
        while True:
            slot_end = slot_start + timedelta(minutes=duration_minutes)
            if slot_end > window_end:
                break
            if not _overlaps(slot_start, slot_end, events):
                display = (
                    f"{d.strftime('%A, %B %-d')} · "
                    f"{slot_start.strftime('%-I:%M %p')} – {slot_end.strftime('%-I:%M %p')}"
                )
                return {
                    "available": True,
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat(),
                    "display": display,
                }
            slot_start += timedelta(minutes=30)

    return {"available": False, "message": "No available slot found on that day."}
