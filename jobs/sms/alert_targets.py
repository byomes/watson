"""jobs/sms/alert_targets.py -- who actually gets an SMS pinned/highlighted
for a same-day congregation alert (birthday, anniversary, ...).

An adult (age unknown, or 18+) is texted directly at their own number, same
as always. A minor -- under 18 by birthdate, or household_role='child' with
no birthdate on file -- is never handed their own SMS thread: the highlight
goes to whichever parent/guardian in their household (household_role IN
('husband','wife','head'), same lookup as jobs/analytics/data_chat.py's
"who are X's parents" pattern) has a phone on file instead. Per Dr. Bill,
2026-09-29: a kid isn't who Bill should be texting, even for their own
birthday.

`cong_conn` is a connection to congregation.db (e.g. from
jobs.connect_cards.reports._conn()), since household_id/household_role
live there, not in watson.db.
"""
from typing import NamedTuple


class AlertTarget(NamedTuple):
    name: str          # whose thread this is -- may be a parent, not the honoree
    phone: str
    honoree: str        # who the alert is actually about
    via_parent: bool


def resolve_targets(
    cong_conn,
    *,
    member_id: int,
    name: str,
    phone: str | None,
    age: int | None,
    household_role: str | None,
) -> list[AlertTarget]:
    """Returns 0+ targets. Empty means "nobody to text" -- e.g. a minor with
    no parent on file with a phone, or an adult with no phone on file. Never
    falls back to the minor's own phone."""
    is_minor = (age is not None and age < 18) or (age is None and household_role == "child")

    if not is_minor:
        if not phone:
            return []
        return [AlertTarget(name=name, phone=phone, honoree=name, via_parent=False)]

    parent_rows = cong_conn.execute(
        """
        SELECT m2.name, m2.phone
        FROM members m1
        JOIN members m2 ON m2.household_id = m1.household_id AND m2.id != m1.id
        WHERE m1.id = ?
          AND m2.household_role IN ('husband', 'wife', 'head')
          AND m2.phone IS NOT NULL AND m2.phone != ''
        """,
        (member_id,),
    ).fetchall()
    return [
        AlertTarget(name=row["name"], phone=row["phone"], honoree=name, via_parent=True)
        for row in parent_rows
    ]
