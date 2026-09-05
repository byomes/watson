"""send_to_person.py -- generic Telegram send to any onboarded person.

Looks up telegram_chat_id on the people table and sends via the bot API.
Any future job can call this for any onboarded person -- no group/channel
sends, no broadcast-all helper, one person per call. A person with no
telegram_chat_id yet (not onboarded via jobs/telegram/seed_claim_codes.py
+ the /start claim flow in bot.py) is never silently dropped and never
falls back to Bill's own chat -- callers must check the return value.
"""
import logging

import requests

from config.settings import TELEGRAM_BOT_TOKEN
from core.database import get_connection

log = logging.getLogger(__name__)


def send_to_person(person_id: int, message: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT name, telegram_chat_id FROM people WHERE id = ?", (person_id,)
        ).fetchone()

    chat_id = row["telegram_chat_id"] if row else None
    if not chat_id:
        log.error(
            "send_to_person: person_id=%s has no telegram_chat_id yet -- not onboarded",
            person_id,
        )
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.error("send_to_person: send failed for person_id=%s: %s", person_id, exc)
        return False

    _log_sent(row["name"], message)
    return True


def _log_sent(recipient: str, message: str) -> None:
    """Best-effort entry in telegram_log for the dashboard's Telegram Log tile."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO telegram_log (direction, message, recipient) VALUES ('out', ?, ?)",
                (message, recipient),
            )
    except Exception as exc:
        log.warning("send_to_person: telegram_log write failed: %s", exc)
