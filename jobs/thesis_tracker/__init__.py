"""jobs/thesis_tracker — Digital Commons / bepress integration (thesis tracker)."""
import os
import sqlite3
from pathlib import Path

import requests

from core.vacation import vacation_gate

DB = Path.home() / "watson" / "data" / "watson.db"

_BOT_TOKEN = lambda: os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
_CHAT_ID   = lambda: os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def send_telegram(text: str) -> None:
    # Snapshot summaries and failure alerts from scrape.py — tagged system_failure,
    # same category as gcal/facebook token health checks, so it always sends.
    if vacation_gate("system_failure", "jobs.thesis_tracker", text):
        return
    token = _BOT_TOKEN()
    chat_id = _CHAT_ID()
    if not (token and chat_id):
        return
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )
