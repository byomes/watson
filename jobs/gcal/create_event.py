from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from pathlib import Path

TOKEN_PATH = Path(__file__).parent.parent.parent / "config" / "token.json"
CREDS_PATH = Path(__file__).parent.parent.parent / "config" / "credentials.json"


def create_event(title: str, start: str, end: str, location: str = "", description: str = "") -> dict:
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
    service = build("calendar", "v3", credentials=creds)

    event_body = {
        "summary": title,
        "location": location,
        "description": description,
        "start": {"dateTime": start, "timeZone": "America/New_York"},
        "end": {"dateTime": end, "timeZone": "America/New_York"},
    }

    event = service.events().insert(calendarId="bill.yomes@gmail.com", body=event_body).execute()
    return event
