"""jobs/adelphos/security_monitor.py — Priority 1 New Account Security Monitor
for Adelphos Academy: polls Moodle for newly created accounts and alerts Bill
via Telegram with per-account Delete/Allow buttons. Built ahead of the
broader course-development jobs because Adelphos is under active
fraudulent-signup abuse (2026-07-31). Live as of 2026-08-01 against the
scoped watson_users Moodle service.

The watermark ("have we alerted on this user id before?") is derived from
MAX(moodle_user_id) in adelphos_new_accounts — there's no separate watermark
table. On a genuinely empty table (first run, or after a table reset), that
derives to 0, which would otherwise make every pre-existing Moodle account
look like a brand-new signup and fire one alert per account (this happened
during 2026-08-01 testing — 38 spurious alerts on real accounts). To avoid
repeating that, a first run seeds one 'pre_existing' row per current account
with no Telegram alert, then real new-signup detection starts on the next run.

Cron (every 5 min — tight interval given active, ongoing abuse):
  */5 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/adelphos/security_monitor.py \
    >> /home/billyomes/watson/logs/adelphos_security_monitor.log 2>&1
"""
import logging
from datetime import datetime, timezone

import requests

from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID
from core.database import get_connection
from core.vacation import vacation_gate
from jobs.adelphos import client
from jobs.adelphos.schema import create_tables

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [adelphos.security_monitor] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def _send(text: str, keyboard: list | None = None) -> int | None:
    # system_failure priority: an active-abuse alert must never be suppressed by vacation mode.
    if vacation_gate("system_failure", "jobs.adelphos.security_monitor", text):
        return None
    payload: dict = {"chat_id": WATSON_CHAT_ID, "text": text}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    resp = requests.post(
        f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["result"]["message_id"]


_SYSTEM_USER_IDS = frozenset({1, 2})  # 1 = guest, 2 = primary site admin


def _fetch_all_users() -> list[dict]:
    """Return the full user roster minus deleted, suspended, and system accounts.

    core_user_get_users requires at least one search criterion but has no
    "match everything" key on Moodle 5.0: recognised keys (email/username/auth/...)
    filter the result, while an UNrecognised key is silently ignored — Moodle attaches
    a per-key warning to the response and returns every non-deleted user. We send a
    deliberately-unrecognised placeholder to pull that full roster (the warning is
    response-only and never logged), then filter client-side here. 'deleted' was the
    old criterion but is not a valid search key — Moodle never returns deleted users
    anyway — and 'suspended' can't be filtered via the API but IS present on every
    returned user, so both (plus the guest/admin system accounts) are dropped below.
    """
    data = client.call("core_user_get_users", criteria=[{"key": "listall", "value": "1"}])
    return [
        u
        for u in data.get("users", [])
        if not u.get("deleted")
        and not u.get("suspended")
        and u.get("id") not in _SYSTEM_USER_IDS
    ]


def run() -> None:
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.error("WATSON_BOT_TOKEN and WATSON_CHAT_ID must be set.")
        return

    create_tables()

    with get_connection() as conn:
        row_count = conn.execute("SELECT COUNT(*) FROM adelphos_new_accounts").fetchone()[0]

        try:
            users = _fetch_all_users()
        except client.MoodleAPIError as exc:
            log.error("core_user_get_users failed — Moodle service is likely missing this function: %s", exc)
            return
        except Exception as exc:
            log.error("Failed to fetch Moodle users: %s", exc)
            return

        if row_count == 0:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            for u in users:
                conn.execute(
                    """INSERT OR IGNORE INTO adelphos_new_accounts
                       (moodle_user_id, fullname, email, username, signup_timestamp,
                        source_ip, status, resolved_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pre_existing', ?)""",
                    (
                        u.get("id", 0),
                        u.get("fullname") or f"{u.get('firstname', '')} {u.get('lastname', '')}".strip(),
                        u.get("email", ""),
                        u.get("username", ""),
                        u.get("timecreated"),
                        u.get("lastip") or "",
                        now,
                    ),
                )
            conn.commit()
            log.info("First run — seeded %d pre-existing account(s) without alerting.", len(users))
            return

        last_seen_id = conn.execute(
            "SELECT COALESCE(MAX(moodle_user_id), 0) FROM adelphos_new_accounts"
        ).fetchone()[0]

        new_users = sorted(
            (u for u in users if u.get("id", 0) > last_seen_id),
            key=lambda u: u["id"],
        )

        if not new_users:
            log.info("No new Adelphos signups since user id %d.", last_seen_id)
            return

        log.info("Found %d new Adelphos signup(s).", len(new_users))

        for u in new_users:
            moodle_id = u["id"]
            fullname = u.get("fullname") or f"{u.get('firstname', '')} {u.get('lastname', '')}".strip()
            email = u.get("email", "")
            username = u.get("username", "")
            signup_ts = u.get("timecreated")
            source_ip = u.get("lastip") or ""

            email_display = email or "(not visible — Moodle privacy setting)"
            ip_display = source_ip or "(not exposed by Moodle API)"

            text = (
                f"🚨 New Adelphos Academy signup\n\n"
                f"Name: {fullname}\n"
                f"Email: {email_display}\n"
                f"Username: {username}\n"
                f"IP: {ip_display}\n"
                f"Signed up: {signup_ts}\n"
            )

            keyboard = [[
                {"text": "🗑 Delete", "callback_data": f"adelphos_delete_{moodle_id}"},
                {"text": "✅ Allow to stay", "callback_data": f"adelphos_allow_{moodle_id}"},
            ]]

            try:
                message_id = _send(text, keyboard)
            except Exception as exc:
                log.error("Failed to send Telegram alert for moodle_user_id=%d: %s", moodle_id, exc)
                message_id = None

            conn.execute(
                """INSERT OR IGNORE INTO adelphos_new_accounts
                   (moodle_user_id, fullname, email, username, signup_timestamp,
                    source_ip, status, telegram_message_id)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (moodle_id, fullname, email, username, signup_ts, source_ip, message_id),
            )
        conn.commit()


if __name__ == "__main__":
    run()
