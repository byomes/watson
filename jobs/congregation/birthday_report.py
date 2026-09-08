"""
Birthday Report -- monthly "birthdays coming up next month" Telegram digest.

Sent on the 15th of each month to Bill Crook and Jim Bouchat, listing every
active member whose birthdate (members.birthdate, YYYY-MM-DD) falls in the
following calendar month, sorted by day. Also flags active members with no
birthdate on file at all, so Bill C / Jim can chase that info down --
per Bill's 2026-09-08 request, this is a nudge to fill data gaps, not a
diff against some separate list they keep elsewhere.

Both the upcoming-birthdays and missing-birthdate sections are scoped to
each recipient's own deacon group (members.deacon, matching
deacon_reports.py's grouping) -- per Bill's 2026-09-08 follow-up, a deacon
should only be nudged about their own people by default. The full,
unscoped congregation-wide list is available via all_birthdays_message()
for on-demand use (e.g. someone explicitly asking for all of a given
month's birthdays) -- not wired to a bot command as of 2026-09-08, just
callable.

Scope: members.active = 1 AND member_status = 'active'. No shepherding/
attendance-history gate like deacon_reports.py's at-risk sections -- a
birthday list is for the whole active roster, not just engaged members.

Telegram-only via jobs/telegram/send_to_person.py, to Bill Crook (people.id
78) and Jim Bouchat (people.id 254) -- the same two deacons the elder
shepherding report goes to (jobs/congregation/elder_shepherding_report.py),
the only ones onboarded to Telegram as of 2026-09-08.

Cron (15th of each month, 8am):
  0 8 15 * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.birthday_report \
    >> /home/billyomes/watson/logs/birthday_report.log 2>&1

Usage:
  python3 -m jobs.congregation.birthday_report
"""

import calendar
import logging
from datetime import date

from jobs.connect_cards.reports import _conn
from jobs.telegram.send_to_person import send_to_person

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

RECIPIENTS = [(78, "Bill Crook"), (254, "Jim Bouchat")]  # (people.id, members.deacon)


def _next_month() -> tuple[int, int]:
    today = date.today()
    if today.month == 12:
        return today.year + 1, 1
    return today.year, today.month + 1


def _birthdays_for_month(month: int, deacon_name: str | None = None) -> list[tuple[int, str, int]]:
    mm = f"{month:02d}"
    query = """
        SELECT name, birthdate
        FROM members
        WHERE active = 1
          AND member_status = 'active'
          AND birthdate IS NOT NULL
          AND birthdate != ''
          AND substr(birthdate, 6, 2) = ?
    """
    params = [mm]
    if deacon_name is not None:
        query += " AND deacon = ?"
        params.append(deacon_name)
    query += " ORDER BY substr(birthdate, 9, 2)"

    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()

    result = []
    for row in rows:
        day = int(row["birthdate"][8:10])
        year = int(row["birthdate"][0:4])
        result.append((day, row["name"], year))
    return result


def _missing_birthdates(deacon_name: str) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM members
            WHERE active = 1
              AND member_status = 'active'
              AND deacon = ?
              AND (birthdate IS NULL OR birthdate = '')
            ORDER BY name
            """,
            (deacon_name,),
        ).fetchall()
    return [row["name"] for row in rows]


def _format_birthday_lines(birthdays: list[tuple[int, str, int]], month: int, year: int) -> list[str]:
    if not birthdays:
        return ["(none on file)"]
    return [
        f"{month:02d}/{day:02d} — {name} (turning {year - birth_year})"
        for day, name, birth_year in birthdays
    ]


def build_message(deacon_name: str) -> str:
    year, month = _next_month()
    month_name = calendar.month_name[month]

    birthdays = _birthdays_for_month(month, deacon_name)
    missing = _missing_birthdates(deacon_name)

    lines = [f"🎂 Birthdays in {month_name} {year} — your group"]
    lines.extend(_format_birthday_lines(birthdays, month, year))

    lines.append("")
    lines.append(f"Missing birthdate on file in your group ({len(missing)}):")
    if missing:
        lines.append(", ".join(missing))
    else:
        lines.append("(none — everyone in your group has a birthdate on file)")

    lines.append("")
    lines.append("- Watson")
    return "\n".join(lines)


def all_birthdays_message(month: int | None = None, year: int | None = None) -> str:
    """Whole-congregation birthday list for a month, unscoped by deacon group.

    For on-demand use when someone explicitly asks for all of a given
    month's birthdays, rather than just their own deacon group.
    """
    if month is None or year is None:
        year, month = _next_month()
    month_name = calendar.month_name[month]

    birthdays = _birthdays_for_month(month)

    lines = [f"🎂 All birthdays in {month_name} {year}"]
    lines.extend(_format_birthday_lines(birthdays, month, year))
    lines.append("")
    lines.append("- Watson")
    return "\n".join(lines)


def main():
    for person_id, deacon_name in RECIPIENTS:
        message = build_message(deacon_name)
        ok = send_to_person(person_id, message)
        if not ok:
            log.error("birthday_report: failed to send to person_id=%s", person_id)
        else:
            log.info("birthday_report: sent to person_id=%s", person_id)


if __name__ == "__main__":
    main()
