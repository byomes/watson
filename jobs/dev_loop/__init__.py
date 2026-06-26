"""Watson Dev Loop — autonomous code generation via FMSPC + Ollama."""
import os
import sqlite3

DB = os.path.expanduser("~/watson/data/watson.db")


def _send_telegram(text: str) -> None:
    import requests as _rq
    token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        _rq.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


def _send_telegram_buttons(text: str, buttons: list) -> None:
    """Send Telegram message with inline keyboard buttons."""
    import requests as _rq
    token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    keyboard = {"inline_keyboard": [[{"text": b["label"], "callback_data": b["data"]} for b in buttons]]}
    try:
        _rq.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": keyboard,
            },
            timeout=10,
        )
    except Exception:
        pass
