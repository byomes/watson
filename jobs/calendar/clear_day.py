import os
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from jobs.gcal.gcal_service import get_service, CALENDAR_ID

NY = ZoneInfo("America/New_York")

SKIP_KEYWORDS = [
    "deep work", "sermon study", "sabbath", "family", "elder",
    "staff", "hair", "pastoral override",
]

OWNER_EMAIL = "bill.yomes@gmail.com"


def _parse_window(message: str) -> datetime:
    msg = message.lower()
    now = datetime.now(NY).replace(second=0, microsecond=0)

    m = re.search(r"(\d+(?:\.\d+)?)\s*hours?", msg)
    if m:
        return now + timedelta(hours=float(m.group(1)))

    return now.replace(hour=23, minute=59, second=0, microsecond=0)


def _should_skip(summary: str) -> bool:
    low = summary.lower()
    if "[skip notes]" in low:
        return True
    return any(kw in low for kw in SKIP_KEYWORDS)


def _send_cancellation_email(guest_email: str, guest_name: str, original_start: str):
    try:
        orig_dt = datetime.fromisoformat(original_start).astimezone(NY)
        orig_str = orig_dt.strftime("%-I:%M %p on %A, %B %-d")
    except Exception:
        orig_str = original_start

    name_str = guest_name or "there"
    body = (
        f"Hi {name_str},\n\n"
        "Due to an unexpected pastoral need, your appointment scheduled for "
        f"{orig_str} has been cancelled.\n\n"
        "Please visit williamckyomes.com/meet to book a new time that works for you.\n\n"
        "We apologize for any inconvenience.\n\n"
        "Sincerely,\n"
        "Watson\n"
        "AI-powered digital assistant\n"
        "Office of Dr. Bill Yomes\n"
        "williamckyomes.com/start"
    )

    msg = MIMEText(body)
    msg["Subject"] = "Your Appointment with Pastor Bill Has Been Cancelled"
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

    result = svc.events().list(
        calendarId=CALENDAR_ID,
        timeMin=now.isoformat(),
        timeMax=block_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    to_cancel = []
    for ev in result.get("items", []):
        summary = ev.get("summary", "")
        if _should_skip(summary):
            continue
        start_str = ev["start"].get("dateTime", ev["start"].get("date", ""))
        if not start_str:
            continue
        attendees = ev.get("attendees", [])
        guest_email = next(
            (a["email"] for a in attendees if a.get("email") != OWNER_EMAIL),
            None,
        )
        guest_name = next(
            (a.get("displayName", "") for a in attendees if a.get("email") != OWNER_EMAIL),
            "",
        )
        to_cancel.append({
            "id": ev["id"],
            "summary": summary,
            "description": ev.get("description", ""),
            "start_str": start_str,
            "guest_email": guest_email,
            "guest_name": guest_name,
        })

    cancel_log = []
    for ev in to_cancel:
        new_description = "⚠️ CANCELLED — guest notified to rebook\n\n" + ev["description"]
        svc.events().patch(
            calendarId=CALENDAR_ID,
            eventId=ev["id"],
            body={
                "summary": "CANCELLED: " + ev["summary"],
                "description": new_description,
            },
        ).execute()

        if ev["guest_email"]:
            try:
                _send_cancellation_email(ev["guest_email"], ev["guest_name"], ev["start_str"])
                cancel_log.append(f"  {ev['summary']} → cancelled, guest notified")
            except Exception as exc:
                cancel_log.append(f"  {ev['summary']} → cancelled (email failed: {exc})")
        else:
            cancel_log.append(f"  {ev['summary']} → cancelled, no guest email")

    svc.events().insert(
        calendarId=CALENDAR_ID,
        body={
            "summary": "Pastoral Override",
            "start": {"dateTime": now.isoformat(), "timeZone": "America/New_York"},
            "end":   {"dateTime": block_end.isoformat(), "timeZone": "America/New_York"},
            "transparency": "opaque",
        },
    ).execute()

    count = len(to_cancel)
    end_str = block_end.strftime("%-I:%M %p")
    lines = [f"Pastoral Override set. {count} appointment(s) cancelled and guests notified."]
    lines.extend(cancel_log)
    lines.append(f"Blocking event created until {end_str}.")
    return "\n".join(lines)
