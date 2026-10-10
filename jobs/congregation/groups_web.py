"""jobs/congregation/groups_web.py -- Flask Blueprint backing wtsn.me/cat/groups:
a roster check-off for small groups and special events (Men's Fraternity,
Woven, Remix, Men's Breakfast, ...), the group-attendance leg of the planned
"connectedness" view (see notes/small_group_attendance_sketch.md).

Series and session dates come from the unified Subsplash calendar cache
(church_calendar_events in watson.db, jobs/church_calendar/calendars.py); the
attendance itself lives in congregation.db next to `attendance` so team chat
can join it to members.

Celebrate Recovery is confidential (Bill, 2026-10-06): it is HEAD COUNT ONLY.
Its page offers one number and the backend refuses any named record for it,
so no names can ever land in group_attendance for that series.

Auth/trust model: same as servants_web.py -- every route needs header
X-Watson-Key == GROUPS_API_KEY (dedicated key), a shared link for leaders.

Mount: from jobs.congregation.groups_web import groups_web_bp; app.register_blueprint(groups_web_bp)
"""
import os
import sqlite3
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

from core.database import get_connection as _watson_conn
from jobs.congregation.servants_web import _cascade_members

CONGREGATION_DB = os.path.expanduser("~/watson/data/congregation.db")
groups_web_bp = Blueprint("groups_web", __name__)

_CALENDARS = ("Small Groups", "Special Events")
_SESSION_WINDOW_DAYS = 45


def _api_key() -> str:
    return os.getenv("GROUPS_API_KEY", "")


def _conn():
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _bootstrap() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series TEXT NOT NULL,
                event_date TEXT NOT NULL,
                member_id INTEGER NOT NULL REFERENCES members(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(series, event_date, member_id)
            )""")
        # A group's regulars (like the kids app's class roster): everyone listed here
        # shows with a toggle each session. Marking someone present does NOT add them;
        # regulars are added only on purpose (roster_add).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_roster (
                series TEXT NOT NULL,
                member_id INTEGER NOT NULL REFERENCES members(id),
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (series, member_id)
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_counts (
                series TEXT NOT NULL,
                event_date TEXT NOT NULL,
                guests INTEGER NOT NULL DEFAULT 0,
                headcount INTEGER,
                PRIMARY KEY (series, event_date)
            )""")


_bootstrap()


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _api_key() or request.headers.get("X-Watson-Key") != _api_key():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def is_counts_only(series: str) -> bool:
    return "celebrate recovery" in (series or "").lower()


def _series_list() -> list[dict]:
    with _watson_conn() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT series, title FROM church_calendar_events WHERE active=1 "
            f"AND calendar IN ({','.join('?' * len(_CALENDARS))}) ORDER BY title", _CALENDARS).fetchall()
    return [{"series": r["series"], "title": r["title"], "counts_only": is_counts_only(r["title"])} for r in rows]


def _session_dates(series: str) -> list[str]:
    """Calendar dates for this series from today back _SESSION_WINDOW_DAYS, newest first."""
    lo = (date.today() - timedelta(days=_SESSION_WINDOW_DAYS)).isoformat()
    with _watson_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT start_date FROM church_calendar_events WHERE series=? AND start_date BETWEEN ? AND date('now','localtime') "
            "ORDER BY start_date DESC", (series, lo)).fetchall()
    return [r["start_date"] for r in rows if r["start_date"]]


def _registrations(series: str, event_date: str) -> tuple[bool, set[int]]:
    """(known, member_ids) for who signed up on Subsplash for this series on this date.

    `known` is False when Subsplash has no registration form for that session
    (most small groups), so the page never labels anyone "not registered" for a
    group that does not take sign-ups. Registrations are copied into watson.db by
    jobs/church_calendar/registrations.py; people who could not be matched to a
    member (member_id NULL) are left out. Honors the same pause switch the
    connection view uses."""
    title = next((s["title"] for s in _series_list() if s["series"] == series), "")
    if not title or not event_date:
        return False, set()
    with _watson_conn() as wconn:
        paused = wconn.execute("SELECT value FROM system_settings WHERE key='subsplash_registrations_paused'").fetchone()
        if paused and paused["value"] == "1":
            return False, set()
        rows = wconn.execute(
            "SELECT member_id FROM subsplash_registrations WHERE event_title=? AND date(event_start)=?",
            (title, event_date)).fetchall()
        form = wconn.execute(
            "SELECT 1 FROM subsplash_event_regs WHERE title=? AND start_date=? AND has_form=1",
            (title, event_date)).fetchone()
    return bool(rows or form), {r["member_id"] for r in rows if r["member_id"] is not None}


def _valid(series: str, event_date: str) -> str | None:
    """Error string if this series/date can't be recorded, else None."""
    if series not in {s["series"] for s in _series_list()}:
        return "unknown series"
    if event_date not in _session_dates(series):
        return "date must be a recent session of this group"
    return None


@groups_web_bp.route("/api/cat/groups/state", methods=["GET"])
@_require_key
def state():
    series_list = _series_list()
    series = request.args.get("series", "")
    if series not in {s["series"] for s in series_list}:
        return jsonify({"series_list": series_list}), 200
    dates = _session_dates(series)
    event_date = request.args.get("date", "")
    event_date = event_date if event_date in dates else (dates[0] if dates else "")
    out = {"series_list": series_list, "series": series, "dates": dates, "event_date": event_date,
           "counts_only": is_counts_only(series), "roster": [], "guests": 0, "headcount": None}
    with _conn() as conn:
        if event_date:
            c = conn.execute("SELECT guests, headcount FROM group_counts WHERE series=? AND event_date=?",
                             (series, event_date)).fetchone()
            if c:
                out["guests"], out["headcount"] = c["guests"], c["headcount"]
        if out["counts_only"]:
            return jsonify(out), 200
        # event_date may be "" (group has not met yet): then nobody is "present" and the
        # leader can still build the regulars list.
        # "Registered" (signed up on Subsplash) is kept apart from "present" (actually came):
        # a registrant who is not a regular still gets a row, and checking someone off
        # never turns a sign-up into attendance or the reverse.
        known, registered = _registrations(series, event_date)
        reg_ids = sorted(registered)
        rows = conn.execute(
            f"""SELECT m.id, m.name,
                      EXISTS(SELECT 1 FROM group_attendance g2 WHERE g2.series=? AND g2.event_date=? AND g2.member_id=m.id) AS present,
                      EXISTS(SELECT 1 FROM group_roster r2 WHERE r2.series=? AND r2.member_id=m.id) AS regular
               FROM members m
               WHERE m.active NOT IN ('disconnected','deceased') AND (
                   m.id IN (SELECT member_id FROM group_roster WHERE series=?)
                   OR m.id IN (SELECT member_id FROM group_attendance WHERE series=? AND event_date=?)
                   OR m.id IN ({','.join('?' * len(reg_ids))}))
               ORDER BY m.name""", (series, event_date, series, series, series, event_date, *reg_ids)).fetchall()
        out["registration_known"] = known
        out["roster"] = [{"id": r["id"], "name": r["name"], "present": bool(r["present"]),
                          "registered": r["id"] in registered, "regular": bool(r["regular"])} for r in rows]
    return jsonify(out), 200


@groups_web_bp.route("/api/cat/groups/lookup", methods=["GET"])
@_require_key
def lookup():
    with _conn() as conn:
        return jsonify({"candidates": [{"id": c["id"], "name": c["name"]}
                                       for c in _cascade_members(conn, request.args.get("name", ""))]}), 200


@groups_web_bp.route("/api/cat/groups/toggle", methods=["POST"])
@_require_key
def toggle():
    d = request.get_json(force=True) or {}
    series, event_date, member_id, present = d.get("series", ""), d.get("event_date", ""), d.get("member_id"), bool(d.get("present"))
    if is_counts_only(series):
        return jsonify({"error": "this group records a head count only, no names"}), 403
    if not isinstance(member_id, int):
        return jsonify({"error": "member_id (int) is required"}), 400
    err = _valid(series, event_date)
    if err:
        return jsonify({"error": err}), 400
    with _conn() as conn:
        if not conn.execute("SELECT 1 FROM members WHERE id=?", (member_id,)).fetchone():
            return jsonify({"error": "no such member"}), 404
        if present:
            conn.execute("INSERT OR IGNORE INTO group_attendance (series, event_date, member_id) VALUES (?,?,?)",
                         (series, event_date, member_id))
        else:
            conn.execute("DELETE FROM group_attendance WHERE series=? AND event_date=? AND member_id=?",
                         (series, event_date, member_id))
    return jsonify({"member_id": member_id, "present": present}), 200


@groups_web_bp.route("/api/cat/groups/counts", methods=["POST"])
@_require_key
def counts():
    d = request.get_json(force=True) or {}
    series, event_date = d.get("series", ""), d.get("event_date", "")
    err = _valid(series, event_date)
    if err:
        return jsonify({"error": err}), 400
    guests, headcount = d.get("guests"), d.get("headcount")
    if is_counts_only(series):
        guests = 0
        if not isinstance(headcount, int) or not 0 <= headcount <= 500:
            return jsonify({"error": "headcount (0-500) is required"}), 400
    else:
        headcount = None
        if not isinstance(guests, int) or not 0 <= guests <= 100:
            return jsonify({"error": "guests (0-100) is required"}), 400
    with _conn() as conn:
        conn.execute("""INSERT INTO group_counts (series, event_date, guests, headcount) VALUES (?,?,?,?)
                        ON CONFLICT(series, event_date) DO UPDATE SET guests=excluded.guests, headcount=excluded.headcount""",
                     (series, event_date, guests or 0, headcount))
    return jsonify({"guests": guests or 0, "headcount": headcount}), 200


@groups_web_bp.route("/api/cat/groups/remove", methods=["POST"])
@_require_key
def remove():
    """Takes someone off a group's regulars list (like the kids app's X). Also clears
    their mark for the date on screen, so they vanish from what the leader sees now.
    Other dates' attendance history is kept."""
    d = request.get_json(force=True) or {}
    series, event_date, member_id = d.get("series", ""), d.get("event_date", ""), d.get("member_id")
    if is_counts_only(series):
        return jsonify({"error": "this group records a head count only, no names"}), 403
    if not isinstance(member_id, int):
        return jsonify({"error": "member_id (int) is required"}), 400
    if series not in {x["series"] for x in _series_list()}:
        return jsonify({"error": "unknown series"}), 400
    if event_date and event_date not in _session_dates(series):
        return jsonify({"error": "date must be a recent session of this group"}), 400
    with _conn() as conn:
        conn.execute("DELETE FROM group_roster WHERE series=? AND member_id=?", (series, member_id))
        if event_date:
            conn.execute("DELETE FROM group_attendance WHERE series=? AND event_date=? AND member_id=?",
                         (series, event_date, member_id))
    return jsonify({"member_id": member_id, "removed": True}), 200


@groups_web_bp.route("/api/cat/groups/roster_add", methods=["POST"])
@_require_key
def roster_add():
    """Add someone to a group's regulars without marking them present (used to set up
    the list before the group's first session, and by anyone adding a regular)."""
    d = request.get_json(force=True) or {}
    series, member_id = d.get("series", ""), d.get("member_id")
    if is_counts_only(series):
        return jsonify({"error": "this group records a head count only, no names"}), 403
    if series not in {x["series"] for x in _series_list()}:
        return jsonify({"error": "unknown series"}), 400
    if not isinstance(member_id, int):
        return jsonify({"error": "member_id (int) is required"}), 400
    with _conn() as conn:
        if not conn.execute("SELECT 1 FROM members WHERE id=?", (member_id,)).fetchone():
            return jsonify({"error": "no such member"}), 404
        conn.execute("INSERT OR IGNORE INTO group_roster (series, member_id) VALUES (?,?)", (series, member_id))
    return jsonify({"member_id": member_id, "added": True}), 200
