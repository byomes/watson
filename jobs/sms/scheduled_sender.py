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
from jobs.sms import send_core

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

            error_body, _status, message_id = send_core.send_and_record(conn, row["thread_id"], row["body"], None, None)
            if error_body:
                conn.execute(
                    "UPDATE sms_scheduled_messages SET status = 'failed', error = ? WHERE id = ?",
                    (error_body.get("error") or "send failed", row["id"]),
                )
                conn.commit()
                log.error("Scheduled message id=%s failed to send: %s", row["id"], error_body.get("error"))
                continue

            conn.execute("DELETE FROM sms_scheduled_messages WHERE id = ?", (row["id"],))
            conn.commit()
            log.info("Sent scheduled message id=%s to thread_id=%s (message_id=%s)", row["id"], row["thread_id"], message_id)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
