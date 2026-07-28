"""jobs/email_activity/api.py — Flask Blueprint proxying Brevo's transactional
email event log for the dashboard's Email Activity tile.

Mount on the Watson dashboard app:
    from jobs.email_activity.api import email_activity_bp
    app.register_blueprint(email_activity_bp)

Read-only visibility layer only — does not touch the send path
(jobs/email_job/brevo_send.py, jobs/arc/api.py's resend_welcome()) at all.
"""
import os
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request, session

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BREVO_EVENTS_URL = "https://api.brevo.com/v3/smtp/statistics/events"
_TIMEOUT = 15
_MAX_DAYS = 90
_DEFAULT_DAYS = 7
_LIMIT = 500

email_activity_bp = Blueprint("email_activity", __name__)


def _require_admin_session(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _flatten_event(raw: dict) -> dict:
    return {
        "date": raw.get("date"),
        "email": raw.get("email"),
        "subject": raw.get("subject") or raw.get("tag") or "",
        "event": raw.get("event"),
        "reason": raw.get("reason") or "",
    }


@email_activity_bp.route("/api/email-activity", methods=["GET"])
@_require_admin_session
def email_activity():
    api_key = os.getenv("BREVO_API_KEY", "")
    if not api_key:
        return jsonify({"error": "BREVO_API_KEY not set in .env"}), 500

    try:
        days = int(request.args.get("days", _DEFAULT_DAYS))
    except ValueError:
        days = _DEFAULT_DAYS
    days = max(1, min(days, _MAX_DAYS))

    event = (request.args.get("event") or "").strip()
    email = (request.args.get("email") or "").strip()

    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    params = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "limit": _LIMIT,
        "sort": "desc",
    }
    if event:
        params["event"] = event
    if email:
        params["email"] = email

    headers = {
        "accept": "application/json",
        "api-key": api_key,
    }

    try:
        resp = requests.get(BREVO_EVENTS_URL, params=params, headers=headers, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        return jsonify({"error": f"Brevo API request failed: {exc}"}), 502

    if resp.status_code == 404:
        # Brevo returns 404 (not an empty events list) when nothing matches the window/filters.
        return jsonify([]), 200

    if not resp.ok:
        return jsonify({"error": f"Brevo API {resp.status_code}: {resp.text}"}), 502

    data = resp.json()
    events = data.get("events") or []
    return jsonify([_flatten_event(e) for e in events]), 200
