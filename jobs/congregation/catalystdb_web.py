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
No hard deletes -- /deactivate just sets active='disconnected' (or
'deceased'), consistent with the rest of this codebase's soft-delete
convention (members.active already gates every other congregation.db
view)."""
import os
import sqlite3
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

catalystdb_web_bp = Blueprint("catalystdb_web", __name__)

_API_KEY = lambda: os.getenv("CATALYSTDB_API_KEY", "")

# Every members column Donna/Bill can edit from the grid. id/created_at are
# intentionally excluded (immutable); everything else on the table is here.
#
# 2026-09-24 Phase 6: status, member_status, partnership_status,
# deacon_status, status_reason, status_since, status_note, and
# snowbird_return columns dropped entirely -- partner/active/residency
# (see ~/.claude/plans/zesty-cuddling-robin.md) are now the only source of
# truth. active_v2 was renamed to active (the old boolean active column was
# dropped first) once every read/write site was confirmed switched over.
_EDITABLE_COLUMNS = {
    "name", "email", "phone", "campus_preference", "first_visit_date",
    "notes", "address", "household_id", "deacon",
    "birthdate", "household_role", "gender", "started_serving_date", "service_pin_notes",
    "partner", "active", "residency", "anniversary", "unsubscribed",
    "connected",
}

# "connected" is a virtual field, not a real column -- Connected is computed
# live from attendance (_connected() below) for everyone by default. Editing
# it (2026-09-24, Bill's request -- some old one-off attendees, e.g.
# anniversary-service guests we know aren't coming back, need a permanent
# manual reclassification) writes to connected_override instead, which
# _connected_or_override() then prefers over the computed value. '--' clears
# the override (writes NULL), handing the member back to Watson's normal
# attendance-based computation -- this never touches or disables that
# computation for anyone else, including the still-live auto-reinstatement
# rule in attendance_intake.py (a disconnected member who attends still
# flips back to active; that's the separate `active` column, untouched by
# any of this).
_CONNECTED_REAL_COLUMN = "connected_override"

_CONNECTED_REGULAR_MIN_VISITS = 6
_CONNECTED_WINDOW_DAYS = 56  # 8 weeks, inclusive
_CONNECTED_CURRENT_DAYS_MAX = 13
_CONNECTED_AT_RISK_DAYS_MAX = 27


def _connected(conn, member_id: int, today: date) -> str:
    """Partner/Connected/Active/Deacon/Residency redesign's Connected ladder --
    built fresh from jobs.congregation.attendance only (not connect_cards),
    per Bill's 2026-09-24 call ("brand new section, from attendance data").
    Returns 'neighbor' (2026-09-24, Bill's call) for a member with zero
    attendance rows -- someone in the database who has never actually
    attended a service.

    regular = 6+ attendances in the trailing rolling 8-calendar-week window
    ending *today*. at_risk/critical only apply to someone who has reached
    regular at some point in their history (checked via a sliding window
    ending on each of their own attendance dates, since that's always where
    a window's count is maximized) -- gate confirmed with Bill. A former
    regular who attended within the last 13 days but has since dipped under
    the 6-in-8wk bar is still shown 'regular' rather than falling into a gap
    the original spec didn't cover (mirrors elder_shepherding_report.py's
    _bucket() 0-13-day 'current' cutoff for the same population)."""
    rows = conn.execute(
        "SELECT DISTINCT service_date FROM attendance WHERE member_id = ? ORDER BY service_date",
        (member_id,),
    ).fetchall()
    dates = [date.fromisoformat(r[0]) for r in rows]
    if not dates:
        return "neighbor"

    visit_count = len(dates)
    last_seen = dates[-1]
    days_since = (today - last_seen).days

    window_start_now = today - timedelta(days=_CONNECTED_WINDOW_DAYS - 1)
    regular_now = sum(1 for d in dates if window_start_now <= d <= today) >= _CONNECTED_REGULAR_MIN_VISITS

    def _count_window_ending(anchor: date) -> int:
        start = anchor - timedelta(days=_CONNECTED_WINDOW_DAYS - 1)
        return sum(1 for d in dates if start <= d <= anchor)

    ever_regular = regular_now or any(
        _count_window_ending(d) >= _CONNECTED_REGULAR_MIN_VISITS for d in dates
    )

    if regular_now or (ever_regular and days_since <= _CONNECTED_CURRENT_DAYS_MAX):
        return "regular"
    if ever_regular:
        return "at risk" if days_since <= _CONNECTED_AT_RISK_DAYS_MAX else "critical"
    if visit_count == 1:
        return "1st time"
    if visit_count == 2:
        return "2nd time"
    return "guest"


def _connected_or_override(conn, member_id: int, override: str | None, today: date) -> str:
    """The value GET /state actually returns for Connected: the manual
    override if one's set, else the live computation. Kept separate from
    _connected() itself so the pure-computation function stays easy to
    reason about/test on its own."""
    return override if override else _connected(conn, member_id, today)


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
    today = date.today()
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM members ORDER BY name").fetchall()
        members = []
        for r in rows:
            m = dict(r)
            m["connected"] = _connected_or_override(conn, m["id"], m.get(_CONNECTED_REAL_COLUMN), today)
            members.append(m)
    return jsonify({"members": members})


@catalystdb_web_bp.route("/api/cat/catalystdb/update", methods=["POST"])
@_require_key
def update():
    """Single or batch edit: {"ids": [1,2,3], "field": "active", "value": "active"}."""
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

    # "connected" is virtual -- see _CONNECTED_REAL_COLUMN's comment above.
    # '--' clears the override (NULL), handing the member back to the live
    # computation instead of literally storing the string '--' as a rung on
    # the ladder.
    write_field = _CONNECTED_REAL_COLUMN if field == "connected" else field
    write_value = None if (field == "connected" and value == "--") else value

    with _conn() as conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE members SET {write_field} = ?, updated_at = datetime('now') "
            f"WHERE id IN ({placeholders})",
            (write_value, *ids),
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
            # "connected" is virtual -- see _CONNECTED_REAL_COLUMN's comment
            # above. '--' means "no override", i.e. nothing to write here.
            if col == "connected":
                if data[col] == "--":
                    continue
                fields.append(_CONNECTED_REAL_COLUMN)
            else:
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
        member = dict(row)
        member["connected"] = _connected_or_override(conn, new_id, member.get(_CONNECTED_REAL_COLUMN), date.today())
    return jsonify({"member": member}), 201


@catalystdb_web_bp.route("/api/cat/catalystdb/deactivate", methods=["POST"])
@_require_key
def deactivate():
    """Soft-delete: sets active (default 'disconnected', or 'deceased' if
    given) for the given ids. No hard deletes from this screen.
    {"ids": [1,2,3], "target": "deceased"} to mark deceased instead of
    disconnected; target defaults to 'disconnected', matching this
    endpoint's pre-2026-09-24 behavior most closely."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    target = data.get("target", "disconnected")
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids (non-empty list) is required"}), 400
    if target not in ("disconnected", "deceased"):
        return jsonify({"error": "target must be 'disconnected' or 'deceased'"}), 400
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "ids must be integers"}), 400

    with _conn() as conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE members SET active = ?, updated_at = datetime('now') "
            f"WHERE id IN ({placeholders})",
            (target, *ids),
        )
    return jsonify({"deactivated": len(ids), "target": target})
