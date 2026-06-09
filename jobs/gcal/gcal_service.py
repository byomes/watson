from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

NY = ZoneInfo("America/New_York")
CALENDAR_ID = "bill.yomes@gmail.com"
TOKEN_FILE = Path.home() / "watson" / "config" / "token.json"
CREDENTIALS_FILE = Path.home() / "watson" / "config" / "credentials.json"
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]


def get_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            auth_url, _ = flow.authorization_url(prompt='consent')
            print("\nOpen this URL in any browser to authorize Watson:\n")
            print(auth_url)
            print()
            code = input("Paste the authorization code here: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials
            TOKEN_FILE.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def _to_rfc3339(dt: datetime) -> str:
    return dt.isoformat()


def _parse_event(event: dict) -> dict:
    start = event["start"].get("dateTime", event["start"].get("date", ""))
    end   = event["end"].get("dateTime", event["end"].get("date", ""))
    return {
        "id":      event["id"],
        "summary": event.get("summary", ""),
        "start":   start,
        "end":     end,
        "status":  event.get("status", "confirmed"),
    }


def get_events(date_start: datetime, date_end: datetime) -> list:
    svc = get_service()
    result = svc.events().list(
        calendarId=CALENDAR_ID,
        timeMin=_to_rfc3339(date_start),
        timeMax=_to_rfc3339(date_end),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return [_parse_event(e) for e in result.get("items", [])]


def create_event(title: str, start_dt: datetime, end_dt: datetime, description: str, attendee_email: str) -> str:
    svc = get_service()
    body = {
        "summary":     title,
        "description": description,
        "start":       {"dateTime": _to_rfc3339(start_dt), "timeZone": "America/New_York"},
        "end":         {"dateTime": _to_rfc3339(end_dt),   "timeZone": "America/New_York"},
        "attendees":   [{"email": attendee_email}],
    }
    event = svc.events().insert(calendarId=CALENDAR_ID, body=body, sendUpdates="all").execute()
    return event["id"]


def mark_busy(start_dt: datetime, end_dt: datetime, title: str = "Unavailable") -> str:
    svc = get_service()
    body = {
        "summary":      title,
        "start":        {"dateTime": _to_rfc3339(start_dt), "timeZone": "America/New_York"},
        "end":          {"dateTime": _to_rfc3339(end_dt),   "timeZone": "America/New_York"},
        "transparency": "opaque",
    }
    event = svc.events().insert(calendarId=CALENDAR_ID, body=body).execute()
    return event["id"]


def mark_day_busy_from_now() -> int:
    now = datetime.now(NY)
    end_of_day = now.replace(hour=23, minute=59, second=0, microsecond=0)
    remaining = get_events(now, end_of_day)
    count = len([e for e in remaining if e.get("summary") != "Unavailable"])
    mark_busy(now, end_of_day)
    return count


def reschedule_event(event_id: str, new_start_dt: datetime, new_end_dt: datetime) -> dict:
    svc = get_service()
    body = {
        "start": {"dateTime": _to_rfc3339(new_start_dt), "timeZone": "America/New_York"},
        "end":   {"dateTime": _to_rfc3339(new_end_dt),   "timeZone": "America/New_York"},
    }
    return svc.events().patch(calendarId=CALENDAR_ID, eventId=event_id, body=body).execute()


def get_todays_events() -> list:
    now = datetime.now(NY)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return get_events(start, end)


def cancel_event(event_id: str) -> None:
    svc = get_service()
    svc.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
