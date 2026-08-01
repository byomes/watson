"""jobs/adelphos/security_monitor.py — Priority 1 New Account Security Monitor
for Adelphos Academy: polls Moodle for newly created accounts and alerts Bill
via Telegram with per-account Delete/Allow buttons. Built ahead of the
broader course-development jobs because Adelphos is under active
fraudulent-signup abuse (2026-07-31). Live as of 2026-08-01 against the
scoped watson_users Moodle service.

Delete is a two-tap confirm flow handled in bot.py / actions.py: the first
tap only asks for confirmation, the second tap fires core_user_delete_users
(true deletion, not suspend — per Bill's 2026-08-01 decision).

Cron (every 5 min — tight interval given active, ongoing abuse):
  */5 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/adelphos/security_monitor.py \
    >> /home/billyomes/watson/logs/adelphos_security_monitor.log 2>&1
"""
import logging

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


def _fetch_all_users() -> list[dict]:
    data = client.call("core_user_get_users", criteria=[{"key": "deleted", "value": "0"}])
    return data.get("users", [])


def run() -> None:
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.error("WATSON_BOT_TOKEN and WATSON_CHAT_ID must be set.")
        return

    create_tables()

    with get_connection() as conn:
        last_seen_id = conn.execute(
            "SELECT COALESCE(MAX(moodle_user_id), 0) FROM adelphos_new_accounts"
        ).fetchone()[0]

        try:
            users = _fetch_all_users()
        except client.MoodleAPIError as exc:
            log.error("core_user_get_users failed — Moodle service is likely missing this function: %s", exc)
            return
        except Exception as exc:
            log.error("Failed to fetch Moodle users: %s", exc)
            return

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

            text = (
                f"🚨 New Adelphos Academy signup\n\n"
                f"Name: {fullname}\n"
                f"Email: {email}\n"
                f"Username: {username}\n"
                f"Signed up: {signup_ts}\n"
            )
            if source_ip:
                text += f"IP: {source_ip}\n"

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
