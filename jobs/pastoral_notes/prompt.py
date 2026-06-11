"""
Queries Google Calendar for appointments that ended in the last 15 minutes
and prompts for pastoral notes via Telegram.
Run on a cron every ~5 minutes.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.gcal.gcal_service import get_service, CALENDAR_ID
from jobs.pastoral_notes.db import get_db

log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")

_SKIP_KEYWORDS = {
    "deep work", "sermon study", "sabbath", "family",
    "elder meeting", "staff meeting", "lunch", "hair",
}


def _send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)


def _should_skip(title: str) -> bool:
    lower = title.lower()
    if "[skip notes]" in lower:
        return True
    return any(kw in lower for kw in _SKIP_KEYWORDS)


def _already_prompted(event_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM notes_pending WHERE event_id = ?", (event_id,)
        ).fetchone()
    return row is not None


def _record_pending(event_id: str, title: str, appointment_time: str) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO notes_pending
               (event_id, appointment_title, appointment_time, prompted_at, status)
               VALUES (?, ?, ?, datetime('now'), 'pending')""",
            (event_id, title, appointment_time),
        )


def run() -> None:
    now = datetime.now(NY)
    window_start = now - timedelta(minutes=15)

    svc = get_service()
    result = svc.events().list(
        calendarId=CALENDAR_ID,
        timeMin=window_start.isoformat(),
        timeMax=now.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    for event in result.get("items", []):
        title = event.get("summary", "").strip()
        if not title:
            continue

        # Only process events whose end falls in the window
        end_raw = event["end"].get("dateTime", "")
        if not end_raw:
            continue
        try:
            end_dt = datetime.fromisoformat(end_raw)
        except ValueError:
            continue
        if not (window_start <= end_dt <= now):
            continue

        if _should_skip(title):
            log.info("Skipping event (keyword match): %s", title)
            continue

        event_id = event["id"]
        if _already_prompted(event_id):
            log.info("Already prompted for event: %s", event_id)
            continue

        appointment_time = end_dt.astimezone(NY).strftime("%-I:%M %p")
        _record_pending(event_id, title, appointment_time)

        msg = (
            f"You just met with {title}. "
            f"Any notes to store? Reply with your notes, or reply 'skip' to dismiss."
        )
        _send_telegram(msg)
        log.info("Prompted for notes: %s", title)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
