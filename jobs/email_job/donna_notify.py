"""jobs/email_job/donna_notify.py -- gated email sends to Donna Redman.

Per feedback_donna_email_schedule: any email to Donna queues for the next
Tue/Wed/Thu 9am slot rather than sending immediately, mirroring
jobs/telegram/donna_notify.py's Telegram gate (see that module's docstring
for the parallel rationale). A send requested outside the window is queued
in donna_email_queue and picked up by the next cron tick of this module
(run standalone) once the window opens.
"""
import json
import logging
from datetime import datetime

from core.database import get_connection
from jobs.email_job.brevo_send import send_email

log = logging.getLogger(__name__)

DONNA_EMAIL = "donna@catalyst302.com"
DONNA_NAME = "Donna Redman"

ALLOWED_WEEKDAYS = (1, 2, 3)  # Tue, Wed, Thu (datetime.weekday(): Mon=0)
WINDOW_HOUR = 9               # 9:00-9:59am


def _in_window(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return now.weekday() in ALLOWED_WEEKDAYS and now.hour == WINDOW_HOUR


def _ensure_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS donna_email_queue (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            subject    TEXT NOT NULL,
            body       TEXT NOT NULL,
            tags_json  TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            sent_at    TEXT
        )
    """)


def _queue(subject: str, body: str, tags: list | None) -> None:
    with get_connection() as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO donna_email_queue (subject, body, tags_json) VALUES (?, ?, ?)",
            (subject, body, json.dumps(tags) if tags else None),
        )
        conn.commit()
    log.info("donna_notify(email): outside Tue/Wed/Thu 9am window, queued '%s'", subject)


def send_to_donna(subject: str, body: str, tags: list | None = None) -> bool:
    """Send now if within the Tue/Wed/Thu 9am window, otherwise queue for
    the next window (see module docstring)."""
    if _in_window():
        result = send_email(DONNA_EMAIL, DONNA_NAME, subject, body, tags=tags)
        return result["success"]
    _queue(subject, body, tags)
    return True


def flush_queue() -> int:
    """Send every still-queued email, but only once the window is open --
    meant to be called on a cron tick. Returns the number sent."""
    if not _in_window():
        return 0
    with get_connection() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, subject, body, tags_json FROM donna_email_queue "
            "WHERE sent_at IS NULL ORDER BY id"
        ).fetchall()
        sent = 0
        for row in rows:
            tags = json.loads(row["tags_json"]) if row["tags_json"] else None
            result = send_email(DONNA_EMAIL, DONNA_NAME, row["subject"], row["body"], tags=tags)
            if result["success"]:
                conn.execute(
                    "UPDATE donna_email_queue SET sent_at = datetime('now') WHERE id = ?",
                    (row["id"],),
                )
                sent += 1
        conn.commit()
    return sent


if __name__ == "__main__":
    n = flush_queue()
    print(f"Flushed {n} queued Donna email(s).")
