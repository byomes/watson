"""jobs/congregation/catalystdb_web.py -- Flask Blueprint backing the
wtsn.me/cat/catalystdb full members-database admin screen (Bill's
2026-09-23 request: a single screen for Bill and Donna to search, filter,
edit, and batch-edit every member field). Same shared-key auth pattern as
servants_web.py -- header X-Watson-Key matching CATALYSTDB_API_KEY -- plus
a PIN gate on the Next.js side (see watson-tools/src/lib/catalystdbAuth.ts).

Editable columns are allowlisted (_EDITABLE_COLUMNS) so the update/batch
endpoint can never write to an arbitrary column name from the request body.
No hard deletes -- /deactivate just sets active=0, consistent with the rest
of this codebase's soft-delete convention (members.active already gates
every other congregation.db view)."""
import os
import sqlite3
from functools import wraps

from flask import Blueprint, jsonify, request

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

catalystdb_web_bp = Blueprint("catalystdb_web", __name__)

_API_KEY = lambda: os.getenv("CATALYSTDB_API_KEY", "")

# Every members column Donna/Bill can edit from the grid. id/created_at are
# intentionally excluded (immutable); everything else on the table is here.
_EDITABLE_COLUMNS = {
    "name", "email", "phone", "campus_preference", "first_visit_date", "status",
    "notes", "carrier", "active", "shepherding_exempt", "member_status",
    "status_reason", "status_since", "status_note", "snowbird_return",
    "partnership_status", "address", "household_id", "deacon", "deacon_status",
    "birthdate", "household_role", "gender", "started_serving_date", "service_pin_notes",
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


@catalystdb_web_bp.route("/api/cat/catalystdb/state", methods=["GET"])
@_require_key
def get_state():
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM members ORDER BY name").fetchall()
    return jsonify({"members": [dict(r) for r in rows]})


@catalystdb_web_bp.route("/api/cat/catalystdb/update", methods=["POST"])
@_require_key
def update():
    """Single or batch edit: {"ids": [1,2,3], "field": "member_status", "value": "active"}."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    field = data.get("field")
    value = data.get("value", None)
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids (non-empty list) is required"}), 400
    if field not in _EDITABLE_COLUMNS:
        return jsonify({"error": f"'{field}' is not an editable column"}), 400
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "ids must be integers"}), 400

    with _conn() as conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE members SET {field} = ?, updated_at = datetime('now') "
            f"WHERE id IN ({placeholders})",
            (value, *ids),
        )
    return jsonify({"updated": len(ids), "field": field})


@catalystdb_web_bp.route("/api/cat/catalystdb/create", methods=["POST"])
@_require_key
def create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    fields, placeholders, values = ["name"], ["?"], [name]
    for col in _EDITABLE_COLUMNS:
        if col == "name":
            continue
        if col in data and data[col] not in (None, ""):
            fields.append(col)
            placeholders.append("?")
            values.append(data[col])

    with _conn() as conn:
        cur = conn.execute(
            f"INSERT INTO members ({', '.join(fields)}) VALUES ({', '.join(placeholders)})",
            values,
        )
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM members WHERE id = ?", (new_id,)).fetchone()
    return jsonify({"member": dict(row)}), 201


@catalystdb_web_bp.route("/api/cat/catalystdb/deactivate", methods=["POST"])
@_require_key
def deactivate():
    """Soft-delete: sets active=0 for the given ids. No hard deletes from this screen."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids (non-empty list) is required"}), 400
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "ids must be integers"}), 400

    with _conn() as conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE members SET active = 0, updated_at = datetime('now') WHERE id IN ({placeholders})",
            ids,
        )
    return jsonify({"deactivated": len(ids)})
