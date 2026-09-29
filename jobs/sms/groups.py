"""jobs/sms/groups.py — resolves a Watson SMS broadcast group ("anything
Bill defines") into a phone-number recipient list.

A group is a filter dict (dimension -> list of values, OR'd within and
across dimensions, ANDed with active_only) layered under manual
include/exclude overrides in sms_group_members (jobs/sms/schema.py's
CREATE_GROUPS/CREATE_GROUP_MEMBERS). Always excludes members with no phone
on file or unsubscribed=1 -- there is no override for that, even a manual
include.

filter_json shape (any/all keys optional, missing/empty = "no restriction
on this dimension"):
    {"deacons": [...], "teams": [...], "roles": [...], "campuses": [...]}

An entirely empty filter (with active_only true, the default) is
"Everyone" -- every active member with a phone.
"""
import json
import os
import sqlite3

from jobs.sms.carrier_lookup import normalize_phone

CONGREGATION_DB = os.path.expanduser("~/watson/data/congregation.db")


def _cong_conn():
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def group_options() -> dict:
    """Distinct values for each filterable dimension, for the group-builder
    UI's pickers. Blank/placeholder deacon values ('', '--') are dropped."""
    conn = _cong_conn()
    try:
        deacons = [r[0] for r in conn.execute(
            "SELECT DISTINCT deacon FROM members WHERE deacon IS NOT NULL AND TRIM(deacon) NOT IN ('', '--') ORDER BY deacon"
        ).fetchall()]
        teams = [r[0] for r in conn.execute(
            "SELECT DISTINCT team_name FROM team_memberships WHERE active = 1 ORDER BY team_name"
        ).fetchall()]
        roles = [r[0] for r in conn.execute(
            "SELECT DISTINCT role FROM leadership_roles WHERE is_active = 1 ORDER BY role"
        ).fetchall()]
        campuses = [r[0] for r in conn.execute(
            "SELECT DISTINCT campus_preference FROM members WHERE campus_preference IS NOT NULL AND TRIM(campus_preference) != '' ORDER BY campus_preference"
        ).fetchall()]
        return {"deacons": deacons, "teams": teams, "roles": roles, "campuses": campuses}
    finally:
        conn.close()


def resolve_filter(filter_json: dict, active_only: bool) -> list[dict]:
    """Resolves the filter dimensions only (no manual overrides) against
    congregation.db. Returns [{member_id, phone, contact_name}], deduped by
    phone, always excluding no-phone and unsubscribed members."""
    deacons = [d for d in (filter_json.get("deacons") or []) if d]
    teams = [t for t in (filter_json.get("teams") or []) if t]
    roles = [r for r in (filter_json.get("roles") or []) if r]
    campuses = [c for c in (filter_json.get("campuses") or []) if c]
    has_dimension = bool(deacons or teams or roles or campuses)

    conn = _cong_conn()
    try:
        base = (
            "SELECT id, name, phone FROM members "
            "WHERE phone IS NOT NULL AND TRIM(phone) != '' AND unsubscribed = 0"
        )
        params: list = []
        if active_only:
            base += " AND active = 'active'"

        if not has_dimension:
            rows = conn.execute(base, params).fetchall()
        else:
            member_ids: set[int] = set()
            if deacons:
                q = base + f" AND deacon IN ({','.join('?' * len(deacons))})"
                member_ids.update(r["id"] for r in conn.execute(q, params + deacons).fetchall())
            if teams:
                q = (
                    base + f" AND id IN (SELECT member_id FROM team_memberships "
                    f"WHERE active = 1 AND team_name IN ({','.join('?' * len(teams))}))"
                )
                member_ids.update(r["id"] for r in conn.execute(q, params + teams).fetchall())
            if roles:
                q = (
                    base + f" AND id IN (SELECT member_id FROM leadership_roles "
                    f"WHERE is_active = 1 AND role IN ({','.join('?' * len(roles))}))"
                )
                member_ids.update(r["id"] for r in conn.execute(q, params + roles).fetchall())
            if campuses:
                q = base + f" AND campus_preference IN ({','.join('?' * len(campuses))})"
                member_ids.update(r["id"] for r in conn.execute(q, params + campuses).fetchall())

            if not member_ids:
                rows = []
            else:
                placeholders = ",".join("?" * len(member_ids))
                rows = conn.execute(
                    base + f" AND id IN ({placeholders})", params + list(member_ids)
                ).fetchall()

        seen_phones: set[str] = set()
        resolved = []
        for r in rows:
            phone = normalize_phone(r["phone"])
            if not phone or phone in seen_phones:
                continue
            seen_phones.add(phone)
            resolved.append({"member_id": r["id"], "phone": phone, "contact_name": r["name"]})
        return resolved
    finally:
        conn.close()


def resolve_group(conn, group_id: int) -> tuple[dict | None, list[dict]]:
    """Loads a saved group from watson.db (`conn`), resolves its filter,
    then applies its manual include/exclude overrides. Returns (group_row
    as dict or None, recipients)."""
    row = conn.execute("SELECT * FROM sms_groups WHERE id = ?", (group_id,)).fetchone()
    if not row:
        return None, []

    group = dict(row)
    if group.get("manual_only"):
        recipients = []
    else:
        filter_json = json.loads(group["filter_json"] or "{}")
        recipients = resolve_filter(filter_json, bool(group["active_only"]))
    return group, apply_overrides(conn, group_id, recipients)


def apply_overrides(conn, group_id: int, recipients: list[dict]) -> list[dict]:
    """Layers sms_group_members include/exclude rows on top of a resolved
    filter's recipients. Include rows for a phone already present are a
    no-op; exclude always wins over both the filter match and an include
    row for the same phone (last write in the loop below)."""
    by_phone = {r["phone"]: r for r in recipients}

    overrides = conn.execute(
        "SELECT member_id, phone, contact_name, mode FROM sms_group_members WHERE group_id = ?",
        (group_id,),
    ).fetchall()
    for o in overrides:
        phone = normalize_phone(o["phone"])
        if not phone:
            continue
        if o["mode"] == "exclude":
            by_phone.pop(phone, None)
        else:
            by_phone.setdefault(phone, {"member_id": o["member_id"], "phone": phone, "contact_name": o["contact_name"]})

    return list(by_phone.values())
