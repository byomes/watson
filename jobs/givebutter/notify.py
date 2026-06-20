#!/usr/bin/env python3
"""
notify.py — Find unthanked Givebutter transactions and send Telegram preview messages.

Sends each preview via direct Telegram Bot API HTTP POST with an inline keyboard
"Approve & Send" button. Approval and email delivery are handled by the
always-running watson-bot.service (handle_thank_callback in bot/bot.py).

Cron: 15 6 * * * PYTHONPATH=/home/billyomes/watson \
  /home/billyomes/watson/venv/bin/python -m jobs.givebutter.notify \
  >> /home/billyomes/watson/logs/givebutter_notify.log 2>&1
"""
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv

from jobs.givebutter.templates import first_gift_email, repeat_gift_email

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "donors.db"
LOG_PATH = BASE_DIR / "logs" / "givebutter_notify.log"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("WATSON_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("WATSON_CHAT_ID", "")

log = logging.getLogger(__name__)


# ── DB ────────────────────────────────────────────────────────────────────────

def _get_unthanked() -> list[dict]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT t.id, t.amount, t.given_at,
               d.name, d.email, d.gift_count
        FROM transactions t
        JOIN donors d ON d.id = t.donor_id
        WHERE t.thanked = 0
          AND d.email IS NOT NULL AND d.email != ''
        ORDER BY t.given_at ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Formatting ────────────────────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _build_preview(row: dict, subject: str, html_body: str) -> str:
    given_at = (row.get("given_at") or "")[:10]
    plain_body = _html_to_text(html_body)
    preview = (
        f"<b>Thank-you needed</b>\n\n"
        f"<b>Donor:</b> {row['name']}\n"
        f"<b>Amount:</b> ${row['amount']:.2f}\n"
        f"<b>Gift #:</b> {row['gift_count']}\n"
        f"<b>Date:</b> {given_at}\n\n"
        f"<b>Subject:</b> {subject}\n\n"
        f"{plain_body}"
    )
    return preview[:4096]


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_preview(text: str, txn_id: int) -> None:
    """POST a preview message with an inline Approve keyboard to the Telegram Bot API."""
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "✅ Approve & Send", "callback_data": f"thank:{txn_id}"},
                        {"text": "✏️ Edit in Kit", "callback_data": f"edit_thank:{txn_id}"},
                    ]
                ]
            },
        },
        timeout=10,
    )
    r.raise_for_status()


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    rows = _get_unthanked()
    if not rows:
        log.info("No unthanked transactions — nothing to do.")
        return

    log.info("Found %d unthanked transaction(s).", len(rows))

    for row in rows:
        txn_id = row["id"]
        gift_count = row["gift_count"] or 1

        if gift_count == 1:
            subject, html_body = first_gift_email(row["name"], row["amount"])
        else:
            subject, html_body = repeat_gift_email(row["name"], row["amount"], gift_count)

        text = _build_preview(row, subject, html_body)
        try:
            _send_preview(text, txn_id)
            log.info("Preview sent: txn %d (%s).", txn_id, row["name"])
        except Exception as exc:
            log.error("Preview failed for txn %d: %s", txn_id, exc)

    log.info("notify.py done — approval handled by watson-bot.service.")


if __name__ == "__main__":
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(),
        ],
    )
    run()
