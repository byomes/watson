"""jobs/congregation/servants_web.py — Flask Blueprint backing the
wtsn.me/cat/servants staff tool: lists every volunteer/serving team roster
(team_memberships, added 2026-09-22 -- see [[project_servant_banquet_tracking]])
with each person's role and started_serving_date, for team leaders to review
and correct, plus an "add missing person" flow when a leader notices a name
isn't on the list yet.

Mount on the Watson dashboard app:
    from jobs.congregation.servants_web import servants_web_bp
    app.register_blueprint(servants_web_bp)

Auth: same shared-secret pattern as jobs/congregation/attendance_web.py --
every route requires header X-Watson-Key matching SERVANTS_API_KEY, a
DEDICATED key for this consumer (not ATTENDANCE_API_KEY or any other), per
this codebase's one-key-per-external-consumer convention. The dashboard is
reachable publicly via Tailscale Funnel, so this route needs its own gate
regardless of the Funnel.

Trust model: same as attendance_web.py -- a shared link sent to leaders, no
per-leader login, since this is explicitly a "send to my leaders to verify"
tool (Bill's 2026-09-22 request), the same trust level already established
for the attendance-correction tool.

Name resolution for "add missing person" mirrors
jobs/congregation/serving_edit.py's _cascade (exact -> partial -> last-word
-> first-word), kept as its own copy here (self-contained blueprint files,
same convention attendance_web.py itself follows) but returns structured
JSON (candidates list) rather than a formatted Telegram reply string, since
the frontend needs to render a picker for an ambiguous match rather than
just display text.
"""
import os
import sqlite3
from functools import wraps

from flask import Blueprint, jsonify, request

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

servants_web_bp = Blueprint("servants_web", __name__)

_API_KEY = lambda: os.getenv("SERVANTS_API_KEY", "")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _last_name_key(name: str) -> str:
    parts = (name or "").strip().split()
    return parts[-1].lower() if parts else ""


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _cascade_members(conn, query: str) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    words = query.split()

    def _q(term: str, exact: bool) -> list[dict]:
        op = "= ?" if exact else "LIKE ?"
        val = term if exact else f"%{term}%"
        rows = conn.execute(
            "SELECT id, name, started_serving_date FROM members"
            f" WHERE active = 1 AND name {op} COLLATE NOCASE ORDER BY name",
            (val,),
        ).fetchall()
        return [dict(r) for r in rows]

    rows = _q(query, exact=True)
    if not rows:
        rows = _q(query, exact=False)
    if not rows and len(words) > 1:
        rows = _q(words[-1], exact=False)
        if len(rows) > 1:
            from jobs.people.lookup import _narrow_by_first_name
            rows = _narrow_by_first_name(rows, words[0])
    if not rows:
        rows = _q(words[0], exact=False)
    return rows


@servants_web_bp.route("/api/cat/servants/state", methods=["GET"])
@_require_key
def get_state():
    with _conn() as conn:
        rows = conn.execute(
            """SELECT tm.team_name, tm.position, m.id AS member_id, m.name, m.started_serving_date
               FROM team_memberships tm JOIN members m ON m.id = tm.member_id
               WHERE m.active = 1
               ORDER BY tm.team_name"""
        ).fetchall()

    teams: dict[str, list[dict]] = {}
    for r in rows:
        teams.setdefault(r["team_name"], []).append({
            "id": r["member_id"],
            "name": r["name"],
            "position": r["position"],
            "started_serving_date": r["started_serving_date"],
        })
    for members in teams.values():
        members.sort(key=lambda m: (_last_name_key(m["name"]), m["name"] or ""))

    team_list = [
        {"team_name": name, "members": members}
        for name, members in sorted(teams.items())
    ]
    return jsonify({"teams": team_list}), 200


@servants_web_bp.route("/api/cat/servants/lookup", methods=["GET"])
@_require_key
def lookup_name():
    """Used by the frontend's add-person form to check whether a typed name
    matches an existing member (and show their name/date for confirmation)
    before submitting -- avoids accidentally creating a duplicate member for
    someone already on file under a slightly different spelling."""
    query = request.args.get("name", "").strip()
    if not query:
        return jsonify({"candidates": []}), 200
    with _conn() as conn:
        candidates = _cascade_members(conn, query)
    return jsonify({"candidates": candidates}), 200


@servants_web_bp.route("/api/cat/servants/add", methods=["POST"])
@_require_key
def add_servant():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    team_name = (data.get("team_name") or "").strip()
    position = (data.get("position") or "").strip() or None
    started_serving_date = (data.get("started_serving_date") or "").strip() or None
    # member_id, when present, means the frontend already resolved this to
    # an exact existing person (via /lookup) and the leader confirmed it --
    # skips re-running the fuzzy cascade, which matters because a second
    # fuzzy pass over the same free-text name could theoretically resolve
    # differently (e.g. a name added between the lookup and submit).
    member_id = data.get("member_id")
    create_new = bool(data.get("create_new"))

    if not name:
        return jsonify({"error": "name is required"}), 400
    if not team_name:
        return jsonify({"error": "team_name is required"}), 400
    if started_serving_date:
        try:
            from datetime import date as _date
            _date.fromisoformat(started_serving_date)
        except ValueError:
            return jsonify({"error": "started_serving_date must be YYYY-MM-DD"}), 400

    with _conn() as conn:
        if member_id is not None:
            row = conn.execute(
                "SELECT id, name FROM members WHERE id = ? AND active = 1", (member_id,)
            ).fetchone()
            if not row:
                return jsonify({"error": "that member_id was not found"}), 404
        elif create_new:
            cur = conn.execute(
                "INSERT INTO members (name, status, member_status) VALUES (?, 'active', 'active')",
                (name,),
            )
            member_id = cur.lastrowid
            row = {"id": member_id, "name": name}
        else:
            candidates = _cascade_members(conn, name)
            if not candidates:
                return jsonify({
                    "error": "no matching member found",
                    "resolution": "not_found",
                }), 409
            if len(candidates) > 1:
                return jsonify({
                    "error": "more than one matching member",
                    "resolution": "ambiguous",
                    "candidates": candidates,
                }), 409
            row = candidates[0]
            member_id = row["id"]

        conn.execute(
            "INSERT INTO team_memberships (member_id, team_name, position) VALUES (?, ?, ?) "
            "ON CONFLICT(member_id, team_name) DO UPDATE SET position = excluded.position",
            (member_id, team_name, position),
        )
        if started_serving_date:
            conn.execute(
                "UPDATE members SET started_serving_date = ? WHERE id = ?",
                (started_serving_date, member_id),
            )
        conn.commit()

    return jsonify({
        "member_id": member_id,
        "name": row["name"],
        "team_name": team_name,
        "position": position,
        "started_serving_date": started_serving_date,
    }), 200
