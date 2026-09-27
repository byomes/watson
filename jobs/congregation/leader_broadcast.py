"""jobs/congregation/leader_broadcast.py — resolves elders/staff/deacons/
"all leaders"/named-leader Telegram broadcast targets for bot.py's
"send a message to <group>: <text>" fast path (added 2026-09-27).

No LLM/API call anywhere in this module or its caller -- Bill's own exact
wording is relayed to recipients, never composed, per
[[feedback_ai_never_originates_relational_language]]. This module only
resolves WHO a group phrase means and WHETHER they're reachable over
Telegram; it never touches message content.

- "elders"/"staff" -> congregation.db leadership_roles (is_active=1),
  scoped to the role values Bill actually uses for those titles.
- "deacons" -> jobs.congregation.deacon_reports.list_deacons() -- the same
  members.deacon-derived roster used everywhere else in this codebase
  (DEACON_OPTIONS in the catalystdb grid, deacon reports, etc.), not a
  second/different deacon concept.
- "all leaders" -> union of every active leadership_roles holder, every
  deacon, and every active watson.db team_members row.
- named leader (anything else) -> cascade-matched against that same
  all_leaders() pool: exact -> partial -> last name (narrowed by
  first-name/nickname via jobs.people.lookup._narrow_by_first_name).

Reachability is watson.db people.telegram_chat_id -- a name with no chat_id
on file is reported back to the caller as unreachable, never silently
dropped (Bill's 2026-09-27 decision).
"""
import os
import sqlite3

CONG_DB = os.path.expanduser("~/watson/data/congregation.db")
WATSON_DB = os.path.expanduser("~/watson/data/watson.db")

_ELDER_ROLES = {"elder", "teaching elder", "shepherding elder", "financial elder"}
_STAFF_ROLES = {"staff"}


def _cong_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CONG_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _watson_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _names_by_leadership_role(roles: set[str]) -> list[str]:
    placeholders = ",".join("?" for _ in roles)
    with _cong_conn() as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT m.name FROM leadership_roles lr
                JOIN members m ON m.id = lr.member_id
                WHERE lr.is_active = 1 AND lr.role IN ({placeholders})
                ORDER BY m.name""",
            tuple(roles),
        ).fetchall()
    return [r["name"] for r in rows]


def elders() -> list[str]:
    return _names_by_leadership_role(_ELDER_ROLES)


def staff() -> list[str]:
    return _names_by_leadership_role(_STAFF_ROLES)


def deacons() -> list[str]:
    from jobs.congregation.deacon_reports import list_deacons
    return list_deacons()


_OWNER_NAMES = {"bill yomes", "dr. bill yomes"}


def all_leaders() -> list[str]:
    """Union of every active leadership_roles holder, every deacon, and
    every team_members row that's both active AND status='active' (most
    team_members rows carry active=1 but status='stalled' -- a genuinely
    disengaged/placeholder roster entry, not someone broadcasts should
    reach; see jobs.people.lookup._OWNER_NAME for the same active/status
    split elsewhere in this codebase). Bill himself is excluded -- he's
    always the sender, never a broadcast target. Broadest "leader" pool
    Watson knows about, and the pool named-leader matching searches."""
    with _cong_conn() as conn:
        lr_rows = conn.execute(
            "SELECT DISTINCT m.name FROM leadership_roles lr "
            "JOIN members m ON m.id = lr.member_id WHERE lr.is_active = 1"
        ).fetchall()
    names = {r["name"] for r in lr_rows}
    names |= set(deacons())
    with _watson_conn() as conn:
        tm_rows = conn.execute(
            "SELECT DISTINCT name FROM team_members WHERE active = 1 AND status = 'active'"
        ).fetchall()
    names |= {r["name"] for r in tm_rows}
    names = {n for n in names if n.strip().lower() not in _OWNER_NAMES}
    return sorted(names)


# Alias phrase (lowercase, whitespace-collapsed) -> (canonical label, resolver)
_GROUPS: dict[str, tuple[str, "callable[[], list[str]]"]] = {}
for _alias in ("elder", "elders"):
    _GROUPS[_alias] = ("elders", elders)
for _alias in ("staff",):
    _GROUPS[_alias] = ("staff", staff)
for _alias in ("deacon", "deacons"):
    _GROUPS[_alias] = ("deacons", deacons)
for _alias in ("leader", "leaders", "all leaders", "everyone", "all", "team"):
    _GROUPS[_alias] = ("all leaders", all_leaders)


def resolve_group(text: str) -> tuple[str, list[str]] | None:
    """Returns (canonical_label, names) if `text` is a known fixed-group
    phrase, else None (caller should then try resolve_named_leader)."""
    key = " ".join(text.strip().lower().split())
    entry = _GROUPS.get(key)
    if not entry:
        return None
    label, resolver = entry
    return label, resolver()


def resolve_named_leader(text: str) -> list[str]:
    """Cascade-match free text against all_leaders(): exact -> partial ->
    last name (narrowed by first-name/nickname) -> first name. Returns
    every match still standing, so the caller can tell an unambiguous hit
    from a name needing more specificity."""
    from jobs.people.lookup import _narrow_by_first_name

    query = " ".join(text.strip().split())
    if not query:
        return []
    pool = all_leaders()
    lower_pool = {n.lower(): n for n in pool}

    exact = lower_pool.get(query.lower())
    if exact:
        return [exact]

    words = query.split()
    partial = [n for n in pool if query.lower() in n.lower()]
    if partial:
        return partial

    if len(words) > 1:
        last = [n for n in pool if words[-1].lower() in n.lower()]
        if len(last) > 1:
            narrowed = _narrow_by_first_name([{"name": n} for n in last], words[0])
            return [r["name"] for r in narrowed]
        if last:
            return last

    return [n for n in pool if words[0].lower() in n.lower()]


def recipients_with_status(names: list[str]) -> tuple[list[dict], list[str]]:
    """Splits `names` into (reachable [{"name", "chat_id"}], unreachable
    [name, ...]) based on watson.db people.telegram_chat_id."""
    if not names:
        return [], []
    placeholders = ",".join("?" for _ in names)
    with _watson_conn() as conn:
        rows = conn.execute(
            f"SELECT name, telegram_chat_id FROM people WHERE name COLLATE NOCASE IN ({placeholders})",
            names,
        ).fetchall()
    chat_ids = {r["name"].lower(): r["telegram_chat_id"] for r in rows}
    reachable = []
    unreachable = []
    for n in names:
        chat_id = chat_ids.get(n.lower())
        if chat_id:
            reachable.append({"name": n, "chat_id": chat_id})
        else:
            unreachable.append(n)
    return reachable, unreachable
