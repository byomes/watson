import logging
import os
from pathlib import Path

import whisper
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from briefing.builder import build_telegram_briefing
from config.settings import ARCHIVE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection, init_db

log = logging.getLogger(__name__)

_AUTHORIZED_ID = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else None

_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        log.info("Loading Whisper model...")
        _whisper_model = whisper.load_model("base")
        log.info("Whisper ready")
    return _whisper_model


def _save_voice_note(transcript):
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO voice_notes (transcript, status) VALUES (?, 'new')",
            (transcript,),
        )
        return cursor.lastrowid


def _is_authorized(update):
    return update.effective_chat.id == _AUTHORIZED_ID


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return

    voice = update.message.voice
    log.info("Received voice message (duration=%ds, file_id=%s)", voice.duration, voice.file_id)

    await update.message.reply_text("Transcribing...")

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = ARCHIVE_DIR / f"voice_{voice.file_id}.ogg"

    try:
        tg_file = await context.bot.get_file(voice.file_id)
        await tg_file.download_to_drive(str(audio_path))
        log.info("Downloaded to %s", audio_path)

        model = _get_whisper_model()
        result = model.transcribe(str(audio_path))
        transcript = result["text"].strip()
        log.info("Transcribed: %s", transcript[:120])

        note_id = _save_voice_note(transcript)
        log.info("Saved voice note #%d", note_id)

        await update.message.reply_text(f"Got it. Voice note saved.\n\n_{transcript}_",
                                        parse_mode="Markdown")
    finally:
        if audio_path.exists():
            audio_path.unlink()
            log.debug("Deleted temp file %s", audio_path)


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    text = (
        "Watson commands:\n"
        "/briefing — fetch and send today's research briefing\n"
        "/help — show this message\n\n"
        "Send a voice message to transcribe and save it.\n"
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
    note_id = _save_voice_note(text)
    log.info("Saved note #%d", note_id)

    await update.message.reply_text("Got it. Note saved.")


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    if not _AUTHORIZED_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set in .env")

    init_db()
    _get_whisper_model()  # warm up at startup rather than on first message

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
