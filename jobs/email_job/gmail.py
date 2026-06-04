import base64
import os
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CONFIG_DIR = Path.home() / "watson" / "config"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "token.json"


def get_service():
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            flow.redirect_uri = 'urn:ietf:wg:oauth:2.0:oob'
        auth_url, _ = flow.authorization_url(prompt='consent')
        print('Visit this URL:', auth_url)
        code = input('Enter the auth code: ')
        flow.fetch_token(code=code)
        creds = flow.credentials
        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def send_email(to, subject, body):
    service = get_service()
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()


def get_unread(label="INBOX", max=50):
    service = get_service()
    response = (
        service.users()
        .messages()
        .list(userId="me", labelIds=[label, "UNREAD"], maxResults=max)
        .execute()
    )

    messages = response.get("messages", [])
    results = []
    for msg in messages:
        results.append(get_message(msg["id"]))
    return results


def mark_as_read(message_id):
    service = get_service()
    return (
        service.users()
        .messages()
        .modify(userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]})
        .execute()
    )


def get_message(message_id):
    service = get_service()
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()

    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    body = _extract_body(msg["payload"])

    return {
        "id": msg["id"],
        "subject": headers.get("Subject", ""),
        "sender": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "body": body,
    }


def _extract_body(payload):
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain":
                data = part["body"].get("data", "")
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        # Recurse into nested multipart
        for part in payload["parts"]:
            result = _extract_body(part)
            if result:
                return result
        return ""

    data = payload["body"].get("data", "")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return ""
