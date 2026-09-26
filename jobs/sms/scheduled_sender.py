"""jobs/sms/scheduled_sender.py -- fires due delayed SMS sends created via
POST /api/sms/threads/<id>/scheduled (Watson SMS's delayed-send feature).

# * * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.sms.scheduled_sender

Mirrors jobs/reminders/check_reminders.py's shape: send_at is a UTC
"YYYY-MM-DD HH:MM:SS" string compared directly against SQLite's own
datetime('now'). A send failure never raises -- it's recorded on the row
(status='failed', error set) so it shows up in the app instead of silently
vanishing, and never blocks the rest of the due batch.
"""
import logging

from core.database import get_connection
from jobs.sms import gateway_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main():
    conn = get_connection()
    try:
        due = conn.execute(
            "SELECT * FROM sms_scheduled_messages WHERE status = 'pending' AND send_at <= datetime('now')"
        ).fetchall()

        for row in due:
            thread = conn.execute("SELECT * FROM sms_threads WHERE id = ?", (row["thread_id"],)).fetchone()
            if not thread:
                conn.execute("DELETE FROM sms_scheduled_messages WHERE id = ?", (row["id"],))
                conn.commit()
                log.warning("Scheduled message id=%s has no thread (id=%s) -- dropped", row["id"], row["thread_id"])
                continue

            result = gateway_client.send_message(thread["phone"], row["body"])
            if not result["success"]:
                conn.execute(
                    "UPDATE sms_scheduled_messages SET status = 'failed', error = ? WHERE id = ?",
                    (result.get("error") or "send failed", row["id"]),
                )
                conn.commit()
                log.error("Scheduled message id=%s failed to send: %s", row["id"], result.get("error"))
                continue

            conn.execute(
                """INSERT INTO sms_messages (thread_id, direction, body, gateway_message_id, status)
                   VALUES (?, 'out', ?, ?, 'sent')""",
                (row["thread_id"], row["body"], result.get("gateway_message_id")),
            )
            conn.execute(
                """UPDATE sms_threads
                   SET last_message_at = datetime('now'),
                       last_message_preview = ?,
                       unread = 0
                   WHERE id = ?""",
                (row["body"], row["thread_id"]),
            )
            conn.execute("DELETE FROM sms_scheduled_messages WHERE id = ?", (row["id"],))
            conn.commit()
            log.info("Sent scheduled message id=%s to thread_id=%s", row["id"], row["thread_id"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
