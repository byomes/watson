"""jobs/congregation/kidsatt_weekly.py -- weekly Sunday 2:55pm send of kids
attendance tracker to servants (via Telegram to defaults or SMS to overrides).

Checks for servant overrides set via /cat/kidstoday form. If overrides exist
for today, sends SMS to the override servants. If no overrides, sends Telegram
to default leaders (Tara for Nursery/PreK, Lucie for Elementary).

Cron:
  55 14 * * 0 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.kidsatt_weekly \
    >> /home/billyomes/watson/logs/kidsatt_weekly.log 2>&1
"""
import logging
import sqlite3
from datetime import date
from pathlib import Path

from jobs.telegram.send_to_person import send_to_person
from core.database import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kidsatt_weekly] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CONGREGATION_DB = Path(__file__).resolve().parents[2] / "data" / "congregation.db"
KIDSATT_URL = "https://wtsn.me/cat/kidsatt"

DEFAULT_SERVANTS = {
    "Nursery": {"name": "Tara Mathena", "person_id": 450, "phone": ""},
    "Pre-K": {"name": "Tara Mathena", "person_id": 450, "phone": ""},
    "Elementary": {"name": "Lucie Hale", "person_id": 332, "phone": ""},
}


def get_today_servants() -> dict:
    """Fetch servant assignments for today, using overrides if any exist."""
    today = date.today().isoformat()
    conn = sqlite3.connect(str(CONGREGATION_DB))
    conn.row_factory = sqlite3.Row

    servants = dict(DEFAULT_SERVANTS)

    try:
        cursor = conn.execute(
            """
            SELECT kso.class_name, m.name, kso.person_id, m.phone
            FROM kids_servant_overrides kso
            JOIN members m ON m.id = kso.member_id
            WHERE kso.event_date = ?
            """,
            (today,),
        )
        for row in cursor:
            servants[row["class_name"]] = {
                "name": row["name"],
                "person_id": row["person_id"],
                "phone": row["phone"] or "",
            }
    finally:
        conn.close()

    return servants


def send_sms(phone: str, name: str, message: str) -> bool:
    """Send SMS via Watson's SMS bridge."""
    try:
        watson_conn = get_connection()
        watson_conn.execute(
            """
            INSERT INTO sms_messages (to_number, body, direction, status, created_at)
            VALUES (?, ?, 'outbound', 'queued', datetime('now'))
            """,
            (phone, message),
        )
        watson_conn.commit()
        log.info("Queued SMS to %s (%s)", name, phone)
        return True
    except Exception as e:
        log.error("Failed to queue SMS to %s: %s", name, e)
        return False


def main() -> None:
    servants = get_today_servants()
    message = f"Kids attendance tracker:\n{KIDSATT_URL}"

    sent_anyone = False

    for class_name, info in servants.items():
        person_id = info.get("person_id")
        phone = info.get("phone")
        name = info.get("name")

        # Check if this is an override (has phone and it's not a default)
        if phone and name not in ["Tara Mathena", "Lucie Hale"]:
            if send_sms(phone, name, message):
                log.info("Sent SMS to %s for %s", name, class_name)
                sent_anyone = True
        elif person_id:
            if send_to_person(person_id, message):
                log.info("Sent kidsatt Telegram to %s (%s)", name, class_name)
                sent_anyone = True
            else:
                log.error("FAILED to send to %s (person_id=%s)", name, person_id)

    if not sent_anyone:
        log.warning("Failed to send to any servants")


if __name__ == "__main__":
    main()
