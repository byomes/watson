import logging
import os
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    MessageHandler, filters, ContextTypes,
)

from briefing.builder import build_telegram_briefing
from config.settings import ARCHIVE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection, init_db
from core.scorer import _BOOST

log = logging.getLogger(__name__)

_AUTHORIZED_ID = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else None


def _save_note(text):
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO voice_notes (transcript, status) VALUES (?, 'new')",
            (text,),
        )
        return cursor.lastrowid


def _is_authorized(update):
    return update.effective_chat.id == _AUTHORIZED_ID


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "Voice transcription is currently disabled. Send a text message to save a note."
    )


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    text = (
        "Watson commands:\n"
        "/briefing — fetch and send today's research briefing\n"
        "/help — show this message\n\n"
        "Send a text message to save it as a note."
    )
    await update.message.reply_text(text)


async def handle_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.reply_text("Fetching briefing...")
    try:
        briefing = build_telegram_briefing()
        await update.message.reply_text(briefing)
    except Exception as exc:
        log.error("Briefing failed: %s", exc)
        await update.message.reply_text(f"Briefing failed: {exc}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return

    text = update.message.text.strip()
    if not text:
        return

    log.info("Received text note: %s", text[:120])
    note_id = _save_note(text)
    log.info("Saved note #%d", note_id)

    await update.message.reply_text("Got it. Note saved.")


_REJECT_REASONS = [
    "Not theology/apologetics",
    "Event/conference announcement",
    "Product/book promotion",
    "Podcast only",
    "Too shallow",
    "Already know this",
    "Wrong audience",
    "Too old",
    "Wrong format",
    "Other",
]


async def _send_reject_keyboard(update: Update, item_id: int) -> None:
    """Look up item_id in briefing_items and reply with the inline rejection keyboard."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title FROM briefing_items WHERE id = ?",
            (item_id,),
        ).fetchone()

    if not row:
        await update.message.reply_text(f"No briefing item with id {item_id}.")
        return

    keyboard = [
        [
            InlineKeyboardButton(r, callback_data=f"reject:{item_id}:{r}")
            for r in _REJECT_REASONS[i:i + 2]
        ]
        for i in range(0, len(_REJECT_REASONS), 2)
    ]

    await update.message.reply_text(
        f"Reject: {row['title'][:80]}\nChoose a reason:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /reject {item_id}")
        return

    await _send_reject_keyboard(update, int(context.args[0]))


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return

    payload = context.args[0] if context.args else ""
    if payload.startswith("reject_") and payload[7:].isdigit():
        await _send_reject_keyboard(update, int(payload[7:]))
    else:
        await update.message.reply_text("Watson is running.")


async def handle_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return

    item_id       = int(parts[1])
    reject_reason = parts[2]

    with get_connection() as conn:
        row = conn.execute(
            "SELECT title, summary, source_name FROM briefing_items WHERE id = ?",
            (item_id,),
        ).fetchone()

        if not row:
            await query.edit_message_text("Item not found.")
            return

        conn.execute(
            "UPDATE briefing_items SET dismissed = 1, reject_reason = ? WHERE id = ?",
            (reject_reason, item_id),
        )

        text     = f"{row['title']} {row['summary'] or ''}"
        keywords = {m.lower() for m in _BOOST.findall(text)}
        for kw in keywords:
            existing = conn.execute(
                "SELECT id FROM rejection_patterns "
                "WHERE source_name = ? AND keyword = ? AND reason = ?",
                (row["source_name"], kw, reject_reason),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE rejection_patterns SET count = count + 1, "
                    "last_seen = datetime('now') WHERE id = ?",
                    (existing["id"],),
                )
            else:
                conn.execute(
                    "INSERT INTO rejection_patterns (source_name, keyword, reason) "
                    "VALUES (?, ?, ?)",
                    (row["source_name"], kw, reject_reason),
                )

    log.info("Rejected item %d (%s): %s", item_id, reject_reason, row["title"][:60])
    await query.edit_message_text(
        f"Rejected: {row['title'][:80]} — {reject_reason}",
        reply_markup=None,
    )


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    if not _AUTHORIZED_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set in .env")

    init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("briefing", handle_briefing))
    app.add_handler(CommandHandler("reject", handle_reject))
    app.add_handler(CallbackQueryHandler(handle_reject_callback, pattern=r"^reject:"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Watson bot listening (chat_id=%d)...", _AUTHORIZED_ID)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    main()
