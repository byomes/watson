"""jobs/congregation/family_edit.py -- congregation.db edits reachable from
Telegram and (mark_spouse/mark_child only) the deacon app: adding a child as
a new member record, correcting an existing member's birthdate, and (added
2026-09-12) marking spouse/child relationships between existing members via
household_id + household_role.

add_child/update_birthdate are restricted to Bill Crook, Jim Bouchat, Donna
Redman, and Bill Yomes -- bot.py's _FAMILY_EDIT_ALLOWLIST for the first
three (routed through _handle_text_body's onboarded-leader branch) and a
parallel, no-allowlist call from _handle_general for Bill's own chat (same
pattern as _extract_deacon_assign / _DEACON_ASSIGN_ALLOWLIST). These are
otherwise-unguarded free-text writes to congregation.db, so keep the
allowlist hardcoded here rather than opening it to every onboarded leader.

Added 2026-09-10 after Sophia DiMatteo turned 18 and birthday_daily_alert.py
never caught it -- she'd never been entered as her own `members` row, just
missing entirely from the DiMatteo household (household_id H023). Children
are frequently never added as their own member record; this gives the four
allowlisted people a fast Telegram path to add them (and fix birthdates in
general) without dashboard/web access.

mark_spouse/mark_child (2026-09-12): Pastor Tyler asked Watson "who is
so-and-so's wife" and there was no way to answer it -- household_id groups a
family together but never said WHO within it is the spouse vs. a child vs.
the head, and matching on last name alone would wrongly conflate siblings
or parent/child pairs sharing a surname. household_role (see
migrate_household_role.py) plus the existing household_id grouping answers
this with a plain self-join -- see jobs/analytics/data_chat.py's spouse/
child/parent examples. These two are the write side: they let a leader tell
Watson about a relationship between two members ALREADY on file (add_child
above stays the tool for a brand-new member). Per Bill's 2026-09-12 request
("I want all leaders to be able to help manage families"), these are
deliberately NOT gated by _FAMILY_EDIT_ALLOWLIST -- open to every onboarded
leader via Telegram (bot.py) and every logged-in deacon via the deacon app
(deacons_web.py's mark_spouse_by_id/mark_child_by_id below, id-based since
that frontend already has both members' ids from the roster it loaded, so
none of _resolve_one's fuzzy-match ambiguity handling applies there)."""
import re
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
                                  address, household_id, deacon, birthdate, household_role)
            VALUES (?, ?, 'visitor', 1, 'active', ?, ?, ?, ?, 'child')
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


_HOUSEHOLD_ID_RE = re.compile(r"^H(\d+)$")


def _next_household_id(conn) -> str:
    """H001-style id one past the highest currently assigned -- matches the
    format import_deacon_directory.py's CSV import already uses."""
    max_n = 0
    for row in conn.execute("SELECT DISTINCT household_id FROM members WHERE household_id IS NOT NULL"):
        m = _HOUSEHOLD_ID_RE.match(row["household_id"] or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"H{max_n + 1:03d}"


def _resolve_one(conn, query: str) -> dict | str:
    """Single matching active member (id/name/household_id/household_role),
    or an error string (no match / ambiguous) for the caller to return as-is.
    Used by the Telegram (free-text) entry points -- the deacon-app entry
    points below use _resolve_by_id instead, since that frontend already
    has a specific member's id from the roster it already loaded and never
    needs fuzzy matching."""
    hits = _cascade(conn, query, "id, name, household_id, household_role")
    if not hits:
        return f'I couldn\'t find anyone matching "{query}".'
    if len(hits) > 1:
        names = ", ".join(h["name"] for h in hits)
        return f'I found more than one match for "{query}": {names}. Can you be more specific?'
    return hits[0]


def _resolve_by_id(conn, member_id: int) -> dict | str:
    """Single active member by id (id/name/household_id/household_role), or
    an error string for the caller to return as-is."""
    row = conn.execute(
        "SELECT id, name, household_id, household_role FROM members WHERE id = ? AND active = 1",
        (member_id,),
    ).fetchone()
    return dict(row) if row else f"No active member with id {member_id}."


def _join_households(conn, a: dict, b: dict) -> str | None:
    """Reconciles a's and b's household_id so both end up in the same one,
    mutating whichever dict needs to change (and writing it to the DB).
    Refuses (returns an error string instead) when both already belong to
    different, populated households -- that's a merge of two families,
    which risks scrambling whoever else is already in either one; a human
    needs to sort that out via dashboard Member Management first. Returns
    None on success."""
    a_hh, b_hh = a["household_id"], b["household_id"]
    if a_hh and b_hh and a_hh != b_hh:
        return (
            f'{a["name"]} is in household {a_hh} and {b["name"]} is in household {b_hh} -- '
            "I won't merge two different households automatically since other people may "
            "already be in them. Fix one of their household assignments first (dashboard "
            "Member Management), then try again."
        )
    if a_hh and not b_hh:
        conn.execute("UPDATE members SET household_id = ? WHERE id = ?", (a_hh, b["id"]))
        b["household_id"] = a_hh
    elif b_hh and not a_hh:
        conn.execute("UPDATE members SET household_id = ? WHERE id = ?", (b_hh, a["id"]))
        a["household_id"] = b_hh
    elif not a_hh and not b_hh:
        new_hh = _next_household_id(conn)
        conn.execute("UPDATE members SET household_id = ? WHERE id IN (?, ?)", (new_hh, a["id"], b["id"]))
        a["household_id"] = b["household_id"] = new_hh
    return None


def _other_role_holders(conn, household_id: str, role: str, exclude_ids: set[int]) -> list[str]:
    """Names of other active members already holding `role` in `household_id`,
    excluding exclude_ids -- used to flag a likely-stale prior spouse/head
    left behind by a remarriage or correction, rather than silently letting
    two people end up tagged the same role in one household."""
    placeholders = ",".join("?" for _ in exclude_ids) or "NULL"
    rows = conn.execute(
        f"SELECT name FROM members WHERE household_id = ? AND household_role = ? "
        f"AND active = 1 AND id NOT IN ({placeholders})",
        (household_id, role, *exclude_ids),
    ).fetchall()
    return [r["name"] for r in rows]


def _mark_spouse_core(conn, a: dict, b: dict, sender_name: str) -> tuple[bool, str]:
    """Shared logic behind mark_spouse (Telegram, free-text) and
    mark_spouse_by_id (deacon app, already has both ids from the roster it
    loaded) -- household_role 'head'/'spouse', sharing one household_id.
    Whichever of the two already holds 'head' keeps it; otherwise `a`
    becomes 'head' and `b` becomes 'spouse' (arbitrary but consistent --
    the two roles are interchangeable for answering "who is X's spouse",
    no hierarchy implied). Returns (ok, message)."""
    if a["id"] == b["id"]:
        return False, f"{a['name']} can't be their own spouse."
    if a["household_role"] == "child" or b["household_role"] == "child":
        child = a if a["household_role"] == "child" else b
        return False, (
            f'{child["name"]} is on file as a child -- did you mean to mark them as a spouse? '
            "If that's right, fix their role first."
        )

    merge_error = _join_households(conn, a, b)
    if merge_error:
        return False, merge_error

    role_a, role_b = ("spouse", "head") if b["household_role"] == "head" else ("head", "spouse")
    warn_names = _other_role_holders(conn, a["household_id"], role_b, {a["id"], b["id"]})

    conn.execute("UPDATE members SET household_role = ? WHERE id = ?", (role_a, a["id"]))
    conn.execute("UPDATE members SET household_role = ? WHERE id = ?", (role_b, b["id"]))

    message = f"Done — {a['name']} and {b['name']} are now marked as spouses (household {a['household_id']}). — logged by {sender_name}"
    if warn_names:
        message += f" Note: {', '.join(warn_names)} was also marked '{role_b}' in that household — you may want to review."
    return True, message


def _mark_child_core(conn, child: dict, parent: dict, sender_name: str) -> tuple[bool, str]:
    """Shared logic behind mark_child (Telegram) and mark_child_by_id
    (deacon app) -- household_role 'child' for `child`, sharing `parent`'s
    household_id. `parent`'s own role defaults to 'head' if unset; an
    existing 'head'/'spouse' role is left as-is. Returns (ok, message)."""
    if child["id"] == parent["id"]:
        return False, f"{child['name']} can't be their own parent."
    if parent["household_role"] == "child":
        return False, (
            f'{parent["name"]} is on file as a child themselves -- are you sure they\'re '
            f'{child["name"]}\'s parent? Fix their role first if not.'
        )

    merge_error = _join_households(conn, child, parent)
    if merge_error:
        return False, merge_error

    if not parent["household_role"]:
        conn.execute("UPDATE members SET household_role = 'head' WHERE id = ?", (parent["id"],))
    conn.execute("UPDATE members SET household_role = 'child' WHERE id = ?", (child["id"],))

    message = f"Done — {child['name']} is now marked as {parent['name']}'s child (household {parent['household_id']}). — logged by {sender_name}"
    return True, message


def mark_spouse(name1_query: str, name2_query: str, sender_name: str) -> str:
    """Telegram entry point -- free-text name matching. Use add_child()
    instead of this for someone not yet in congregation.db."""
    with _conn() as conn:
        a = _resolve_one(conn, name1_query)
        if isinstance(a, str):
            return a
        b = _resolve_one(conn, name2_query)
        if isinstance(b, str):
            return b
        _, message = _mark_spouse_core(conn, a, b, sender_name)
    return message


def mark_child(child_query: str, parent_query: str, sender_name: str) -> str:
    """Telegram entry point -- free-text name matching. Use add_child()
    instead of this for someone not yet in congregation.db."""
    with _conn() as conn:
        child = _resolve_one(conn, child_query)
        if isinstance(child, str):
            return child
        parent = _resolve_one(conn, parent_query)
        if isinstance(parent, str):
            return parent
        _, message = _mark_child_core(conn, child, parent, sender_name)
    return message


def mark_spouse_by_id(member_id: int, spouse_id: int, sender_name: str) -> tuple[bool, str]:
    """Deacon-app entry point (jobs/congregation/deacons_web.py) -- both
    ids come from the roster the app already loaded, so no fuzzy matching
    or ambiguity handling is needed here."""
    with _conn() as conn:
        a = _resolve_by_id(conn, member_id)
        if isinstance(a, str):
            return False, a
        b = _resolve_by_id(conn, spouse_id)
        if isinstance(b, str):
            return False, b
        return _mark_spouse_core(conn, a, b, sender_name)


def mark_child_by_id(child_id: int, parent_id: int, sender_name: str) -> tuple[bool, str]:
    """Deacon-app entry point -- see mark_spouse_by_id."""
    with _conn() as conn:
        child = _resolve_by_id(conn, child_id)
        if isinstance(child, str):
            return False, child
        parent = _resolve_by_id(conn, parent_id)
        if isinstance(parent, str):
            return False, parent
        return _mark_child_core(conn, child, parent, sender_name)
