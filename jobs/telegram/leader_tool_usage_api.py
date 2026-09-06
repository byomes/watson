"""jobs/telegram/leader_tool_usage_api.py -- Flask Blueprint serving the
dashboard's Leader Usage tile: per-leader use count and last-used timestamp
for Telegram-based tools built for onboarded team members/deacons (see
jobs/telegram/leader_tool_usage.py).

Mount on the Watson dashboard app:
    from jobs.telegram.leader_tool_usage_api import leader_tool_usage_bp
    app.register_blueprint(leader_tool_usage_bp)

Read-only visibility layer -- does not touch the logging path.
"""
from functools import wraps

from flask import Blueprint, jsonify, session

from jobs.telegram.leader_tool_usage import build_report

leader_tool_usage_bp = Blueprint("leader_tool_usage", __name__)


def _require_admin_session(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@leader_tool_usage_bp.route("/api/leader-tool-usage", methods=["GET"])
@_require_admin_session
def leader_tool_usage():
    return jsonify(build_report()), 200
