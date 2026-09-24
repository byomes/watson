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

Also backs wtsn.me/cat/serving (added 2026-09-22, Bill's "who actually
served on Sunday" request): a weekly check-off of who from each team's
roster actually showed up, distinct from the general congregation
attendance table (that one tracks who was AT church; this tracks who
SERVED on their team that Sunday). New serving_attendance table -- one row
per (member, team, service_date) actually served, presence-only, same
"no row = didn't serve" convention as the main `attendance` table (see
attendance_web.py's module docstring). The expected roster for a given
Sunday is just team_memberships itself, per Bill's "the roster of regularly
expected positions and teams" -- no separate weekly-schedule/rotation
concept exists yet.
"""
import os
import sqlite3
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

_RECENT_SUNDAYS_COUNT = 10


def _most_recent_sunday() -> date:
    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    return today - timedelta(days=days_since_sunday)


def _recent_sundays(count: int) -> list[str]:
    latest = _most_recent_sunday()
    return [(latest - timedelta(weeks=i)).isoformat() for i in range(count)]

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
               WHERE m.active = 1 AND tm.active = 1
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
    # Leaders float to the top of their team's list (Bill's 2026-09-22
    # request), everyone else stays sorted by last name below them. "Leader"
    # is read straight off team_memberships.position (e.g. "Leader",
    # "Elementary Kids Leader", "Remix Adult Leader") -- the actual free-text
    # values already in the Team Members List Export import, not a separate
    # role enum.
    for members in teams.values():
        members.sort(
            key=lambda m: (
                0 if "leader" in (m["position"] or "").lower() else 1,
                _last_name_key(m["name"]),
                m["name"] or "",
            )
        )

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
            # status/member_status dropped (schema default now covers status;
            # active_v2/partner/residency are the new columns -- see
            # family_edit.py's inserts for the same pattern). gender/
            # campus_preference/deacon default to the '--' blank convention
            # since a servant added here has none of that on file yet.
            cur = conn.execute(
                "INSERT INTO members (name, active_v2, partner, residency, gender, "
                "campus_preference, deacon) VALUES (?, 'active', 'np', 'local', '--', '--', '--')",
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

        # ON CONFLICT also resets active=1 -- this is also how "add person"
        # brings someone back who was previously marked no-longer-serving on
        # this team (see remove_servant below, which sets active=0 rather
        # than deleting the row).
        conn.execute(
            "INSERT INTO team_memberships (member_id, team_name, position, active) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(member_id, team_name) DO UPDATE SET position = excluded.position, active = 1",
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


@servants_web_bp.route("/api/cat/servants/remove", methods=["POST"])
@_require_key
def remove_servant():
    """"Mark as no longer serving" from the frontend -- sets this one
    team_memberships row's active flag to 0 rather than deleting it (Bill's
    2026-09-22 correction: the original delete-on-click behavior lost the
    person's serving history on this team; classifying them as no-longer-
    serving keeps the row, just excludes it from get_state's roster).
    Scoped to this team only -- a person on multiple teams keeps their
    other rows untouched. Does NOT touch members.started_serving_date or
    the member row itself -- that's tenure/identity data independent of
    any one team assignment. Re-adding the same person via /add (see the
    ON CONFLICT clause above) resets active back to 1."""
    data = request.get_json(force=True) or {}
    member_id = data.get("member_id")
    team_name = (data.get("team_name") or "").strip()

    if not isinstance(member_id, int):
        return jsonify({"error": "member_id (int) is required"}), 400
    if not team_name:
        return jsonify({"error": "team_name is required"}), 400

    with _conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM team_memberships WHERE member_id = ? AND team_name = ? AND active = 1",
            (member_id, team_name),
        ).fetchone()
        if not existing:
            return jsonify({"error": "not found"}), 404
        conn.execute(
            "UPDATE team_memberships SET active = 0 WHERE member_id = ? AND team_name = ?",
            (member_id, team_name),
        )
        conn.commit()

    return jsonify({"member_id": member_id, "team_name": team_name, "removed": True}), 200


@servants_web_bp.route("/api/cat/serving/state", methods=["GET"])
@_require_key
def get_serving_state():
    requested = request.args.get("date", "").strip()
    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    service_date = requested if requested in valid_dates else _most_recent_sunday().isoformat()

    with _conn() as conn:
        rows = conn.execute(
            """SELECT tm.team_name, tm.position, m.id AS member_id, m.name
               FROM team_memberships tm JOIN members m ON m.id = tm.member_id
               WHERE m.active = 1 AND tm.active = 1
                 AND tm.team_name NOT IN (SELECT team_name FROM serving_excluded_teams)
               ORDER BY tm.team_name"""
        ).fetchall()
        served_keys = {
            (row["member_id"], row["team_name"])
            for row in conn.execute(
                "SELECT member_id, team_name FROM serving_attendance WHERE service_date = ?",
                (service_date,),
            )
        }

    teams: dict[str, list[dict]] = {}
    for r in rows:
        teams.setdefault(r["team_name"], []).append({
            "id": r["member_id"],
            "name": r["name"],
            "position": r["position"],
            "served": (r["member_id"], r["team_name"]) in served_keys,
        })
    for members in teams.values():
        members.sort(
            key=lambda m: (
                0 if "leader" in (m["position"] or "").lower() else 1,
                _last_name_key(m["name"]),
                m["name"] or "",
            )
        )

    team_list = [
        {"team_name": name, "members": members}
        for name, members in sorted(teams.items())
    ]
    return jsonify({
        "service_date": service_date,
        "recent_sundays": _recent_sundays(_RECENT_SUNDAYS_COUNT),
        "teams": team_list,
    }), 200


@servants_web_bp.route("/api/cat/serving/toggle", methods=["POST"])
@_require_key
def toggle_serving():
    data = request.get_json(force=True) or {}
    member_id = data.get("member_id")
    team_name = (data.get("team_name") or "").strip()
    service_date = (data.get("service_date") or "").strip()
    served = bool(data.get("served"))

    valid_dates = set(_recent_sundays(_RECENT_SUNDAYS_COUNT))
    if not isinstance(member_id, int):
        return jsonify({"error": "member_id (int) is required"}), 400
    if not team_name:
        return jsonify({"error": "team_name is required"}), 400
    if service_date not in valid_dates:
        return jsonify({"error": "service_date must be one of the recent Sundays"}), 400

    with _conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM team_memberships WHERE member_id = ? AND team_name = ?",
            (member_id, team_name),
        ).fetchone()
        if not existing:
            return jsonify({"error": "that person isn't on this team"}), 404

        already_served = conn.execute(
            "SELECT 1 FROM serving_attendance WHERE member_id = ? AND team_name = ? AND service_date = ?",
            (member_id, team_name, service_date),
        ).fetchone() is not None

        if served and not already_served:
            conn.execute(
                "INSERT INTO serving_attendance (member_id, team_name, service_date) VALUES (?, ?, ?)",
                (member_id, team_name, service_date),
            )
        elif not served and already_served:
            conn.execute(
                "DELETE FROM serving_attendance WHERE member_id = ? AND team_name = ? AND service_date = ?",
                (member_id, team_name, service_date),
            )
        conn.commit()

    return jsonify({
        "member_id": member_id, "team_name": team_name, "service_date": service_date, "served": served,
    }), 200
