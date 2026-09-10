"""
Birthday Daily Alert -- Telegram nudge to Dr. Bill every morning listing
anyone in the congregation whose birthday is today, so he can text them
himself. Distinct from birthday_report.py (that one is a monthly
look-ahead digest to the deacons, scoped to their own groups); this one
is same-day, congregation-wide, and goes to Dr. Bill only.

Scope: members.active = 1 AND member_status = 'active' AND birthdate's
month/day matches today. Includes phone number (when on file) so Dr.
Bill can act on the message directly without a lookup.

Sends nothing when no one has a birthday today -- a daily "no birthdays"
message would just be noise.

Cron (7am daily):
  0 7 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.birthday_daily_alert \
    >> /home/billyomes/watson/logs/birthday_daily_alert.log 2>&1

Usage:
  python3 -m jobs.congregation.birthday_daily_alert
"""

import logging
from datetime import date

from core.vacation import vacation_gate
from jobs.connect_cards.reports import _conn
from jobs.telegram.send_to_person import send_to_person

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BILL_PERSON_ID = 7  # people.id for Bill Yomes (watson.db)


def _todays_birthdays() -> list[tuple[str, int, str | None]]:
    today = date.today()
    mmdd = today.strftime("%m-%d")

    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT name, birthdate, phone
            FROM members
            WHERE active = 1
              AND member_status = 'active'
              AND birthdate IS NOT NULL
              AND birthdate != ''
              AND substr(birthdate, 6, 5) = ?
            ORDER BY name
            """,
            (mmdd,),
        ).fetchall()

    result = []
    for row in rows:
        birth_year = int(row["birthdate"][0:4])
        result.append((row["name"], today.year - birth_year, row["phone"]))
    return result


def build_message(birthdays: list[tuple[str, int, str | None]]) -> str:
    lines = ["🎂 Birthdays today"]
    for name, age, phone in birthdays:
        phone_part = phone if phone else "no phone on file"
        lines.append(f"{name} (turning {age}) — {phone_part}")
    lines.append("")
    lines.append("- Watson")
    return "\n".join(lines)


def main():
    birthdays = _todays_birthdays()
    if not birthdays:
        log.info("birthday_daily_alert: no birthdays today, nothing sent")
        return

    message = build_message(birthdays)
    if vacation_gate("normal", "jobs.congregation.birthday_daily_alert", message):
        return

    ok = send_to_person(BILL_PERSON_ID, message)
    if not ok:
        log.error("birthday_daily_alert: failed to send to Bill")
    else:
        log.info("birthday_daily_alert: sent %d birthday(s) to Bill", len(birthdays))


if __name__ == "__main__":
    main()
