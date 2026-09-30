"""jobs/congregation/kids_servants_web.py -- Flask Blueprint backing the
wtsn.me/cat/kidstoday staff tool. Lets Donna set a servant override (a
different member than the default leader) for one or more of the Sunday
kids classes on a given date -- kidsatt_weekly.py (the Sunday 2:55pm sender)
reads kids_servant_overrides to decide who actually gets contacted that
week: the default leader via Telegram, or an override servant via SMS.

Auth: same shared-secret pattern as kids_attendance_web.py -- every route
requires header X-Watson-Key matching KIDS_SERVANTS_API_KEY, a dedicated key
for this consumer (this codebase's one-key-per-external-consumer convention).

Mount on the Watson dashboard app:
    from jobs.congregation.kids_servants_web import kids_servants_web_bp
    app.register_blueprint(kids_servants_web_bp)
"""
import os
import sqlite3
from datetime import date
from functools import wraps

from flask import Blueprint, jsonify, request

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

kids_servants_web_bp = Blueprint("kids_servants_web", __name__)

_API_KEY = lambda: os.getenv("KIDS_SERVANTS_API_KEY", "")

CLASS_NAMES = ["Nursery", "Pre-K", "Elementary"]

# Same TARGETS as jobs/congregation/pin_collection.py / kidsatt_weekly.py --
# Tara covers Nursery + Pre-K, Lucie covers Elementary, per Bill.
DEFAULT_SERVANTS = {
    "Nursery": {"member_id": 236, "person_id": 450, "name": "Tara Mathena"},
    "Pre-K": {"member_id": 236, "person_id": 450, "name": "Tara Mathena"},
    "Elementary": {"member_id": 102, "person_id": 332, "name": "Lucie Hale"},
}


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@kids_servants_web_bp.route("/api/cat/kidstoday/state", methods=["GET"])
@_require_key
def get_state():
    requested = (request.args.get("date") or "").strip()
    try:
        service_date = requested if requested else date.today().isoformat()
        date.fromisoformat(service_date)
    except ValueError:
        service_date = date.today().isoformat()

    servants = dict(DEFAULT_SERVANTS)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT class_name, member_id, person_id FROM kids_servant_overrides WHERE event_date = ?",
            (service_date,),
        ).fetchall()
        for row in rows:
            member = conn.execute("SELECT name FROM members WHERE id = ?", (row["member_id"],)).fetchone()
            if member:
                servants[row["class_name"]] = {
                    "member_id": row["member_id"],
                    "person_id": row["person_id"],
                    "name": member["name"],
                }

    return jsonify({"service_date": service_date, "class_names": CLASS_NAMES, "servants": servants}), 200


@kids_servants_web_bp.route("/api/cat/kidstoday/search", methods=["GET"])
@_require_key
def search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"results": []}), 200
    like = f"%{q}%"
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id as member_id, name FROM members "
            "WHERE active NOT IN ('disconnected', 'deceased') "
            "AND name NOT LIKE '%CAMPUS%' AND name NOT LIKE '%SYSTEM%' AND name NOT LIKE '%TEST%' "
            "AND name LIKE ? ORDER BY name LIMIT 20",
            (like,),
        ).fetchall()
    results = [{"member_id": r["member_id"], "name": r["name"]} for r in rows]
    return jsonify({"results": results}), 200


@kids_servants_web_bp.route("/api/cat/kidstoday/save", methods=["POST"])
@_require_key
def save():
    data = request.get_json(force=True) or {}
    servants = data.get("servants") or {}
    requested_date = (data.get("service_date") or "").strip()
    try:
        service_date = requested_date if requested_date else date.today().isoformat()
        date.fromisoformat(service_date)
    except ValueError:
        return jsonify({"error": "service_date must be YYYY-MM-DD"}), 400

    with _conn() as conn:
        conn.execute("DELETE FROM kids_servant_overrides WHERE event_date = ?", (service_date,))
        for class_name, servant in servants.items():
            if class_name not in CLASS_NAMES:
                continue
            member_id = servant.get("member_id")
            default = DEFAULT_SERVANTS.get(class_name)
            if not member_id or (default and member_id == default["member_id"]):
                continue  # matches default -- no override row needed
            member = conn.execute("SELECT id FROM members WHERE id = ?", (member_id,)).fetchone()
            if not member:
                continue
            conn.execute(
                "INSERT INTO kids_servant_overrides (event_date, class_name, member_id, person_id) "
                "VALUES (?, ?, ?, ?)",
                (service_date, class_name, member_id, servant.get("person_id") or 0),
            )
        conn.commit()

    return jsonify({"success": True, "service_date": service_date}), 200
