"""jobs/congregation/elder_shepherding_report_web.py — Flask Blueprint
backing the wtsn.me/cat/shepherdingreport read-only elder view: every
non-excluded member, grouped by deacon, sorted worst-attendance-bucket
first within each group.

Mount on the Watson dashboard app:
    from jobs.congregation.elder_shepherding_report_web import elder_shepherding_report_web_bp
    app.register_blueprint(elder_shepherding_report_web_bp)

Auth: same shared-secret pattern as jobs/congregation/attendance_web.py --
every route requires header X-Watson-Key matching SHEPHERDING_REPORT_API_KEY,
a DEDICATED key for this consumer (the watson-tools wtsn.me app), per this
codebase's one-key-per-external-consumer convention. Read-only (GET only) --
there's no companion write route the way attendance_web.py has a toggle.

This exists because jobs/congregation/elder_shepherding_report.py's Telegram
message is deliberately counts-only to stay under Telegram's character
limit; this route serves the full named breakdown that message links to.
"""
import os
from functools import wraps

from flask import Blueprint, jsonify, request

from jobs.congregation.elder_shepherding_report import build_deacon_group_names
from jobs.connect_cards.shepherding_report import _today

elder_shepherding_report_web_bp = Blueprint("elder_shepherding_report_web", __name__)

_API_KEY = lambda: os.getenv("SHEPHERDING_REPORT_API_KEY", "")


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _API_KEY() or request.headers.get("X-Watson-Key") != _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@elder_shepherding_report_web_bp.route("/api/cat/shepherdingreport/state", methods=["GET"])
@_require_key
def get_state():
    return jsonify({
        "generated_date": _today(),
        "groups": build_deacon_group_names(),
    }), 200
