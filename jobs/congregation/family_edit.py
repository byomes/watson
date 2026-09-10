"""jobs/congregation/family_edit.py -- Telegram-driven congregation.db edits:
adding a child as a new member record, and correcting an existing member's
birthdate.

Restricted to Bill Crook, Jim Bouchat, Donna Redman, and Bill Yomes --
bot.py's _FAMILY_EDIT_ALLOWLIST for the first three (routed through
_handle_text_body's onboarded-leader branch) and a parallel, no-allowlist
call from _handle_general for Bill's own chat (same pattern as
_extract_deacon_assign / _DEACON_ASSIGN_ALLOWLIST). These are otherwise-
unguarded free-text writes to congregation.db, so keep the allowlist
hardcoded here rather than opening it to every onboarded leader.

Added 2026-09-10 after Sophia DiMatteo turned 18 and birthday_daily_alert.py
never caught it -- she'd never been entered as her own `members` row, just
missing entirely from the DiMatteo household (household_id H023). Children
are frequently never added as their own member record; this gives the four
allowlisted people a fast Telegram path to add them (and fix birthdates in
general) without dashboard/web access.
"""
import sqlite3
from datetime import date, datetime

from dateutil import parser as _dateutil_parser

from jobs.people.lookup import CONG_DB


def _conn():
    conn = sqlite3.connect(CONG_DB)
    conn.row_factory = sqlite3.Row
    return conn


def parse_birthdate(raw: str) -> str | None:
    """Parse a loosely-formatted date into YYYY-MM-DD, or None if it isn't
    parseable or isn't a plausible birthdate (in the future, or before 1900)."""
    raw = (raw or "").strip().strip(".,!")
    if not raw:
        return None
    try:
        parsed = _dateutil_parser.parse(raw, default=datetime(2000, 1, 1))
    except (ValueError, OverflowError):
        return None
    d = parsed.date()
    if d > date.today() or d.year < 1900:
        return None
    return d.isoformat()


def _cascade(conn, query: str, columns: str) -> list[dict]:
    """Same exact->partial->last-word->first-word cascade as jobs.people.lookup,
    scoped to active members only -- kept as its own copy since this needs
    different columns (household_id/address/campus_preference/deacon for
    add_child, birthdate for update_birthdate) than lookup.py's variants."""
    query = query.strip()
    if not query:
        return []
    words = query.split()

    def _q(term: str, exact: bool) -> list[dict]:
        op = "= ?" if exact else "LIKE ?"
        val = term if exact else f"%{term}%"
        rows = conn.execute(
            f"SELECT {columns} FROM members"
            f" WHERE active = 1 AND name {op} COLLATE NOCASE ORDER BY name",
            (val,),
        ).fetchall()
        return [dict(r) for r in rows]

    rows = _q(query, exact=True)
    if not rows:
        rows = _q(query, exact=False)
    if not rows and len(words) > 1:
        rows = _q(words[-1], exact=False)
    if not rows:
        rows = _q(words[0], exact=False)
    return rows


def add_child(child_name: str, parent_query: str, date_raw: str, sender_name: str) -> str:
    child_name = child_name.strip()
    birthdate = parse_birthdate(date_raw)
    if not birthdate:
        return f'I couldn\'t parse "{date_raw}" as a birthdate.'

    with _conn() as conn:
        parents = _cascade(conn, parent_query, "id, name, household_id, address, campus_preference, deacon")
        if not parents:
            return f'I couldn\'t find an active member matching "{parent_query}" to attach {child_name} to.'
        if len(parents) > 1:
            names = ", ".join(p["name"] for p in parents)
            return f'I found more than one match for "{parent_query}": {names}. Can you be more specific?'
        parent = parents[0]

        existing = _cascade(conn, child_name, "name, birthdate")
        exact = [m for m in existing if m["name"].lower() == child_name.lower()]
        if exact:
            on_file = exact[0]["birthdate"] or "none on file"
            return (
                f'{exact[0]["name"]} is already on file (birthdate: {on_file}). Say something like '
                f'"{exact[0]["name"]}\'s birthday is {birthdate}" instead if that\'s wrong.'
            )

        conn.execute(
            """
            INSERT INTO members (name, campus_preference, status, active, member_status,
                                  address, household_id, deacon, birthdate)
            VALUES (?, ?, 'visitor', 1, 'active', ?, ?, ?, ?)
            """,
            (child_name, parent["campus_preference"], parent["address"],
             parent["household_id"], parent["deacon"], birthdate),
        )

    age = date.today().year - int(birthdate[:4])
    return f"Added {child_name} (born {birthdate}, turning {age}) to {parent['name']}'s household. — logged by {sender_name}"


def update_birthdate(name_query: str, date_raw: str, sender_name: str) -> str:
    birthdate = parse_birthdate(date_raw)
    if not birthdate:
        return f'I couldn\'t parse "{date_raw}" as a birthdate.'

    with _conn() as conn:
        hits = _cascade(conn, name_query, "id, name, birthdate")
        if not hits:
            return f'I couldn\'t find anyone matching "{name_query}".'
        if len(hits) > 1:
            names = ", ".join(h["name"] for h in hits)
            return f'I found more than one match: {names}. Can you be more specific?'
        member = hits[0]
        prior = member["birthdate"] or "none on file"

        conn.execute("UPDATE members SET birthdate = ? WHERE id = ?", (birthdate, member["id"]))

    return f"Done — {member['name']}'s birthday updated to {birthdate} (was {prior}). — logged by {sender_name}"
