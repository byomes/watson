"""jobs/congregation/serving_edit.py -- congregation.db edits for the
banquet length-of-service tracking (members.started_serving_date,
members.service_pin_notes) and team rosters (team_memberships), added
2026-09-22 alongside jobs/analytics/data_chat.py's/bot.py's read-only
"how long has X been serving"/"what team is X on" Q&A -- see
[[project_servant_banquet_tracking]].

Restricted to Donna Redman via bot.py's _SERVING_TEAMS_EDIT_ALLOWLIST (the
onboarded-leader branch) plus a parallel, no-allowlist call from
_handle_general for Bill's own chat -- same two-wiring pattern
family_edit.py's module docstring describes for add_child/update_birthdate.
These are otherwise-unguarded free-text writes to congregation.db, so keep
the allowlist hardcoded in bot.py rather than opening it to every onboarded
leader (per Bill's 2026-09-22 request: "Donna and myself", not every leader).

_cascade below is the same exact->partial->last-word->first-word member
name resolution as family_edit.py's own copy -- kept as a separate copy
for the same reason that module gives (different columns needed), not
imported from there since family_edit.py's is a private (leading
underscore) helper not meant to be reused across modules."""
import re
import sqlite3

from jobs.congregation.family_edit import parse_birthdate
from jobs.people.lookup import CONG_DB


def _conn():
    conn = sqlite3.connect(CONG_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _cascade(conn, query: str, columns: str) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    words = query.split()

    def _q(term: str, exact: bool) -> list[dict]:
        op = "= ?" if exact else "LIKE ?"
        val = term if exact else f"%{term}%"
        rows = conn.execute(
            f"SELECT {columns} FROM members"
            f" WHERE active_v2 NOT IN ('disconnected', 'deceased') AND name {op} COLLATE NOCASE ORDER BY name",
            (val,),
        ).fetchall()
        return [dict(r) for r in rows]

    rows = _q(query, exact=True)
    if not rows:
        rows = _q(query, exact=False)
    if not rows and len(words) > 1:
        rows = _q(words[-1], exact=False)
        if len(rows) > 1:
            from jobs.people.lookup import _narrow_by_first_name
            rows = _narrow_by_first_name(rows, words[0])
    if not rows:
        rows = _q(words[0], exact=False)
    return rows


def _resolve_one_member(conn, query: str, columns: str) -> dict | str:
    hits = _cascade(conn, query, columns)
    if not hits:
        return f'I couldn\'t find anyone matching "{query}".'
    if len(hits) > 1:
        names = ", ".join(h["name"] for h in hits)
        return f'I found more than one match: {names}. Can you be more specific?'
    return hits[0]


def update_serving_date(name_query: str, date_raw: str, sender_name: str) -> str:
    """Set/correct a member's started_serving_date -- mirrors
    family_edit.update_birthdate exactly (same parse_birthdate reuse, same
    resolve-one-or-error shape)."""
    parsed = parse_birthdate(date_raw)  # generic loose-date parser, not birthdate-specific despite the name
    if not parsed:
        return f'I couldn\'t parse "{date_raw}" as a date.'

    with _conn() as conn:
        member = _resolve_one_member(conn, name_query, "id, name, started_serving_date")
        if isinstance(member, str):
            return member
        prior = member["started_serving_date"] or "none on file"
        conn.execute("UPDATE members SET started_serving_date = ? WHERE id = ?", (parsed, member["id"]))

    return f"Done — {member['name']}'s serving start date updated to {parsed} (was {prior}). — logged by {sender_name}"


def update_pin_notes(name_query: str, pin_text: str, sender_name: str) -> str:
    """Set/correct a member's service_pin_notes (free text, e.g. "2yr Pin",
    "15yr Pin in 01/2024") -- same resolve-one-or-error shape as
    update_serving_date above."""
    pin_text = (pin_text or "").strip(" .,")
    if not pin_text:
        return "I need the pin text to record, e.g. \"2yr Pin\"."

    with _conn() as conn:
        member = _resolve_one_member(conn, name_query, "id, name, service_pin_notes")
        if isinstance(member, str):
            return member
        prior = member["service_pin_notes"] or "none on file"
        conn.execute("UPDATE members SET service_pin_notes = ? WHERE id = ?", (pin_text, member["id"]))

    return f"Done — {member['name']}'s pin notes updated to \"{pin_text}\" (was \"{prior}\"). — logged by {sender_name}"


def _resolve_team_name(conn, team_query: str, member_id: int | None = None) -> str | list[str]:
    """Resolve a free-text team phrase to exactly one canonical team_name.
    When member_id is given (REMOVE path), only searches that member's own
    current teams -- a much narrower, low-ambiguity set, so removal never
    needs to consider every team in the church. When member_id is None (ADD
    path), searches every team_name ever used; a phrase matching zero
    existing teams is treated as a brand-new team (returned as-is, trimmed)
    rather than an error, since team rosters aren't a fixed enum -- adding
    someone to a team nobody's been on yet is a legitimate first use.
    Returns the resolved name, or a list of candidates if ambiguous (caller
    turns that into a "be more specific" reply)."""
    team_query = team_query.strip()
    if member_id is not None:
        rows = conn.execute(
            "SELECT DISTINCT team_name FROM team_memberships WHERE member_id = ? AND team_name LIKE ?",
            (member_id, f"%{team_query}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT team_name FROM team_memberships WHERE team_name LIKE ?",
            (f"%{team_query}%",),
        ).fetchall()
    names = [r["team_name"] for r in rows]
    if len(names) == 1:
        return names[0]
    if len(names) > 1:
        return names
    return team_query if member_id is None else []


def add_to_team(name_query: str, team_query: str, sender_name: str, position: str | None = None) -> str:
    with _conn() as conn:
        member = _resolve_one_member(conn, name_query, "id, name")
        if isinstance(member, str):
            return member
        resolved = _resolve_team_name(conn, team_query)
        if isinstance(resolved, list):
            return f'"{team_query}" matches more than one team: {", ".join(resolved)}. Can you be more specific?'
        conn.execute(
            "INSERT INTO team_memberships (member_id, team_name, position) VALUES (?, ?, ?) "
            "ON CONFLICT(member_id, team_name) DO UPDATE SET position = excluded.position",
            (member["id"], resolved, (position or "").strip() or None),
        )
    pos_note = f" ({position.strip()})" if position and position.strip() else ""
    return f"Done — added {member['name']} to {resolved}{pos_note}. — logged by {sender_name}"


def _normalize_team_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _resolve_existing_team_name(conn, team_query: str) -> str | list[str] | None:
    """Resolve a free-text team phrase to exactly one EXISTING team_name --
    fail-closed (returns None on no match) rather than _resolve_team_name's
    ADD-path behavior of treating an unmatched phrase as a brand-new team.
    Used by exclude/include_team_from_serving below, where there's no such
    thing as excluding a team that doesn't exist yet -- a silent "brand new
    team" fallback there would create a phantom row instead of erroring.
    Tries a plain substring match first, then a punctuation-insensitive
    match (e.g. "Building Maintenance" -> "BUILDING & MAINTENANCE") since
    real phrasing often drops the "&"; caught live 2026-09-23 when Donna's
    exact wording didn't substring-match the team's actual stored name."""
    team_query = team_query.strip()
    rows = conn.execute(
        "SELECT DISTINCT team_name FROM team_memberships WHERE team_name LIKE ?",
        (f"%{team_query}%",),
    ).fetchall()
    names = [r["team_name"] for r in rows]
    if not names:
        norm_query = _normalize_team_text(team_query)
        all_rows = conn.execute("SELECT DISTINCT team_name FROM team_memberships").fetchall()
        names = [r["team_name"] for r in all_rows if _normalize_team_text(r["team_name"]) == norm_query]
    if len(names) == 1:
        return names[0]
    if len(names) > 1:
        return names
    return None


def add_leadership_role(name_query: str, role_title: str, sender_name: str) -> str:
    """Upsert a leadership_roles row (member_id, role) -- same resolve-one-or-
    error shape as update_serving_date/update_pin_notes above. role_title is
    free text (a specific title, e.g. "Worship Leader"), stored lowercased to
    match this table's existing convention (see [[project_catalyst_leadership_roles]]).
    Reactivates via ON CONFLICT if the same (member, role) already exists but
    was deactivated, exactly like the dashboard's /api/members/<id>/roles
    upsert."""
    role_title = (role_title or "").strip(" .,")
    if not role_title:
        return "I need the title to record, e.g. \"Worship Leader\"."

    with _conn() as conn:
        member = _resolve_one_member(conn, name_query, "id, name")
        if isinstance(member, str):
            return member
        conn.execute(
            """INSERT INTO leadership_roles (member_id, role, is_active) VALUES (?, ?, 1)
               ON CONFLICT(member_id, role) DO UPDATE SET is_active = 1""",
            (member["id"], role_title.lower()),
        )
    return f"Done — {member['name']} tagged as \"{role_title}\". — logged by {sender_name}"


def exclude_team_from_serving(team_query: str, sender_name: str, note: str = "") -> str:
    """Hide a team from the wtsn.me/cat/serving Sunday check-off page
    (servants_web.py's get_serving_state, filtered against
    serving_excluded_teams) -- for teams that don't serve every Sunday.
    Does NOT touch team_memberships -- rosters/positions everywhere else
    are unaffected, only this one page's team list."""
    with _conn() as conn:
        resolved = _resolve_existing_team_name(conn, team_query)
        if resolved is None:
            return f'I couldn\'t find an existing team matching "{team_query}".'
        if isinstance(resolved, list):
            return f'"{team_query}" matches more than one team: {", ".join(resolved)}. Can you be more specific?'
        conn.execute(
            "INSERT INTO serving_excluded_teams (team_name, note) VALUES (?, ?) "
            "ON CONFLICT(team_name) DO UPDATE SET note = excluded.note",
            (resolved, (note or "").strip() or f"excluded by {sender_name}"),
        )
    return f"Done — {resolved} removed from the Sunday Serve list. — logged by {sender_name}"


def include_team_in_serving(team_query: str, sender_name: str) -> str:
    """Reverse of exclude_team_from_serving -- puts a team back on the
    Sunday check-off page."""
    with _conn() as conn:
        resolved = _resolve_existing_team_name(conn, team_query)
        if resolved is None:
            return f'I couldn\'t find an existing team matching "{team_query}".'
        if isinstance(resolved, list):
            return f'"{team_query}" matches more than one team: {", ".join(resolved)}. Can you be more specific?'
        deleted = conn.execute(
            "DELETE FROM serving_excluded_teams WHERE team_name = ?", (resolved,)
        ).rowcount
    if not deleted:
        return f"{resolved} wasn't excluded from the Sunday Serve list."
    return f"Done — {resolved} added back to the Sunday Serve list. — logged by {sender_name}"


def remove_from_team(name_query: str, team_query: str, sender_name: str) -> str:
    with _conn() as conn:
        member = _resolve_one_member(conn, name_query, "id, name")
        if isinstance(member, str):
            return member
        resolved = _resolve_team_name(conn, team_query, member_id=member["id"])
        # _resolve_team_name returns a list in TWO distinct cases here (no
        # member_id passed to force a bare string fallback like add_to_team
        # gets) -- empty (no match) vs multiple (ambiguous) -- these need
        # different replies, so check length, not just isinstance. A bug
        # found testing this 2026-09-22: checking isinstance(resolved, list)
        # alone caught the empty-list "no match" case as if it were
        # "matches more than one", producing a nonsense "matches more than
        # one of X's teams: " (empty list) reply instead of listing X's
        # actual current teams.
        if isinstance(resolved, list) and not resolved:
            current = [
                r["team_name"] for r in conn.execute(
                    "SELECT team_name FROM team_memberships WHERE member_id = ? ORDER BY team_name", (member["id"],)
                ).fetchall()
            ]
            if not current:
                return f"{member['name']} isn't on any team."
            return f'{member["name"]} isn\'t on a team matching "{team_query}". Their teams: {", ".join(current)}.'
        if isinstance(resolved, list):
            return f'"{team_query}" matches more than one of {member["name"]}\'s teams: {", ".join(resolved)}. Can you be more specific?'
        conn.execute(
            "DELETE FROM team_memberships WHERE member_id = ? AND team_name = ?", (member["id"], resolved)
        )
    return f"Done — removed {member['name']} from {resolved}. — logged by {sender_name}"
