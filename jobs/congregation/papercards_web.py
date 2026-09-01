"""jobs/congregation/papercards_web.py — Flask Blueprint backing the
wtsn.me/cat/papercards staff tool: lets Donna key in paper connect cards
turned in at church, writing straight to congregation.db (no email/IMAP
round-trip the way jobs/connect_cards/intake.py's digital-form path works).

Auth: same shared-secret pattern as every other /cat/ tool -- header
X-Watson-Key matching PAPERCARDS_API_KEY, a DEDICATED key for this consumer
(the watson-tools wtsn.me app), per this codebase's one-key-per-external-
consumer convention.

Member matching reuses jobs.congregation.member_match.find_or_create_member
(email -> phone -> fuzzy name -> new member), the same helper intake.py
uses -- not intake.py's own _resolve_member/conflict-logging wrapper, which
exists specifically for its automated-parse-of-an-email path. Donna is a
human looking at the physical card while she types, so that extra
conflict-flagging layer isn't needed here.

The card's service date is NOT inferred (no "most recent Sunday" guess) --
Donna picks it herself via a date field on the form, since she knows which
Sunday (or other service) the physical card actually came from and that's
more reliable than any date math on when she happens to be typing it in.

Mount on the Watson dashboard app:
    from jobs.congregation.papercards_web import papercards_web_bp
    app.register_blueprint(papercards_web_bp)
"""
import os
import sqlite3
from datetime import date
from functools import wraps

from flask import Blueprint, jsonify, request

from jobs.congregation.member_match import find_or_create_member

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

papercards_web_bp = Blueprint("papercards_web", __name__)

_API_KEY = lambda: os.getenv("PAPERCARDS_API_KEY", "")

_VALID_CAMPUSES = ("Wilmington", "Online")

# Canonical step keys -- same values jobs/connect_cards/shepherding_report.py's
# _STEP_NAMES and the next_steps.step column use everywhere else.
_NEXT_STEP_KEYS = {"follow_jesus", "baptism", "grow_faith", "catalyst_partner", "small_group", "ministry_team"}


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


@papercards_web_bp.route("/api/cat/papercards/submit", methods=["POST"])
@_require_key
def submit():
    data = request.get_json(force=True) or {}

    service_date_raw = (data.get("service_date") or "").strip()
    campus = (data.get("campus") or "").strip()
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    is_first_visit = bool(data.get("is_first_visit"))
    how_heard = (data.get("how_heard") or "").strip()
    next_steps = data.get("next_steps") or []
    questions_comments = (data.get("questions_comments") or "").strip()
    prayer_request = (data.get("prayer_request") or "").strip()
    prayer_leadership_only = bool(data.get("prayer_leadership_only"))

    try:
        service_date = date.fromisoformat(service_date_raw).isoformat()
    except ValueError:
        return jsonify({"error": "service_date must be a valid date (YYYY-MM-DD)"}), 400
    if campus not in _VALID_CAMPUSES:
        return jsonify({"error": f"campus must be one of {_VALID_CAMPUSES}"}), 400
    if not first_name:
        return jsonify({"error": "first_name is required"}), 400
    if not last_name:
        return jsonify({"error": "last_name is required"}), 400
    if not isinstance(next_steps, list) or not all(isinstance(s, str) for s in next_steps):
        return jsonify({"error": "next_steps must be a list of strings"}), 400
    next_steps = [s for s in next_steps if s in _NEXT_STEP_KEYS]

    name = f"{first_name} {last_name}".strip()

    qc_parts = []
    if questions_comments:
        qc_parts.append(questions_comments)
    if is_first_visit and how_heard:
        qc_parts.append(f"How they heard about Catalyst: {how_heard}")
    final_questions_comments = "\n\n".join(qc_parts) or None

    with _conn() as conn:
        member_id = find_or_create_member(conn, name, email, phone, service_date)

        conn.execute(
            """
            INSERT INTO connect_cards
              (member_id, service_date, campus, raw_text, questions_comments,
               prayer_request, next_steps, is_first_visit, prayer_request_public)
            VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                member_id,
                service_date,
                campus,
                final_questions_comments,
                prayer_request or None,
                ", ".join(next_steps) or None,
                1 if is_first_visit else 0,
                0 if prayer_leadership_only else 1,
            ),
        )
        card_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        already_present = conn.execute(
            "SELECT 1 FROM attendance WHERE member_id = ? AND service_date = ?",
            (member_id, service_date),
        ).fetchone() is not None
        if not already_present:
            conn.execute(
                "INSERT INTO attendance (member_id, service_date, campus, card_id) VALUES (?, ?, ?, ?)",
                (member_id, service_date, campus, card_id),
            )

        for step_key in next_steps:
            conn.execute(
                "INSERT INTO next_steps (member_id, card_id, step, date) VALUES (?, ?, ?, ?)",
                (member_id, card_id, step_key, service_date),
            )

        if prayer_request:
            conn.execute(
                "INSERT INTO prayer_requests (member_id, card_id, request_text, date, leadership_only)"
                " VALUES (?, ?, ?, ?, ?)",
                (member_id, card_id, prayer_request, service_date, 1 if prayer_leadership_only else 0),
            )

        if is_first_visit:
            conn.execute(
                "INSERT INTO follow_ups (member_id, card_id, note) VALUES (?, ?, ?)",
                (member_id, card_id, "First-time visitor"),
            )

        conn.commit()

    return jsonify({"ok": True, "member_id": member_id, "card_id": card_id, "service_date": service_date}), 200
