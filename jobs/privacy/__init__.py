"""jobs/privacy — Privacy Guard: find family members' personal listings on
data-broker sites and remove them, gated by a single Telegram approve/skip
per match. See memory/WATSON_ARCHITECTURE.md and the original build spec for
the full design; this package holds only the shared send_telegram() helper
other jobs/privacy/* modules import.
"""
import os

import requests

from core.vacation import vacation_gate

_BOT_TOKEN = lambda: os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
_CHAT_ID = lambda: os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(text: str, priority: str = "normal", reply_markup: dict | None = None) -> None:
    if vacation_gate(priority, "jobs.privacy", text):
        return
    token, chat_id = _BOT_TOKEN(), _CHAT_ID()
    if not (token and chat_id):
        return
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
        )
    except Exception:
        pass
