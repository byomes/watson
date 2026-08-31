"""jobs/telegram/dashboard_api.py -- Flask Blueprint serving the dashboard's
Telegram Log tile: outbound messages Watson has sent, now that it sends to
onboarded leaders (jobs/telegram/send_to_person.py) and not just Bill.

Mount on the Watson dashboard app:
    from jobs.telegram.dashboard_api import telegram_log_bp
    app.register_blueprint(telegram_log_bp)

Read-only visibility layer -- does not touch the send path.
"""
from functools import wraps

from flask import Blueprint, jsonify, request, session

from core.database import get_connection

_DEFAULT_DAYS = 7
_MAX_DAYS = 90
_LIMIT = 300

telegram_log_bp = Blueprint("telegram_log", __name__)


def _require_admin_session(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@telegram_log_bp.route("/api/telegram-log", methods=["GET"])
@_require_admin_session
def telegram_log():
    try:
        days = int(request.args.get("days", _DEFAULT_DAYS))
    except ValueError:
        days = _DEFAULT_DAYS
    days = max(1, min(days, _MAX_DAYS))

    recipient = (request.args.get("recipient") or "").strip()

    # created_at is stored via datetime('now', 'localtime') (bot.py's _log_tg,
    # send_to_person.py's _log_sent) -- the cutoff must be computed the same
    # way or it drifts against UTC by the local offset.
    query = (
        "SELECT id, message, recipient, created_at FROM telegram_log "
        "WHERE direction = 'out' AND created_at >= datetime('now', 'localtime', ?)"
    )
    params: list = [f"-{days} days"]
    if recipient:
        query += " AND recipient LIKE ?"
        params.append(f"%{recipient}%")
    query += " ORDER BY id DESC LIMIT ?"
    params.append(_LIMIT)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return jsonify([
        {
            "id": r["id"],
            "recipient": r["recipient"] or "Bill",
            "message": r["message"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]), 200
