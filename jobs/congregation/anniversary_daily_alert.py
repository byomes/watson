"""
Anniversary Daily Alert -- same pattern as birthday_daily_alert.py, for
wedding anniversaries: a Telegram nudge every morning to Dr. Bill, Jim
Bouchat, and Bill Crook, plus a pinned highlight + push notification on
the SMS app (wtsn.me/sms).

Anniversaries are couple-level, not member-level: two spouses sharing a
household usually have the SAME anniversary date recorded on BOTH of
their member rows (see jobs/congregation/family_dates.py's
record_anniversaries). This job groups matching rows by household_id so
a married couple produces ONE Telegram line, not two -- and on the SMS
side, each distinct phone number in the couple gets the highlight (so
whichever thread Bill actually uses to reach them shows the note), but
the same phone is never highlighted twice even if both spouses' rows
point at it.

Reuses jobs/sms/alert_targets.py's minor-routing check defensively (a
household_role='child' member should never end up here since
anniversaries only ever get recorded on 'husband'/'wife' rows, but if bad
data ever put one there, this refuses to text a kid rather than assuming
it's safe).

Scope: members.active NOT IN (disconnected, deceased) AND residency = 'local'
AND anniversary's month/day matches today.

Sends nothing when no one has an anniversary today -- a daily "no
anniversaries" message would just be noise.

Cron (7:05am daily, right after birthday_daily_alert):
  5 7 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.anniversary_daily_alert \
    >> /home/billyomes/watson/logs/anniversary_daily_alert.log 2>&1

Usage:
  python3 -m jobs.congregation.anniversary_daily_alert
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


class Member(NamedTuple):
    id: int
    name: str
    phone: str | None
    household_role: str | None


class Couple(NamedTuple):
    names: str
    years: int
    phone: str | None  # first phone on file among the group, for the Telegram line
    members: list[Member]


def _todays_anniversaries() -> list[Couple]:
    today = date.today()
    mmdd = today.strftime("%m-%d")

    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, anniversary, phone, household_id, household_role
            FROM members
            WHERE active NOT IN ('disconnected', 'deceased')
              AND residency = 'local'
              AND anniversary IS NOT NULL
              AND anniversary != ''
              AND substr(anniversary, 6, 5) = ?
            ORDER BY household_id, household_role
            """,
            (mmdd,),
        ).fetchall()

    groups: dict[str, list] = {}
    order: list[str] = []
    for row in rows:
        key = row["household_id"] or f"solo-{row['id']}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    couples = []
    for key in order:
        group_rows = groups[key]
        anniv_year = int(group_rows[0]["anniversary"][0:4])
        members = [
            Member(id=r["id"], name=r["name"], phone=r["phone"], household_role=r["household_role"])
            for r in group_rows
        ]
        couples.append(Couple(
            names=" & ".join(m.name for m in members),
            years=today.year - anniv_year,
            phone=next((m.phone for m in members if m.phone), None),
            members=members,
        ))
    return couples


def build_message(couples: list[Couple]) -> str:
    lines = ["💍 Anniversaries today"]
    for c in couples:
        phone_part = c.phone if c.phone else "no phone on file"
        lines.append(f"{c.names} ({c.years} years): {phone_part}")
    lines.append("")
    return "\n".join(lines)


def _highlight_sms(couples: list[Couple]) -> None:
    """Pins each couple's anniversary to the top of the SMS app's thread
    list, once per distinct phone number in the couple. A push failure or
    missing phone must never break the rest of the alert."""
    from core.database import get_connection as _watson_conn
    from jobs.sms.alert_targets import resolve_targets
    from jobs.sms.bridge import _get_or_create_thread
    from jobs.sms.carrier_lookup import normalize_phone
    from jobs.sms import push as sms_push
    from jobs.sms import settings as sms_settings

    with _conn() as cong:
        for couple in couples:
            seen_phones: set[str] = set()
            for member in couple.members:
                if not member.phone:
                    continue

                targets = resolve_targets(
                    cong,
                    member_id=member.id,
                    name=member.name,
                    phone=member.phone,
                    age=None,
                    household_role=member.household_role,
                )
                if not targets:
                    log.warning(
                        "anniversary_daily_alert: no valid SMS target for %s (household %s), skipping",
                        member.name, couple.names,
                    )
                    continue

                for target in targets:
                    phone_digits = normalize_phone(target.phone)
                    if not phone_digits or phone_digits in seen_phones:
                        continue
                    seen_phones.add(phone_digits)

                    note = f"\U0001F48D {couple.names} anniversary today -- {couple.years} years"
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
                        log.error("anniversary_daily_alert: sms highlight failed for %s: %s", couple.names, exc)
                        continue

                    if sms_settings.should_silence_notifications():
                        continue
                    try:
                        sms_push.send_push_to_all({
                            "title": f"\U0001F48D {couple.names}'s anniversary today",
                            "body": f"{couple.years} years -- tap to send a text",
                            "thread_id": thread_id,
                            "url": f"/sms?thread={thread_id}",
                        })
                    except Exception as exc:  # noqa: BLE001 -- a push failure must never break the alert
                        log.warning("anniversary_daily_alert: sms push failed for thread_id=%s: %s", thread_id, exc)


def main():
    couples = _todays_anniversaries()
    if not couples:
        log.info("anniversary_daily_alert: no anniversaries today, nothing sent")
        return

    message = build_message(couples)
    if vacation_gate("normal", "jobs.congregation.anniversary_daily_alert", message):
        return

    for person_id in RECIPIENT_PERSON_IDS:
        ok = send_to_person(person_id, message)
        if not ok:
            log.error("anniversary_daily_alert: failed to send to person_id=%s", person_id)
        else:
            log.info(
                "anniversary_daily_alert: sent %d anniversary(ies) to person_id=%s",
                len(couples),
                person_id,
            )

    _highlight_sms(couples)


if __name__ == "__main__":
    main()
