import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from briefing.builder import build_telegram_briefing
from config.settings import ARCHIVE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection, init_db

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


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    if not _AUTHORIZED_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set in .env")

    init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("briefing", handle_briefing))
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
