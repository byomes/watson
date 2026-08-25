"""jobs/congregation/deacon_admin_api.py — Flask Blueprint exposing a
key-gated congregation People / Deacon-assignment API for the standalone
watson-people Vercel app (Jim Bouchat's admin tool).

Mount on the Watson dashboard app:
    from jobs.congregation.deacon_admin_api import deacon_admin_bp
    app.register_blueprint(deacon_admin_bp)

Auth: same shared-secret pattern as jobs/writing_room/api.py -- every route
requires header X-Watson-Key matching DEACON_ADMIN_API_KEY. This is a
DEDICATED key, not WRITING_ROOM_API_KEY or any other consumer's key, per
this codebase's one-key-per-external-consumer convention.

Deliberately separate from the dashboard's own /api/members* routes
(jobs/dashboard/app.py) rather than extending them: those routes currently
have no auth guard of their own (they were built assuming Tailscale-only
network access), so bolting external write access onto them would widen
that existing exposure. Locking down /api/members* is tracked as its own
follow-up task.
"""
import os
from functools import wraps

from flask import Blueprint, jsonify, request

from jobs.connect_cards.reports import _conn
from jobs.connect_cards.shepherding_report import _STEP_NAMES, _cutoff
from jobs.congregation.deacon_reports import (
    EXCLUDED_DEACON_VALUES,
    _PRAYER_WINDOW_DAYS,
    _STEPS_WINDOW_DAYS,
    list_deacons,
)

deacon_admin_bp = Blueprint("deacon_admin", __name__)

_API_KEY = lambda: os.getenv("DEACON_ADMIN_API_KEY", "")

_LAST_SEEN_NEVER = "1900-01-01"

_PEOPLE_FIELDS = (
    "m.id, m.name, m.email, m.phone, m.address, m.household_id, "
    "m.deacon, m.deacon_status, m.member_status, "
    "MAX("
    f"  COALESCE((SELECT MAX(service_date) FROM connect_cards WHERE member_id = m.id), '{_LAST_SEEN_NEVER}'),"
    f"  COALESCE((SELECT MAX(service_date) FROM attendance  WHERE member_id = m.id), '{_LAST_SEEN_NEVER}')"
    ") AS last_seen"
)

_UPDATABLE_FIELDS = {"deacon", "deacon_status", "email", "phone", "address"}


def _attach_shepherding_info(conn, people: list[dict]) -> None:
    """Mutates each person dict in place: prayer_requests + next_steps,
    last 90 days each, all visibility levels (both watson-people logins
    are leadership-tier, unlike the per-deacon email reports)."""
    member_ids = [p["id"] for p in people]
    if not member_ids:
        return
    placeholders = ",".join("?" for _ in member_ids)

    prayers_by_member: dict = {}
    for pr in conn.execute(
        f"SELECT member_id, request_text, date FROM prayer_requests "
        f"WHERE member_id IN ({placeholders}) AND date >= ? ORDER BY date DESC",
        member_ids + [_cutoff(_PRAYER_WINDOW_DAYS)],
    ):
        prayers_by_member.setdefault(pr["member_id"], []).append(
            {"request_text": pr["request_text"], "date": pr["date"]}
        )

    steps_by_member: dict = {}
    for ns in conn.execute(
        f"SELECT member_id, step, date FROM next_steps "
        f"WHERE member_id IN ({placeholders}) AND date >= ? ORDER BY date DESC",
        member_ids + [_cutoff(_STEPS_WINDOW_DAYS)],
    ):
        steps_by_member.setdefault(ns["member_id"], []).append(
            {"step": ns["step"], "label": _STEP_NAMES.get(ns["step"], ns["step"]), "date": ns["date"]}
        )

    for p in people:
        p["prayer_requests"] = prayers_by_member.get(p["id"], [])
        p["next_steps"] = steps_by_member.get(p["id"], [])


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@deacon_admin_bp.route("/api/deacon-admin/people", methods=["GET"])
@_require_key
def list_people():
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_PEOPLE_FIELDS} FROM members m GROUP BY m.id ORDER BY m.name COLLATE NOCASE"
        ).fetchall()
        people = [dict(r) for r in rows]
        _attach_shepherding_info(conn, people)
    return jsonify(people), 200


@deacon_admin_bp.route("/api/deacon-admin/deacons", methods=["GET"])
@_require_key
def list_deacons_route():
    return jsonify(list_deacons()), 200


@deacon_admin_bp.route("/api/deacon-admin/people/<int:member_id>", methods=["PATCH"])
@_require_key
def update_person(member_id):
    data = request.get_json(force=True) or {}
    fields = {k: v for k, v in data.items() if k in _UPDATABLE_FIELDS}
    if not fields:
        return jsonify({"error": "nothing to update"}), 400

    if "deacon" in fields:
        # Free-text by design: deacons aren't a fixed enum, they're whatever
        # distinct values exist in this column (see deacon_reports.list_deacons).
        # A brand-new name here becomes a real, selectable deacon the moment
        # it's saved -- only the reserved bucket labels are blocked.
        deacon_val = (fields["deacon"] or "").strip()
        if deacon_val in EXCLUDED_DEACON_VALUES:
            return jsonify({"error": f"{deacon_val!r} is a reserved label, not an individual deacon"}), 400
        fields["deacon"] = deacon_val or None

    with _conn() as conn:
        existing = conn.execute("SELECT id FROM members WHERE id = ?", (member_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE members SET {set_clause} WHERE id = ?", (*fields.values(), member_id))
        row = conn.execute(
            f"SELECT {_PEOPLE_FIELDS} FROM members m WHERE m.id = ? GROUP BY m.id", (member_id,)
        ).fetchone()
        person = dict(row)
        _attach_shepherding_info(conn, [person])

    return jsonify(person), 200
