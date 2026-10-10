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
from jobs.sms.sms_send import send_sms as _send_sms_gateway

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kidsatt_weekly] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CONGREGATION_DB = Path(__file__).resolve().parents[2] / "data" / "congregation.db"
KIDSATT_URL = "https://wtsn.me/cat/tracker?tab=kids"

DEFAULT_SERVANTS = {
    "Nursery": {"name": "Tara Mathena", "person_id": 450, "phone": "", "is_override": False},
    "Pre-K": {"name": "Tara Mathena", "person_id": 450, "phone": "", "is_override": False},
    "Elementary": {"name": "Lucie Hale", "person_id": 332, "phone": "", "is_override": False},
}


def get_today_servants() -> dict:
    """Fetch servant assignments for today, using overrides if any exist."""
    today = date.today().isoformat()
    conn = sqlite3.connect(str(CONGREGATION_DB))
    conn.row_factory = sqlite3.Row

    servants = {k: dict(v) for k, v in DEFAULT_SERVANTS.items()}

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
                "is_override": True,
            }
    finally:
        conn.close()

    return servants


def send_sms(phone: str, name: str, message: str) -> bool:
    """Send SMS via the email-to-SMS gateway (jobs.sms.sms_send), resolving
    carrier through the existing phone_carriers cache. No carrier param --
    an override servant may not have a confirmed carrier on file yet; in
    that case this fails with needs_carrier so it surfaces in the log
    rather than silently no-op'ing."""
    result = _send_sms_gateway(name, phone, "", message)
    if not result.get("success"):
        log.error("SMS send failed for %s (%s): %s", name, phone, result.get("error"))
    return bool(result.get("success"))


def main() -> None:
    servants = get_today_servants()
    message = f"Kids attendance tracker:\n{KIDSATT_URL}"

    # Dedup recipients across classes -- Tara covers both Nursery and Pre-K
    # by default, so without this she'd get the same Telegram message twice.
    # Keyed by (channel, recipient) so a Telegram default and an SMS
    # override are never conflated even if they somehow shared an id.
    seen: set[tuple[str, str]] = set()
    sent_anyone = False

    for class_name, info in servants.items():
        name = info.get("name")
        if info.get("is_override"):
            phone = info.get("phone")
            if not phone:
                log.error("Override for %s (%s) has no phone on file -- skipped", class_name, name)
                continue
            key = ("sms", phone)
            if key in seen:
                continue
            seen.add(key)
            if send_sms(phone, name, message):
                log.info("Sent SMS to %s for %s", name, class_name)
                sent_anyone = True
        else:
            person_id = info.get("person_id")
            key = ("telegram", str(person_id))
            if key in seen:
                continue
            seen.add(key)
            if send_to_person(person_id, message):
                log.info("Sent kidsatt Telegram to %s (%s)", name, class_name)
                sent_anyone = True
            else:
                log.error("FAILED to send to %s (person_id=%s)", name, person_id)

    if not sent_anyone:
        log.warning("Failed to send to any servants")


if __name__ == "__main__":
    main()
