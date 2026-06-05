import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from jobs.calendar.availability import get_available_slots_grouped

booking_bp = Blueprint("booking", __name__)
NY = ZoneInfo("America/New_York")
CALENDAR_ID = "bill.yomes@gmail.com"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]

_DEFAULT_TOKEN = str(Path.home() / "watson" / "config" / "token.json")


def _get_calendar_service():
    token_file = Path(os.environ.get("GOOGLE_CALENDAR_TOKEN", _DEFAULT_TOKEN))
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_file.write_text(creds.to_json())
        else:
            raise RuntimeError("Calendar credentials missing or invalid. Run OAuth flow.")
    return build("calendar", "v3", credentials=creds)


def _send_smtp(to: str, subject: str, body_plain: str) -> None:
    smtp_host = os.environ.get("WATSON_SMTP_HOST", "")
    smtp_port = int(os.environ.get("WATSON_SMTP_PORT", "587"))
    smtp_user = os.environ.get("WATSON_SMTP_USER", "")
    smtp_pass = os.environ.get("WATSON_SMTP_PASS", "")
    from_addr = "watson@williamckyomes.com"

    body_html = body_plain.replace("\n", "<br>")
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    msg["From"] = f"Watson <{from_addr}>"
    msg.attach(MIMEText(body_plain, "plain"))
    msg.attach(MIMEText(f"<html><body>{body_html}</body></html>", "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(smtp_user, smtp_pass)
        smtp.sendmail(from_addr, [to], msg.as_string())


def _create_calendar_event(name, start_dt, end_dt, description, attendee_email):
    svc = _get_calendar_service()
    body = {
        "summary":     f"Appointment: {name}",
        "description": description,
        "start":       {"dateTime": start_dt.isoformat(), "timeZone": "America/New_York"},
        "end":         {"dateTime": end_dt.isoformat(),   "timeZone": "America/New_York"},
        "attendees":   [{"email": attendee_email}],
    }
    event = svc.events().insert(calendarId=CALENDAR_ID, body=body, sendUpdates="all").execute()
    return event["id"]


def _send_confirmation_to_guest(name, email, meeting_type, display, confirmation_id):
    body = (
        f"Hi {name},\n\n"
        f"Your {meeting_type} appointment with Pastor Bill is confirmed.\n\n"
        f"Date & Time: {display}\n"
        f"Reference: {confirmation_id}\n\n"
        f"For virtual appointments, Pastor Bill will reach out with connection details "
        f"before your meeting.\n\n"
        f"If you need to cancel or reschedule, please email pastorbill@catalyst302.com.\n\n"
        f"— Office of Dr. Bill Yomes\n"
        f"williamckyomes.com"
    )
    _send_smtp(email, "Your Appointment with Pastor Bill is Confirmed", body)


def _notify_pastor(name, email, meeting_type, display, reason, confirmation_id):
    body = (
        f"New {meeting_type} appointment booked\n\n"
        f"Name:    {name}\n"
        f"Email:   {email}\n"
        f"Time:    {display}\n"
        f"Reason:  {reason}\n"
        f"Ref:     {confirmation_id}"
    )
    _send_smtp("pastorbill@catalyst302.com", f"New Appointment: {name}", body)


@booking_bp.route("/api/availability")
def availability():
    try:
        meeting_type = request.args.get("type", "virtual")
        days = get_available_slots_grouped(meeting_type)
        return jsonify({"days": days})
    except Exception:
        return jsonify({"days": []})


@booking_bp.route("/api/book", methods=["POST"])
def book():
    data = request.get_json(force=True) or {}
    for field in ("name", "email", "reason", "slot", "type", "confirmationId"):
        if not data.get(field):
            return jsonify({"error": f"{field} required"}), 400

    name            = data["name"]
    email           = data["email"]
    reason          = data["reason"]
    slot            = data["slot"]
    meeting_type    = data["type"]
    confirmation_id = data["confirmationId"]

    if not isinstance(slot, dict) or not slot.get("start") or not slot.get("end"):
        return jsonify({"error": "slot must include start and end"}), 400

    try:
        start_dt = datetime.fromisoformat(slot["start"]).astimezone(NY)
        end_dt   = datetime.fromisoformat(slot["end"]).astimezone(NY)
    except Exception as exc:
        return jsonify({"error": f"invalid slot format: {exc}"}), 400

    display = (
        f"{start_dt.strftime('%A, %B %-d at %-I:%M %p')}"
        f" — {end_dt.strftime('%-I:%M %p')}"
    )

    try:
        description = (
            f"Type: {meeting_type}\n"
            f"Reason: {reason}\n"
            f"Email: {email}\n"
            f"Ref: {confirmation_id}"
        )
        _create_calendar_event(name, start_dt, end_dt, description, email)
    except Exception as exc:
        return jsonify({"error": f"calendar error: {exc}"}), 500

    try:
        _send_confirmation_to_guest(name, email, meeting_type, display, confirmation_id)
        _notify_pastor(name, email, meeting_type, display, reason, confirmation_id)
    except Exception as exc:
        return jsonify({"error": f"email error: {exc}"}), 500

    return jsonify({"confirmationId": confirmation_id})
