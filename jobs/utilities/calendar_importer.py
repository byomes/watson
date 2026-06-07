"""jobs/utilities/calendar_importer.py — import .ics files into Google Calendar."""
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


def parse_ics(path: str) -> list:
    try:
        from icalendar import Calendar
        data = Path(path).read_bytes()
        cal = Calendar.from_ical(data)
        events = []
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            def _dt(key):
                val = component.get(key)
                if val is None:
                    return ""
                dt = val.dt if hasattr(val, "dt") else val
                return str(dt)
            events.append({
                "summary": str(component.get("SUMMARY", "")),
                "start": _dt("DTSTART"),
                "end": _dt("DTEND"),
                "location": str(component.get("LOCATION", "")),
                "description": str(component.get("DESCRIPTION", ""))[:200],
            })
        return events
    except Exception as exc:
        log.error("parse_ics failed: %s", exc)
        return []


def import_to_google(path: str) -> str:
    events = parse_ics(path)
    if not events:
        return "No events found in the file."

    try:
        from jobs.gcal.calendar import get_service
        service = get_service()
    except Exception as exc:
        log.error("Google Calendar auth failed: %s", exc)
        return f"Calendar auth failed: {exc}"

    imported = 0
    errors = 0
    for ev in events:
        try:
            body = {"summary": ev["summary"]}
            if ev["start"]:
                if "T" in ev["start"]:
                    body["start"] = {"dateTime": ev["start"], "timeZone": "America/New_York"}
                    body["end"] = {"dateTime": ev["end"] or ev["start"], "timeZone": "America/New_York"}
                else:
                    body["start"] = {"date": ev["start"][:10]}
                    body["end"] = {"date": ev["end"][:10] if ev["end"] else ev["start"][:10]}
            if ev["location"]:
                body["location"] = ev["location"]
            if ev["description"]:
                body["description"] = ev["description"]
            service.events().insert(calendarId="primary", body=body).execute()
            imported += 1
        except Exception as exc:
            log.warning("Event import failed (%s): %s", ev["summary"], exc)
            errors += 1

    return f"Imported {imported} event(s). {errors} error(s)."


def run(message: str = None) -> str:
    if not message:
        return "Calendar importer ready. Send a file path to import events."

    match = re.search(r'[\w/~.\-]+\.ics', message)
    if not match:
        return "Please provide a path to a .ics file."

    path = match.group().replace("~", str(Path.home()))
    if not Path(path).exists():
        return f"File not found: {path}"

    events = parse_ics(path)
    if not events:
        return f"No events found in {path}."

    lines = [f"Found {len(events)} event(s) in {path}:\n"]
    for ev in events[:10]:
        lines.append(f"• {ev['summary']} — {ev['start']}")
        if ev["location"]:
            lines.append(f"  📍 {ev['location']}")
    if len(events) > 10:
        lines.append(f"  ...and {len(events) - 10} more.")
    lines.append("\nSay 'import to google' to add these to your calendar.")
    return "\n".join(lines)
