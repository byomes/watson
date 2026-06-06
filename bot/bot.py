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

import json
import logging
import os
import re
from datetime import date, datetime
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
from jobs.email_job.email_queue import add_to_email_queue, init_email_db
from jobs.email_job.gmail import send_as_watson
from jobs.email_intake import init_gmail_inbox
from jobs.people.api import people_create, people_list, people_get, congregation_search
import jobs.gcal.pending as pending_module
from jobs.gcal import reasoner
from jobs.intent.classifier import classify as _classify_intent

log = logging.getLogger(__name__)

_AUTHORIZED_ID = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else None

# Pending skill proposals keyed by Telegram chat_id
_pending_skills: dict[int, str] = {}

_SKILL_AFFIRM = {"yes", "yes please", "go ahead", "build it", "sure", "do it", "yep", "yeah"}
_SKILL_DENY = {"no", "never mind", "nope", "cancel", "don't", "no thanks"}


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
        "/menu — show interactive menu\n"
        "/briefing — fetch today's research briefing\n"
        "/queue — show pending blog drafts and publish dates\n"
        "/fbqueue — show scheduled Facebook posts\n"
        "/fbcancel &lt;id&gt; — cancel a queued post\n"
        "/emailqueue — show articles queued for weekly email\n"
        "/emailcancel &lt;id&gt; — remove an article from the email queue\n"
        "/saved — show your saved for later list\n"
        "/help — show this message\n\n"
        "Send <b>#blog</b> followed by markdown to queue a blog draft.\n"
        "Drafts publish automatically Tue/Thu/Sat at 10am.\n\n"
        "Watson add book: Title by Author — link\n"
        "Watson list books\n"
        "Watson reading: Title\n"
        "Watson finished: Title\n"
        "Watson remove book: Title\n"
        "Send a photo of a book cover to add it\n"
        "Send an Amazon/Goodreads URL to add it\n\n"
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
            await query.edit_message_text(
                f"✅ <b>Queued for Facebook</b>\n\n{row['title'][:80]}",
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

    text = update.message.text or ""
    if not text.strip():
        return

    if text.startswith("\U0001f4d8 TO FACEBOOK"):
        await _handle_facebook_share(update, text)
        return

    chat_id = update.effective_chat.id

    # Normalize smart quotes from mobile keyboards
    text_clean = text.replace("’", "'").replace("‘", "'")
    text_lower = text_clean.lower().strip()

    # Strip "watson" prefix if present
    for prefix in ("watson,", "watson"):
        if text_lower.startswith(prefix):
            text_lower = text_lower[len(prefix):].strip()
            text_clean = text_clean[len(prefix):].strip()
            break

    # Handle CONFIRM / CANCEL for pending actions (calendar and skill proposals)
    if text_lower in ("yes", "confirm", "yes do it", "book it", "go ahead") or text_lower in _SKILL_AFFIRM:
        if chat_id in _pending_skills:
            pending_desc = _pending_skills.pop(chat_id)
            from jobs.skillbuilder import router as _router
            job_path = _router._generate_job_path(pending_desc)
            import threading
            threading.Thread(
                target=_router._build_in_background,
                args=(pending_desc, job_path, "telegram"),
                daemon=True,
            ).start()
            await update.message.reply_text("Building that skill now. I'll notify you via Telegram when it's ready.")
            return
        p = pending_module.get_pending(chat_id)
        if p:
            await _execute_pending(update, context, p)
            return

    if text_lower in ("no", "cancel", "don't book", "never mind") or text_lower in _SKILL_DENY:
        if chat_id in _pending_skills:
            del _pending_skills[chat_id]
            await update.message.reply_text("Got it. Let me know if you need anything else.")
            return
        p = pending_module.get_pending(chat_id)
        if p:
            pending_module.cancel_pending(p["id"])
            await update.message.reply_text("Got it — cancelled.")
            return

    # Skill routing — before intent classification
    try:
        from jobs.skillbuilder import router as _router
        route_result = _router.route(text_clean, "telegram")
    except Exception as exc:
        log.warning("Skill router failed: %s", exc)
        route_result = {"action": "chat"}

    if route_result["action"] == "skill":
        await update.message.reply_text("✓ " + route_result["result"])
        return

    if route_result["action"] == "build":
        import threading
        threading.Thread(
            target=_router._build_in_background,
            args=(route_result["description"], route_result["job_path"], "telegram"),
            daemon=True,
        ).start()
        await update.message.reply_text("Building that skill now. I'll notify you via Telegram when it's ready.")
        return

    if route_result["action"] == "propose":
        _pending_skills[chat_id] = text_clean
        await update.message.reply_text(route_result["message"])
        return

    # Classify intent via Ollama llama3.2:3b
    result = _classify_intent(text_clean)
    intent = result.get("intent", "general")
    params = result.get("params", {})
    log.info("Intent: %s | Params: %s", intent, params)

    if intent == "calendar_query":
        await _handle_calendar_day(update, context, params)
    elif intent == "calendar_busy":
        await _handle_mark_busy(update, context)
    elif intent == "calendar_availability":
        await _handle_calendar_availability(update, context, params)
    elif intent == "block_time":
        await _handle_block_time(update, context, params)
    elif intent == "book_appointment":
        await _handle_book_appointment(update, context, params)
    elif intent == "task_create":
        await _handle_task_create(update, context, params)
    elif intent == "task_list":
        await _handle_task_list(update, context)
    elif intent == "task_done":
        await _handle_task_done(update, context, params)
    else:
        await _handle_general(update, context, text_clean)


# --- New intent handlers --------------------------------------------------

async def _handle_block_time(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    duration_minutes = int(params.get("duration_minutes") or 60)
    day_str = params.get("day") or "today"
    title = params.get("title") or "Blocked"
    chat_id = update.effective_chat.id
    try:
        slot = reasoner.find_best_slot(day_str, duration_minutes)
        if not slot["available"]:
            await update.message.reply_text(slot["message"])
            return
        pending_module.save_pending(chat_id, "block_time", params, slot)
        await update.message.reply_text(
            f"📅 I found a slot for {title}:\n\n{slot['display']}\n\nReply YES to book it or NO to cancel."
        )
    except Exception as exc:
        log.error("Block time failed: %s", exc)
        await update.message.reply_text(f"Error finding slot: {exc}")


async def _handle_book_appointment(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    name = params.get("name")
    email_addr = params.get("email")
    if not name:
        await update.message.reply_text("Who is this appointment with? Please give me their name.")
        return
    if not email_addr:
        await update.message.reply_text(f"What is {name}'s email address?")
        return
    duration_minutes = int(params.get("duration_minutes") or 60)
    day_str = params.get("day") or "next wednesday"
    chat_id = update.effective_chat.id
    try:
        slot = reasoner.find_best_slot(day_str, duration_minutes)
        if not slot["available"]:
            await update.message.reply_text(slot["message"])
            return
        pending_module.save_pending(chat_id, "book_appointment", params, slot)
        await update.message.reply_text(
            f"📅 I found a slot for {name}:\n\n{slot['display']}\n\nReply YES to book it or NO to cancel."
        )
    except Exception as exc:
        log.error("Book appointment failed: %s", exc)
        await update.message.reply_text(f"Error finding slot: {exc}")


def _ensure_tasks_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            due_datetime TEXT,
            status       TEXT NOT NULL DEFAULT 'active',
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)


async def _handle_task_create(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    title = params.get("title", "")
    due = params.get("due_datetime")
    if not title:
        await update.message.reply_text("What should I call this task?")
        return
    try:
        with get_connection() as conn:
            _ensure_tasks_table(conn)
            conn.execute("INSERT INTO tasks (title, due_datetime) VALUES (?, ?)", (title, due))
        if due:
            await update.message.reply_text(f"✅ Reminder set for {title} on {due}.")
        else:
            await update.message.reply_text(f"✅ Got it — added '{title}' to your tasks.")
    except Exception as exc:
        log.error("Task create failed: %s", exc)
        await update.message.reply_text(f"Error saving task: {exc}")


async def _handle_task_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        with get_connection() as conn:
            _ensure_tasks_table(conn)
            rows = conn.execute(
                "SELECT id, title, due_datetime FROM tasks WHERE status = 'active' ORDER BY id ASC"
            ).fetchall()
        if not rows:
            await update.message.reply_text("No active tasks.")
            return
        lines = ["📋 Your tasks:\n"]
        for r in rows:
            line = f"• {r['title']}"
            if r["due_datetime"]:
                line += f" — {r['due_datetime']}"
            lines.append(line)
        await update.message.reply_text("\n".join(lines))
    except Exception as exc:
        log.error("Task list failed: %s", exc)
        await update.message.reply_text(f"Error loading tasks: {exc}")


async def _handle_task_done(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    title = params.get("title", "")
    if not title:
        await update.message.reply_text("Which task should I mark done?")
        return
    try:
        with get_connection() as conn:
            _ensure_tasks_table(conn)
            conn.execute(
                "UPDATE tasks SET status = 'done' WHERE status = 'active' AND title LIKE ?",
                (f"%{title}%",),
            )
        await update.message.reply_text(f"✅ Marked done: {title}")
    except Exception as exc:
        log.error("Task done failed: %s", exc)
        await update.message.reply_text(f"Error updating task: {exc}")


async def _handle_general(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    try:
        import requests as _req
        resp = _req.post(
            "http://localhost:11434/api/chat",
            json={
                "model": "llama3.2:3b",
                "messages": [{"role": "user", "content": text}],
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        reply = resp.json()["message"]["content"].strip()
        if not reply:
            reply = "I didn't get a response."
    except Exception as exc:
        log.error("Ollama general chat failed: %s", exc)
        reply = "I'm having trouble thinking right now. Try again in a moment."
    await update.message.reply_text(reply)


async def _execute_pending(update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict) -> None:
    action_type = pending["action_type"]
    params = pending["params"]
    slot = pending["proposed_slot"]
    pending_id = pending["id"]

    if action_type not in ("block_time", "book_appointment"):
        await update.message.reply_text("I don't know how to execute that action.")
        return

    title = params.get("title") or (
        f"Meeting with {params.get('name', 'Guest')}"
        if action_type == "book_appointment"
        else "Blocked"
    )
    try:
        start_dt = datetime.fromisoformat(slot["start"])
        end_dt = datetime.fromisoformat(slot["end"])
        display = slot.get("display", "")

        if action_type == "block_time":
            from jobs.gcal.calendar import mark_busy
            mark_busy(start_dt, end_dt, title)
        else:
            from jobs.gcal.calendar import create_event
            create_event(title, start_dt, end_dt, "", params.get("email", ""))

        pending_module.confirm_pending(pending_id)
        await update.message.reply_text(f"✅ Booked — {title} on {display}")

        try:
            send_as_watson(
                "pastorbill@catalyst302.com",
                f"Watson booked: {title}",
                f"Watson has booked the following on your calendar:\n\n{title}\n{display}",
            )
        except Exception as email_exc:
            log.warning("Booking notification email failed: %s", email_exc)

    except Exception as exc:
        log.error("Execute pending failed: %s", exc)
        pending_module.cancel_pending(pending_id)
        await update.message.reply_text(f"Sorry, I couldn't book that — calendar error: {exc}")


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
    elif payload.startswith("share_") and payload[6:].isdigit():
        item_id = int(payload[6:])
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, title, summary, url FROM briefing_items WHERE id=?",
                (item_id,)
            ).fetchone()
        if not row:
            await update.message.reply_text("Article not found.")
            return
        title   = row["title"]
        summary = row["summary"] or ""
        url     = row["url"] or ""
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
    elif payload.startswith("email_") and payload[6:].isdigit():
        item_id = int(payload[6:])
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, title, summary, url FROM briefing_items WHERE id=?",
                (item_id,)
            ).fetchone()
        if not row:
            await update.message.reply_text("Article not found.")
            return
        title   = row["title"]
        summary = row["summary"] or ""
        url     = row["url"] or ""
        with get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO email_queue (title, summary, url, status)
                   VALUES (?, ?, ?, 'queued')""",
                (title, summary, url)
            )
            item_id_db = cursor.lastrowid
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Queue for Email", callback_data=f"email_approve:{item_id_db}"),
                InlineKeyboardButton("🗑 Discard", callback_data=f"email_discard:{item_id_db}"),
            ]
        ])
        await update.message.reply_text(
            f"📧 <b>Queue for weekly email?</b>\n\n<b>{title}</b>\n\n{summary[:200]}\n\n{url}",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    elif payload.startswith("savelater_") and payload[10:].isdigit():
        item_id = int(payload[10:])
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, title, url, source_name, source_type, summary FROM briefing_items WHERE id=?",
                (item_id,)
            ).fetchone()
        if not row:
            await update.message.reply_text("Article not found.")
            return
        with get_connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO reading_list (title, url, source_name, source_type, summary, status)
                   VALUES (?, ?, ?, ?, ?, 'unread')""",
                (row["title"], row["url"], row["source_name"], row["source_type"], row["summary"])
            )
        await update.message.reply_text(
            f"🔖 <b>Saved for later</b>\n\n{row['title'][:80]}",
            parse_mode="HTML",
        )
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



async def handle_email_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    if query.data.startswith("email_approve:"):
        item_id = int(query.data[len("email_approve:"):])
        with get_connection() as conn:
            row = conn.execute(
                "SELECT title FROM email_queue WHERE id=?", (item_id,)
            ).fetchone()
        if not row:
            await query.edit_message_text("Item not found.")
            return
        await query.edit_message_text(
            f"✅ <b>Queued for weekly email</b>\n\n{row['title'][:80]}",
            parse_mode="HTML",
            reply_markup=None,
        )

    if query.data.startswith("email_discard:"):
        item_id = int(query.data[len("email_discard:"):])
        with get_connection() as conn:
            conn.execute("UPDATE email_queue SET status='discarded' WHERE id=?", (item_id,))
        await query.edit_message_text("Discarded.", reply_markup=None)


async def handle_emailqueue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, title, created_at FROM email_queue
               WHERE status = 'queued'
               ORDER BY created_at ASC"""
        ).fetchall()
    if not rows:
        await update.message.reply_text("Email queue is empty.")
        return
    lines = ["<b>Email Queue:</b>\n"]
    for r in rows:
        title = (r["title"] or "Untitled")[:60]
        added = r["created_at"] or ""
        lines.append(f"📧 #{r['id']} — {title}\n📅 {added}")
    lines.append("\nSend /emailcancel &lt;id&gt; to remove an article.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_emailcancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /emailcancel <id>")
        return
    item_id = int(context.args[0])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title FROM email_queue WHERE id=?", (item_id,)
        ).fetchone()
        if not row:
            await update.message.reply_text(f"No item with id {item_id}.")
            return
        conn.execute("UPDATE email_queue SET status='cancelled' WHERE id=?", (item_id,))
    await update.message.reply_text(f"❌ Removed #{item_id}: {row['title'][:60]}")


async def handle_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    raw = " ".join(context.args) if context.args else ""
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3 or not parts[0] or not parts[1] or not parts[2]:
        await update.message.reply_text("Usage: /draft [to] | [subject] | [body]")
        return
    to, subject, body = parts[0], parts[1], parts[2]
    try:
        send_as_watson(to, subject, body)
        await update.message.reply_text(
            f"✉️ Sent to {to}\nSubject: {subject}"
        )
    except Exception as exc:
        log.error("send_as_watson failed: %s", exc)
        await update.message.reply_text(f"Failed to send email: {exc}")


async def handle_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, from_address, subject FROM gmail_inbox
               WHERE status = 'queue'
               ORDER BY received_at DESC"""
        ).fetchall()
    if not rows:
        await update.message.reply_text("Inbox queue is empty.")
        return
    lines = ["<b>Inbox Queue:</b>\n"]
    for r in rows:
        lines.append(f"#{r['id']} — {r['from_address'][:40]}\n{r['subject'][:70]}")
    await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")


async def handle_read(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /read <id>")
        return
    item_id = int(context.args[0])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT from_address, subject, full_body FROM gmail_inbox WHERE id=?",
            (item_id,),
        ).fetchone()
    if not row:
        await update.message.reply_text(f"No email with id {item_id}.")
        return
    text = f"From: {row['from_address']}\nSubject: {row['subject']}\n\n{row['full_body'][:3500]}"
    await update.message.reply_text(text)


async def handle_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, title, url, source_name, status
               FROM reading_list
               WHERE status != 'finished'
               ORDER BY date_added DESC"""
        ).fetchall()
    if not rows:
        await update.message.reply_text("Your saved list is empty.")
        return
    lines = ["<b>Saved for Later:</b>\n"]
    for r in rows:
        source = f" — {r['source_name']}" if r['source_name'] else ""
        title = (r['title'] or 'Untitled')[:60]
        url = r['url'] or ''
        status_icon = "📖" if r['status'] == 'reading' else "🔖"
        if url:
            lines.append(f"{status_icon} <a href='{url}'>{title}</a>{source}\n/savedremove_{r['id']}")
        else:
            lines.append(f"{status_icon} {title}{source}\n/savedremove_{r['id']}")
    await update.message.reply_text(
        "\n\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def handle_savedremove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    text = update.message.text.strip()
    prefix = "/savedremove_"
    if not text.startswith(prefix):
        return
    raw = text[len(prefix):]
    if not raw.isdigit():
        await update.message.reply_text("Invalid id.")
        return
    entry_id = int(raw)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT title FROM reading_list WHERE id=?", (entry_id,)
        ).fetchone()
        if not row:
            await update.message.reply_text("Item not found.")
            return
        conn.execute("DELETE FROM reading_list WHERE id=?", (entry_id,))
    await update.message.reply_text(f"🗑 Removed: {row['title'][:60]}")


async def handle_fbqueue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, title, status, scheduled_time, posted_time
               FROM facebook_queue
               WHERE status IN ('approved', 'posted')
               ORDER BY scheduled_time ASC
               LIMIT 10"""
        ).fetchall()
    if not rows:
        await update.message.reply_text("Facebook queue is empty.")
        return
    lines = ["<b>Facebook Queue:</b>\n"]
    for r in rows:
        status_icon = "✅" if r["status"] == "approved" else "📤"
        sched = r["scheduled_time"] or r["posted_time"] or "unscheduled"
        title = (r["title"] or "Untitled")[:60]
        lines.append(f"{status_icon} #{r['id']} — {title}\n📅 {sched}")
    lines.append("\nSend /fbcancel &lt;id&gt; to remove a post from the queue.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_fbcancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /fbcancel <id>")
        return
    post_id = int(context.args[0])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, status FROM facebook_queue WHERE id=?",
            (post_id,)
        ).fetchone()
        if not row:
            await update.message.reply_text(f"No post with id {post_id}.")
            return
        if row["status"] == "posted":
            await update.message.reply_text("That post has already been published.")
            return
        conn.execute(
            "UPDATE facebook_queue SET status='cancelled' WHERE id=?",
            (post_id,)
        )
    await update.message.reply_text(f"❌ Cancelled post #{post_id}: {row['title'][:60]}")


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📘 Facebook Queue", callback_data="menu_fbqueue"),
            InlineKeyboardButton("📧 Email Queue", callback_data="menu_emailqueue"),
        ],
        [
            InlineKeyboardButton("🔖 Saved for Later", callback_data="menu_saved"),
        ],
        [
            InlineKeyboardButton("📚 Reading List", callback_data="menu_booklist"),
            InlineKeyboardButton("➕ Add Book", callback_data="menu_addbook"),
        ],
        [
            InlineKeyboardButton("🎙 Ask Watson", callback_data="menu_ask"),
            InlineKeyboardButton("📰 Briefing", callback_data="menu_briefing"),
        ],
    ])
    await update.message.reply_text(
        "<b>Watson Menu</b>\n\nWhat would you like to do?",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    if query.data == "menu_fbqueue":
        await query.edit_message_reply_markup(reply_markup=None)
        await handle_fbqueue(update, context)

    elif query.data == "menu_emailqueue":
        await query.edit_message_reply_markup(reply_markup=None)
        await handle_emailqueue(update, context)

    elif query.data == "menu_booklist":
        await query.edit_message_reply_markup(reply_markup=None)
        from jobs.reading_list import list_books
        books = list_books()
        if not books:
            await query.message.reply_text("Your reading list is empty.")
            return
        icons = {"queued": "📋", "reading": "📖", "finished": "✅"}
        lines = ["<b>Reading List:</b>\n"]
        for b in books:
            icon = icons.get(b.get("status", "queued"), "📋")
            line = f"{icon} #{b['id']} <b>{b['title']}</b> — {b['author']}"
            if b.get("link"):
                line += f"\n    <a href='{b['link']}'>Link</a>"
            lines.append(line)
        lines.append("\nTo remove a book: <code>Watson remove book #&lt;id&gt;</code>")
        lines.append("To update status: <code>Watson reading #&lt;id&gt;</code> or <code>Watson finished #&lt;id&gt;</code>")
        await query.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)

    elif query.data == "menu_addbook":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "Send a book using any of these methods:\n\n"
            "• <code>Watson add book: Title by Author</code>\n"
            "• Paste an Amazon or Goodreads URL\n"
            "• Send a photo of the cover (coming soon)",
            parse_mode="HTML"
        )

    elif query.data == "menu_ask":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "Ask me anything about your sermons:\n\n"
            "<code>/ask what did I preach on suffering?</code>",
            parse_mode="HTML"
        )

    elif query.data == "menu_briefing":
        await query.edit_message_reply_markup(reply_markup=None)
        await handle_briefing(update, context)

    elif query.data == "menu_saved":
        await query.edit_message_reply_markup(reply_markup=None)
        await handle_saved(update, context)


async def handle_book_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    if query.data.startswith("book_reading:"):
        book_id = int(query.data[len("book_reading:"):])
        from jobs.reading_list import update_status_by_id
        book = update_status_by_id(book_id, "reading")
        if book:
            await query.edit_message_text(f"📖 Now reading: {book['title']}", reply_markup=None)
        else:
            await query.edit_message_text("Book not found.", reply_markup=None)

    elif query.data.startswith("book_finished:"):
        book_id = int(query.data[len("book_finished:"):])
        from jobs.reading_list import update_status_by_id
        book = update_status_by_id(book_id, "finished")
        if book:
            await query.edit_message_text(f"✅ Finished: {book['title']}", reply_markup=None)
        else:
            await query.edit_message_text("Book not found.", reply_markup=None)

    elif query.data.startswith("book_remove:"):
        book_id = int(query.data[len("book_remove:"):])
        from jobs.reading_list import remove_book_by_id
        book = remove_book_by_id(book_id)
        if book:
            await query.edit_message_text(f"🗑 Removed: {book['title']}", reply_markup=None)
        else:
            await query.edit_message_text("Book not found.", reply_markup=None)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "📸 Book cover recognition is coming soon.\n\nFor now, use:\n<code>Watson add book: Title by Author</code>",
        parse_mode="HTML"
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


# --- Calendar handlers --------------------------------------------------------

async def _handle_calendar_day(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    from zoneinfo import ZoneInfo
    from jobs.gcal.calendar import get_events
    ny = ZoneInfo("America/New_York")
    day_str = (params or {}).get("day") if params else None
    try:
        if day_str and day_str != "today":
            d = reasoner.parse_day(day_str)
        else:
            d = datetime.now(ny).date()
        day_start = datetime(d.year, d.month, d.day, 0, 0, tzinfo=ny)
        day_end = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=ny)
        events = get_events(day_start, day_end)
        if not events:
            label = "Today" if d == datetime.now(ny).date() else d.strftime("%A, %B %-d")
            await update.message.reply_text(f"📅 Nothing on the calendar for {label}.")
            return
        label = "Today" if d == datetime.now(ny).date() else d.strftime("%A, %B %-d")
        lines = [f"📅 {label}'s Schedule\n"]
        for e in events:
            start_str = e.get("start", "")
            if "T" not in start_str:
                time_fmt = "All Day"
            else:
                try:
                    start_dt = datetime.fromisoformat(start_str).astimezone(ny)
                    time_fmt = start_dt.strftime("%-I:%M %p")
                except Exception:
                    time_fmt = start_str
            lines.append(f"{time_fmt} — {e.get('summary', '(no title)')}")
        await update.message.reply_text("\n".join(lines))
    except Exception as exc:
        log.error("Calendar day failed: %s", exc)
        await update.message.reply_text(f"Calendar error: {exc}")


async def _handle_mark_busy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from jobs.gcal.calendar import mark_day_busy_from_now
    try:
        mark_day_busy_from_now()
        await update.message.reply_text("🚫 Done — marked rest of today as busy.")
    except Exception as exc:
        log.error("Mark busy failed: %s", exc)
        await update.message.reply_text(f"Error: {exc}")


async def _handle_calendar_availability(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    from jobs.gcal.availability import get_available_slots_next_30_days
    try:
        all_slots = get_available_slots_next_30_days("virtual")
        lines = ["📆 Next available slots:\n"]
        count = 0
        for date_str, slots in all_slots.items():
            if count >= 5:
                break
            d = datetime.strptime(date_str, "%Y-%m-%d")
            day_label = d.strftime("%A, %b %-d")
            for slot in slots:
                if count >= 5:
                    break
                lines.append(f"{day_label} — {slot['display']}")
                count += 1
        if count == 0:
            await update.message.reply_text("📆 No available slots in the next 30 days.")
        else:
            await update.message.reply_text("\n".join(lines))
    except Exception as exc:
        log.error("Calendar availability failed: %s", exc)
        await update.message.reply_text(f"Error: {exc}")
def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    if not _AUTHORIZED_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set in .env")

    init_db()
    init_fb_db()
    init_email_db()
    init_gmail_inbox()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",    handle_start))
    app.add_handler(CommandHandler("menu",     handle_menu))
    app.add_handler(CommandHandler("help",     handle_help))
    app.add_handler(CommandHandler("briefing", handle_briefing))
    app.add_handler(CommandHandler("reject",   handle_reject))
    app.add_handler(CommandHandler("queue",    handle_queue))
    app.add_handler(CommandHandler("fbqueue",     handle_fbqueue))
    app.add_handler(CommandHandler("fbcancel",    handle_fbcancel))
    app.add_handler(CommandHandler("emailqueue",  handle_emailqueue))
    app.add_handler(CommandHandler("emailcancel", handle_emailcancel))
    app.add_handler(CommandHandler("draft",       handle_draft))
    app.add_handler(CommandHandler("inbox",       handle_inbox))
    app.add_handler(CommandHandler("read",        handle_read))
    app.add_handler(CommandHandler("saved",       handle_saved))
    app.add_handler(CommandHandler("ask",         handle_ask))
    app.add_handler(CallbackQueryHandler(handle_reject_callback, pattern=r"^reject:"))
    app.add_handler(CallbackQueryHandler(handle_facebook_callback, pattern=r"^fb_"))
    app.add_handler(CallbackQueryHandler(handle_email_callback, pattern=r"^email_"))
    app.add_handler(CallbackQueryHandler(handle_book_callback, pattern=r"^book_"))
    app.add_handler(CallbackQueryHandler(handle_menu_callback, pattern=r"^menu_"))
    app.add_handler(MessageHandler(filters.Regex(r"^/savedremove_\d+$"), handle_savedremove))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
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





