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

import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    MessageHandler, filters, ContextTypes,
)

from briefing.builder import build_telegram_briefing
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WATSON_SYSTEM
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
from jobs.givebutter.templates import first_gift_email, repeat_gift_email

log = logging.getLogger(__name__)

_AUTHORIZED_ID = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else None

_DONORS_DB = Path(__file__).resolve().parents[1] / "data" / "donors.db"
_KIT_API_KEY = os.getenv("KIT_API_KEY", "")
_KIT_API_SECRET = os.getenv("KIT_API_SECRET", "")
_KIT_SENDER_EMAIL = os.getenv("KIT_SENDER_EMAIL", "")
_KIT_SENDER_NAME = os.getenv("KIT_SENDER_NAME", "")

# Pending skill proposals keyed by Telegram chat_id
_pending_skills: dict[int, str] = {}

# LOW-confidence intent results awaiting user confirmation
_pending_intents: dict[int, dict] = {}

# Pending capability gap proposals (from audit) — resolved via DB

# Per-chat message counter for background reflection
_msg_counter: dict[int, int] = {}


def _maybe_reflect(chat_id: int) -> None:
    _msg_counter[chat_id] = _msg_counter.get(chat_id, 0) + 1
    if _msg_counter[chat_id] % 10 == 0:
        import threading
        from jobs.memory.reflect import reflect
        session_id = f"telegram_{chat_id}"
        threading.Thread(target=reflect, args=(session_id, None), daemon=True).start()

_SKILL_AFFIRM = {"yes", "yes please", "go ahead", "build it", "sure", "do it", "yep", "yeah"}
_SKILL_DENY = {"no", "never mind", "nope", "cancel", "don't", "no thanks"}


def _get_next_proposed_gap() -> dict | None:
    """Return the oldest proposed capability gap from the DB, or None."""
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS capability_gaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gap_name TEXT NOT NULL,
                    reason TEXT,
                    job_path TEXT,
                    description TEXT,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            row = conn.execute(
                "SELECT * FROM capability_gaps WHERE status='proposed' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _set_gap_status(gap_id: int, status: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE capability_gaps SET status=? WHERE id=?", (status, gap_id)
            )
    except Exception as exc:
        log.warning("Gap status update failed: %s", exc)


# --- Givebutter thank-you helpers ------------------------------------

def _gb_get_txn(txn_id: int) -> dict | None:
    if not _DONORS_DB.exists():
        return None
    conn = sqlite3.connect(str(_DONORS_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT t.id, t.amount, t.thanked,
               d.name, d.email, d.gift_count
        FROM transactions t
        JOIN donors d ON d.id = t.donor_id
        WHERE t.id = ?
    """, (txn_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _gb_mark_thanked(txn_id: int) -> None:
    conn = sqlite3.connect(str(_DONORS_DB))
    conn.execute("UPDATE transactions SET thanked=1 WHERE id=?", (txn_id,))
    conn.commit()
    conn.close()


def _gb_mark_thanked_2(txn_id: int) -> None:
    conn = sqlite3.connect(str(_DONORS_DB))
    conn.execute("UPDATE transactions SET thanked=2 WHERE id=?", (txn_id,))
    conn.commit()
    conn.close()


def _gb_add_kit_reminder(donor_name: str) -> None:
    from datetime import date
    title = f"Edit and send thank-you email to {donor_name} in Kit"
    today = date.today().isoformat()
    with get_connection() as conn:
        _ensure_reminders_table(conn)
        conn.execute(
            "INSERT INTO reminders (title, due_datetime, reminder_time, status, created_at, updated_at) "
            "VALUES (?, ?, NULL, 'active', datetime('now'), datetime('now'))",
            (title, today),
        )


def _gb_get_kit_subscriber_id(email: str) -> int | None:
    import requests as _req
    r = _req.get(
        "https://api.convertkit.com/v3/subscribers",
        params={"api_secret": _KIT_API_SECRET, "email_address": email},
        timeout=10,
    )
    r.raise_for_status()
    subscribers = r.json().get("subscribers", [])
    return subscribers[0]["id"] if subscribers else None


def _gb_send_kit_email(to_email: str, subject: str, html_body: str) -> None:
    import requests as _req
    subscriber_id = _gb_get_kit_subscriber_id(to_email)
    if subscriber_id is None:
        raise ValueError(f"Subscriber not found in Kit: {to_email}")
    r = _req.post(
        "https://api.kit.com/v4/broadcasts",
        headers={
            "Authorization": f"Bearer {_KIT_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "broadcast": {
                "subject": subject,
                "content": html_body,
                "from_name": _KIT_SENDER_NAME,
                "email_address": _KIT_SENDER_EMAIL,
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


def _gb_create_kit_draft(to_email: str, subject: str, html_body: str) -> None:
    import requests as _req
    r = _req.post(
        "https://api.convertkit.com/v3/broadcasts",
        json={
            "api_secret": _KIT_API_SECRET,
            "subject": subject,
            "content": html_body,
            "from_name": _KIT_SENDER_NAME,
            "from_email": _KIT_SENDER_EMAIL,
            "description": f"Thank-you draft for {to_email}",
            "public": False,
        },
        timeout=15,
    )
    r.raise_for_status()


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


def _ensure_chat_tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_sessions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title      TEXT    NOT NULL DEFAULT 'New Chat',
        created_at TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        role       TEXT    NOT NULL,
        content    TEXT    NOT NULL,
        source     TEXT,
        created_at TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
    )""")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
    if "source" not in cols:
        conn.execute("ALTER TABLE chat_messages ADD COLUMN source TEXT")


def _get_or_create_telegram_session() -> int:
    today = date.today().strftime("%Y-%m-%d")
    title = f"Telegram - {today}"
    with get_connection() as conn:
        _ensure_chat_tables(conn)
        row = conn.execute(
            "SELECT id FROM chat_sessions WHERE title = ?", (title,)
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO chat_sessions (title) VALUES (?)", (title,))
        return cur.lastrowid


def _log_telegram_exchange(user_text: str, reply_text: str) -> None:
    try:
        session_id = _get_or_create_telegram_session()
        with get_connection() as conn:
            _ensure_chat_tables(conn)
            conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, source) VALUES (?, ?, ?, ?)",
                (session_id, "user", user_text, "telegram"),
            )
            conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, source) VALUES (?, ?, ?, ?)",
                (session_id, "assistant", reply_text, "telegram"),
            )
            conn.execute(
                "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
                (session_id,),
            )
    except Exception as exc:
        log.warning("Failed to log telegram exchange: %s", exc)


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

    # Normalize smart quotes from mobile keyboards
    text_clean = text.replace("'", "'").replace("'", "'")
    text_lower = text_clean.lower().strip()

    # Reply-threading: route replies to Watson-sent messages before any other logic
    if update.message.reply_to_message:
        replied_id = update.message.reply_to_message.message_id
        from jobs.telegram.pending import get_pending_by_message_id
        tg_pending = get_pending_by_message_id(replied_id)
        if tg_pending:
            handled = await _route_tg_pending_reply(update, context, text_clean, tg_pending)
            if handled:
                _log_telegram_exchange(text_clean, f"[reply-threaded: {tg_pending['type']}]")
                log.info("DEBUG pre-check: reply-threaded (%s)", tg_pending['type'])
                return

    # Store every incoming message for resend capability
    from jobs.telegram.resend_last import store_message, get_last_message
    if text_lower != 'resend':
        store_message(text_clean)
    else:
        last = get_last_message()
        if last:
            text_clean = last
            text_lower = last.lower()
        else:
            await update.message.reply_text("No previous message to resend.")
            log.info("DEBUG pre-check: resend (no previous message)")
            return

    # Strip "watson" prefix if present
    for prefix in ("watson,", "watson"):
        if text_lower.startswith(prefix):
            text_lower = text_lower[len(prefix):].strip()
            text_clean = text_clean[len(prefix):].strip()
            break

    # Watson build: request — route to Gemini coder
    if text_lower.startswith("build:") or text_lower.startswith("watson build:"):
        from jobs.dev.gemini_coder import request_build
        description = re.sub(r'^(?:watson\s+)?build:\s*', '', text_clean, flags=re.IGNORECASE).strip()
        await update.message.reply_text("Sending to Gemini...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, request_build, description)
        log.info("DEBUG pre-check: build: command")
        return

    # Watson debug: request — route to Gemini debugger
    if text_lower.startswith("debug:") or text_lower.startswith("watson debug:"):
        from jobs.dev.gemini_coder import request_debug
        description = re.sub(r'^(?:watson\s+)?debug:\s*', '', text_clean, flags=re.IGNORECASE).strip()
        await update.message.reply_text("Sending to Gemini debugger...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, request_debug, description)
        log.info("DEBUG pre-check: debug: command")
        return

    # apply/cancel gemini build
    _apply_match = re.match(r'^apply\s+(\d+)$', text_lower)
    _cancel_match = re.match(r'^cancel\s+(\d+)$', text_lower)
    if _apply_match:
        from jobs.dev.gemini_coder import apply_build
        build_id = int(_apply_match.group(1))
        await update.message.reply_text(f"Applying build {build_id}...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, apply_build, build_id)
        log.info("DEBUG pre-check: apply build")
        return
    if _cancel_match:
        from jobs.dev.gemini_coder import cancel_build
        build_id = int(_cancel_match.group(1))
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, cancel_build, build_id)
        log.info("DEBUG pre-check: cancel build")
        return

    # Build pipeline — natural language trigger "build <request>" (no colon)
    if text_lower.startswith("build ") and not text_lower.startswith("build:"):
        from jobs.dev import build_pipeline as _bp
        import threading
        _build_req = text_clean[6:].strip()
        threading.Thread(
            target=_bp.run,
            args=(_build_req, update.effective_chat.id),
            daemon=True,
        ).start()
        await update.message.reply_text("🔨 Build pipeline started...")
        log.info("DEBUG pre-check: build pipeline (no colon)")
        return

    if text.startswith("\U0001f4d8 TO FACEBOOK"):
        await _handle_facebook_share(update, text)
        log.info("DEBUG pre-check: facebook share")
        return

    chat_id = update.effective_chat.id

    # Pastoral notes reply handling
    from jobs.pastoral_notes.db import get_db
    _notes_db = get_db()
    _pending_note = _notes_db.execute(
        "SELECT * FROM notes_pending WHERE status='pending' ORDER BY prompted_at DESC LIMIT 1"
    ).fetchone()
    if _pending_note:
        from jobs.pastoral_notes.handler import handle_notes_reply
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, handle_notes_reply, text_clean)
        log.info("DEBUG pre-check: pastoral notes reply")
        return

    # Email reply approval — "send" / "change: [text]" / "cancel"
    if text_lower.strip() == "go":
        from jobs.email_reply.handler import resolve_send
        result = resolve_send()
        await update.message.reply_text(result["msg"])
        log.info("DEBUG pre-check: email reply send (go)")
        return

    if text_lower.startswith("change:"):
        changed_text = text_clean[text_clean.lower().index("change:") + len("change:"):].strip()
        if changed_text:
            from jobs.email_reply.handler import resolve_change
            result = resolve_change(changed_text)
            await update.message.reply_text(result["msg"])
            log.info("DEBUG pre-check: email reply change")
            return

    # Build pipeline approval
    if text_lower == "approve":
        from jobs.dev import build_pipeline as _bp
        if _bp.has_pending_approval(chat_id):
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _bp.handle_approval, chat_id, text_clean)
            log.info("DEBUG pre-check: build pipeline approval")
            return

    # Handle CONFIRM / CANCEL for pending actions (calendar, skill proposals, capability gaps)
    if text_lower in ("yes", "confirm", "yes do it", "book it", "go ahead") or text_lower in _SKILL_AFFIRM:
        if chat_id in _pending_intents:
            _pi = _pending_intents.pop(chat_id)
            await _dispatch_intent(update, context, _pi["result"], _pi["text_clean"])
            log.info("DEBUG pre-check: confirmed pending intent")
            return
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
            log.info("DEBUG pre-check: confirmed pending skill build")
            return
        gap = _get_next_proposed_gap()
        if gap:
            _set_gap_status(gap["id"], "approved")
            from jobs.skillbuilder import router as _router
            import threading
            description = gap.get("description") or gap["gap_name"]
            job_path = gap.get("job_path", "jobs/misc/new_skill.py")
            threading.Thread(
                target=_router._build_in_background,
                args=(description, job_path, "telegram"),
                daemon=True,
            ).start()
            await update.message.reply_text(
                f"Building {gap['gap_name']} now. I'll notify you via Telegram when it's ready."
            )
            log.info("DEBUG pre-check: confirmed capability gap build")
            return
        p = pending_module.get_pending(chat_id)
        if p:
            await _execute_pending(update, context, p)
            log.info("DEBUG pre-check: confirmed pending calendar action")
            return

    if text_lower in ("no", "cancel", "don't book", "never mind") or text_lower in _SKILL_DENY:
        if text_lower == "cancel":
            from jobs.email_reply.handler import resolve_cancel
            _er = resolve_cancel()
            if _er["ok"]:
                await update.message.reply_text(_er["msg"])
                log.info("DEBUG pre-check: cancel email reply")
                return
        if chat_id in _pending_skills:
            del _pending_skills[chat_id]
            await update.message.reply_text("Got it. Let me know if you need anything else.")
            log.info("DEBUG pre-check: cancelled pending skill")
            return
        gap = _get_next_proposed_gap()
        if gap:
            _set_gap_status(gap["id"], "rejected")
            await update.message.reply_text("Got it, skipped.")
            log.info("DEBUG pre-check: rejected capability gap")
            return
        p = pending_module.get_pending(chat_id)
        if p:
            pending_module.cancel_pending(p["id"])
            await update.message.reply_text("Got it — cancelled.")
            log.info("DEBUG pre-check: cancelled pending calendar action")
            return


    # Report menu
    if text_lower in ("reports", "report menu"):
        from jobs.connect_cards.report_menu import get_telegram_menu
        menu = get_telegram_menu()
        await update.message.reply_text(menu, parse_mode="Markdown")
        _log_telegram_exchange(text_clean, menu)
        log.info("DEBUG pre-check: report menu")
        return

    _report_match = re.match(r'^report\s+(.+)', text_lower)
    if _report_match:
        from jobs.connect_cards.report_menu import run_report, report_to_telegram
        _rname = text_clean[_report_match.start(1):].strip()
        _result = run_report(_rname)
        if _result is None:
            reply = f"No report found matching '{_rname}'."
        else:
            _, _html = _result
            reply = report_to_telegram(_html)
        await update.message.reply_text(reply, parse_mode="Markdown")
        _log_telegram_exchange(text_clean, reply)
        log.info("DEBUG pre-check: report run")
        return

    # Riddle answer reveal follow-up — only fires when a riddle is pending
    _RIDDLE_ANSWER_TRIGGERS = (
        "what's the answer", "whats the answer", "what is the answer",
        "reveal the answer", "tell me the answer", "give me the answer",
    )
    if any(t in text_lower for t in _RIDDLE_ANSWER_TRIGGERS):
        from jobs.misc.riddle import reveal_answer as _reveal_riddle_answer
        _riddle_ans = _reveal_riddle_answer()
        if _riddle_ans:
            await update.message.reply_text(_riddle_ans, parse_mode="Markdown")
            _log_telegram_exchange(text_clean, _riddle_ans)
            log.info("DEBUG pre-check: riddle answer")
            return
        # No pending riddle — fall through to normal handling.

    # QR code generation
    _QR_TRIGGERS = ('qr code', 'qr-code', 'make a qr', 'give me a qr',
                    'generate a qr', 'create a qr', 'make qr', 'qr for')
    if any(t in text_lower for t in _QR_TRIGGERS):
        import io as _io
        from jobs.qr.qr_generate import generate_qr as _gen_qr
        _qr_patterns = [
            r'(?:make a|give me a|generate a|create a|make|give me)\s+qr\s+(?:code\s+)?(?:for\s+)?(.+)',
            r'qr\s+(?:code\s+)?(?:for\s+)?(.+)',
        ]
        _qr_content = None
        for _pat in _qr_patterns:
            _m = re.search(_pat, text_lower)
            if _m:
                _qr_content = text_clean[_m.start(1):].strip()
                break
        if _qr_content:
            try:
                _filepath, _png = _gen_qr(_qr_content)
                context.user_data['last_qr'] = {'content': _qr_content, 'png_bytes': _png}
                await update.message.reply_photo(
                    photo=_io.BytesIO(_png),
                    caption=f'QR code for: {_qr_content}',
                )
                _log_telegram_exchange(text_clean, f'[QR code generated for: {_qr_content}]')
            except Exception as _exc:
                log.error("QR generation failed: %s", _exc)
                await update.message.reply_text(f'QR generation failed: {_exc}')
        else:
            await update.message.reply_text('What should the QR code contain?')
        log.info("DEBUG pre-check: QR code generation")
        return

    # QR email follow-up: "email this to [name]" / "send this to [name]"
    _email_qr_match = re.search(r'(?:email|send)\s+this\s+(?:qr\s+)?to\s+(.+)', text_lower)
    _last_qr = context.user_data.get('last_qr') if context.user_data else None
    if _email_qr_match and _last_qr:
        _contact_name = _email_qr_match.group(1).strip().rstrip('.')
        from jobs.people.lookup import lookup_member as _lm
        _hits = _lm(_contact_name)
        _contact = next((c for c in _hits if c.get('email')), None)
        if _contact:
            from jobs.qr.qr_generate import send_qr_email as _send_qr_email
            try:
                _send_qr_email(
                    _contact['email'], _contact['name'],
                    _last_qr['content'], bytes(_last_qr['png_bytes']),
                )
                await update.message.reply_text(
                    f"QR code sent to {_contact['name']} ({_contact['email']})."
                )
            except Exception as _exc:
                await update.message.reply_text(f'Failed to send email: {_exc}')
        else:
            await update.message.reply_text(
                f"No contact found for '{_contact_name}'. Check the name and try again."
            )
        log.info("DEBUG pre-check: QR email follow-up")
        return

    # SMS interception
    _sms_triggers = (
        'text ', 'send a text', 'send text', 'shoot a text',
        'shoot them a text', 'shoot her a text', 'shoot him a text',
    )
    if any(t in text_lower for t in _sms_triggers):
        from jobs.sms.sms_send import send_sms_to_contact as _sms_to_contact, send_sms as _sms_direct

        _sms_me = re.search(
            r'(?:text|send a text to)\s+me\s+(?:that\s+|saying\s+)?(.+)',
            text_clean, re.IGNORECASE,
        )
        _sms_contact_m = re.search(
            r'(?:text|send a text to|send text to|shoot a text to)\s+(\w+(?:\s+\w+)?)\s*(?::|that\s+|saying\s+|to say\s+)?\s*(.+)',
            text_clean, re.IGNORECASE,
        )

        if _sms_me:
            _sms_msg = _sms_me.group(1).strip()
            _owner_phone = os.environ.get('WATSON_OWNER_PHONE')
            _owner_carrier = os.environ.get('WATSON_OWNER_CARRIER', 'verizon')
            if _owner_phone:
                _result = _sms_direct('Dr. Bill', _owner_phone, _owner_carrier, _sms_msg)
                reply = f"Text sent to you." if _result['success'] else f"Failed: {_result['error']}"
            else:
                reply = "WATSON_OWNER_PHONE not set in .env."
            await update.message.reply_text(reply)
            _log_telegram_exchange(text_clean, reply)
            log.info("DEBUG pre-check: SMS to self")
            return

        elif _sms_contact_m:
            _sms_name = _sms_contact_m.group(1).strip()
            _sms_msg = _sms_contact_m.group(2).strip()
            from jobs.people.lookup import lookup_member as _lm_sms
            _hits = _lm_sms(_sms_name)
            _contact = next((c for c in _hits if c.get('phone')), None)
            if _contact:
                _result = _sms_to_contact(_contact, _sms_msg)
                reply = f"Text sent to {_contact['name']}." if _result['success'] else f"Failed: {_result['error']}"
            else:
                reply = f"No contact found for '{_sms_name}'."
            await update.message.reply_text(reply)
            _log_telegram_exchange(text_clean, reply)
            log.info("DEBUG pre-check: SMS to contact")
            return

    import re as _re
    if _re.search(r'what.*(time|hour).*is it|what time|current time', text_clean.lower()):
        from jobs.time_check import run as _time_run
        reply = _time_run()
        await update.message.reply_text(reply)
        _log_telegram_exchange(text_clean, reply)
        log.info("DEBUG pre-check: time check")
        return

    # Remind me intake
    _remind_timed_m = _re.match(r'^remind me at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+(.+)', text_lower)
    _remind_plain_m = None if _remind_timed_m else _re.match(r'^remind me\s+(.+)', text_lower)
    if _remind_timed_m or _remind_plain_m:
        from jobs.reminders import parse_reminder_time
        if _remind_timed_m:
            _rt = parse_reminder_time(_remind_timed_m.group(1))
            _title = text_clean[_remind_timed_m.start(2):].strip() if _rt else text_clean[len("remind me at "):].strip()
        else:
            _rt = None
            _title = text_clean[_remind_plain_m.start(1):].strip()
        if _title:
            with get_connection() as conn:
                _ensure_reminders_table(conn)
                conn.execute(
                    "INSERT INTO reminders (title, due_datetime, reminder_time, status, created_at, updated_at) "
                    "VALUES (?, datetime('now'), ?, 'active', datetime('now'), datetime('now'))",
                    (_title, _rt),
                )
            reply = f"⏰ Reminder set for {_rt}: {_title}" if _rt else f"⏰ Reminder saved: {_title}"
            await update.message.reply_text(reply)
            _log_telegram_exchange(text_clean, reply)
            log.info("DEBUG pre-check: remind me intake")
            return

    from jobs.skillbuilder import router as _router

    # 1. Factual queries → web search
    from jobs.skillbuilder.router import _SKILL_PRE_CHECKS
    msg_lower_check = text_clean.lower().strip()
    for slug, triggers in _SKILL_PRE_CHECKS.items():
        if any(trigger in msg_lower_check for trigger in triggers):
            if slug == "image_search":
                await _handle_image_search(update, context, {"query": text_clean})
                log.info("DEBUG pre-check: skill pre-check (image_search)")
                return
            try:
                _skills = _router._load_skills("telegram")
                _skill = next((s for s in _skills if s["slug"] == slug), None)
                if _skill:
                    result = _router._run_skill(_skill, message=text_clean)
                else:
                    result = f"Skill '{slug}' not available."
            except Exception as exc:
                log.error("Pre-check skill dispatch failed (%s): %s", slug, exc)
                result = f"Skill error: {exc}"
            await update.message.reply_text("✓ " + str(result))
            log.info("DEBUG pre-check: skill pre-check (%s)", slug)
            return

    if getattr(_router, '_is_factual_query', None) and _router._is_factual_query(text_clean):
        from jobs.research.web_search import run as web_search_run
        ws_result = web_search_run(text_clean)
        reply = "✓ " + ws_result
        await update.message.reply_text(reply)
        _log_telegram_exchange(text_clean, reply)
        _maybe_reflect(chat_id)
        log.info("DEBUG pre-check: factual query → web search")
        return

    # 2. Skill routing — explicit triggers only (skill/build/propose/wrap_up)
    # action:"chat" falls through to the intent classifier below
    try:
        route_result = _router.route(text_clean, "telegram")
    except Exception as exc:
        log.warning("Skill router failed: %s", exc)
        route_result = {"action": "chat"}

    if route_result["action"] == "skill":
        skill_result = route_result.get("result")
        if skill_result is None:
            try:
                _skills = _router._load_skills("telegram")
                _skill = next((s for s in _skills if s["slug"] == route_result["slug"]), None)
                if _skill:
                    skill_result = _router._run_skill(_skill, message=route_result.get("message", text_clean))
                else:
                    skill_result = f"Skill '{route_result['slug']}' not available."
            except Exception as exc:
                log.error("Pre-check skill run failed (%s): %s", route_result.get("slug"), exc)
                skill_result = f"Skill error: {exc}"
        reply = "✓ " + str(skill_result)
        await update.message.reply_text(reply)
        _log_telegram_exchange(text_clean, reply)
        _maybe_reflect(chat_id)
        log.info("DEBUG pre-check: skill router action:skill (%s)", route_result.get("slug"))
        return

    if route_result["action"] == "wrap_up":
        import threading
        from jobs.memory.wrap_up import wrap_up as _wrap_up
        session_id = f"telegram_{chat_id}"
        threading.Thread(
            target=_wrap_up, args=(session_id, None), daemon=True
        ).start()
        await update.message.reply_text("Wrapping up this session. I'll save it to memory and notify you via Telegram.")
        log.info("DEBUG pre-check: skill router action:wrap_up")
        return

    if route_result["action"] == "build":
        import threading
        threading.Thread(
            target=_router._build_in_background,
            args=(route_result["description"], route_result["job_path"], "telegram"),
            daemon=True,
        ).start()
        await update.message.reply_text("Building that skill now. I'll notify you via Telegram when it's ready.")
        log.info("DEBUG pre-check: skill router action:build")
        return

    if route_result["action"] == "propose":
        _pending_skills[chat_id] = text_clean
        await update.message.reply_text(route_result["message"])
        log.info("DEBUG pre-check: skill router action:propose")
        return

    # Safety net: if any build trigger leaked past the router, fire the build now.
    if any(t in text_clean.lower() for t in _router._BUILD_TRIGGERS):
        import threading
        description = _router._extract_build_description(text_clean)
        job_path = _router._generate_job_path(description)
        threading.Thread(
            target=_router._build_in_background,
            args=(description, job_path, "telegram"),
            daemon=True,
        ).start()
        await update.message.reply_text("Building that skill now. I'll notify you via Telegram when it's ready.")
        log.info("DEBUG pre-check: safety net build trigger")
        return

    # 4. Classify intent via Ollama llama3.2:3b (non-blocking)
    result = await asyncio.to_thread(_classify_intent, text_clean)
    log.info("DEBUG classifier raw result: %s", result)
    intent = result.get("intent", "general")
    params = result.get("params", {})
    confidence = result.get("confidence", "HIGH")
    log.info("Intent: %s | Params: %s | Confidence: %s", intent, params, confidence)

    if confidence == "LOW":
        _intent_plain = {
            "contact_lookup": f"look up {params.get('name', 'someone')}",
            "image_search": f"search for an image of {params.get('query', 'something')}",
            "calendar_query": f"check your calendar for {params.get('day', 'today')}",
            "block_time": f"block {params.get('duration_minutes', 60)} minutes for {params.get('title', 'something')}",
            "reminder_create": f"set a reminder: {params.get('title', '...')}",
            "task_create": f"add a task: {params.get('title', '...')}",
            "task_list": "list your tasks",
            "book_appointment": f"book an appointment with {params.get('name', 'someone')}",
        }
        desc = _intent_plain.get(intent, "handle this")
        _pending_intents[chat_id] = {"result": result, "text_clean": text_clean}
        await update.message.reply_text(f"Just to confirm — are you asking me to {desc}?")
        return

    await _dispatch_intent(update, context, result, text_clean)
    if confidence == "MEDIUM":
        await update.message.reply_text("Is that right?")


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
        sent = await update.message.reply_text(
            f"📅 I found a slot for {title}:\n\n{slot['display']}\n\nReply YES to book it or NO to cancel."
        )
        try:
            from jobs.telegram.pending import store_pending_action
            store_pending_action("calendar_booking", sent.message_id, {"chat_id": chat_id})
        except Exception:
            pass
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
        sent = await update.message.reply_text(
            f"📅 I found a slot for {name}:\n\n{slot['display']}\n\nReply YES to book it or NO to cancel."
        )
        try:
            from jobs.telegram.pending import store_pending_action
            store_pending_action("calendar_booking", sent.message_id, {"chat_id": chat_id})
        except Exception:
            pass
    except Exception as exc:
        log.error("Book appointment failed: %s", exc)
        await update.message.reply_text(f"Error finding slot: {exc}")


async def _dispatch_intent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: dict,
    text_clean: str,
) -> None:
    """Route a classified intent result to the appropriate handler."""
    intent = result.get("intent", "general")
    log.info("DEBUG dispatch intent: %s params: %s", intent, result.get("params", {}))
    params = result.get("params", {})
    chat_id = update.effective_chat.id
    if intent == "contact_lookup":
        await _handle_contact_lookup(update, context, params)
    elif intent == "calendar_query":
        await _handle_calendar_day(update, context, params)
    elif intent == "calendar_busy":
        await _handle_mark_busy(update, context)
    elif intent == "calendar_availability":
        await _handle_calendar_availability(update, context, params)
    elif intent == "block_time":
        await _handle_block_time(update, context, params)
    elif intent == "book_appointment":
        await _handle_book_appointment(update, context, params)
    elif intent == "reminder_create":
        await _handle_reminder_create(update, context, params)
    elif intent == "task_create":
        await _handle_task_create(update, context, params)
    elif intent == "task_list":
        await _handle_task_list(update, context)
    elif intent == "task_done":
        await _handle_task_done(update, context, params)
    elif intent == "image_search":
        await _handle_image_search(update, context, params)
    else:
        reply = await _handle_general(update, context, text_clean)
        _log_telegram_exchange(text_clean, reply)
        _maybe_reflect(chat_id)


async def _handle_contact_lookup(
    update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict
) -> None:
    from jobs.people.lookup import lookup_member
    name = re.sub(r"'s$", "", (params.get("name") or "").strip(), flags=re.IGNORECASE).strip()
    if not name:
        await update.message.reply_text("Who would you like me to look up?")
        return
    members = lookup_member(name)
    if not members:
        reply = f"No members found matching '{name}'."
    else:
        lines = []
        for m in members:
            contact = " | ".join(filter(None, [m.get("email"), m.get("phone"), m.get("campus_preference")]))
            lines.append(f"*{m['name']}* — {contact}" if contact else f"*{m['name']}*")
        reply = "\n".join(lines)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def _handle_image_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict
) -> None:
    from telegram import InputMediaPhoto
    from jobs.social.image_search import get_image_urls
    query = (params.get("query") or "").strip()
    if not query:
        await update.message.reply_text("What would you like images of?")
        return
    urls = await asyncio.to_thread(get_image_urls, query)
    if not urls:
        await update.message.reply_text("No images found.")
        return
    bot = update.get_bot()
    chat_id = update.effective_chat.id
    try:
        if len(urls) == 1:
            await bot.send_photo(chat_id=chat_id, photo=urls[0])
        else:
            media = [InputMediaPhoto(media=url) for url in urls[:10]]
            await bot.send_media_group(chat_id=chat_id, media=media)
    except Exception as exc:
        log.error("Image send failed: %s", exc)
        await update.message.reply_text("Found images but couldn't send them: " + str(exc))


def _ensure_reminders_table(conn) -> None:
    from jobs.reminders import ensure_reminders_schema
    ensure_reminders_schema(conn)


async def _handle_reminder_create(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    title = params.get("title", "")
    due = params.get("due_datetime")
    if not title:
        await update.message.reply_text("What should I remind you about?")
        return
    # Extract HH:MM from due_datetime if present
    reminder_time = None
    if due:
        try:
            reminder_time = datetime.fromisoformat(due).strftime("%H:%M")
        except Exception:
            pass
    try:
        with get_connection() as conn:
            _ensure_reminders_table(conn)
            conn.execute(
                "INSERT INTO reminders (title, due_datetime, reminder_time, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'active', datetime('now'), datetime('now'))",
                (title, due or "", reminder_time),
            )
        reply = f"⏰ Reminder set for {reminder_time}: {title}" if reminder_time else f"⏰ Reminder saved: {title}"
        await update.message.reply_text(reply)
    except Exception as exc:
        log.error("Reminder create failed: %s", exc)
        await update.message.reply_text(f"Error saving reminder: {exc}")


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


async def _get_general_reply(text: str) -> str:
    try:
        import requests as _req
        resp = _req.post(
            "http://localhost:11434/api/chat",
            json={
                "model": "llama3.2:3b",
                "messages": [
                    {"role": "system", "content": WATSON_SYSTEM},
                    {"role": "user", "content": text},
                ],
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        reply = resp.json()["message"]["content"].strip()
        return reply or "I didn't get a response."
    except Exception as exc:
        log.error("Ollama general chat failed: %s", exc)
        return "I'm having trouble thinking right now. Try again in a moment."


async def _handle_general(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> str:
    _possessive = re.search(r"(\w+)'s\s+(?:email|phone|number|contact)", text, re.IGNORECASE)
    if _possessive:
        from jobs.people.lookup import lookup_member
        _hits = lookup_member(_possessive.group(1))
        if _hits:
            _lines = []
            for m in _hits:
                _contact = " | ".join(filter(None, [m.get("email"), m.get("phone"), m.get("campus_preference")]))
                _lines.append(f"*{m['name']}* — {_contact}" if _contact else f"*{m['name']}*")
            await update.message.reply_text("\n".join(_lines), parse_mode="Markdown")
            return ""
    reply = await _get_general_reply(text)
    await update.message.reply_text(reply)
    return reply


async def _route_tg_pending_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, tg_pending: dict
) -> bool:
    """Dispatch a reply-to-Watson-message to the correct pending action handler.

    Returns True if consumed, False to fall through to normal routing.
    """
    from jobs.telegram.pending import mark_done, mark_cancelled

    action_type = tg_pending["type"]
    payload = tg_pending["payload"]
    pending_id = tg_pending["id"]
    text_lower = text.lower().strip()

    if action_type == "email_draft":
        record_id = payload.get("record_id")
        if text_lower == "go":
            from jobs.email_reply.handler import resolve_send_by_id
            result = resolve_send_by_id(record_id)
            await update.message.reply_text(result["msg"])
            if result["ok"]:
                mark_done(pending_id)
            return True
        if text_lower.startswith("change:"):
            changed = text[text_lower.index("change:") + len("change:"):].strip()
            from jobs.email_reply.handler import resolve_change_by_id
            result = resolve_change_by_id(record_id, changed)
            await update.message.reply_text(result["msg"])
            if result["ok"]:
                mark_done(pending_id)
            return True
        if text_lower in ("cancel", "no", "never mind"):
            from jobs.email_reply.handler import resolve_cancel_by_id
            result = resolve_cancel_by_id(record_id)
            if result["ok"]:
                await update.message.reply_text(result["msg"])
                mark_cancelled(pending_id)
            return True
        return False

    if action_type == "pastoral_note":
        from jobs.pastoral_notes.handler import handle_notes_reply
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, handle_notes_reply, text)
        mark_done(pending_id)
        return True

    if action_type == "calendar_booking":
        chat_id = payload.get("chat_id") or update.effective_chat.id
        if text_lower in ("yes", "confirm", "yes do it", "book it", "go ahead"):
            p = pending_module.get_pending(chat_id)
            if p:
                await _execute_pending(update, context, p)
                mark_done(pending_id)
                return True
        if text_lower in ("no", "cancel", "don't book", "never mind"):
            p = pending_module.get_pending(chat_id)
            if p:
                pending_module.cancel_pending(p["id"])
                await update.message.reply_text("Got it — cancelled.")
                mark_cancelled(pending_id)
                return True
        return False

    return False


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
            from jobs.gcal.gcal_service import mark_busy
            mark_busy(start_dt, end_dt, title)
        else:
            from jobs.gcal.gcal_service import create_event
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


async def handle_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not _is_authorized(update):
        await query.answer()
        return

    if query.data.startswith("cmd_approve_"):
        proposal_id = int(query.data[len("cmd_approve_"):])
        await query.answer("Executing...")
        await query.edit_message_text("⏳ Running...")
        import threading
        from jobs.dev.command_executor import execute_command
        threading.Thread(
            target=execute_command,
            args=(proposal_id,),
            daemon=True,
        ).start()

    elif query.data.startswith("cmd_reject_"):
        proposal_id = int(query.data[len("cmd_reject_"):])
        await query.answer("Cancelled")
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path
        _db = _Path(os.getenv("WATSON_DB", str(_Path(__file__).resolve().parents[1] / "data" / "watson.db")))
        _conn = _sqlite3.connect(str(_db))
        _conn.execute("UPDATE command_proposals SET status='rejected' WHERE id=?", (proposal_id,))
        _conn.commit()
        _conn.close()
        await query.edit_message_text("❌ Command cancelled.", reply_markup=None)


async def handle_acquire_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not _is_authorized(update):
        await query.answer()
        return

    if query.data.startswith("acquire_approve_"):
        acquisition_id = int(query.data[len("acquire_approve_"):])
        await query.answer("Acquiring skill...")
        await query.edit_message_text("✅ Acquisition approved. Building now...")
        import threading
        from jobs.skillbuilder.acquire import execute_acquisition
        threading.Thread(
            target=execute_acquisition,
            args=(acquisition_id,),
            daemon=True,
        ).start()

    elif query.data.startswith("acquire_reject_"):
        acquisition_id = int(query.data[len("acquire_reject_"):])
        await query.answer("Rejected")
        with get_connection() as conn:
            conn.execute(
                "UPDATE skill_acquisitions SET status='rejected' WHERE id=?",
                (acquisition_id,),
            )
        await query.edit_message_text("❌ Acquisition rejected.")


async def handle_thank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    txn_id = int(query.data.split(":", 1)[1])
    row = await asyncio.to_thread(_gb_get_txn, txn_id)

    if row is None:
        await query.edit_message_text("Transaction not found in donors.db.")
        return

    if row["thanked"]:
        await query.edit_message_text(f"Already sent to {row['name']}.")
        return

    name = row["name"]
    email = row["email"]
    gift_count = row["gift_count"] or 1
    amount = row["amount"]

    if gift_count == 1:
        subject, html_body = first_gift_email(name, amount)
    else:
        subject, html_body = repeat_gift_email(name, amount, gift_count)

    await query.edit_message_text(f"Sending thank-you to {name}…")

    try:
        await asyncio.to_thread(_gb_send_kit_email, email, subject, html_body)
        await asyncio.to_thread(_gb_mark_thanked, txn_id)
        log.info("Givebutter thank-you sent: txn %d → %s", txn_id, email)
        await query.edit_message_text(f"✅ Sent to {name}.")
    except Exception as exc:
        log.error("Givebutter thank-you failed for txn %d: %s", txn_id, exc)
        await query.edit_message_text(f"❌ Send failed: {exc}")


async def handle_edit_thank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    txn_id = int(query.data.split(":", 1)[1])
    row = await asyncio.to_thread(_gb_get_txn, txn_id)

    if row is None:
        await query.edit_message_text("Transaction not found in donors.db.")
        return

    if row["thanked"]:
        status = "sent" if row["thanked"] == 1 else "already drafted"
        await query.edit_message_text(f"Already {status} for {row['name']}.")
        return

    name = row["name"]
    email = row["email"]
    gift_count = row["gift_count"] or 1
    amount = row["amount"]

    if gift_count == 1:
        subject, html_body = first_gift_email(name, amount)
    else:
        subject, html_body = repeat_gift_email(name, amount, gift_count)

    await query.edit_message_text(f"Creating Kit draft for {name}…")

    try:
        await asyncio.to_thread(_gb_create_kit_draft, email, subject, html_body)
        await asyncio.to_thread(_gb_mark_thanked_2, txn_id)
        await asyncio.to_thread(_gb_add_kit_reminder, name)
        log.info("Givebutter Kit draft created: txn %d → %s", txn_id, email)
        await query.edit_message_text(f"✏️ Draft saved in Kit for {name}. Reminder added.")
    except Exception as exc:
        log.error("Givebutter Kit draft failed for txn %d: %s", txn_id, exc)
        await query.edit_message_text(f"❌ Draft creation failed: {exc}")


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
    from jobs.gcal.gcal_service import get_events
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
    from jobs.gcal.gcal_service import mark_day_busy_from_now
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
    from jobs.email_reply.handler import init_table as init_email_reply_table
    init_email_reply_table()

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
    app.add_handler(CallbackQueryHandler(handle_command_callback, pattern=r"^cmd_"))
    app.add_handler(CallbackQueryHandler(handle_acquire_callback, pattern=r"^acquire_"))
    app.add_handler(CallbackQueryHandler(handle_reject_callback, pattern=r"^reject:"))
    app.add_handler(CallbackQueryHandler(handle_facebook_callback, pattern=r"^fb_"))
    app.add_handler(CallbackQueryHandler(handle_email_callback, pattern=r"^email_"))
    app.add_handler(CallbackQueryHandler(handle_book_callback, pattern=r"^book_"))
    app.add_handler(CallbackQueryHandler(handle_menu_callback, pattern=r"^menu_"))
    app.add_handler(CallbackQueryHandler(handle_thank_callback, pattern=r"^thank:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_edit_thank_callback, pattern=r"^edit_thank:\d+$"))
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





