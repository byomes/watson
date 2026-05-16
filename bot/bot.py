"""
bot.py — Watson Telegram bot.

Commands:
  /briefing  — fetch and send today's research briefing
  /help      — show this message
  /start     — confirm Watson is running
  /queue     — show pending blog drafts and their scheduled dates

Message handling:
  #blog <markdown> — save blog draft to queue; scheduler publishes Tue/Thu/Sat 10am
  📘 TO FACEBOOK   — sent by briefing button; Watson drafts post, asks for approval
  anything else    — save as a voice note
"""

import logging
import os
from datetime import date
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    MessageHandler, filters, ContextTypes,
)

from briefing.builder import build_telegram_briefing
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection, init_db
from core.scorer import _BOOST
from jobs.ask import ask
from jobs.facebook.facebook_post import add_to_queue, init_db as init_fb_db

log = logging.getLogger(__name__)

_AUTHORIZED_ID = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else None


# --- DB helpers -------------------------------------------------------

def _save_note(text):
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO voice_notes (transcript, status) VALUES (?, 'new')",
            (text,),
        )
        return cursor.lastrowid


def _save_blog_draft(title: str, slug: str, body: str) -> int:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blog_drafts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                title          TEXT NOT NULL,
                slug           TEXT NOT NULL,
                body           TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'pending',
                scheduled_date TEXT,
                published_at   TEXT,
                created_at     TEXT NOT NULL DEFAULT (date('now'))
            )
        """)
        cursor = conn.execute(
            """INSERT INTO blog_drafts (title, slug, body, status)
               VALUES (?, ?, ?, 'pending')""",
            (title, slug, body),
        )
        return cursor.lastrowid


def _get_draft_queue() -> list:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blog_drafts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                title          TEXT NOT NULL,
                slug           TEXT NOT NULL,
                body           TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'pending',
                scheduled_date TEXT,
                published_at   TEXT,
                created_at     TEXT NOT NULL DEFAULT (date('now'))
            )
        """)
        return conn.execute(
            """SELECT id, title, scheduled_date, status
               FROM blog_drafts
               WHERE status = 'pending'
               ORDER BY id ASC"""
        ).fetchall()


def _is_authorized(update):
    return update.effective_chat.id == _AUTHORIZED_ID


# --- Blog draft handler -----------------------------------------------

async def _handle_blog_draft(update: Update, text: str) -> None:
    """Save #blog message to DB queue. Scheduler publishes Tue/Thu/Sat at 10am."""
    await update.message.reply_text("📝 Saving blog draft...")

    today = date.today().strftime("%Y-%m-%d")
    lines = text.strip().splitlines()

    if lines and lines[0].startswith("#"):
        title = lines[0].lstrip("#").strip()
        body  = "\n".join(lines[1:]).strip()
    else:
        title = f"Blog Draft {today}"
        body  = text.strip()

    # Build slug
    slug = title.lower()
    for ch in " !?:,;'\"":
        slug = slug.replace(ch, "-")
    slug = "-".join(p for p in slug.split("-") if p)

    draft_id = _save_blog_draft(title, slug, body)
    log.info("Blog draft saved to DB: #%d — %s", draft_id, title)

    await update.message.reply_text(
        f"✅ <b>Draft queued</b>\n\n"
        f"<b>{title}</b>\n\n"
        f"Scheduled for next available Tue/Thu/Sat at 10am.\n"
        f"Send /queue to see the publish schedule.",
        parse_mode="HTML",
    )


# --- Bot handlers -----------------------------------------------------

async def handle_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return

    drafts = _get_draft_queue()
    if not drafts:
        await update.message.reply_text("No drafts in queue.")
        return

    lines = ["<b>Draft queue:</b>\n"]
    for d in drafts:
        sched = d["scheduled_date"] or "unscheduled"
        lines.append(f"#{d['id']} — {d['title'][:50]}\n    📅 {sched}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


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
        "/briefing — fetch today's research briefing\n"
        "/queue — show pending blog drafts and publish dates\n"
        "/help — show this message\n\n"
        "Send <b>#blog</b> followed by markdown to queue a blog draft.\n"
        "Drafts publish automatically Tue/Thu/Sat at 10am.\n\n"
        "Send any other text to save as a note."
    )
    await update.message.reply_text(text, parse_mode="HTML")


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


# --- Facebook queue handler -------------------------------------------

def _parse_facebook_message(text: str) -> dict:
    """Parse the 📘 TO FACEBOOK message from the briefing button."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    # Remove the tag line
    lines = [l for l in lines if l != "📘 TO FACEBOOK"]
    title   = lines[0] if len(lines) > 0 else ""
    summary = lines[1] if len(lines) > 1 else ""
    url     = lines[2] if len(lines) > 2 else ""
    return {"title": title, "summary": summary, "url": url}


async def _handle_facebook_share(update: Update, text: str) -> None:
    """Draft a Facebook post and ask Bill to approve or edit."""
    parsed = _parse_facebook_message(text)
    title   = parsed["title"]
    summary = parsed["summary"]
    url     = parsed["url"]

    draft = f"{title}\n\n{summary}\n\n{url}"

    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO facebook_queue (title, summary, url, draft_text, status)
               VALUES (?, ?, ?, ?, 'draft')""",
            (title, summary, url, draft)
        )
        draft_id = cursor.lastrowid

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Queue it", callback_data=f"fb_approve:{draft_id}"),
            InlineKeyboardButton("🗑 Discard", callback_data=f"fb_discard:{draft_id}"),
        ]
    ])

    await update.message.reply_text(
        f"📘 <b>Facebook draft:</b>\n\n{draft}\n\n"
        f"<i>Will post Mon/Wed/Fri/Sat at 9am</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def handle_facebook_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    if query.data == "fb_edit":
        await query.edit_message_text(
            "✏️ Reply with your edited post text and I'll queue it.\n\nStart your message with <code>#fb</code> to queue it directly.",
            parse_mode="HTML",
            reply_markup=None,
        )
        return

    if query.data.startswith("fb_approve:"):
        draft_id = int(query.data[len("fb_approve:"):])
        with get_connection() as conn:
            row = conn.execute(
                "SELECT title, summary, url, draft_text FROM facebook_queue WHERE id=?",
                (draft_id,)
            ).fetchone()
        if not row:
            await query.edit_message_text("Draft not found.")
            return
        result = add_to_queue(row["title"], row["summary"], row["url"], row["draft_text"])
        if result:
            post_id, slot = result
            slot_str = slot.strftime("%A, %b %-d at %-I:%M %p")
            await query.edit_message_text(
                f"✅ <b>Queued for Facebook</b>\n\n"
                f"{row['draft_text']}\n\n"
                f"📅 Scheduled: {slot_str}",
                parse_mode="HTML",
                reply_markup=None,
            )
        else:
            await query.edit_message_text("No available slots in the next 4 weeks.")

    if query.data.startswith("fb_discard:"):
        draft_id = int(query.data[len("fb_discard:"):])
        with get_connection() as conn:
            conn.execute("UPDATE facebook_queue SET status='discarded' WHERE id=?", (draft_id,))
        await query.edit_message_text("Discarded.", reply_markup=None)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return

    text = update.message.text.strip()
    if not text:
        return

    if text.lower().startswith("#blog"):
        draft_text = text[5:].strip()
        if not draft_text:
            await update.message.reply_text(
                "Send your markdown after #blog:\n\n"
                "<code>#blog\n# Title\n\nBody text...</code>",
                parse_mode="HTML",
            )
            return
        await _handle_blog_draft(update, draft_text)
        return

    if text.startswith("📘 TO FACEBOOK"):
        await _handle_facebook_share(update, text)
        return

    if text.lower().startswith("#fb"):
        draft_text = text[3:].strip()
        if not draft_text:
            await update.message.reply_text("Send your post text after #fb.")
            return
        result = add_to_queue("", "", "", draft_text)
        if result:
            post_id, slot = result
            slot_str = slot.strftime("%A, %b %-d at %-I:%M %p")
            await update.message.reply_text(
                f"✅ <b>Queued for Facebook</b>\n\n{draft_text}\n\n📅 {slot_str}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("No available slots in the next 4 weeks.")
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



async def handle_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ask what did I preach on suffering?")
        return
    question = " ".join(context.args)
    await update.message.reply_text("Searching your sermons...")
    try:
        answer = ask(question)
        await update.message.reply_text(answer)
    except Exception as exc:
        log.error("Ask failed: %s", exc)
        await update.message.reply_text(f"Ask failed: {exc}")
def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    if not _AUTHORIZED_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set in .env")

    init_db()
    init_fb_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",    handle_start))
    app.add_handler(CommandHandler("help",     handle_help))
    app.add_handler(CommandHandler("briefing", handle_briefing))
    app.add_handler(CommandHandler("reject",   handle_reject))
    app.add_handler(CommandHandler("queue",    handle_queue))
    app.add_handler(CommandHandler("ask",      handle_ask))
    app.add_handler(CallbackQueryHandler(handle_reject_callback, pattern=r"^reject:"))
    app.add_handler(CallbackQueryHandler(handle_facebook_callback, pattern=r"^fb_"))
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





