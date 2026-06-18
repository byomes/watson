import os
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from jobs.gcal.gcal_service import get_service, CALENDAR_ID
from jobs.gcal.availability import _overlaps, get_available_slots

NY = ZoneInfo("America/New_York")

SKIP_KEYWORDS = [
    "deep work", "sermon study", "sabbath", "family", "elder",
    "staff", "hair", "pastoral override",
]

OWNER_EMAIL = "bill.yomes@gmail.com"


def _parse_window(message: str):
    """Return (block_end, description) where block_end is a datetime."""
    msg = message.lower()
    now = datetime.now(NY).replace(second=0, microsecond=0)

    # "X hours" / "X hour"
    m = re.search(r"(\d+(?:\.\d+)?)\s*hours?", msg)
    if m:
        hours = float(m.group(1))
        return now + timedelta(hours=hours)

    # "day" or "today" → rest of day
    return now.replace(hour=23, minute=59, second=0, microsecond=0)


def _should_skip(summary: str) -> bool:
    low = summary.lower()
    if "[skip notes]" in low:
        return True
    return any(kw in low for kw in SKIP_KEYWORDS)


def _find_next_slot(duration_minutes: int) -> tuple[datetime, datetime] | None:
    """Find the next open slot of at least duration_minutes across booking windows."""
    from datetime import date
    today = date.today()
    for i in range(60):
        d = today + timedelta(days=i)
        slots = get_available_slots(d, "virtual")
        for slot in slots:
            s = datetime.fromisoformat(slot["start"]).astimezone(NY)
            e = datetime.fromisoformat(slot["end"]).astimezone(NY)
            if (e - s).total_seconds() / 60 >= duration_minutes:
                return s, s + timedelta(minutes=duration_minutes)
    return None


def _send_reschedule_email(guest_email: str, guest_name: str, original_start: str):
    try:
        orig_dt = datetime.fromisoformat(original_start).astimezone(NY)
        orig_str = orig_dt.strftime("%-I:%M %p on %A, %B %-d")
    except Exception:
        orig_str = original_start

    name_str = guest_name or "there"
    body = (
        f"Hi {name_str},\n\n"
        "Due to an unexpected pastoral need, your appointment scheduled for "
        f"{orig_str} has been rescheduled.\n\n"
        "Please visit williamckyomes.com/meet to book a new time that works for you.\n\n"
        "We apologize for any inconvenience.\n\n"
        "Sincerely,\n"
        "Watson\n"
        "AI-powered digital assistant\n"
        "Office of Dr. Bill Yomes\n"
        "williamckyomes.com/start"
    )

    msg = MIMEText(body)
    msg["Subject"] = "Your Appointment with Pastor Bill Has Been Rescheduled"
    msg["From"] = os.environ.get("WATSON_SMTP_FROM", "watson@williamckyomes.com")
    msg["To"] = guest_email

    host = os.environ.get("WATSON_SMTP_HOST", "")
    port = int(os.environ.get("WATSON_SMTP_PORT", "587"))
    user = os.environ.get("WATSON_SMTP_USER", "")
    pw = os.environ.get("WATSON_SMTP_PASS", "")

    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, pw)
        smtp.send_message(msg)


def run(message: str) -> str:
    now = datetime.now(NY).replace(second=0, microsecond=0)
    block_end = _parse_window(message)

    svc = get_service()

    # Fetch events from now until block_end
    result = svc.events().list(
        calendarId=CALENDAR_ID,
        timeMin=now.isoformat(),
        timeMax=block_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    raw_events = result.get("items", [])

    to_reschedule = []
    for ev in raw_events:
        summary = ev.get("summary", "")
        if _should_skip(summary):
            continue
        start_str = ev["start"].get("dateTime", ev["start"].get("date", ""))
        end_str = ev["end"].get("dateTime", ev["end"].get("date", ""))
        if not start_str or not end_str:
            continue
        try:
            ev_start = datetime.fromisoformat(start_str).astimezone(NY)
            ev_end = datetime.fromisoformat(end_str).astimezone(NY)
        except Exception:
            continue
        duration_min = int((ev_end - ev_start).total_seconds() / 60)
        attendees = ev.get("attendees", [])
        guest_email = next(
            (a["email"] for a in attendees if a.get("email") != OWNER_EMAIL),
            None,
        )
        guest_name = next(
            (a.get("displayName", "") for a in attendees if a.get("email") != OWNER_EMAIL),
            "",
        )
        to_reschedule.append({
            "id": ev["id"],
            "summary": summary,
            "start_str": start_str,
            "duration_min": duration_min,
            "guest_email": guest_email,
            "guest_name": guest_name,
        })

    move_log = []
    for ev in to_reschedule:
        slot = _find_next_slot(ev["duration_min"])
        if slot is None:
            move_log.append(f"  {ev['summary']} → no slot found (left as-is)")
            continue

        new_start, new_end = slot
        svc.events().patch(
            calendarId=CALENDAR_ID,
            eventId=ev["id"],
            body={
                "start": {"dateTime": new_start.isoformat(), "timeZone": "America/New_York"},
                "end":   {"dateTime": new_end.isoformat(),   "timeZone": "America/New_York"},
            },
        ).execute()

        new_str = new_start.strftime("%a %b %-d at %-I:%M %p")
        move_log.append(f"  {ev['summary']} → {new_str}")

        if ev["guest_email"]:
            try:
                _send_reschedule_email(ev["guest_email"], ev["guest_name"], ev["start_str"])
            except Exception as exc:
                move_log.append(f"    (email to {ev['guest_email']} failed: {exc})")

    # Create the blocking event
    svc.events().insert(
        calendarId=CALENDAR_ID,
        body={
            "summary": "Pastoral Override",
            "start": {"dateTime": now.isoformat(), "timeZone": "America/New_York"},
            "end":   {"dateTime": block_end.isoformat(), "timeZone": "America/New_York"},
            "transparency": "opaque",
        },
    ).execute()

    count = len(to_reschedule)
    end_str = block_end.strftime("%-I:%M %p")
    lines = [
        f"Pastoral Override set. {count} appointment(s) rescheduled and guests notified.",
    ]
    if move_log:
        lines.extend(move_log)
    lines.append(f"Blocking event created until {end_str}.")
    return "\n".join(lines)
