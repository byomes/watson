"""jobs/telegram/donna_notify.py -- gated Telegram sends to Donna Redman.

Per Dr. Bill's 2026-09-29 rule: Donna is never messaged via Telegram
outside 9am-8pm, and never after 8pm at all without a specific directive
and confirmation from Dr. Bill himself -- that override is a human decision
each time, not a code path, so there is no bypass function here. A send
requested outside the window is queued in donna_telegram_queue and picked
up by the next cron tick of this module (run standalone) once the window
is open, including the next morning for anything held overnight.

Every current call site that messages Donna via Telegram
(jobs/congregation/family_dates.py, jobs/congregation/
notify_donna_fluro_review.py) goes through send_to_donna/
send_buttons_to_donna here instead of calling jobs.telegram.send_to_person
directly, so nothing can reach her outside the window by skipping this
module. Any new Donna-Telegram call site should do the same.
"""
import json
import logging
from datetime import datetime

from core.database import get_connection
from jobs.telegram.send_to_person import send_buttons_to_person, send_to_person

log = logging.getLogger(__name__)

DONNA_PERSON_ID = 12  # people.id for Donna Redman -- see family_dates.py's own copy of this mapping

WINDOW_START_HOUR = 9   # 9:00am
WINDOW_END_HOUR = 20    # 8:00pm (exclusive -- 7:59pm is the last minute in-window)


def _in_window(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR


def _ensure_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS donna_telegram_queue (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            message       TEXT NOT NULL,
            keyboard_json TEXT,
            created_at    TEXT DEFAULT (datetime('now')),
            sent_at       TEXT
        )
    """)


def _queue(message: str, keyboard: list | None) -> None:
    with get_connection() as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO donna_telegram_queue (message, keyboard_json) VALUES (?, ?)",
            (message, json.dumps(keyboard) if keyboard else None),
        )
        conn.commit()
    log.info("donna_notify: outside 9am-8pm window, queued message (len=%d chars)", len(message))


def send_to_donna(message: str) -> bool:
    """Send now if within the 9am-8pm window, otherwise queue for the next
    window (see module docstring)."""
    if _in_window():
        return send_to_person(DONNA_PERSON_ID, message)
    _queue(message, None)
    return True


def send_buttons_to_donna(message: str, inline_keyboard: list[list[dict]]) -> bool:
    if _in_window():
        return send_buttons_to_person(DONNA_PERSON_ID, message, inline_keyboard)
    _queue(message, inline_keyboard)
    return True


def flush_queue() -> int:
    """Send every still-queued message, but only once the window is open --
    meant to be called on a cron tick. Returns the number sent."""
    if not _in_window():
        return 0
    with get_connection() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, message, keyboard_json FROM donna_telegram_queue "
            "WHERE sent_at IS NULL ORDER BY id"
        ).fetchall()
        sent = 0
        for row in rows:
            keyboard = json.loads(row["keyboard_json"]) if row["keyboard_json"] else None
            ok = (
                send_buttons_to_person(DONNA_PERSON_ID, row["message"], keyboard)
                if keyboard
                else send_to_person(DONNA_PERSON_ID, row["message"])
            )
            if ok:
                conn.execute(
                    "UPDATE donna_telegram_queue SET sent_at = datetime('now') WHERE id = ?",
                    (row["id"],),
                )
                sent += 1
        conn.commit()
    return sent


if __name__ == "__main__":
    n = flush_queue()
    print(f"Flushed {n} queued Donna message(s).")
