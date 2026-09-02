"""jobs/congregation/deacons_web.py — Flask Blueprint backing the
wtsn.me/cat/deacons unified deacon roster tool.

Unified by design: every deacon and Jim see the SAME full roster (sortable/
filterable by deacon), not a view scoped to "their" people only — deacons
regularly cover for each other when one is sick or traveling, and a scoped
view would break that. See jobs/congregation/deacon_reports.py's docstring
for the per-deacon EMAIL reports, which are scoped and remain unchanged.

Auth: X-Watson-Key matching DEACONS_API_KEY (a DEDICATED key, not
DEACON_ADMIN_API_KEY or any other consumer's, per this codebase's
one-key-per-external-consumer convention). Like every other /cat/ tool,
there is no per-user login here (built 2026-08-31) -- the shared key gates
the whole tool, matching wtsn.me/cat/attendance and wtsn.me/cat/duplicates.
Per-deacon identity (needed for scoped Telegram alerts, not for this
roster) is deferred to a later build, once alerting is actually built.

Because there is no per-user login, leadership-only prayer requests
(prayer_requests.leadership_only = 1) are deliberately NEVER returned by
this blueprint's roster query -- not gated per-caller, just excluded
outright. Leadership-tier pastoral content stays exclusively in
deacon_reports.py's existing Master Shepherding Report / Pastor Bill's
List (both manually triggered, email-only, unchanged by this file). This
is a deliberate tightening vs. jobs/congregation/deacon_admin_api.py (the
watson-people backend this tool replaces), which returns ALL prayer
requests with no leadership_only gate -- safe there only because both
watson-people logins are leadership-tier; not safe once every deacon gets
access here.

Mount on the Watson dashboard app:
    from jobs.congregation.deacons_web import deacons_web_bp
    app.register_blueprint(deacons_web_bp)
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

deacons_web_bp = Blueprint("deacons_web", __name__)

_API_KEY = lambda: os.getenv("DEACONS_API_KEY", "")

_LAST_SEEN_NEVER = "1900-01-01"

_ROSTER_FIELDS = (
    "m.id, m.name, m.email, m.phone, m.address, m.birthdate, m.household_id, "
    "m.deacon, m.deacon_status, m.member_status, "
    "MAX("
    f"  COALESCE((SELECT MAX(service_date) FROM connect_cards WHERE member_id = m.id), '{_LAST_SEEN_NEVER}'),"
    f"  COALESCE((SELECT MAX(service_date) FROM attendance  WHERE member_id = m.id), '{_LAST_SEEN_NEVER}')"
    ") AS last_seen"
)

_UPDATABLE_FIELDS = {"name", "deacon", "deacon_status", "email", "phone", "address", "birthdate"}

# EXCLUDED_DEACON_VALUES (from deacon_reports.py) also contains "Inactive" --
# that exclusion is about keeping it out of list_deacons()/Master Report
# sections, not about blocking it here. This tool is exactly where a deacon
# is meant to set someone to "Inactive" (or move them back off it), so only
# the three truly-reserved bucket labels are blocked from being PATCHed.
_BLOCKED_DEACON_VALUES = EXCLUDED_DEACON_VALUES - {"Inactive"}


def _attach_shepherding_info(conn, people: list[dict]) -> None:
    """Mutates each person dict in place: prayer_requests (leadership_only
    excluded -- see module docstring) + next_steps (last 90 days each) +
    follow_ups (full history, no window -- these are deacon-logged and
    typically few enough per person that a cutoff would just hide the
    ones worth seeing)."""
    member_ids = [p["id"] for p in people]
    if not member_ids:
        return
    placeholders = ",".join("?" for _ in member_ids)

    prayers_by_member: dict = {}
    for pr in conn.execute(
        f"SELECT member_id, request_text, date FROM prayer_requests "
        f"WHERE member_id IN ({placeholders}) AND date >= ? AND leadership_only = 0 "
        f"ORDER BY date DESC",
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

    follow_ups_by_member: dict = {}
    for fu in conn.execute(
        f"SELECT member_id, note, status, created_at FROM follow_ups "
        f"WHERE member_id IN ({placeholders}) ORDER BY created_at DESC",
        member_ids,
    ):
        follow_ups_by_member.setdefault(fu["member_id"], []).append(
            {"note": fu["note"], "status": fu["status"], "created_at": fu["created_at"]}
        )

    for p in people:
        p["prayer_requests"] = prayers_by_member.get(p["id"], [])
        p["next_steps"] = steps_by_member.get(p["id"], [])
        p["follow_ups"] = follow_ups_by_member.get(p["id"], [])


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@deacons_web_bp.route("/api/cat/deacons/roster", methods=["GET"])
@_require_key
def get_roster():
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_ROSTER_FIELDS} FROM members m "
            f"WHERE m.member_status IS NULL OR m.member_status != 'deceased' "
            f"GROUP BY m.id ORDER BY m.name COLLATE NOCASE"
        ).fetchall()
        people = [dict(r) for r in rows]
        _attach_shepherding_info(conn, people)
    return jsonify(people), 200


@deacons_web_bp.route("/api/cat/deacons/list", methods=["GET"])
@_require_key
def get_deacon_list():
    return jsonify(list_deacons()), 200


@deacons_web_bp.route("/api/cat/deacons/member/<int:member_id>", methods=["PATCH"])
@_require_key
def update_member(member_id):
    data = request.get_json(force=True) or {}
    fields = {k: v for k, v in data.items() if k in _UPDATABLE_FIELDS}
    if not fields:
        return jsonify({"error": "nothing to update"}), 400

    if "name" in fields:
        # members.name is NOT NULL and is the join key/display key used
        # throughout the rest of the app (attendance, connect cards,
        # reports) -- never let it be saved blank. The frontend sends a
        # single combined "First Last" string (it splits/joins on the
        # UI side for editing; there's no separate first/last column).
        name_val = (fields["name"] or "").strip()
        if not name_val:
            return jsonify({"error": "name cannot be blank"}), 400
        fields["name"] = name_val

    if "deacon" in fields:
        # Free-text by design: deacons aren't a fixed enum, they're whatever
        # distinct values exist in this column (see deacon_reports.list_deacons).
        # A brand-new name here becomes a real, selectable deacon the moment
        # it's saved -- only the reserved bucket labels are blocked.
        deacon_val = (fields["deacon"] or "").strip()
        if deacon_val in _BLOCKED_DEACON_VALUES:
            return jsonify({"error": f"{deacon_val!r} is a reserved label, not an individual deacon"}), 400
        fields["deacon"] = deacon_val or None

    with _conn() as conn:
        existing = conn.execute("SELECT id FROM members WHERE id = ?", (member_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE members SET {set_clause} WHERE id = ?", (*fields.values(), member_id))
        row = conn.execute(
            f"SELECT {_ROSTER_FIELDS} FROM members m WHERE m.id = ? GROUP BY m.id", (member_id,)
        ).fetchone()
        person = dict(row)
        _attach_shepherding_info(conn, [person])

    return jsonify(person), 200


@deacons_web_bp.route("/api/cat/deacons/member/<int:member_id>/follow-up", methods=["POST"])
@_require_key
def add_follow_up(member_id):
    data = request.get_json(force=True) or {}
    note = (data.get("note") or "").strip()
    if not note:
        return jsonify({"error": "note is required"}), 400

    with _conn() as conn:
        existing = conn.execute("SELECT id FROM members WHERE id = ?", (member_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404
        conn.execute(
            "INSERT INTO follow_ups (member_id, note, status) VALUES (?, ?, 'open')",
            (member_id, note),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, member_id, note, status, created_at FROM follow_ups WHERE id = last_insert_rowid()"
        ).fetchone()

    return jsonify(dict(row)), 201
