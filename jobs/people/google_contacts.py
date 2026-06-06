"""jobs/people/google_contacts.py — Import Google Contacts into the People Registry."""
import logging
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR = Path.home() / "watson" / "config"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "token.json"
SYNC_TOKEN_FILE = CONFIG_DIR / "contacts_sync_token.txt"
DB_PATH = Path(os.getenv("WATSON_DB", str(Path.home() / "watson" / "data" / "watson.db")))

SCOPES = ["https://www.googleapis.com/auth/contacts.readonly"]

log = logging.getLogger(__name__)


def _get_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        else:
            if not CREDENTIALS_FILE.exists():
                raise RuntimeError(f"credentials.json not found at {CREDENTIALS_FILE}")
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("\nAuthorization required for Google Contacts.")
            print(f"Visit this URL:\n\n  {auth_url}\n")
            code = input("Enter the auth code: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials
            TOKEN_FILE.write_text(creds.to_json())

    return build("people", "v1", credentials=creds)


def _ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            email       TEXT,
            phone       TEXT,
            info        TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _fetch_connections(service, sync_token=None):
    """Yield all person resources, handling pagination."""
    params = {
        "resourceName": "people/me",
        "personFields": "names,emailAddresses,phoneNumbers,organizations,biographies",
        "pageSize": 1000,
    }
    if sync_token:
        params["syncToken"] = sync_token
    else:
        params["requestSyncToken"] = True

    while True:
        result = service.people().connections().list(**params).execute()
        for person in result.get("connections", []):
            yield person
        next_token = result.get("nextPageToken")
        new_sync_token = result.get("nextSyncToken")
        if new_sync_token:
            SYNC_TOKEN_FILE.write_text(new_sync_token)
        if not next_token:
            break
        params["pageToken"] = next_token
        params.pop("syncToken", None)
        params.pop("requestSyncToken", None)


def _parse_person(person: dict) -> dict | None:
    names = person.get("names", [])
    emails = person.get("emailAddresses", [])
    phones = person.get("phoneNumbers", [])
    orgs = person.get("organizations", [])

    name = names[0].get("displayName", "").strip() if names else ""
    email = emails[0].get("value", "").strip() if emails else ""
    phone = phones[0].get("value", "").strip() if phones else ""

    if orgs:
        org = orgs[0]
        org_name = org.get("name", "").strip()
        title = org.get("title", "").strip()
        if org_name and title:
            info = f"{org_name} — {title}"
        elif org_name:
            info = org_name
        elif title:
            info = title
        else:
            info = ""
    else:
        info = ""

    if not name and not email:
        return None

    return {"name": name or email, "email": email or None, "phone": phone or None, "info": info or None}


def _upsert(conn: sqlite3.Connection, p: dict) -> str:
    """Insert or update a contact. Returns 'inserted', 'updated', or 'skipped'."""
    existing = None
    if p["email"]:
        existing = conn.execute(
            "SELECT id, phone, info FROM people WHERE email = ? COLLATE NOCASE",
            (p["email"],),
        ).fetchone()
    if existing is None and p["name"]:
        existing = conn.execute(
            "SELECT id, phone, info FROM people WHERE name = ? COLLATE NOCASE",
            (p["name"],),
        ).fetchone()

    if existing:
        updates = {}
        if p["phone"] and not existing[1]:
            updates["phone"] = p["phone"]
        if p["info"] and not existing[2]:
            updates["info"] = p["info"]
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE people SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                list(updates.values()) + [existing[0]],
            )
            return "updated"
        return "skipped"

    conn.execute(
        "INSERT INTO people (name, email, phone, info) VALUES (?, ?, ?, ?)",
        (p["name"], p["email"], p["phone"], p["info"]),
    )
    return "inserted"


def import_contacts(sync_only: bool = False) -> dict:
    service = _get_service()
    conn = _ensure_db()

    sync_token = None
    if sync_only and SYNC_TOKEN_FILE.exists():
        sync_token = SYNC_TOKEN_FILE.read_text().strip() or None

    counts = {"inserted": 0, "updated": 0, "skipped": 0, "total": 0}
    try:
        for person in _fetch_connections(service, sync_token=sync_token):
            parsed = _parse_person(person)
            if parsed is None:
                counts["skipped"] += 1
                counts["total"] += 1
                continue
            result = _upsert(conn, parsed)
            counts[result] += 1
            counts["total"] += 1
        conn.commit()
    finally:
        conn.close()

    return counts


def run() -> str:
    counts = import_contacts(sync_only=False)
    return (
        f"Google Contacts import complete: {counts['inserted']} new, "
        f"{counts['updated']} updated, {counts['skipped']} skipped. "
        f"Total contacts: {counts['total']}"
    )


def sync() -> str:
    counts = import_contacts(sync_only=True)
    return (
        f"Google Contacts sync complete: {counts['inserted']} new, "
        f"{counts['updated']} updated, {counts['skipped']} skipped. "
        f"Total contacts: {counts['total']}"
    )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        print(sync())
    else:
        print(run())
