"""jobs/bodyrec/api.py — Flask Blueprint for bodyrec (Next.js/Vercel) → Watson DB.

Mount on the Watson dashboard app:
    from jobs.bodyrec.api import bodyrec_bp
    app.register_blueprint(bodyrec_bp)
"""
import logging
import os
import sys
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.bodyrec import PROFILES, get_db

log = logging.getLogger(__name__)

bodyrec_bp = Blueprint("bodyrec", __name__)

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")

_ENTRY_FIELDS = (
    "weight", "neck", "waist", "hip", "height",
    "fat_percent", "fat_lbs", "lean_lbs", "notes",
)


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Watson-Key") != _API_KEY() or not _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _entry_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "profile": row["profile"],
        "date": row["date"],
        "weight": row["weight"],
        "neck": row["neck"],
        "waist": row["waist"],
        "hip": row["hip"],
        "height": row["height"],
        "fat_percent": row["fat_percent"],
        "fat_lbs": row["fat_lbs"],
        "lean_lbs": row["lean_lbs"],
        "notes": row["notes"],
    }


# ── Entries ────────────────────────────────────────────────────────────────────

@bodyrec_bp.route("/api/bodyrec/entries", methods=["GET"])
@_require_key
def list_entries():
    profile = request.args.get("profile", "")
    if profile not in PROFILES:
        return jsonify({"error": "profile must be one of: bill, mel"}), 400

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM body_entries WHERE profile = ? ORDER BY date ASC",
            (profile,),
        ).fetchall()
        return jsonify([_entry_row_to_dict(r) for r in rows]), 200
    finally:
        conn.close()


@bodyrec_bp.route("/api/bodyrec/entries", methods=["DELETE"])
@_require_key
def clear_entries():
    """Bulk-delete all entries for a profile (mirrors clearAllData's entries.delete)."""
    profile = request.args.get("profile", "")
    if profile not in PROFILES:
        return jsonify({"error": "profile must be one of: bill, mel"}), 400

    conn = get_db()
    try:
        conn.execute("DELETE FROM body_entries WHERE profile = ?", (profile,))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


@bodyrec_bp.route("/api/bodyrec/entries", methods=["POST"])
@_require_key
def create_entry():
    data    = request.get_json(force=True)
    entry_id = data.get("id")
    profile  = data.get("profile")
    date     = data.get("date")

    if not (entry_id and profile and date):
        return jsonify({"error": "id, profile, and date are required"}), 400
    if profile not in PROFILES:
        return jsonify({"error": "profile must be one of: bill, mel"}), 400

    values = [data.get(f) for f in _ENTRY_FIELDS]

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM body_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if existing:
            return jsonify({"error": "entry with this id already exists"}), 409

        conn.execute(
            "INSERT INTO body_entries (id, profile, date, weight, neck, waist, hip, "
            "height, fat_percent, fat_lbs, lean_lbs, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, profile, date, *values),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM body_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return jsonify(_entry_row_to_dict(row)), 200
    except Exception as exc:
        log.error("create_entry failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()


@bodyrec_bp.route("/api/bodyrec/entries/<entry_id>", methods=["PATCH"])
@_require_key
def update_entry(entry_id):
    data    = request.get_json(force=True)
    profile = data.get("profile")

    if profile not in PROFILES:
        return jsonify({"error": "profile must be one of: bill, mel"}), 400

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM body_entries WHERE id = ? AND profile = ?",
            (entry_id, profile),
        ).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404

        set_clauses = []
        values = []
        if "date" in data:
            set_clauses.append("date = ?")
            values.append(data["date"])
        for f in _ENTRY_FIELDS:
            if f in data:
                set_clauses.append(f"{f} = ?")
                values.append(data[f])

        if set_clauses:
            values.append(entry_id)
            conn.execute(
                f"UPDATE body_entries SET {', '.join(set_clauses)} WHERE id = ?",
                values,
            )
            conn.commit()

        row = conn.execute(
            "SELECT * FROM body_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return jsonify(_entry_row_to_dict(row)), 200
    except Exception as exc:
        log.error("update_entry failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()


@bodyrec_bp.route("/api/bodyrec/entries/<entry_id>", methods=["DELETE"])
@_require_key
def delete_entry(entry_id):
    profile = request.args.get("profile", "")
    if profile not in PROFILES:
        return jsonify({"error": "profile must be one of: bill, mel"}), 400

    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM body_entries WHERE id = ? AND profile = ?",
            (entry_id, profile),
        )
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


# ── Settings ───────────────────────────────────────────────────────────────────

@bodyrec_bp.route("/api/bodyrec/settings", methods=["GET"])
@_require_key
def get_settings():
    profile = request.args.get("profile", "")
    if profile not in PROFILES:
        return jsonify({"error": "profile must be one of: bill, mel"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM body_settings WHERE profile = ?", (profile,)
        ).fetchone()
        if not row:
            return jsonify({"height": None, "goal_fat_percent": None, "goal_weight": None}), 200
        return jsonify({
            "height": row["height"],
            "goal_fat_percent": row["goal_fat_percent"],
            "goal_weight": row["goal_weight"],
        }), 200
    finally:
        conn.close()


@bodyrec_bp.route("/api/bodyrec/settings", methods=["DELETE"])
@_require_key
def clear_settings():
    """Delete the settings row for a profile (mirrors clearAllData's settings.delete)."""
    profile = request.args.get("profile", "")
    if profile not in PROFILES:
        return jsonify({"error": "profile must be one of: bill, mel"}), 400

    conn = get_db()
    try:
        conn.execute("DELETE FROM body_settings WHERE profile = ?", (profile,))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


@bodyrec_bp.route("/api/bodyrec/settings", methods=["PUT"])
@_require_key
def upsert_settings():
    data    = request.get_json(force=True)
    profile = data.get("profile")
    if profile not in PROFILES:
        return jsonify({"error": "profile must be one of: bill, mel"}), 400

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO body_settings (profile, height, goal_fat_percent, goal_weight) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(profile) DO UPDATE SET "
            "height = excluded.height, "
            "goal_fat_percent = excluded.goal_fat_percent, "
            "goal_weight = excluded.goal_weight",
            (profile, data.get("height"), data.get("goal_fat_percent"), data.get("goal_weight")),
        )
        conn.commit()
        return jsonify({"ok": True}), 200
    except Exception as exc:
        log.error("upsert_settings failed: %s", exc)
        return jsonify({"error": "server error"}), 500
    finally:
        conn.close()
