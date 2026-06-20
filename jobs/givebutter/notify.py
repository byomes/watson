#!/usr/bin/env python3
"""
notify.py — Preview unthanked Givebutter transactions in Telegram and send thank-you emails.

Finds all transactions where thanked=0, drafts a thank-you email for each,
sends a Telegram preview with an Approve button. When Bill approves, sends
the email via Kit API and marks the transaction thanked=1.

Usage:
    PYTHONPATH=/home/billyomes/watson \
    /home/billyomes/watson/venv/bin/python -m jobs.givebutter.notify

Note: stop watson-bot.service before running (both use the same bot token).
"""
import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from jobs.givebutter.templates import first_gift_email, repeat_gift_email

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "donors.db"
LOG_PATH = BASE_DIR / "logs" / "givebutter_notify.log"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("WATSON_BOT_TOKEN", "")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID") or os.getenv("WATSON_CHAT_ID") or 0)
KIT_API_KEY = os.getenv("KIT_API_KEY", "")
KIT_API_SECRET = os.getenv("KIT_API_SECRET", "")
KIT_SENDER_EMAIL = os.getenv("KIT_SENDER_EMAIL", "")
KIT_SENDER_NAME = os.getenv("KIT_SENDER_NAME", "")

APPROVAL_TIMEOUT = 600  # seconds to wait for approvals

log = logging.getLogger(__name__)

# Keyed by str(transaction.id); values hold email payload for callback handler
_pending: dict[str, dict] = {}


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


def _mark_thanked(txn_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE transactions SET thanked=1 WHERE id=?", (txn_id,))
    conn.commit()
    conn.close()


# ── Kit email ─────────────────────────────────────────────────────────────────

def _get_kit_subscriber_id(email: str) -> int | None:
    """Look up a Kit subscriber by email address. Returns subscriber_id or None."""
    r = requests.get(
        "https://api.convertkit.com/v3/subscribers",
        params={"api_secret": KIT_API_SECRET, "email_address": email},
        timeout=10,
    )
    r.raise_for_status()
    subscribers = r.json().get("subscribers", [])
    return subscribers[0]["id"] if subscribers else None


def _send_kit_email(to_email: str, subject: str, html_body: str) -> None:
    """Send a targeted email via Kit v4 broadcast API to a single subscriber."""
    subscriber_id = _get_kit_subscriber_id(to_email)
    if subscriber_id is None:
        raise ValueError(f"Subscriber not found in Kit: {to_email}")

    r = requests.post(
        "https://api.kit.com/v4/broadcasts",
        headers={
            "Authorization": f"Bearer {KIT_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "broadcast": {
                "subject": subject,
                "content": html_body,
                "from_name": KIT_SENDER_NAME,
                "email_address": KIT_SENDER_EMAIL,
                "subscriber_filter": [
                    {"all": [{"type": "subscriber_id", "ids": [subscriber_id]}]}
                ],
                "send_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "public": False,
            }
        },
        timeout=15,
    )
    r.raise_for_status()
    log.info("Kit email sent to %s — %s", to_email, subject)


# ── Telegram helpers ──────────────────────────────────────────────────────────

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


# ── Callback handler ──────────────────────────────────────────────────────────

async def _on_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    txn_id_str = query.data.split(":", 1)[1]
    payload = _pending.get(txn_id_str)

    if not payload:
        await query.edit_message_text("Already processed.")
        return

    name = payload["name"]
    to_email = payload["email"]
    subject = payload["subject"]
    html_body = payload["html_body"]

    await query.edit_message_text(f"Sending thank-you to {name}…")

    try:
        await asyncio.to_thread(_send_kit_email, to_email, subject, html_body)
        _mark_thanked(int(txn_id_str))
        del _pending[txn_id_str]
        log.info("Thank-you sent and marked: txn %s → %s", txn_id_str, to_email)
        await query.edit_message_text(f"✅ Thank-you sent to {name} ({to_email}).")
    except Exception as exc:
        log.error("Failed to send thank-you for txn %s: %s", txn_id_str, exc)
        await query.edit_message_text(f"❌ Send failed for {name}: {exc}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    rows = _get_unthanked()
    if not rows:
        log.info("No unthanked transactions — nothing to do.")
        return

    log.info("Found %d unthanked transaction(s).", len(rows))

    # Build pending dict and prepare previews
    previews: list[tuple[dict, str, str, str]] = []
    for row in rows:
        txn_id = str(row["id"])
        gift_count = row["gift_count"] or 1
        if gift_count == 1:
            subject, html_body = first_gift_email(row["name"], row["amount"])
        else:
            subject, html_body = repeat_gift_email(row["name"], row["amount"], gift_count)

        _pending[txn_id] = {
            "name": row["name"],
            "email": row["email"],
            "subject": subject,
            "html_body": html_body,
        }
        previews.append((row, txn_id, subject, html_body))

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(_on_approve, pattern=r"^thank:\d+$"))

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        for row, txn_id, subject, html_body in previews:
            text = _build_preview(row, subject, html_body)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve & Send", callback_data=f"thank:{txn_id}")]
            ])
            await app.bot.send_message(
                chat_id=CHAT_ID,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            log.info("Preview sent for txn %s (%s).", txn_id, row["name"])

        # Wait for approvals up to APPROVAL_TIMEOUT seconds
        elapsed = 0
        while _pending and elapsed < APPROVAL_TIMEOUT:
            await asyncio.sleep(2)
            elapsed += 2

        if _pending:
            remaining = list(_pending.keys())
            log.info("Timeout reached — %d transaction(s) still pending: %s", len(remaining), remaining)

        await app.updater.stop()
        await app.stop()

    log.info("notify.py finished.")


if __name__ == "__main__":
    Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(),
        ],
    )
    asyncio.run(main())
