"""jobs/congregation/kids_attendance_web.py -- Flask Blueprint backing the
wtsn.me/cat/kidsatt staff tool (kids-class attendance, present/absent
toggles + move-between-classes), same shape as jobs/congregation/
attendance_web.py's adult attendance tool.

Auth: same shared-secret pattern as attendance_web.py -- every route
requires header X-Watson-Key matching KIDS_ATTENDANCE_API_KEY, a dedicated
key for this consumer, not ATTENDANCE_API_KEY or any other (this
codebase's one-key-per-external-consumer convention).

Mount on the Watson dashboard app:
    from jobs.congregation.kids_attendance_web import kids_attendance_web_bp
    app.register_blueprint(kids_attendance_web_bp)

Data model note: like `attendance`, `kids_checkin` is the only signal for
"present" -- there's no separate absent record, and (kid_id, event_date)
has no unique constraint (see migrate_kids_checkin_tables.py), so toggle()
below checks existence before insert/delete the same way attendance_web's
toggle() does rather than relying on the schema to prevent a double row.

A leader-created/moved row gets a synthetic subsplash_checkin_id
(`leader_manual:<kid_id>:<event_date>`, stable across a class move on the
same date so toggling off/on again or moving doesn't spawn a second row)
since that column is NOT NULL and real Subsplash rows carry a real one.
checkin_source='leader_manual' distinguishes it from the Subsplash-sourced
rows kids_checkin_import.py writes, though toggle-absent removes a row
regardless of its source -- same "leader can correct anything" philosophy
attendance_web.py's toggle has for adults.

CLASS_NAMES is the fixed, ordered (youngest to oldest) set of real
classrooms seen in Subsplash data as of 2026-09-29 (Nursery, Pre-K,
Elementary Kids Church -- no Toddlers room currently in use, despite that
column existing on the older, separate `classroom_attendance` aggregate-
headcount table). "Move up/down" in the frontend just means picking a
different entry in this list.
"""
import os
import sqlite3
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

kids_attendance_web_bp = Blueprint("kids_attendance_web", __name__)

_API_KEY = lambda: os.getenv("KIDS_ATTENDANCE_API_KEY", "")

_RECENT_SUNDAYS_COUNT = 10

CLASS_NAMES = ["Nursery", "Pre-K", "Elementary Kids Church"]


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


def _last_name_key(first: str, last: str) -> str:
    return (last or first or "").lower()


def _synthetic_checkin_id(kid_id: int, service_date: str) -> str:
    return f"leader_manual:{kid_id}:{service_date}"


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@kids_attendance_web_bp.route("/api/cat/kidsatt/state", methods=["GET"])
@_require_key
def get_state():
    requested = request.args.get("date", "").strip()
    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    service_date = requested if requested in valid_dates else _most_recent_sunday().isoformat()

    with _conn() as conn:
        kids = conn.execute("SELECT id, first_name, last_name, current_class FROM kids").fetchall()

        today_rows = {
            row["kid_id"]: row["class_name"]
            for row in conn.execute(
                "SELECT kid_id, class_name FROM kids_checkin WHERE event_date = ?", (service_date,)
            )
        }

    buckets: dict[str, list[dict]] = {name: [] for name in CLASS_NAMES}
    for k in kids:
        present = k["id"] in today_rows
        # today's actual checkin (if any) always wins; otherwise a kid's
        # persistent current_class places them, and a kid with NO
        # current_class (removed via the "X" button/remove()) and no
        # checkin today simply doesn't appear anywhere -- that's the point
        # of current_class existing as its own field rather than being
        # derived from "most recent checkin" forever.
        class_name = today_rows.get(k["id"]) or k["current_class"]
        if not class_name:
            continue
        if class_name not in buckets:
            buckets[class_name] = []  # a class name outside CLASS_NAMES (shouldn't normally happen)
        name = f"{k['first_name']} {k['last_name'] or ''}".strip()
        buckets[class_name].append({"id": k["id"], "name": name, "present": present})

    classes = []
    for class_name in list(CLASS_NAMES) + sorted(c for c in buckets if c not in CLASS_NAMES):
        kids_list = sorted(buckets[class_name], key=lambda e: e["name"].split()[-1].lower() if e["name"] else "")
        classes.append({
            "class_name": class_name,
            "present_count": sum(1 for e in kids_list if e["present"]),
            "kids": kids_list,
        })

    return jsonify({
        "service_date": service_date,
        "recent_sundays": _recent_sundays(_RECENT_SUNDAYS_COUNT),
        "class_names": CLASS_NAMES,
        "classes": classes,
    }), 200


@kids_attendance_web_bp.route("/api/cat/kidsatt/toggle", methods=["POST"])
@_require_key
def toggle():
    data = request.get_json(force=True) or {}
    kid_id = data.get("kid_id")
    service_date = (data.get("service_date") or "").strip()
    present = bool(data.get("present"))
    class_name = (data.get("class_name") or "").strip()

    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    if not isinstance(kid_id, int):
        return jsonify({"error": "kid_id (int) is required"}), 400
    if service_date not in valid_dates:
        return jsonify({"error": "service_date must be one of the recent Sundays"}), 400
    if present and class_name not in CLASS_NAMES:
        return jsonify({"error": f"class_name must be one of {CLASS_NAMES} when marking present"}), 400

    with _conn() as conn:
        existing = conn.execute("SELECT id FROM kids WHERE id = ?", (kid_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404

        already_present = conn.execute(
            "SELECT 1 FROM kids_checkin WHERE kid_id = ? AND event_date = ?",
            (kid_id, service_date),
        ).fetchone() is not None

        if present and not already_present:
            conn.execute(
                "INSERT INTO kids_checkin (kid_id, subsplash_checkin_id, event_id, class_name, event_date, "
                " checked_in_at, checkin_source) VALUES (?, ?, NULL, ?, ?, datetime('now'), 'leader_manual')",
                (kid_id, _synthetic_checkin_id(kid_id, service_date), class_name, service_date),
            )
        elif not present and already_present:
            conn.execute(
                "DELETE FROM kids_checkin WHERE kid_id = ? AND event_date = ?",
                (kid_id, service_date),
            )
        conn.commit()

    return jsonify({"kid_id": kid_id, "service_date": service_date, "present": present}), 200


@kids_attendance_web_bp.route("/api/cat/kidsatt/move", methods=["POST"])
@_require_key
def move():
    data = request.get_json(force=True) or {}
    kid_id = data.get("kid_id")
    service_date = (data.get("service_date") or "").strip()
    class_name = (data.get("class_name") or "").strip()

    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    if not isinstance(kid_id, int):
        return jsonify({"error": "kid_id (int) is required"}), 400
    if service_date not in valid_dates:
        return jsonify({"error": "service_date must be one of the recent Sundays"}), 400
    if class_name not in CLASS_NAMES:
        return jsonify({"error": f"class_name must be one of {CLASS_NAMES}"}), 400

    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM kids_checkin WHERE kid_id = ? AND event_date = ?", (kid_id, service_date)
        ).fetchone()
        if not row:
            return jsonify({"error": "kid isn't marked present for this date -- toggle present first"}), 400
        conn.execute("UPDATE kids_checkin SET class_name = ? WHERE id = ?", (class_name, row["id"]))
        # A move is very likely a permanent reclassification (aged up a
        # room, etc.), not just a one-Sunday correction -- update the
        # persistent default too, not only today's record.
        conn.execute(
            "UPDATE kids SET current_class = ?, updated_at = datetime('now') WHERE id = ?", (class_name, kid_id)
        )
        conn.commit()

    return jsonify({"kid_id": kid_id, "service_date": service_date, "class_name": class_name}), 200


@kids_attendance_web_bp.route("/api/cat/kidsatt/remove", methods=["POST"])
@_require_key
def remove():
    """Takes a kid out of the tool's view entirely: clears their
    persistent current_class and, if they happen to have a checkin row for
    the currently selected date, removes that too, so the "X" button
    always fully removes them from what's on screen right now regardless
    of whether they were showing as present or absent. Does NOT delete the
    kid or their attendance history -- a genuine future Subsplash checkin
    (kids_checkin_import.py) or another move() re-populates current_class."""
    data = request.get_json(force=True) or {}
    kid_id = data.get("kid_id")
    service_date = (data.get("service_date") or "").strip()

    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    if not isinstance(kid_id, int):
        return jsonify({"error": "kid_id (int) is required"}), 400
    if service_date not in valid_dates:
        return jsonify({"error": "service_date must be one of the recent Sundays"}), 400

    with _conn() as conn:
        existing = conn.execute("SELECT id FROM kids WHERE id = ?", (kid_id,)).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404
        conn.execute(
            "DELETE FROM kids_checkin WHERE kid_id = ? AND event_date = ?", (kid_id, service_date)
        )
        conn.execute(
            "UPDATE kids SET current_class = NULL, updated_at = datetime('now') WHERE id = ?", (kid_id,)
        )
        conn.commit()

    return jsonify({"kid_id": kid_id, "removed": True}), 200
