"""jobs/privacy/dashboard_api.py — Flask Blueprint for the dashboard's
Privacy Guard tile (More tab). Read-only visibility layer only — this
module never calls submit_removal()/approve_removal()/skip_removal() and
has no write routes. Approve/skip stays Telegram-only
(bot.py:handle_privacy_callback); this is a viewer, not a control surface.

Mount on the Watson dashboard app:
    from jobs.privacy.dashboard_api import privacy_guard_bp
    app.register_blueprint(privacy_guard_bp)
"""
from functools import wraps

from flask import Blueprint, jsonify, session

from core.database import get_connection

privacy_guard_bp = Blueprint("privacy_guard", __name__)


def _require_admin_session(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@privacy_guard_bp.route("/api/privacy-removals", methods=["GET"])
@_require_admin_session
def privacy_removals():
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT r.id, r.status, r.matched_url, r.confidence_score,
                      r.submitted_at, r.next_rescan_at, r.created_at, r.failure_reason,
                      p.name AS person_name, b.name AS broker_name
               FROM privacy_removals r
               JOIN family_profiles p ON p.id = r.person_id
               JOIN privacy_brokers b ON b.id = r.broker_id
               ORDER BY r.created_at DESC"""
        ).fetchall()
        return jsonify([dict(row) for row in rows]), 200
    finally:
        conn.close()
