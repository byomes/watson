"""jobs/congregation/shepcheck_web.py -- Flask Blueprint backing the
wtsn.me/cat/shepcheck elder-level prayer-contact accountability report
(Bill's 2026-09-24 request, trial phase). Shows the week's prayer requests
alongside when a deacon was notified and when/whether they responded
(jobs/telegram/prayer_notify.py's prayer_contact_log), including any
escalation to Bill and his own follow-up status.

Same shared-key + per-person PIN auth pattern as catalystdb_web.py --
header X-Watson-Key matching SHEPCHECK_API_KEY gates every route, plus
verify_pin (shepcheck_pins table, scrypt hash via
jobs.congregation.deacon_pin_auth -- reused as-is, no deacon-specific
coupling) for the actual elder login. Locks the calling IP out after
shepcheck_login_lockout.MAX_FAILED_ATTEMPTS (3) consecutive wrong PINs.

Read-only for the trial: no action routes here, just /state. The Telegram
buttons (jobs/telegram/prayer_notify.py, bot.py's pr_* handlers) are still
the only way contact status actually changes.
"""
import os
import sqlite3
from functools import wraps

from flask import Blueprint, jsonify, request

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

shepcheck_web_bp = Blueprint("shepcheck_web", __name__)

_API_KEY = lambda: os.getenv("SHEPCHECK_API_KEY", "")


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


def _ensure_pins_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shepcheck_pins (
            person_name TEXT PRIMARY KEY,
            pin_hash    TEXT NOT NULL,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _alert_login_locked(client_ip: str) -> None:
    """Best-effort ping to Bill when an IP gets locked out, same pattern as
    catalystdb_web.py's _alert_login_locked."""
    try:
        import requests
        from jobs.congregation.shepcheck_login_lockout import MAX_FAILED_ATTEMPTS

        token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return
        text = (
            f"⚠️ Shepherding Check-In login locked after {MAX_FAILED_ATTEMPTS} failed PIN "
            f"attempts from {client_ip}. Message me \"unlock login\" to clear it. - Watson"
        )
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


@shepcheck_web_bp.route("/api/cat/shepcheck/verify_pin", methods=["POST"])
@_require_key
def verify_pin():
    from jobs.congregation import shepcheck_login_lockout
    from jobs.congregation.deacon_pin_auth import check_pin

    data = request.get_json(force=True) or {}
    pin = (data.get("pin") or "").strip()
    client_ip = (data.get("client_ip") or "").strip() or "unknown"

    with _conn() as conn:
        _ensure_pins_table(conn)
        if shepcheck_login_lockout.is_locked(conn, client_ip):
            return jsonify({"matches": [], "locked": True}), 200

        matches = []
        if pin:
            rows = conn.execute("SELECT person_name, pin_hash FROM shepcheck_pins").fetchall()
            matches = [row["person_name"] for row in rows if check_pin(pin, row["pin_hash"])]

        if matches:
            shepcheck_login_lockout.record_success(conn, client_ip)
            just_locked = False
        else:
            just_locked = shepcheck_login_lockout.record_failure(conn, client_ip)

    if just_locked:
        _alert_login_locked(client_ip)

    return jsonify({"matches": matches, "locked": just_locked}), 200


@shepcheck_web_bp.route("/api/cat/shepcheck/state", methods=["GET"])
@_require_key
def get_state():
    """One entry per prayer request sent to a deacon in the last 7 days,
    with its contact-log status and, if escalated, the linked follow-up
    entry sent to Bill. Trial phase: this will only ever return Kellianne's
    one test request until prayer_notify.send_notification gets wired into
    a real weekly dispatch job."""
    with _conn() as conn:
        from jobs.telegram.prayer_notify import ensure_schema
        ensure_schema(conn)

        root_rows = conn.execute("""
            SELECT pcl.*, pr.request_text, pr.date AS request_date, m.name AS member_name
            FROM prayer_contact_log pcl
            JOIN prayer_requests pr ON pr.id = pcl.prayer_request_id
            JOIN members m ON m.id = pr.member_id
            WHERE pcl.parent_log_id IS NULL
              AND pcl.sent_at >= datetime('now', '-7 days')
            ORDER BY pcl.sent_at DESC
        """).fetchall()

        def _entry(row):
            return {
                "log_id": row["id"],
                "prayer_request_id": row["prayer_request_id"],
                "member_name": row["member_name"],
                "request_text": row["request_text"],
                "request_date": row["request_date"],
                "deacon_name": row["deacon_name"],
                "status": row["status"],
                "sent_at": row["sent_at"],
                "contacted_at": row["contacted_at"],
                "remind_at": row["remind_at"],
                "snooze_hours": row["snooze_hours"],
                "escalated_at": row["escalated_at"],
                "escalation_note": row["escalation_note"],
            }

        requests_out = []
        for row in root_rows:
            entry = _entry(row)
            escalation = None
            if row["escalated_to_log_id"]:
                esc_row = conn.execute(
                    "SELECT * FROM prayer_contact_log WHERE id = ?", (row["escalated_to_log_id"],)
                ).fetchone()
                if esc_row:
                    escalation = {
                        "log_id": esc_row["id"],
                        "status": esc_row["status"],
                        "sent_at": esc_row["sent_at"],
                        "contacted_at": esc_row["contacted_at"],
                        "remind_at": esc_row["remind_at"],
                        "snooze_hours": esc_row["snooze_hours"],
                    }
            entry["escalation"] = escalation
            requests_out.append(entry)

    return jsonify({"requests": requests_out}), 200
