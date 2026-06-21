"""jobs/writing_room/remind.py — video call reminders to all active partners.

Cron: */15 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/writing_room/remind.py
"""
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.writing_room import bootstrap_db, get_db, send_email, send_telegram

log = logging.getLogger(__name__)


def _format_dt(iso: str) -> tuple[str, str]:
    """Return (day_date_str, time_str) from ISO 8601."""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%A, %B %-d"), dt.strftime("%-I:%M %p")
    except ValueError:
        return iso, ""


def _send_call_reminder(call: dict, tier: str) -> None:
    conn = get_db()
    try:
        partners = conn.execute(
            "SELECT email FROM writing_room_partners WHERE status = 'active'"
        ).fetchall()
        emails = [p["email"] for p in partners]

        if not emails:
            return

        day_date, time_str = _format_dt(call["scheduled_at"])
        label   = "Tomorrow" if tier == "24h" else "In 1 Hour"
        subject = f"Writing Room Call {label} — {call['title']}"
        body    = (
            f"Hi Writing Room,\n\n"
            f"Just a reminder about our upcoming call:\n\n"
            f"  {call['title']}\n"
            f"  {day_date} at {time_str}\n"
            f"  Join here: {call['meeting_url'] or '(link coming)'}\n\n"
            f"See you there.\n\n"
            f"— William Yomes"
        )

        col = "reminder_24h_sent" if tier == "24h" else "reminder_1h_sent"
        for email in emails:
            try:
                send_email(email, subject, body)
            except Exception as exc:
                log.error("Reminder email failed for %s: %s", email, exc)

        conn.execute(f"UPDATE writing_room_calls SET {col} = 1 WHERE id = ?", (call["id"],))
        conn.commit()

        tomorrow_label = "tomorrow" if tier == "24h" else "in 1 hour"
        send_telegram(
            f"📞 Writing Room — Call Reminder Sent\n\n"
            f"\"{call['title']}\" is {tomorrow_label}.\n"
            f"Reminder emailed to {len(emails)} partner{'s' if len(emails) != 1 else ''}."
        )
    finally:
        conn.close()


def main() -> None:
    bootstrap_db()
    now  = datetime.now(timezone.utc)
    conn = get_db()
    try:
        calls = conn.execute("SELECT * FROM writing_room_calls").fetchall()
    finally:
        conn.close()

    for call in calls:
        try:
            scheduled = datetime.fromisoformat(call["scheduled_at"])
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            delta = (scheduled - now).total_seconds()

            if not call["reminder_24h_sent"] and 23 * 3600 <= delta <= 25 * 3600:
                _send_call_reminder(dict(call), "24h")
            elif not call["reminder_1h_sent"] and 55 * 60 <= delta <= 65 * 60:
                _send_call_reminder(dict(call), "1h")
        except Exception as exc:
            log.error("Remind check failed for call %d: %s", call["id"], exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
