"""jobs/congregation/elder_shepherding_report_web.py — Flask Blueprint
backing the wtsn.me/cat/shepherdingreport elder view: every non-excluded
member, grouped by deacon, sorted worst-attendance-bucket first within each
group, with a "last seen" date correction route for use during follow-up.

Mount on the Watson dashboard app:
    from jobs.congregation.elder_shepherding_report_web import elder_shepherding_report_web_bp
    app.register_blueprint(elder_shepherding_report_web_bp)

Auth: same shared-secret pattern as jobs/congregation/attendance_web.py --
every route requires header X-Watson-Key matching SHEPHERDING_REPORT_API_KEY,
a DEDICATED key for this consumer (the watson-tools wtsn.me app), per this
codebase's one-key-per-external-consumer convention.

This exists because jobs/congregation/elder_shepherding_report.py's Telegram
message is deliberately counts-only to stay under Telegram's character
limit; this route serves the full named breakdown that message links to.

set_last_seen (added 2026-09-07): lets a deacon record better information
learned during follow-up (e.g. "actually I talked to them, they were here
three weeks ago") without a separate trip to /cat/attendance. "Last seen"
here isn't a stored field -- build_deacon_group_names() derives it from
MAX(connect_cards.service_date, attendance.service_date) -- so recording a
correction means inserting an attendance row for that date, exactly like
attendance_web.py's toggle route does for the weekly Sunday check-in. campus
is required (NOT NULL) on that table; resolved from the member's own
campus_preference the same way attendance_web.py's get_state() does,
collapsed to Wilmington/Online since those are the only two toggle() will
accept as an actual attended campus (Hybrid/Inactive describe a preference,
not a campus someone attended). Tagged with source='shepherding_report_
followup' so these corrections are distinguishable from card-intake/toggle
rows if that's ever needed.
"""
import os
from datetime import date
from functools import wraps

from flask import Blueprint, jsonify, request

from jobs.congregation.elder_shepherding_report import build_deacon_group_names
from jobs.connect_cards.reports import _conn
from jobs.connect_cards.shepherding_report import _today

elder_shepherding_report_web_bp = Blueprint("elder_shepherding_report_web", __name__)

_API_KEY = lambda: os.getenv("SHEPHERDING_REPORT_API_KEY", "")


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@elder_shepherding_report_web_bp.route("/api/cat/shepherdingreport/state", methods=["GET"])
@_require_key
def get_state():
    return jsonify({
        "generated_date": _today(),
        "groups": build_deacon_group_names(),
    }), 200


@elder_shepherding_report_web_bp.route("/api/cat/shepherdingreport/lastseen", methods=["POST"])
@_require_key
def set_last_seen():
    data = request.get_json(force=True) or {}
    member_id = data.get("member_id")
    service_date = (data.get("service_date") or "").strip()

    if not isinstance(member_id, int):
        return jsonify({"error": "member_id (int) is required"}), 400
    try:
        parsed = date.fromisoformat(service_date)
    except ValueError:
        return jsonify({"error": "service_date must be an ISO date (YYYY-MM-DD)"}), 400
    if parsed > date.today():
        return jsonify({"error": "service_date cannot be in the future"}), 400

    with _conn() as conn:
        member = conn.execute(
            "SELECT id, campus_preference FROM members WHERE id = ?", (member_id,)
        ).fetchone()
        if not member:
            return jsonify({"error": "not found"}), 404

        campus = "Online" if member["campus_preference"] == "Online" else "Wilmington"

        already_present = conn.execute(
            "SELECT 1 FROM attendance WHERE member_id = ? AND service_date = ?",
            (member_id, service_date),
        ).fetchone() is not None

        if not already_present:
            conn.execute(
                "INSERT INTO attendance (member_id, service_date, campus, card_id, source) "
                "VALUES (?, ?, ?, NULL, ?)",
                (member_id, service_date, campus, "shepherding_report_followup"),
            )
            conn.commit()

    return jsonify({"member_id": member_id, "service_date": service_date}), 200
