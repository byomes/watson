from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from jobs.calendar.calendar import get_events

NY = ZoneInfo("America/New_York")
SLOT_DURATION = 45  # minutes

# weekday(): Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
BOOKING_WINDOWS = {
    2: [(10, 0, 13, 0)],                       # Wed: 10am–1pm
    3: [(10, 0, 13, 0), (19, 0, 20, 30)],      # Thu: 10am–1pm, 7–8:30pm
}


def _slots_for_window(d: date, h_start: int, m_start: int, h_end: int, m_end: int) -> list:
    slots = []
    window_start = datetime(d.year, d.month, d.day, h_start, m_start, tzinfo=NY)
    window_end   = datetime(d.year, d.month, d.day, h_end,   m_end,   tzinfo=NY)
    slot_start = window_start
    while True:
        slot_end = slot_start + timedelta(minutes=SLOT_DURATION)
        if slot_end > window_end:
            break
        slots.append((slot_start, slot_end))
        slot_start = slot_end
    return slots


def _overlaps(slot_start: datetime, slot_end: datetime, events: list) -> bool:
    for e in events:
        ev_start_str = e.get("start", "")
        ev_end_str   = e.get("end", "")
        if not ev_start_str or not ev_end_str:
            continue
        try:
            ev_start = datetime.fromisoformat(ev_start_str).astimezone(NY)
            ev_end   = datetime.fromisoformat(ev_end_str).astimezone(NY)
        except Exception:
            continue
        if slot_start < ev_end and slot_end > ev_start:
            return True
    return False


def get_available_slots(d: date, meeting_type: str) -> list:
    weekday = d.weekday()
    windows = BOOKING_WINDOWS.get(weekday)
    if not windows:
        return []

    day_start = datetime(d.year, d.month, d.day, 0,  0,  tzinfo=NY)
    day_end   = datetime(d.year, d.month, d.day, 23, 59, tzinfo=NY)
    events = get_events(day_start, day_end)
    cutoff = datetime.now(NY) + timedelta(hours=2)

    slots = []
    for h_start, m_start, h_end, m_end in windows:
        for slot_start, slot_end in _slots_for_window(d, h_start, m_start, h_end, m_end):
            if slot_start < cutoff:
                continue
            if not _overlaps(slot_start, slot_end, events):
                slots.append({
                    "start":   slot_start.isoformat(),
                    "end":     slot_end.isoformat(),
                    "display": f"{slot_start.strftime('%-I:%M %p')} — {slot_end.strftime('%-I:%M %p')}",
                })
    return slots


def get_available_slots_next_30_days(meeting_type: str) -> dict:
    today = date.today()
    result = {}
    for i in range(30):
        d = today + timedelta(days=i)
        slots = get_available_slots(d, meeting_type)
        if slots:
            result[d.isoformat()] = slots
    return result


def get_available_slots_grouped(meeting_type: str) -> list:
    today = date.today()
    result = []
    for i in range(30):
        d = today + timedelta(days=i)
        slots = get_available_slots(d, meeting_type)
        if slots:
            day_label = datetime(d.year, d.month, d.day).strftime("%A, %B %-d")
            result.append({
                "date":  d.isoformat(),
                "label": day_label,
                "slots": [
                    {"start": s["start"], "end": s["end"], "label": s["display"]}
                    for s in slots
                ],
            })
    return result
