"""jobs/congregation/attendance_web.py — Flask Blueprint backing the
wtsn.me/cat/attendance staff tool (present/absent toggles per member).

Mount on the Watson dashboard app:
    from jobs.congregation.attendance_web import attendance_web_bp
    app.register_blueprint(attendance_web_bp)

Auth: same shared-secret pattern as jobs/congregation/deacon_admin_api.py --
every route requires header X-Watson-Key matching ATTENDANCE_API_KEY. This is
a DEDICATED key for this consumer (the watson-tools wtsn.me app), not
DEACON_ADMIN_API_KEY or any other, per this codebase's one-key-per-external-
consumer convention. The dashboard is reachable publicly via Tailscale
Funnel, so this route needs its own gate regardless of the Funnel.

Data model note: `attendance` rows are the only signal for "present" -- there
is no separate absent record. Presence is keyed by (member_id, service_date)
only (matching jobs/connect_cards/attendance_intake.py's _attendance_exists),
not by campus, so a Hybrid member toggled present under either campus section
shows present under both.
"""
import os
import sqlite3
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

attendance_web_bp = Blueprint("attendance_web", __name__)

_API_KEY = lambda: os.getenv("ATTENDANCE_API_KEY", "")

_RECENT_SUNDAYS_COUNT = 10


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _most_recent_sunday() -> date:
    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    return today - timedelta(days=days_since_sunday)


def _recent_sundays(count: int) -> list[str]:
    latest = _most_recent_sunday()
    return [(latest - timedelta(weeks=i)).isoformat() for i in range(count)]


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@attendance_web_bp.route("/api/cat/attendance/state", methods=["GET"])
@_require_key
def get_state():
    requested = request.args.get("date", "").strip()
    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    service_date = requested if requested in valid_dates else _most_recent_sunday().isoformat()

    with _conn() as conn:
        members = conn.execute(
            "SELECT id, name, campus_preference FROM members "
            "WHERE member_status = 'active' ORDER BY name COLLATE NOCASE"
        ).fetchall()
        present_ids = {
            row["member_id"]
            for row in conn.execute(
                "SELECT DISTINCT member_id FROM attendance WHERE service_date = ?",
                (service_date,),
            )
        }

    wilmington, online = [], []
    for m in members:
        entry = {"id": m["id"], "name": m["name"], "present": m["id"] in present_ids}
        pref = m["campus_preference"]
        if pref in (None, "", "Wilmington", "Hybrid"):
            wilmington.append(entry)
        if pref in ("Online", "Hybrid"):
            online.append(entry)

    return jsonify({
        "service_date": service_date,
        "recent_sundays": _recent_sundays(_RECENT_SUNDAYS_COUNT),
        "wilmington": wilmington,
        "online": online,
    }), 200


@attendance_web_bp.route("/api/cat/attendance/toggle", methods=["POST"])
@_require_key
def toggle():
    data = request.get_json(force=True) or {}
    member_id = data.get("member_id")
    service_date = (data.get("service_date") or "").strip()
    present = bool(data.get("present"))
    campus = (data.get("campus") or "").strip()

    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    if not isinstance(member_id, int):
        return jsonify({"error": "member_id (int) is required"}), 400
    if service_date not in valid_dates:
        return jsonify({"error": "service_date must be one of the recent Sundays"}), 400
    if present and campus not in ("Wilmington", "Online"):
        return jsonify({"error": "campus must be 'Wilmington' or 'Online' when marking present"}), 400

    with _conn() as conn:
        existing = conn.execute("SELECT id FROM members WHERE id = ?", (member_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404

        already_present = conn.execute(
            "SELECT 1 FROM attendance WHERE member_id = ? AND service_date = ?",
            (member_id, service_date),
        ).fetchone() is not None

        if present and not already_present:
            conn.execute(
                "INSERT INTO attendance (member_id, service_date, campus, card_id) VALUES (?, ?, ?, NULL)",
                (member_id, service_date, campus),
            )
        elif not present and already_present:
            conn.execute(
                "DELETE FROM attendance WHERE member_id = ? AND service_date = ?",
                (member_id, service_date),
            )
        conn.commit()

    return jsonify({"member_id": member_id, "service_date": service_date, "present": present}), 200
