"""jobs/congregation/catalystdb_web.py -- Flask Blueprint backing the
wtsn.me/cat/catalystdb full members-database admin screen (Bill's
2026-09-23 request: a single screen for Bill and Donna to search, filter,
edit, and batch-edit every member field). Same shared-key auth pattern as
servants_web.py -- header X-Watson-Key matching CATALYSTDB_API_KEY -- gates
every route, PLUS per-person PIN login (catalystdb_pins table, scrypt hash
via deacon_pin_auth.hash_pin/check_pin -- reused as-is, the algorithm has
no deacon-specific coupling) added 2026-09-23 replacing the original
single shared PIN. verify_pin below mirrors deacons_web.py's route closely
but without that app's session-token layer -- catalystdb has exactly two
known users, not a whole deacon roster, so a signed cookie naming which of
them logged in is enough; see watson-tools/src/lib/catalystdbAuth.ts.
Locks the calling IP out after catalystdb_login_lockout.MAX_FAILED_ATTEMPTS
(3) consecutive wrong PINs -- tighter than the deacon app's 5, since this
PIN gates write access to every member field, not just a roster view.

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


def _ensure_pins_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalystdb_pins (
            person_name TEXT PRIMARY KEY,
            pin_hash    TEXT NOT NULL,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _alert_login_locked(client_ip: str) -> None:
    """Pings Bill directly (this tool has exactly two users) when an IP
    gets locked out -- same one-line-alert pattern as core.congregation_admin's
    _notify_bill. Best-effort: a Telegram hiccup here must never break the
    login response itself."""
    try:
        import requests
        from jobs.congregation.catalystdb_login_lockout import MAX_FAILED_ATTEMPTS

        token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return
        text = (
            f"⚠️ Catalyst Database login locked after {MAX_FAILED_ATTEMPTS} failed PIN "
            f"attempts from {client_ip}. Message me \"unlock login\" to clear it. - Watson"
        )
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


@catalystdb_web_bp.route("/api/cat/catalystdb/verify_pin", methods=["POST"])
@_require_key
def verify_pin():
    """Returns every person_name whose stored PIN matches -- normally at
    most one, since PINs are assigned unique. Never 401s on a wrong PIN --
    an empty matches list IS the "wrong PIN" answer. Locks the calling IP
    out after catalystdb_login_lockout.MAX_FAILED_ATTEMPTS consecutive
    wrong PINs, mirroring deacons_web.py's verify_pin exactly."""
    from jobs.congregation import catalystdb_login_lockout
    from jobs.congregation.deacon_pin_auth import check_pin

    data = request.get_json(force=True) or {}
    pin = (data.get("pin") or "").strip()
    client_ip = (data.get("client_ip") or "").strip() or "unknown"

    with _conn() as conn:
        _ensure_pins_table(conn)
        if catalystdb_login_lockout.is_locked(conn, client_ip):
            return jsonify({"matches": [], "locked": True}), 200

        matches = []
        if pin:
            rows = conn.execute("SELECT person_name, pin_hash FROM catalystdb_pins").fetchall()
            matches = [row["person_name"] for row in rows if check_pin(pin, row["pin_hash"])]

        if matches:
            catalystdb_login_lockout.record_success(conn, client_ip)
            just_locked = False
        else:
            just_locked = catalystdb_login_lockout.record_failure(conn, client_ip)

    if just_locked:
        _alert_login_locked(client_ip)

    return jsonify({"matches": matches, "locked": just_locked}), 200


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
