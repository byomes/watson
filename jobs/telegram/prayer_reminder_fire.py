"""prayer_reminder_fire.py -- fires snoozed deacon prayer-contact reminders.

Cron (every 15 min):
  */15 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.telegram.prayer_reminder_fire >> /home/billyomes/watson/logs/prayer_reminder.log 2>&1
"""
import logging

from jobs.telegram.prayer_notify import _conn, ensure_schema, resend_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main():
    with _conn() as conn:
        ensure_schema(conn)
        due = conn.execute(
            "SELECT id FROM prayer_contact_log "
            "WHERE status = 'snoozed' AND remind_at IS NOT NULL AND remind_at <= datetime('now')"
        ).fetchall()

    for row in due:
        if resend_notification(row["id"]):
            log.info("Re-fired prayer reminder log_id=%s", row["id"])
        else:
            log.error("Failed to re-fire prayer reminder log_id=%s (request no longer eligible)", row["id"])


if __name__ == "__main__":
    main()
