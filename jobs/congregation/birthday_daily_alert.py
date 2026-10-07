"""
Birthday Daily Alert -- Telegram nudge every morning listing anyone in
the congregation whose birthday is today, so recipients can text them
directly. Distinct from birthday_report.py (that one is a monthly
look-ahead digest to the deacons, scoped to their own groups); this one
is same-day, congregation-wide, and goes to Dr. Bill, Jim Bouchat, and
Bill Crook.

Also mirrors into the SMS app (wtsn.me/sms), Bill-only per [[project_backlog
id=39]]: each birthday person is pinned to the top of Bill's thread list
with a highlight note (name + age), plus a push notification. The
highlight is display-only -- it never writes to draft_text, so the
compose box stays empty for Bill to type his own message (see
feedback_ai_never_originates_relational_language.md).

A birthday person under 18 is never handed their own SMS thread -- per
Dr. Bill (2026-09-29), the highlight/push routes to their parent(s)
instead (jobs/sms/alert_targets.py), naming the child in the note so
Bill knows who it's actually for. See anniversary_daily_alert.py for the
same pattern applied to wedding anniversaries.

Scope: members.active NOT IN (disconnected, deceased) AND residency = 'local'
(2026-09-24, replaces active = 1 AND member_status = 'active') AND birthdate's
month/day matches today. Telegram message includes phone number (when on
file) so Dr. Bill can act on the message directly without a lookup.

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
from typing import NamedTuple

from core.vacation import vacation_gate
from jobs.connect_cards.reports import _conn
from jobs.telegram.send_to_person import send_to_person

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

RECIPIENT_PERSON_IDS = [
    7,   # Bill Yomes
    254,  # Jim Bouchat
    78,  # Bill Crook
]


class Birthday(NamedTuple):
    id: int
    name: str
    age: int
    phone: str | None
    household_role: str | None


def _todays_birthdays() -> list[Birthday]:
    today = date.today()
    mmdd = today.strftime("%m-%d")

    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, birthdate, phone, household_role
            FROM members
            WHERE active NOT IN ('disconnected', 'deceased')
              AND residency = 'local'
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
        result.append(Birthday(
            id=row["id"],
            name=row["name"],
            age=today.year - birth_year,
            phone=row["phone"],
            household_role=row["household_role"],
        ))
    return result


def build_message(birthdays: list[Birthday]) -> str:
    lines = ["🎂 Birthdays today"]
    for b in birthdays:
        phone_part = b.phone if b.phone else "no phone on file"
        minor_note = " (minor -- SMS app routes to parent)" if b.age < 18 else ""
        lines.append(f"{b.name} (turning {b.age}): {phone_part}{minor_note}")
    lines.append("")
    return "\n".join(lines)


def _highlight_sms(birthdays: list[Birthday]) -> None:
    """Pins each birthday person to the top of the SMS app's thread list
    (wtsn.me/sms) with a note, and fires a push notification. For a minor,
    the target thread is a parent's (jobs.sms.alert_targets.resolve_targets),
    never the child's own number -- and if no parent has a phone on file,
    that person is silently skipped rather than falling back to texting the
    kid directly. Creates the thread if Bill has never texted the target
    before, same as an inbound text would. A push failure or missing phone
    must never break the rest of the alert -- each person is handled
    independently."""
    from core.database import get_connection as _watson_conn
    from jobs.sms.alert_targets import resolve_targets
    from jobs.sms.bridge import _get_or_create_thread
    from jobs.sms.carrier_lookup import normalize_phone
    from jobs.sms import push as sms_push
    from jobs.sms import settings as sms_settings

    with _conn() as cong:
        for b in birthdays:
            targets = resolve_targets(
                cong,
                member_id=b.id,
                name=b.name,
                phone=b.phone,
                age=b.age,
                household_role=b.household_role,
            )
            if not targets:
                if b.age < 18:
                    log.warning("birthday_daily_alert: no parent phone on file for minor %s, skipping SMS highlight", b.name)
                continue

            for target in targets:
                phone_digits = normalize_phone(target.phone)
                if not phone_digits:
                    continue

                note = f"\U0001F382 {target.honoree}'s birthday today -- turning {b.age}"
                try:
                    conn = _watson_conn()
                    try:
                        thread_id = _get_or_create_thread(conn, phone_digits, target.name)
                        conn.execute(
                            "UPDATE sms_threads SET highlight_note = ?, highlight_date = date('now') WHERE id = ?",
                            (note, thread_id),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                except Exception as exc:  # noqa: BLE001 -- one bad phone/thread can't block the rest
                    log.error("birthday_daily_alert: sms highlight failed for %s (target %s): %s", b.name, target.name, exc)
                    continue

                if sms_settings.should_silence_notifications():
                    continue
                try:
                    sms_push.send_push_to_all({
                        "title": f"\U0001F382 {target.honoree}'s birthday today",
                        "body": f"Turning {b.age} -- tap to send a text",
                        "thread_id": thread_id,
                        "url": f"/sms?thread={thread_id}",
                    })
                except Exception as exc:  # noqa: BLE001 -- a push failure must never break the alert
                    log.warning("birthday_daily_alert: sms push failed for thread_id=%s: %s", thread_id, exc)


def main():
    birthdays = _todays_birthdays()
    if not birthdays:
        log.info("birthday_daily_alert: no birthdays today, nothing sent")
        return

    message = build_message(birthdays)
    if vacation_gate("normal", "jobs.congregation.birthday_daily_alert", message):
        return

    for person_id in RECIPIENT_PERSON_IDS:
        ok = send_to_person(person_id, message)
        if not ok:
            log.error("birthday_daily_alert: failed to send to person_id=%s", person_id)
        else:
            log.info(
                "birthday_daily_alert: sent %d birthday(s) to person_id=%s",
                len(birthdays),
                person_id,
            )

    _highlight_sms(birthdays)


if __name__ == "__main__":
    main()
