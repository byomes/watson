"""jobs/congregation/connection_web.py -- Flask Blueprint backing wtsn.me/cat/connection:
a per-person "connectedness" view that joins the four things Bill says matter
(2026-10-06): worship attendance, small group attendance, serving, and events.

It shows four plain counts over a trailing window (default 90 days), not one
score, and reuses CatalystDB's existing worship ladder (catalystdb_web._connected:
regular / at risk / critical / ...) rather than inventing a second definition.

Audience = the deacon app's login (elders, deacons, staff with a deacon PIN):
every route needs BOTH X-Watson-Key == CONNECTION_API_KEY and a live
X-Deacon-Session (same two-layer pattern as deacons_web.py).

Honesty about data: serving check-offs began 2026-09-20 and group attendance
2026-10-07, so those counts are partial until they have a full window of
history. `coverage` in the response says so, and the "worship only" flag stays
off until group tracking is old enough to mean something.

Celebrate Recovery is never named (group_attendance never holds it), so it
cannot appear here. Follow-up on these signals is shepherd work, not Bill's.
"""
import os
import sqlite3
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

from core.database import get_connection as _watson_conn
from jobs.congregation import deacon_sessions
from jobs.congregation.catalystdb_web import _CONNECTED_REAL_COLUMN, _connected_or_override

CONGREGATION_DB = os.path.expanduser("~/watson/data/congregation.db")
connection_web_bp = Blueprint("connection_web", __name__)

_DEFAULT_WINDOW = 90
_MIN_COVERAGE_DAYS = 56  # group tracking must be this old before "worship only" means anything


def _conn():
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = os.getenv("CONNECTION_API_KEY", "")
        if not key or request.headers.get("X-Watson-Key") != key:
            return jsonify({"error": "unauthorized"}), 401
        with _conn() as conn:
            who = deacon_sessions.resolve(conn, request.headers.get("X-Deacon-Session", ""))
        if not who:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _first_date(conn, table: str, col: str) -> str | None:
    try:
        r = conn.execute(f"SELECT MIN({col}) FROM {table}").fetchone()
        return r[0] if r else None
    except sqlite3.OperationalError:
        return None


def _flag(ladder: str, worship: int, groups: int, serving: int, rostered: bool, reliable: bool) -> str:
    if ladder in ("at risk", "critical"):
        return "slipping"
    if ladder in ("1st time", "2nd time", "guest", "neighbor"):
        return "new" if ladder != "neighbor" else "none"
    if groups or serving:
        return "connected"
    if reliable and worship >= 3 and not rostered:
        return "worship-only"
    return "worship" if worship else "none"


@connection_web_bp.route("/api/cat/connection/state", methods=["GET"])
@_require_auth
def state():
    try:
        window = int(request.args.get("days", _DEFAULT_WINDOW))
    except ValueError:
        window = _DEFAULT_WINDOW
    window = max(14, min(window, 365))
    today = date.today()
    lo = (today - timedelta(days=window)).isoformat()

    with _watson_conn() as wconn:
        regs = wconn.execute(
            """SELECT r.member_id, e.event_name AS event, COALESCE(r.submitted_at, r.created_at) AS at
               FROM event_registrations r JOIN church_events e ON e.id = r.event_id
               WHERE r.member_id IS NOT NULL AND date(COALESCE(r.submitted_at, r.created_at)) >= ?""", (lo,)).fetchall()
    events: dict[int, list[dict]] = {}
    for r in regs:
        events.setdefault(r["member_id"], []).append({"event": r["event"], "date": (r["at"] or "")[:10]})
    # Plus every Subsplash registration copied by jobs/church_calendar/registrations.py
    # (de-duplicated against the email-detected rows above by event title).
    with _watson_conn() as wconn:
        _p = wconn.execute("SELECT value FROM system_settings WHERE key='subsplash_registrations_paused'").fetchone()
        sub = [] if (_p and _p["value"] == "1") else wconn.execute(
            """SELECT member_id, event_title, event_start FROM subsplash_registrations
               WHERE member_id IS NOT NULL AND date(COALESCE(submitted_at, first_seen_at)) >= ?""", (lo,)).fetchall()
    for r in sub:
        have = {e["event"].lower() for e in events.get(r["member_id"], [])}
        if r["event_title"].lower() not in have:
            events.setdefault(r["member_id"], []).append({"event": r["event_title"], "date": (r["event_start"] or "")[:10]})

    with _conn() as conn:
        coverage = {
            "group_since": _first_date(conn, "group_attendance", "event_date"),
            "serving_since": _first_date(conn, "serving_attendance", "service_date"),
        }
        gs = coverage["group_since"]
        reliable = bool(gs) and (today - date.fromisoformat(gs)).days >= _MIN_COVERAGE_DAYS
        coverage["flags_reliable"] = reliable

        worship: dict[int, list[str]] = {}
        for r in conn.execute("SELECT DISTINCT member_id, service_date FROM attendance WHERE service_date >= ?", (lo,)):
            worship.setdefault(r[0], []).append(r[1])
        groups: dict[int, list[dict]] = {}
        for r in conn.execute("SELECT member_id, series, event_date FROM group_attendance WHERE event_date >= ? ORDER BY event_date", (lo,)):
            groups.setdefault(r["member_id"], []).append({"group": r["series"].split("|", 1)[-1], "date": r["event_date"]})
        serving: dict[int, list[dict]] = {}
        for r in conn.execute("SELECT member_id, team_name, service_date FROM serving_attendance WHERE service_date >= ? ORDER BY service_date", (lo,)):
            serving.setdefault(r["member_id"], []).append({"team": r["team_name"], "date": r["service_date"]})
        teams: dict[int, list[str]] = {}
        for r in conn.execute("SELECT member_id, team_name FROM team_memberships WHERE active = 1"):
            teams.setdefault(r["member_id"], []).append(r["team_name"])
        kid_ids = {r[0] for r in conn.execute("SELECT member_id FROM kids WHERE member_id IS NOT NULL")}

        people = []
        for m in conn.execute("SELECT * FROM members WHERE active NOT IN ('disconnected','deceased') ORDER BY name"):
            mid = m["id"]
            if mid in kid_ids:
                continue
            ladder = _connected_or_override(conn, mid, m[_CONNECTED_REAL_COLUMN] if _CONNECTED_REAL_COLUMN in m.keys() else None, today)
            w, g, s, ev = worship.get(mid, []), groups.get(mid, []), serving.get(mid, []), events.get(mid, [])
            people.append({
                "id": mid, "name": m["name"], "deacon": m["deacon"], "ladder": ladder,
                "worship": len(w), "groups": len({(x["group"], x["date"]) for x in g}),
                "serving": len({x["date"] for x in s}), "events": len(ev),
                "teams": teams.get(mid, []),
                "flag": _flag(ladder, len(w), len(g), len(s), bool(teams.get(mid)), reliable),
                "group_list": g, "serving_list": s, "event_list": ev,
                "last_worship": max(w) if w else None,
            })
    return jsonify({"window_days": window, "coverage": coverage, "people": people}), 200
