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
import socket
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
from jobs.people.api import people_create, people_list, people_get, congregation_search
import jobs.gcal.pending as pending_module
from jobs.gcal import reasoner
from jobs.intent.classifier import classify as _classify_intent
from jobs.memory.prompt_builder import build_prompt
from jobs.routing.directive_prefixes import telegram_prefixes as _telegram_prefixes, canonicalize as _canonicalize_prefix
from jobs.givebutter.templates import first_gift_email, repeat_gift_email

log = logging.getLogger(__name__)

_AUTHORIZED_ID = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else None

_DONORS_DB = Path(__file__).resolve().parents[1] / "data" / "donors.db"
_CONG_DB   = Path(__file__).resolve().parents[1] / "data" / "congregation.db"
_KIT_API_KEY = os.getenv("KIT_API_KEY", "")        # v3 reads (tags list)
_KIT_API_SECRET = os.getenv("KIT_API_SECRET", "")  # v3 writes (tags, subscribe, draft)
_KIT_API_KEY_V4 = os.getenv("KIT_API_KEY_V4", "")  # v4 broadcasts (X-Kit-Api-Key header)
_KIT_SENDER_EMAIL = os.getenv("KIT_SENDER_EMAIL", "")
_KIT_SENDER_NAME = os.getenv("KIT_SENDER_NAME", "")
_GMAIL_USER = os.getenv("WATSON_GMAIL_ADDRESS", "")
_GMAIL_APP_PASSWORD = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")

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
        session_id = _get_or_create_telegram_session()
        threading.Thread(target=reflect, args=(session_id, None), daemon=True).start()

_SKILL_AFFIRM = {"yes", "yes please", "go ahead", "build it", "sure", "do it", "yep", "yeah"}
_SKILL_DENY = {"no", "never mind", "nope", "cancel", "don't", "no thanks"}

def _log_routing_correction(original_message: str, detected_intent: str, correct_intent: str = "cancelled_by_user") -> None:
    db_path = os.path.expanduser("~/watson/data/watson.db")
    try:
        with sqlite3.connect(db_path) as _conn:
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS routing_corrections "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, original_message TEXT NOT NULL, "
                "detected_intent TEXT, correct_intent TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            )
            _conn.execute(
                "INSERT INTO routing_corrections (original_message, detected_intent, correct_intent) VALUES (?, ?, ?)",
                (original_message, detected_intent, correct_intent),
            )
    except Exception as _exc:
        log.error("Correction log failed: %s", _exc)




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
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["From"] = "FMS Team <watson@faithmakessense.com>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(_GMAIL_USER, _GMAIL_APP_PASSWORD)
        smtp.sendmail("watson@faithmakessense.com", to_email, msg.as_string())
    print(f"Gmail SMTP: sent to {to_email} — subject: {subject}")


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
        _log_tg('out', reply_text)
    except Exception as exc:
        log.warning("Failed to log telegram exchange: %s", exc)


def _log_tg(direction: str, message: str) -> None:
    db_path = os.path.expanduser("~/watson/data/watson.db")
    try:
        with sqlite3.connect(db_path) as _conn:
            _conn.execute(
                "INSERT INTO telegram_log (direction, message) VALUES (?, ?)",
                (direction, message),
            )
    except Exception as exc:
        log.warning("telegram_log write failed: %s", exc)


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


async def handle_facebook_image_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data  # "fb_img_approve:{id}" / "fb_img_regen:{id}" / "fb_img_discard:{id}"
    action, post_id_str = data.split(":", 1)
    post_id = int(post_id_str)

    from jobs.facebook.image_gen import approve_post, discard_post, regenerate_image

    if action == "fb_img_approve":
        slot = await asyncio.to_thread(approve_post, post_id)
        slot_text = slot.strftime("%Y-%m-%d %H:%M") if slot else "no open slot found"
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n✅ Approved — scheduled for {slot_text}",
            reply_markup=None,
        )
    elif action == "fb_img_discard":
        await asyncio.to_thread(discard_post, post_id)
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n❌ Discarded.",
            reply_markup=None,
        )
    elif action == "fb_img_regen":
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n\U0001F504 Regenerating...",
            reply_markup=None,
        )
        await asyncio.to_thread(regenerate_image, post_id)
        # regenerate_image sends a fresh photo+buttons message itself


# Worst-case stack inside this window (bug #29, all measured on this
# CPU-only host): skill router's own 8s timeout, then classify()'s 55s
# timeout, then a real shot at the general-chat fallback (measured up to
# ~13.2s, budgeted 25s). 8 + 55 + 25 = 88s -> 100s leaves real margin.
_HANDLE_TEXT_TIMEOUT_SECONDS = 100

# Holds references to detached asyncio.create_task() background jobs (e.g. the
# fireflies: directive) so they aren't garbage-collected mid-run — a task with
# no live reference can be silently dropped by the event loop.
_background_tasks: set = set()


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.wait_for(
            _handle_text_body(update, context), timeout=_HANDLE_TEXT_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        log.error("handle_text timed out after %ss", _HANDLE_TEXT_TIMEOUT_SECONDS)
        try:
            await update.message.reply_text(
                "That's taking too long — something's stuck. Try again in a moment."
            )
        except Exception:
            pass
    except Exception as exc:
        log.error("handle_text failed: %s", exc)
        try:
            await update.message.reply_text(f"Something went wrong: {exc}")
        except Exception:
            pass


async def _handle_text_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return

    text = update.message.text or ""
    if not text.strip():
        return

    # Normalize smart quotes from mobile keyboards
    text_clean = text.replace("'", "'").replace("'", "'")
    text_lower = text_clean.lower().strip()
    _log_tg('in', text_clean)

    # Directive prefix intercepts — colon-prefixed commands, highest priority.
    # Prefix set is the canonical registry in jobs/routing/directive_prefixes.py —
    # both this list and the dashboard's read from it, so they stop drifting apart.
    _DIRECTIVE_PREFIXES = _telegram_prefixes()
    for _dpfx_raw in _DIRECTIVE_PREFIXES:
        if text_lower.startswith(_dpfx_raw):
            _dpfx = _canonicalize_prefix(_dpfx_raw)
            _darg = text_clean[len(_dpfx_raw):].strip()
            if _dpfx == "cdb:":
                if _darg.lower().startswith("mark "):
                    await _handle_batch_mark(update, context, _darg)
                else:
                    from jobs.skills.cdb_query import run as _cdb_run_d
                    _dr = await asyncio.to_thread(_cdb_run_d, _darg)
                    await update.message.reply_text(_dr or "No results.")
            elif _dpfx == "kb:":
                await _handle_kb(update, context, text_clean)
            elif _dpfx == "web:":
                from jobs.research.web_search import run as _ws_run_d
                _dr = await asyncio.to_thread(_ws_run_d, _darg)
                await update.message.reply_text(_dr or "No results.")
                _log_telegram_exchange(text_clean, _dr or "")
            elif _dpfx == "task:":
                from jobs.tasks.add_task import run as _at_run_d
                _at_run_d(_darg)
                await update.message.reply_text(f"Task saved: {_darg}")
            elif _dpfx == "note:":
                await _handle_pastoral_note_direct(update, context, _darg)
            elif _dpfx == "remind:":
                with get_connection() as _rc:
                    _ensure_reminders_table(_rc)
                    _rc.execute(
                        "INSERT INTO reminders (title, due_datetime, reminder_time, status, created_at, updated_at) "
                        "VALUES (?, datetime('now'), NULL, 'active', datetime('now'), datetime('now'))",
                        (_darg,),
                    )
                await update.message.reply_text(f"Reminder saved: {_darg}")
                _log_telegram_exchange(text_clean, f"Reminder saved: {_darg}")
            elif _dpfx == "sms:":
                _sms_parts = _darg.split(":", 1)
                if len(_sms_parts) == 2:
                    _sms_name, _sms_body = _sms_parts[0].strip(), _sms_parts[1].strip()
                    from jobs.people.lookup import lookup_member as _lm_d
                    from jobs.sms.sms_send import send_sms_to_contact as _sms_tc_d
                    _hits = _lm_d(_sms_name, update.effective_chat.id)
                    _ct = next((c for c in _hits if c.get("phone")), None)
                    if _ct:
                        _res = await asyncio.to_thread(_sms_tc_d, _ct, _sms_body)
                        if _res["success"]:
                            _dr = f"Text sent to {_ct['name']}."
                        elif _res.get("needs_carrier"):
                            await _send_carrier_confirm_keyboard(update, _ct["name"], _res["phone"], _sms_body)
                            _dr = None
                        else:
                            _dr = f"Failed: {_res['error']}"
                    else:
                        _dr = f"No contact with phone found for '{_sms_name}'."
                    if _dr is not None:
                        await update.message.reply_text(_dr)
                        _log_telegram_exchange(text_clean, _dr)
                else:
                    await update.message.reply_text("Format: sms: Name: message")
            elif _dpfx == "polish:":
                await _handle_polish(update, context, _darg)
            elif _dpfx == "wdb:":
                from jobs.skills.wdb_query import run as _wdb_run_d
                _dr = await asyncio.to_thread(_wdb_run_d, _darg)
                await update.message.reply_text(_dr or "No results.")
            elif _dpfx == "bible:":
                from jobs.bible import run as _bible_run_d
                _dr = await asyncio.to_thread(_bible_run_d, _darg)
                await update.message.reply_text(_dr or "No result.")
                _log_telegram_exchange(text_clean, _dr or "")
            elif _dpfx == "devloop:":
                await _handle_devloop(update, context, _darg)
            elif _dpfx == "bug:":
                if not _darg:
                    await update.message.reply_text("Format: bug: <title>")
                else:
                    with get_connection() as _bc:
                        _bc.execute(
                            "INSERT INTO bug_tracker (title, repo) VALUES (?, 'watson')",
                            (_darg,),
                        )
                    await update.message.reply_text(f"Logged: {_darg}")
                    _log_telegram_exchange(text_clean, f"Logged: {_darg}")
            elif _dpfx == "gutenberg:":
                await _handle_gutenberg_search(update, context, _darg)
            elif _dpfx == "classics:":
                await _handle_classics(update, context, _darg)
            elif _dpfx == "fireflies:":
                if not _darg:
                    await update.message.reply_text("Format: fireflies: <meeting_id>")
                else:
                    _ff_meeting_id = _darg
                    _ff_text_clean = text_clean
                    await update.message.reply_text(
                        f"Processing meeting {_ff_meeting_id} — this may take a few minutes..."
                    )

                    # Detached on purpose: handle_text() wraps this whole function in a
                    # 15s asyncio.wait_for(), and process_meeting() can legitimately take
                    # several minutes (large-transcript Ollama call). Awaiting it here
                    # would let the 15s timeout fire and send a "stuck" message while the
                    # real pipeline kept running in the background — sending Bill a
                    # false-alarm message followed by the actual result. Running it as a
                    # separate task escapes that wrapper; the result is reported back via
                    # its own reply_text() call whenever it actually finishes.
                    async def _run_fireflies_directive():
                        from jobs.meet.fireflies_review import process_meeting
                        try:
                            result = await asyncio.to_thread(process_meeting, _ff_meeting_id)
                        except Exception as exc:
                            log.error("fireflies: directive failed for %s: %s", _ff_meeting_id, exc)
                            result = {"ok": False, "msg": f"Fireflies processing failed: {exc}"}
                        try:
                            await update.message.reply_text(result["msg"])
                        except Exception:
                            pass
                        _log_telegram_exchange(_ff_text_clean, result["msg"])

                    _ff_task = asyncio.create_task(_run_fireflies_directive())
                    _background_tasks.add(_ff_task)
                    _ff_task.add_done_callback(_background_tasks.discard)
            log.info("DEBUG directive: %s", _dpfx)
            return

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

    # KB query — kb: or search the kb: → ChromaDB search
    for _kb_prefix in ("search the kb:", "kb:"):
        if text_lower.startswith(_kb_prefix):
            await _handle_kb(update, context, text_clean)
            log.info("DEBUG pre-check: kb: query (early)")
            return

    if text.startswith("\U0001f4d8 TO FACEBOOK"):
        await _handle_facebook_share(update, text)
        log.info("DEBUG pre-check: facebook share")
        return

    # Writing Room commands
    if text_lower.startswith("room "):
        reply = await _handle_room_command(text_lower[5:].strip(), text_clean[5:].strip())
        await update.message.reply_text(reply)
        log.info("DEBUG pre-check: room command")
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
        await handle_notes_reply(text_clean.strip())
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
        _hits = _lm(_contact_name, update.effective_chat.id)
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

    # Forward Telegram content to any contact — "email/text/sms that to X" (last
    # outbound message) or "email/text/sms this to X: content" (inline content).
    # Supersedes the earlier unbuilt "remember last contact" idea; pulls from
    # telegram_log rather than a dedicated context table. Checked ahead of the
    # SMS interception block below because its free-form contact regex would
    # otherwise swallow "text this/that to X" (captures "this to"/"that to" as
    # a bogus contact name) before reaching these patterns.
    _FORWARD_LAST = re.compile(r'^(email|text|sms)\s+that\s+to\s+(.+)$', re.IGNORECASE)
    _FORWARD_INLINE = re.compile(
        r'^(email|text|sms)\s+this\s+to\s+([^\n:]+)[:\n]?\s*(.*)$',
        re.IGNORECASE | re.DOTALL,
    )
    # Medium-unspecified variants — ask rather than guess (section 2 of spec)
    _FORWARD_LAST_AMBIG = re.compile(r'^send\s+that\s+to\s+(.+)$', re.IGNORECASE)
    _FORWARD_INLINE_AMBIG = re.compile(
        r'^send\s+this\s+to\s+([^\n:]+)[:\n]?\s*(.*)$',
        re.IGNORECASE | re.DOTALL,
    )

    _fwd_medium = None   # 'sms' | 'email' | None (ambiguous)
    _fwd_mode = None      # 'last' | 'inline'
    _fwd_name = None
    _fwd_content = None

    _fm = _FORWARD_LAST.match(text_clean.strip())
    if _fm:
        _fwd_medium = "sms" if _fm.group(1).lower() in ("text", "sms") else "email"
        _fwd_mode = "last"
        _fwd_name = _fm.group(2).strip()
    else:
        _fm = _FORWARD_INLINE.match(text_clean.strip())
        if _fm:
            _fwd_medium = "sms" if _fm.group(1).lower() in ("text", "sms") else "email"
            _fwd_mode = "inline"
            _fwd_name = _fm.group(2).strip()
            _fwd_content = _fm.group(3).strip()
        else:
            _fm = _FORWARD_LAST_AMBIG.match(text_clean.strip())
            if _fm:
                _fwd_mode = "last"
                _fwd_name = _fm.group(1).strip()
            else:
                _fm = _FORWARD_INLINE_AMBIG.match(text_clean.strip())
                if _fm:
                    _fwd_mode = "inline"
                    _fwd_name = _fm.group(1).strip()
                    _fwd_content = _fm.group(2).strip()

    if _fwd_mode:
        if _fwd_mode == "inline" and not _fwd_content:
            await update.message.reply_text(f"What should I send to {_fwd_name}?")
            log.info("DEBUG pre-check: forward — empty inline content")
            return

        if _fwd_medium is None:
            from jobs.telegram.pending import store_pending_action
            sent = await update.message.reply_text(
                f"Email or text — which should I use to reach {_fwd_name}?"
            )
            store_pending_action(
                "forward_medium_clarify",
                sent.message_id,
                {"name": _fwd_name, "mode": _fwd_mode, "content": _fwd_content},
            )
            log.info("DEBUG pre-check: forward — medium ambiguous, asked")
            return

        await _forward_to_contact(update, _fwd_medium, _fwd_name, _fwd_mode, _fwd_content)
        log.info("DEBUG pre-check: forward (%s, %s)", _fwd_medium, _fwd_mode)
        return

    # SMS interception
    # 'text ' must be a startswith check — substring match catches "polish this text for me"
    _sms_triggers = (
        'send a text', 'send text', 'shoot a text',
        'shoot them a text', 'shoot her a text', 'shoot him a text',
    )
    if text_lower.startswith('text ') or any(t in text_lower for t in _sms_triggers):
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
            _hits = _lm_sms(_sms_name, update.effective_chat.id)
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
            if slug == "skip_all":
                from core.database import get_connection
                with get_connection() as _conn:
                    _conn.execute(
                        "UPDATE tg_pending_actions SET status='cancelled' "
                        "WHERE status IN ('pending', 'awaiting_confirmation')"
                    )
                await update.message.reply_text("All pending actions cleared.")
                log.info("DEBUG pre-check: skip all — cleared tg_pending_actions")
                return
            if slug == "image_search":
                await _handle_image_search(update, context, {"query": text_clean})
                log.info("DEBUG pre-check: skill pre-check (image_search)")
                return
            if slug == "kb_export":
                await _handle_kb_export(update, context, text_clean)
                log.info("DEBUG pre-check: skill pre-check (kb_export)")
                return
            if slug == "kb":
                await _handle_kb(update, context, text_clean)
                log.info("DEBUG pre-check: skill pre-check (kb)")
                return
            if slug == "polish":
                await _handle_polish(update, context, text_clean)
                log.info("DEBUG pre-check: skill pre-check (polish)")
                return
            if slug == "cdb_query":
                prefix_end = text_clean.lower().index("cdb:") + len("cdb:")
                question = text_clean[prefix_end:].strip()
                from jobs.skills.cdb_query import run as _cdb_run
                result = await asyncio.to_thread(_cdb_run, question)
                await update.message.reply_text(result or "No results.")
                log.info("DEBUG pre-check: skill pre-check (cdb_query)")
                return
            if slug == "pastoral_notes":
                await _handle_pastoral_note_direct(update, context, text_clean)
                log.info("DEBUG pre-check: skill pre-check (pastoral_notes)")
                return
            if slug == "image_gen":
                from jobs.skills.image_gen_skill import run as _image_gen_run
                result = await asyncio.to_thread(_image_gen_run, text_clean)
                await update.message.reply_text(result or "No result.")
                log.info("DEBUG pre-check: skill pre-check (image_gen)")
                return
            if slug == "add_task" and (
                msg_lower_check.startswith("task:") or msg_lower_check.startswith("tasks:")
            ):
                colon_idx = text_clean.index(":") + 1
                task_text = text_clean[colon_idx:].strip()
                from jobs.tasks.add_task import run as _add_task_direct
                _add_task_direct(task_text)
                await update.message.reply_text("Task saved.")
                log.info("DEBUG pre-check: skill pre-check (add_task direct)")
                return
            if slug == "calendar_query":
                await _handle_calendar_day(update, context, {"day": "today"})
                log.info("DEBUG pre-check: skill pre-check (calendar_query)")
                return
            try:
                _skills = _router._load_skills("telegram")
                _skill = next((s for s in _skills if s["slug"] == slug), None)
                if _skill:
                    result = await asyncio.to_thread(_router._run_skill, _skill, message=text_clean)
                else:
                    result = f"Skill '{slug}' not available."
            except Exception as exc:
                log.error("Pre-check skill dispatch failed (%s): %s", slug, exc)
                result = f"Skill error: {exc}"
            await update.message.reply_text("✓ " + str(result))
            _log_tg('out', "✓ " + str(result))
            log.info("DEBUG pre-check: skill pre-check (%s)", slug)
            return

    if getattr(_router, '_is_factual_query', None) and _router._is_factual_query(text_clean):
        from jobs.research.web_search import run as web_search_run
        ws_result = await asyncio.to_thread(web_search_run, text_clean)
        reply = "✓ " + ws_result
        await update.message.reply_text(reply)
        _log_telegram_exchange(text_clean, reply)
        _maybe_reflect(chat_id)
        log.info("DEBUG pre-check: factual query → web search")
        return

    # 2. Skill routing — explicit triggers only (skill/build/propose/wrap_up)
    # action:"chat" falls through to the intent classifier below
    try:
        route_result = await asyncio.to_thread(_router.route, text_clean, "telegram")
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
                    skill_result = await asyncio.to_thread(
                        _router._run_skill, _skill, message=route_result.get("message", text_clean)
                    )
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
        session_id = _get_or_create_telegram_session()
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

    # 4. Classify intent via Ollama gemma3:4b (non-blocking)
    # Ack before the slow part starts (bug #29) — classify()/general-chat
    # can legitimately take up to _HANDLE_TEXT_TIMEOUT_SECONDS on this
    # CPU-only host, and a silent 15-80s wait reads as a broken bot.
    await update.message.reply_text("💭 Thinking...")
    _classifier_system = build_prompt(task=text_clean, project=None)
    result = await asyncio.to_thread(_classify_intent, text_clean, _classifier_system)
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
    members = lookup_member(name, update.effective_chat.id)
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


async def _handle_kb_export(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    from pathlib import Path
    from jobs.skills.kb_export import run as kb_export_run
    await update.message.reply_text("Building KB export...")
    result = await asyncio.to_thread(kb_export_run, message)
    if not result["ok"]:
        await update.message.reply_text(result["error"])
        return
    zip_path = Path(result["zip_path"])
    try:
        with zip_path.open("rb") as zf:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=zf,
                filename=zip_path.name,
                caption=result["caption"],
            )
        _log_telegram_exchange(message, result["caption"])
    except Exception as exc:
        log.error("KB export send failed: %s", exc)
        await update.message.reply_text(f"Failed to send export: {exc}")
    finally:
        zip_path.unlink(missing_ok=True)


async def _handle_kb(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    lower = text.lower()
    query = text
    for prefix in ("search the kb:", "kb:"):
        if lower.startswith(prefix):
            query = text[len(prefix):].strip()
            break
    if not query:
        await update.message.reply_text("What would you like to search in the knowledge base?")
        return
    await update.message.reply_text("Searching knowledge base...")
    try:
        from jobs.skills.kb_search import search_kb, format_result
        result = await asyncio.to_thread(search_kb, query)
        reply = format_result(result)
        sent = await update.message.reply_text(reply)
        _log_telegram_exchange(text, reply)
        from jobs.telegram.pending import store_pending_action
        store_pending_action("kb_email", sent.message_id, {
            "synopsis": result["synopsis"],
            "sources": result["sources"],
            "query": result["query"],
        })
    except Exception as exc:
        log.error("KB search failed: %s", exc)
        await update.message.reply_text(f"KB search failed: {exc}")


async def _handle_gutenberg_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    if not query:
        await update.message.reply_text("What would you like to search for on Project Gutenberg?")
        return
    await update.message.reply_text("Searching Project Gutenberg...")
    try:
        from jobs.research.gutenberg import search as gutenberg_search
        hits = await asyncio.to_thread(gutenberg_search, query)
    except Exception as exc:
        log.error("Gutenberg search failed: %s", exc)
        await update.message.reply_text(f"Gutenberg search failed: {exc}")
        return
    if not hits:
        await update.message.reply_text(f"No Project Gutenberg matches for: {query}")
        return
    lines = [f'Project Gutenberg results for "{query}":\n']
    for i, hit in enumerate(hits, start=1):
        year = hit["year"] or "n/a"
        lines.append(
            f"{i}. {hit['title']} — {hit['authors']} ({year}) — {hit['download_count']} downloads"
        )
    lines.append("\nReply with a number to download and add it to the classics knowledge base.")
    reply = "\n".join(lines)
    sent = await update.message.reply_text(reply)
    _log_telegram_exchange(query, reply)
    from jobs.telegram.pending import store_pending_action
    store_pending_action("gutenberg_select", sent.message_id, {"candidates": hits, "query": query})


async def _handle_classics(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str) -> None:
    if not question:
        await update.message.reply_text("What would you like to ask the classics knowledge base?")
        return
    await update.message.reply_text("Searching classics knowledge base...")
    try:
        from jobs.skills.kb_search import search_kb, format_result
        result = await asyncio.to_thread(search_kb, question, "gutenberg")
        reply = format_result(result)
        sent = await update.message.reply_text(reply)
        _log_telegram_exchange(question, reply)
        from jobs.telegram.pending import store_pending_action
        store_pending_action("kb_email", sent.message_id, {
            "synopsis": result["synopsis"],
            "sources": result["sources"],
            "query": result["query"],
        })
    except Exception as exc:
        log.error("Classics KB search failed: %s", exc)
        await update.message.reply_text(f"Classics search failed: {exc}")


async def _handle_polish(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    lower = text.lower()
    if lower.startswith("polish this:"):
        text = text[len("polish this:"):].strip()
    if not text:
        await update.message.reply_text("Please include the text to polish after 'polish this:'")
        return
    await update.message.reply_text("Polishing...")
    try:
        from jobs.skills.polish import polish_text
        result = await asyncio.to_thread(polish_text, text)
        await update.message.reply_text(result)
        _log_telegram_exchange(text, result)
    except Exception as exc:
        log.error("Polish skill failed: %s", exc)
        await update.message.reply_text(f"Polish failed: {exc}")


async def _handle_pastoral_note_direct(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    """Save a pastoral note directly — no post-meeting context or person matching."""
    from jobs.pastoral_notes.db import get_db
    lower = text.lower()
    for prefix in ("pastoral notes:", "pastoral note:"):
        if lower.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if not text:
        await update.message.reply_text("Please include the note content after the prefix.")
        return
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO pastoral_notes (person_name, note, status, created_at) VALUES ('Direct Entry', ?, 'active', datetime('now', 'localtime'))",
                (text,),
            )
        await update.message.reply_text("Pastoral note saved.")
    except Exception as exc:
        log.error("Pastoral note save failed: %s", exc)
        await update.message.reply_text(f"Error saving pastoral note: {exc}")


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


def _get_general_reply_sync(text: str) -> str:
    system = build_prompt(task=text, project=None)
    db_path = os.path.expanduser("~/watson/data/watson.db")
    try:
        with sqlite3.connect(db_path) as _rc:
            _rc_count = _rc.execute(
                "SELECT COUNT(*) FROM routing_corrections WHERE created_at >= datetime('now', '-30 days')"
            ).fetchone()[0]
            if _rc_count >= 5:
                system = (
                    "Note: Dr. Bill has corrected Watson's routing recently. "
                    "When intent is ambiguous, ask before acting rather than assuming.\n\n---\n\n"
                    + system
                )
    except Exception:
        pass
    try:
        import requests as _req
        resp = _req.post(
            "http://localhost:11434/api/chat",
            json={
                "model": "llama3.2:3b",
                "messages": [
                    {"role": "system", "content": system},
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


async def _get_general_reply(text: str) -> str:
    return await asyncio.to_thread(_get_general_reply_sync, text)


async def _handle_general(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> str:
    _possessive = re.search(r"(\w+)'s\s+(?:email|phone|number|contact)", text, re.IGNORECASE)
    if _possessive:
        from jobs.people.lookup import lookup_member
        _hits = lookup_member(_possessive.group(1), update.effective_chat.id)
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


_CARRIER_CONFIRM_BUTTONS = (
    ("AT&T", "AT&T"), ("Verizon", "Verizon"), ("T-Mobile", "T-Mobile"),
    ("Boost", "Boost Mobile"), ("Cricket", "Cricket"),
)


async def _send_carrier_confirm_keyboard(update: Update, name: str, phone: str, message: str) -> None:
    """Ask which carrier a number belongs to, via the shared tg_pending_actions
    reply-threading mechanism — buttons for common carriers, or reply with the name."""
    from jobs.telegram.pending import store_pending_action

    prompt = f"I don't have a confirmed carrier on file for {name}'s number. Which one?"
    with get_connection() as conn:
        guess = conn.execute(
            "SELECT carrier FROM phone_carriers WHERE phone_number = ? AND confirmed = 0 AND source = 'numverify'",
            (phone,),
        ).fetchone()
    if guess and guess["carrier"]:
        prompt += f" (NumVerify suggests {guess['carrier']} — unconfirmed, so not used automatically.)"

    sent = await update.message.reply_text(prompt)
    pending_id = store_pending_action(
        "carrier_confirm", sent.message_id, {"name": name, "phone": phone, "message": message}
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"carrier_pick:{pending_id}:{value}")
         for label, value in _CARRIER_CONFIRM_BUTTONS[:3]],
        [InlineKeyboardButton(label, callback_data=f"carrier_pick:{pending_id}:{value}")
         for label, value in _CARRIER_CONFIRM_BUTTONS[3:]]
        + [InlineKeyboardButton("Other — type it", callback_data=f"carrier_other:{pending_id}")],
    ])
    await sent.edit_reply_markup(reply_markup=keyboard)


async def _forward_to_contact(
    update: Update, medium: str, name: str, mode: str, content: str | None
) -> None:
    """Resolve `name` via lookup_member and send `content` via `medium` ('sms'/'email').

    mode='last' pulls the most recent direction='out' telegram_log row as content;
    mode='inline' uses `content` as given. Forwards verbatim — no reformatting.
    """
    if mode == "last":
        with get_connection() as conn:
            row = conn.execute(
                "SELECT message FROM telegram_log WHERE direction='out' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            await update.message.reply_text("Nothing recent to forward.")
            return
        content = row["message"]

    from jobs.people.lookup import lookup_member as _lm_fwd
    hits = _lm_fwd(name, update.effective_chat.id)
    field = "phone" if medium == "sms" else "email"
    contact = next((c for c in hits if c.get(field)), None)
    if not contact:
        if not hits:
            await update.message.reply_text(f"No contact found for '{name}'.")
        else:
            missing = "phone number" if medium == "sms" else "email address"
            await update.message.reply_text(f"Found {hits[0]['name']} but no {missing} on file.")
        return

    if medium == "sms":
        from jobs.sms.sms_send import send_sms_to_contact as _sms_send_fwd
        result = await asyncio.to_thread(_sms_send_fwd, contact, content)
        if result["success"]:
            note = " (truncated to 150 chars)" if len(content) > 150 else ""
            reply = f"Sent to {contact['name']} via text{note}."
            await update.message.reply_text(reply)
            _log_telegram_exchange(f"[forward:sms:{name}]", reply)
        elif result.get("needs_carrier"):
            await _send_carrier_confirm_keyboard(update, contact["name"], result["phone"], content)
        else:
            await update.message.reply_text(f"Failed: {result['error']}")
    else:
        try:
            await asyncio.to_thread(
                send_as_watson, contact["email"], "Message from Dr. Bill", content
            )
            reply = f"Sent to {contact['name']} via email."
            await update.message.reply_text(reply)
            _log_telegram_exchange(f"[forward:email:{name}]", reply)
        except Exception as exc:
            log.error("Forward email send failed: %s", exc)
            await update.message.reply_text(f"Failed: {exc}")


async def handle_carrier_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    data = query.data
    from jobs.telegram.pending import mark_done

    pending_id = int(data.split(":")[1])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, payload FROM tg_pending_actions WHERE id=? AND status='pending'",
            (pending_id,),
        ).fetchone()

    if not row:
        await query.edit_message_text("⚠️ Action expired or already resolved.", reply_markup=None)
        return

    import json as _json
    payload = _json.loads(row["payload"])

    if data.startswith("carrier_other:"):
        await query.edit_message_text(
            f"Reply to this message with {payload['name']}'s carrier (e.g. \"Cricket\")."
        )
        return

    carrier = data.split(":", 2)[2]
    from jobs.sms.carrier_lookup import save_carrier
    from jobs.sms.sms_send import send_sms

    save_carrier(payload["phone"], carrier, source="manual", confirmed=True)
    result = await asyncio.to_thread(send_sms, payload["name"], payload["phone"], carrier, payload["message"])
    mark_done(pending_id)
    if result["success"]:
        await query.edit_message_text(
            f"✅ Carrier set to {carrier}. Text sent to {payload['name']}.", reply_markup=None
        )
    else:
        await query.edit_message_text(
            f"Carrier saved ({carrier}), but send failed: {result['error']}", reply_markup=None
        )


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
        await handle_notes_reply(text)
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

    if action_type == "kb_email":
        if text_lower == "email that to me":
            query = payload.get("query", "")
            synopsis = payload.get("synopsis", "")
            sources = payload.get("sources", [])
            sources_str = "\n".join(f"• {s}" for s in sources)
            body = f"{synopsis}\n\nSources:\n{sources_str}"
            try:
                await asyncio.to_thread(
                    send_as_watson,
                    "pastorbill@catalyst302.com",
                    f"KB Search: {query}",
                    body,
                )
                await update.message.reply_text("✅ Sent to your inbox.")
                mark_done(pending_id)
            except Exception as exc:
                log.error("KB email send failed: %s", exc)
                await update.message.reply_text(f"Email failed: {exc}")
            return True
        return False

    if action_type == "gutenberg_select":
        candidates = payload.get("candidates", [])
        if text_lower in ("cancel", "no", "never mind"):
            mark_cancelled(pending_id)
            await update.message.reply_text("Cancelled.")
            return True
        try:
            choice = int(text_lower)
        except ValueError:
            await update.message.reply_text(
                f"Reply with a number 1-{len(candidates)} to select, or 'cancel' to stop."
            )
            return True
        if choice < 1 or choice > len(candidates):
            await update.message.reply_text(f"Pick a number between 1 and {len(candidates)}.")
            return True
        book = candidates[choice - 1]
        await update.message.reply_text(f"Downloading and ingesting: {book['title']}...")
        from jobs.research.gutenberg import download_and_ingest
        result = await asyncio.to_thread(download_and_ingest, book["id"])
        if not result["ok"]:
            await update.message.reply_text(f"Ingestion failed: {result['error']}")
            return True
        if result["already_ingested"]:
            await update.message.reply_text(
                f"'{result['title']}' is already in the classics knowledge base."
            )
        else:
            await update.message.reply_text(
                f"✅ Added '{result['title']}' to the classics knowledge base — "
                f"{result['chunks_added']} chunks."
            )
        mark_done(pending_id)
        return True

    if action_type == "forward_medium_clarify":
        name = payload.get("name")
        mode = payload.get("mode")
        content = payload.get("content")
        if text_lower in ("cancel", "no", "never mind"):
            mark_cancelled(pending_id)
            await update.message.reply_text("Cancelled.")
            return True
        if text_lower in ("email", "e-mail"):
            medium = "email"
        elif text_lower in ("text", "sms", "text message"):
            medium = "sms"
        else:
            await update.message.reply_text("Email or text?")
            return True
        await _forward_to_contact(update, medium, name, mode, content)
        mark_done(pending_id)
        return True

    if action_type == "carrier_confirm":
        if text_lower in ("cancel", "no", "never mind"):
            mark_cancelled(pending_id)
            await update.message.reply_text("Cancelled.")
            return True
        carrier = text.strip()
        from jobs.sms.carrier_lookup import save_carrier
        from jobs.sms.sms_send import send_sms
        save_carrier(payload["phone"], carrier, source="manual", confirmed=True)
        result = await asyncio.to_thread(
            send_sms, payload["name"], payload["phone"], carrier, payload["message"]
        )
        if result["success"]:
            await update.message.reply_text(f"✅ Carrier set to {carrier}. Text sent to {payload['name']}.")
        else:
            await update.message.reply_text(
                f"Carrier saved ({carrier}), but send failed: {result['error']}"
            )
        mark_done(pending_id)
        return True

    return False


async def _execute_pending(update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict) -> None:
    action_type = pending["action_type"]
    params = pending["params"]
    slot = pending["proposed_slot"]
    pending_id = pending["id"]

    if action_type not in ("block_time", "book_appointment", "calendar_busy"):
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

        if action_type == "calendar_busy":
            from jobs.gcal.gcal_service import mark_busy
            mark_busy(start_dt, end_dt)
            pending_module.confirm_pending(pending_id)
            await update.message.reply_text("🚫 Done — marked rest of today as busy.")
            return
        elif action_type == "block_time":
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


async def handle_email_triage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    data = query.data
    from jobs.telegram.pending import get_pending_by_message_id, mark_done, mark_cancelled

    if data.startswith("et_ingest:"):
        pending_id = int(data[len("et_ingest:"):])
    elif data.startswith("et_markread:"):
        pending_id = int(data[len("et_markread:"):])
    elif data.startswith("et_delete:"):
        pending_id = int(data[len("et_delete:"):])
    else:
        return

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, type, payload FROM tg_pending_actions WHERE id=? AND status='pending'",
            (pending_id,),
        ).fetchone()

    if not row:
        await query.edit_message_text("⚠️ Action expired or already resolved.", reply_markup=None)
        return

    import json as _json
    payload = _json.loads(row["payload"])

    if data.startswith("et_ingest:"):
        import asyncio
        from jobs.email_intake import handle_ingest_action
        msg = await asyncio.to_thread(handle_ingest_action, payload)
        with get_connection() as conn:
            conn.execute("UPDATE tg_pending_actions SET status='done' WHERE id=?", (pending_id,))
        await query.edit_message_text(msg, reply_markup=None)

    elif data.startswith("et_markread:"):
        import asyncio
        from jobs.email_intake import handle_markread_action
        msg = await asyncio.to_thread(handle_markread_action, payload)
        with get_connection() as conn:
            conn.execute("UPDATE tg_pending_actions SET status='done' WHERE id=?", (pending_id,))
        await query.edit_message_text(msg, reply_markup=None)

    elif data.startswith("et_delete:"):
        import asyncio
        from jobs.email_intake import handle_delete_action
        msg = await asyncio.to_thread(handle_delete_action, payload)
        with get_connection() as conn:
            conn.execute("UPDATE tg_pending_actions SET status='done' WHERE id=?", (pending_id,))
        await query.edit_message_text(msg, reply_markup=None)


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


async def handle_vault_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not _is_authorized(update):
        await query.answer()
        return

    if query.data == "vault_unlock":
        await query.answer("Unlocking…")
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post("http://localhost:5200/api/logins/unlock", timeout=5)
            await query.edit_message_text("✅ Vault unlocked.", reply_markup=None)
        except Exception as exc:
            await query.edit_message_text(f"⚠️ Unlock failed: {exc}", reply_markup=None)


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


async def _handle_room_command(cmd: str, cmd_raw: str) -> str:
    """Handle 'room <cmd>' text commands from William."""
    import sqlite3 as _sqlite3
    _DB = os.path.expanduser("~/watson/data/watson.db")

    def _conn():
        c = _sqlite3.connect(_DB)
        c.row_factory = _sqlite3.Row
        return c

    if cmd == "partners":
        with _conn() as c:
            rows = c.execute(
                "SELECT name, email, joined_at FROM writing_room_partners "
                "WHERE status = 'active' ORDER BY joined_at ASC"
            ).fetchall()
        if not rows:
            return "No active Writing Room partners."
        lines = [f"{r['name']} — {r['email']} (joined {(r['joined_at'] or '')[:10]})" for r in rows]
        return f"Writing Room Partners ({len(rows)}):\n\n" + "\n".join(lines)

    if cmd == "pending":
        with _conn() as c:
            rows = c.execute(
                "SELECT id, name, email, created_at FROM writing_room_partners "
                "WHERE status = 'pending' ORDER BY created_at ASC"
            ).fetchall()
        if not rows:
            return "No pending Writing Room applications."
        lines = [f"#{r['id']} {r['name']} — {r['email']}" for r in rows]
        return f"Pending applications ({len(rows)}):\n\n" + "\n".join(lines)

    if cmd.startswith("message "):
        try:
            msg_n = int(cmd.split()[1])
        except (IndexError, ValueError):
            return "Usage: room message [N]"
        with _conn() as c:
            row = c.execute(
                "SELECT * FROM writing_room_messages WHERE id = ?", (msg_n,)
            ).fetchone()
        if not row:
            return f"Message #{msg_n} not found."
        return f"From: {row['name']} ({row['email']})\n\n{row['message']}"

    if cmd.startswith("revoke "):
        email = cmd_raw.split(None, 1)[1].strip() if " " in cmd_raw else ""
        if not email:
            return "Usage: room revoke [email]"
        with _conn() as c:
            row = c.execute(
                "SELECT name FROM writing_room_partners WHERE email = ?", (email,)
            ).fetchone()
            if not row:
                return f"No partner found with email {email}"
            c.execute(
                "UPDATE writing_room_partners SET status = 'revoked' WHERE email = ?", (email,)
            )
        return f"🚫 {row['name']} ({email}) revoked."

    if cmd.startswith("call "):
        # room call [title] [datetime] [url]
        parts = cmd_raw.split(None, 3)
        if len(parts) < 3:
            return "Usage: room call [title] [ISO-datetime] [meeting-url]"
        _, title, scheduled_at, *rest = parts
        meeting_url = rest[0] if rest else None
        with _conn() as c:
            cursor = c.execute(
                "INSERT INTO writing_room_calls (title, scheduled_at, meeting_url) VALUES (?, ?, ?)",
                (title, scheduled_at, meeting_url),
            )
        return f"📅 Call scheduled: {title} at {scheduled_at} (id {cursor.lastrowid})"

    return f"Unknown room command: {cmd}\n\nAvailable: partners, pending, call, message [N], revoke [email]"


async def handle_room_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle room_approve: and room_deny: inline button presses."""
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    import threading

    if query.data.startswith("room_approve:"):
        partner_id = int(query.data.split(":", 1)[1])
        await query.edit_message_text("⏳ Approving…")
        from jobs.writing_room.onboard import process_approval

        def _approve():
            try:
                process_approval(partner_id)
            except Exception as exc:
                log.error("room_approve failed for %d: %s", partner_id, exc)

        threading.Thread(target=_approve, daemon=True).start()
        await query.edit_message_text("✅ Approval in progress — welcome email sending.", reply_markup=None)

    elif query.data.startswith("room_deny:"):
        partner_id = int(query.data.split(":", 1)[1])
        from jobs.writing_room.onboard import process_denial
        threading.Thread(target=process_denial, args=(partner_id,), daemon=True).start()
        await query.edit_message_text("🚫 Denied.", reply_markup=None)


async def handle_meeting_pattern_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle mtp_approve: and mtp_reject: inline button presses from scan_meeting_patterns.py."""
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    data = query.data or ""
    if data.startswith("mtp_approve:"):
        action = "approved"
        pattern_id = int(data[len("mtp_approve:"):])
    elif data.startswith("mtp_reject:"):
        action = "rejected"
        pattern_id = int(data[len("mtp_reject:"):])
    else:
        return

    db_path = os.path.expanduser("~/watson/data/watson.db")
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT prefix FROM meeting_type_patterns WHERE id=?", (pattern_id,)
            ).fetchone()
            if not row:
                await query.edit_message_text("Pattern not found.", reply_markup=None)
                return
            conn.execute(
                "UPDATE meeting_type_patterns SET status=?, resolved_at=datetime('now') WHERE id=?",
                (action, pattern_id),
            )
    except Exception as exc:
        await query.edit_message_text(f"Failed to update: {exc}", reply_markup=None)
        return

    prefix = row["prefix"]
    if action == "approved":
        await query.edit_message_text(
            f"Approved — future '{prefix}' events will get pre-meeting briefs.",
            reply_markup=None,
        )
    else:
        await query.edit_message_text(
            f"Noted — '{prefix}' events will be ignored.",
            reply_markup=None,
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
        answer = await asyncio.to_thread(ask, question)
        await update.message.reply_text(answer)
    except Exception as exc:
        log.error("Ask failed: %s", exc)
        await update.message.reply_text(f"Ask failed: {exc}")


# --- Calendar handlers --------------------------------------------------------

def _calendar_error_text(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "Couldn't reach your calendar — request timed out."
    return f"Couldn't reach your calendar — {exc}"


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
        events = await asyncio.to_thread(get_events, day_start, day_end)
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
        await update.message.reply_text(_calendar_error_text(exc))


async def _handle_mark_busy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from jobs.gcal.gcal_service import NY
    chat_id = update.effective_chat.id
    try:
        now = datetime.now(NY)
        end_of_day = now.replace(hour=23, minute=59, second=0, microsecond=0)
        display = f"{now.strftime('%-I:%M %p')} – 11:59 PM today"
        slot = {"available": True, "start": now.isoformat(), "end": end_of_day.isoformat(), "display": display}
        pending_module.save_pending(chat_id, "calendar_busy", {}, slot)
        sent = await update.message.reply_text(
            f"🚫 Mark the rest of today busy?\n\n{display}\n\nReply YES to confirm or NO to cancel."
        )
        try:
            from jobs.telegram.pending import store_pending_action
            store_pending_action("calendar_booking", sent.message_id, {"chat_id": chat_id})
        except Exception:
            pass
    except Exception as exc:
        log.error("Mark busy failed: %s", exc)
        await update.message.reply_text(_calendar_error_text(exc))


async def _handle_calendar_availability(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    from jobs.gcal.availability import get_available_slots_next_30_days
    try:
        all_slots = await asyncio.to_thread(get_available_slots_next_30_days, "virtual")
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
        await update.message.reply_text(_calendar_error_text(exc))
async def handle_merge_conflict_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle merge_old_ / merge_new_ / skip_ / different_ button taps from conflict_report.py."""
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    data = query.data  # e.g. "merge_old_42" / "merge_new_42" / "skip_42" / "different_42"
    if data.startswith("merge_old_"):
        action = "merge_old"
        conflict_id = int(data[len("merge_old_"):])
    elif data.startswith("merge_new_"):
        action = "merge_new"
        conflict_id = int(data[len("merge_new_"):])
    elif data.startswith("different_"):
        action = "different"
        conflict_id = int(data[len("different_"):])
    else:  # skip_
        action = "skip"
        conflict_id = int(data[len("skip_"):])

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    _RELATED_TABLES = ("connect_cards", "attendance", "next_steps", "prayer_requests", "follow_ups")
    _COPYABLE_FIELDS = (
        "email", "phone", "campus_preference", "first_visit_date",
        "notes", "carrier", "status_reason", "status_note", "snowbird_return",
    )

    conn = sqlite3.connect(str(_CONG_DB))
    conn.row_factory = sqlite3.Row
    try:
        conflict = conn.execute(
            "SELECT * FROM member_conflicts WHERE id = ?", (conflict_id,)
        ).fetchone()

        if not conflict:
            await query.edit_message_text("Conflict not found.", reply_markup=None)
            return

        old_id = conflict["existing_member_id"]
        new_id = conflict["new_member_id"]
        old_name = conflict["existing_name"] or "old record"
        new_name = conflict["new_name"] or "new record"

        if action == "skip":
            conn.execute(
                "UPDATE member_conflicts SET status='skipped' WHERE id=?", (conflict_id,)
            )
            conn.commit()
            await query.edit_message_text("⏭ Skipped — flagged for manual review.", reply_markup=None)
            return

        if action == "different":
            # Two distinct people — no merge, no data changes. Terminal state:
            # find_fuzzy_duplicates()'s pair check and intake.py's exact-match
            # guard both key off member_conflicts rows with real IDs, so this
            # pairing won't be re-flagged in a future report.
            conn.execute(
                "UPDATE member_conflicts SET status='confirmed_different', resolved_at=? WHERE id=?",
                (now, conflict_id),
            )
            conn.commit()
            await query.edit_message_text(
                "✅ Confirmed — two different people, no changes made.", reply_markup=None
            )
            return

        if not old_id or not new_id:
            await query.edit_message_text("❌ Conflict is missing member IDs — cannot merge.", reply_markup=None)
            return

        old_row = conn.execute("SELECT * FROM members WHERE id=?", (old_id,)).fetchone()
        new_row = conn.execute("SELECT * FROM members WHERE id=?", (new_id,)).fetchone()

        if not old_row or not new_row:
            await query.edit_message_text("❌ One or both member records not found.", reply_markup=None)
            return

        if action == "merge_old":
            # Old record is canonical — copy non-null new fields into old where old is missing
            updates = {}
            for field in _COPYABLE_FIELDS:
                new_val = new_row[field] if field in new_row.keys() else None
                old_val = old_row[field] if field in old_row.keys() else None
                if new_val and not old_val:
                    updates[field] = new_val
            if updates:
                set_clause = ", ".join(f"{f}=?" for f in updates)
                conn.execute(
                    f"UPDATE members SET {set_clause} WHERE id=?",
                    list(updates.values()) + [old_id],
                )
            for table in _RELATED_TABLES:
                try:
                    conn.execute(
                        f"UPDATE {table} SET member_id=? WHERE member_id=?", (old_id, new_id)
                    )
                except Exception:
                    pass
            conn.execute("DELETE FROM members WHERE id=?", (new_id,))
            conn.execute(
                "UPDATE member_conflicts SET status='resolved', resolved_at=? WHERE id=?",
                (now, conflict_id),
            )
            conn.commit()
            await query.edit_message_text(
                f"✅ Merged — kept {old_name}, folded in new data.", reply_markup=None
            )

        elif action == "merge_new":
            # New record is canonical — copy non-null old fields into new where new is missing
            updates = {}
            for field in _COPYABLE_FIELDS:
                old_val = old_row[field] if field in old_row.keys() else None
                new_val = new_row[field] if field in new_row.keys() else None
                if old_val and not new_val:
                    updates[field] = old_val
            if updates:
                set_clause = ", ".join(f"{f}=?" for f in updates)
                conn.execute(
                    f"UPDATE members SET {set_clause} WHERE id=?",
                    list(updates.values()) + [new_id],
                )
            for table in _RELATED_TABLES:
                try:
                    conn.execute(
                        f"UPDATE {table} SET member_id=? WHERE member_id=?", (new_id, old_id)
                    )
                except Exception:
                    pass
            conn.execute("DELETE FROM members WHERE id=?", (old_id,))
            conn.execute(
                "UPDATE member_conflicts SET status='resolved', resolved_at=? WHERE id=?",
                (now, conflict_id),
            )
            conn.commit()
            await query.edit_message_text(
                f"✅ Merged — kept {new_name}, folded in old data.", reply_markup=None
            )

    except Exception as exc:
        log.error("merge_conflict callback failed (id=%d action=%s): %s", conflict_id, action, exc)
        await query.edit_message_text(f"❌ Error: {exc}", reply_markup=None)
    finally:
        conn.close()


async def handle_benchmark_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle bench_update: / bench_ignore: button taps from benchmark_check.py."""
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    from jobs.research.benchmark_check import apply_update, ignore_source

    data = query.data  # e.g. "bench_update:7" / "bench_ignore:7"
    action, id_str = data.split(":", 1)
    source_id = int(id_str)

    try:
        result = apply_update(source_id) if action == "bench_update" else ignore_source(source_id)
        prefix = "✅" if result["ok"] else "❌"
        await query.edit_message_text(
            f"{query.message.text}\n\n{prefix} {result['msg']}", reply_markup=None
        )
    except Exception as exc:
        log.error("benchmark callback failed (id=%d action=%s): %s", source_id, action, exc)
        await query.edit_message_text(f"❌ Error: {exc}", reply_markup=None)


async def handle_member_conflict_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle mc_same / mc_diff / mc_update_email / mc_keep_sep / mc_skip button taps."""
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    action, conflict_id_str = query.data.rsplit(":", 1)
    conflict_id = int(conflict_id_str)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(str(_CONG_DB))
    conn.row_factory = sqlite3.Row
    try:
        conflict = conn.execute(
            "SELECT * FROM member_conflicts WHERE id = ?", (conflict_id,)
        ).fetchone()

        if not conflict:
            await query.edit_message_text("Conflict not found.", reply_markup=None)
            return

        if action == "mc_same":
            existing_id = conflict["existing_member_id"]
            new_id      = conflict["new_member_id"]
            if new_id:
                for table in ("connect_cards", "attendance", "next_steps", "prayer_requests", "follow_ups"):
                    conn.execute(
                        f"UPDATE {table} SET member_id = ? WHERE member_id = ?",
                        (existing_id, new_id),
                    )
                conn.execute("DELETE FROM members WHERE id = ?", (new_id,))
            conn.execute(
                "UPDATE member_conflicts SET status='resolved', resolved_at=? WHERE id=?",
                (now, conflict_id),
            )
            conn.commit()
            await query.edit_message_text(
                f"✅ Resolved: Merged {conflict['new_name']} into {conflict['existing_name']}",
                reply_markup=None,
            )

        elif action == "mc_diff":
            conn.execute(
                "UPDATE member_conflicts SET status='resolved', resolved_at=? WHERE id=?",
                (now, conflict_id),
            )
            conn.commit()
            await query.edit_message_text(
                f"✅ Resolved: Kept {conflict['existing_name']} and {conflict['new_name']} as different people",
                reply_markup=None,
            )

        elif action == "mc_update_email":
            conn.execute(
                "UPDATE members SET email = ? WHERE id = ?",
                (conflict["new_email"], conflict["existing_member_id"]),
            )
            conn.execute(
                "UPDATE member_conflicts SET status='resolved', resolved_at=? WHERE id=?",
                (now, conflict_id),
            )
            conn.commit()
            await query.edit_message_text(
                f"✅ Resolved: Updated email for {conflict['existing_name']} to {conflict['new_email']}",
                reply_markup=None,
            )

        elif action == "mc_keep_sep":
            conn.execute(
                "UPDATE member_conflicts SET status='resolved', resolved_at=? WHERE id=?",
                (now, conflict_id),
            )
            conn.commit()
            await query.edit_message_text(
                f"✅ Resolved: Kept {conflict['existing_name']} records separate",
                reply_markup=None,
            )

        elif action == "mc_skip":
            conn.execute(
                "UPDATE member_conflicts SET status='skipped' WHERE id=?", (conflict_id,)
            )
            conn.commit()
            await query.edit_message_text(
                f"⏭ Skipped: {conflict['existing_name']} / {conflict['new_name']}",
                reply_markup=None,
            )

    except Exception as exc:
        log.error("member_conflict callback failed (id=%d action=%s): %s", conflict_id, action, exc)
        await query.edit_message_text(f"❌ Error: {exc}", reply_markup=None)
    finally:
        conn.close()


# ── Batch member update (cdb: mark ...) ──────────────────────────────────────

def _batch_update_message(pending_id: int):
    """Return (text, InlineKeyboardMarkup|None) for the current state of a pending batch update."""
    from jobs.connect_cards.batch_update import (
        get_pending, current_ambiguous, format_ambiguous_prompt, format_preview,
    )
    pending = get_pending(pending_id)
    if not pending:
        return "Batch update not found.", None

    entry = current_ambiguous(pending)
    if entry:
        text = format_ambiguous_prompt(entry)
        buttons = [
            InlineKeyboardButton(f"{i}) {c['name']}", callback_data=f"bu_pick:{pending_id}:{i}")
            for i, c in enumerate(entry["candidates"], 1)
        ]
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("Skip", callback_data=f"bu_pick:{pending_id}:skip")])
        return text, InlineKeyboardMarkup(rows)

    text = format_preview(pending)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm & Apply", callback_data=f"bu_confirm:{pending_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"bu_cancel:{pending_id}"),
    ]])
    return text, keyboard


async def _handle_batch_mark(update: Update, context: ContextTypes.DEFAULT_TYPE, darg: str) -> None:
    """Handle a 'cdb: mark ...' directive — resolves names, then walks the
    Bill through ambiguous picks and a final confirm via inline buttons."""
    from jobs.connect_cards.batch_update import (
        parse_mark_command, validate_value, batch_update_members, create_pending,
    )
    parsed = await asyncio.to_thread(parse_mark_command, darg)
    if parsed is None:
        await update.message.reply_text("Not a recognized mark command.")
        return
    if "error" in parsed:
        await update.message.reply_text(parsed["error"])
        return
    err = validate_value(parsed["field"], parsed["value"])
    if err:
        await update.message.reply_text(err)
        return

    resolution = await asyncio.to_thread(
        batch_update_members, parsed["field"], parsed["value"], parsed["names"]
    )
    pending_id = await asyncio.to_thread(
        create_pending, parsed["field"], parsed["value"], parsed["value_display"], resolution, "telegram"
    )
    text, keyboard = _batch_update_message(pending_id)
    await update.message.reply_text(text, reply_markup=keyboard)


async def handle_batch_update_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle bu_pick / bu_confirm / bu_cancel button taps for batch member updates."""
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    from jobs.connect_cards.batch_update import (
        resolve_current_ambiguous, finalize_pending, cancel_pending,
    )

    data = query.data
    if data.startswith("bu_pick:"):
        _, pending_id_s, choice = data.split(":", 2)
        pending_id = int(pending_id_s)
        result = await asyncio.to_thread(resolve_current_ambiguous, pending_id, choice)
        if isinstance(result, dict) and "error" in result:
            await query.edit_message_text(result["error"], reply_markup=None)
            return
        text, keyboard = _batch_update_message(pending_id)
        await query.edit_message_text(text, reply_markup=keyboard)

    elif data.startswith("bu_confirm:"):
        pending_id = int(data[len("bu_confirm:"):])
        result = await asyncio.to_thread(finalize_pending, pending_id, "Bill (Telegram)")
        if result["errors"]:
            await query.edit_message_text(
                "Batch update failed:\n" + "\n".join(result["errors"]), reply_markup=None
            )
            return
        lines = [f"Applied {len(result['applied'])} update(s):"]
        for a in result["applied"]:
            old = a["old_value"] if a["old_value"] not in (None, "") else "(none)"
            lines.append(f"  {a['name']}: {old} → {a['new_value']}")
        await query.edit_message_text("\n".join(lines), reply_markup=None)

    elif data.startswith("bu_cancel:"):
        pending_id = int(data[len("bu_cancel:"):])
        await asyncio.to_thread(cancel_pending, pending_id)
        await query.edit_message_text("Cancelled.", reply_markup=None)


# ── Dev Loop handlers ─────────────────────────────────────────────────────────

async def _handle_devloop(update: Update, context: ContextTypes.DEFAULT_TYPE, description: str) -> None:
    """Handle `devloop: <description>` — create new project and trigger loop."""
    import re
    import threading
    from jobs.dev_loop.trigger import trigger_dev_loop

    description = description.strip()
    if not description:
        await update.message.reply_text("Usage: devloop: <description of what to build>")
        return

    slug = re.sub(r"[^a-z0-9]+", "-", description.lower())[:32].strip("-")
    title = description[:60]

    await update.message.reply_text(
        f"Dev Loop starting\n"
        f"Slug: {slug}\n"
        f"Sending to FMSPC…"
    )

    def _run():
        result = trigger_dev_loop(slug=slug, title=title, input_type="description", input_text=description)
        if not result["ok"]:
            import requests as _rq
            from core.vacation import vacation_gate
            fail_text = f"Dev Loop failed to start: {result.get('error')}"
            token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
            if token and chat_id and not vacation_gate("system_failure", "bot.bot._handle_devloop", fail_text):
                try:
                    _rq.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": fail_text},
                        timeout=10,
                    )
                except Exception:
                    pass

    threading.Thread(target=_run, daemon=True).start()


async def handle_devloop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle devloop_keep:<slug> and devloop_stop:<slug> inline button callbacks."""
    import threading
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    data = query.data or ""
    if data.startswith("devloop_keep:"):
        slug = data[len("devloop_keep:"):]
        db_path = os.path.expanduser("~/watson/data/watson.db")
        try:
            import sqlite3 as _sq
            with _sq.connect(db_path) as _c:
                _c.row_factory = _sq.Row
                row = _c.execute("SELECT * FROM dev_projects WHERE slug=?", (slug,)).fetchone()
        except Exception:
            row = None

        if not row:
            await query.edit_message_text(f"Project '{slug}' not found.", reply_markup=None)
            return

        if dict(row).get("status") != "paused":
            await query.edit_message_text(f"Project '{slug}' is not paused.", reply_markup=None)
            return

        await query.edit_message_text(
            f"Dev Loop — RESUMING\n{slug}\n\nExtending by 3 more iterations…",
            reply_markup=None,
        )

        row_d = dict(row)
        def _run():
            from jobs.dev_loop.trigger import trigger_dev_loop
            trigger_dev_loop(
                slug=slug,
                title=row_d["title"],
                input_type=row_d["input_type"],
                input_text=row_d["input_text"],
                start_iteration=row_d["current_iteration"] + 1,
                extend_by=3,
            )
        threading.Thread(target=_run, daemon=True).start()

    elif data.startswith("devloop_stop:"):
        slug = data[len("devloop_stop:"):]
        db_path = os.path.expanduser("~/watson/data/watson.db")
        try:
            import sqlite3 as _sq
            with _sq.connect(db_path) as _c:
                _c.execute("UPDATE dev_projects SET status='stopped', updated_at=datetime('now') WHERE slug=?", (slug,))
        except Exception as exc:
            await query.edit_message_text(f"Failed to stop: {exc}", reply_markup=None)
            return
        await query.edit_message_text(
            f"Dev Loop — STOPPED\n{slug}\n\nStopped. Review code on dashboard.",
            reply_markup=None,
        )


async def handle_git_sync_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle gs_pull:/gs_skip: button taps from jobs/dev/git_sync.py's needs-decision alerts."""
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    data = query.data or ""
    if data.startswith("gs_pull:"):
        action = "pull"
        pending_id = int(data[len("gs_pull:"):])
    elif data.startswith("gs_skip:"):
        action = "skip"
        pending_id = int(data[len("gs_skip:"):])
    else:
        return

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, type, payload FROM tg_pending_actions WHERE id=? AND status='pending'",
            (pending_id,),
        ).fetchone()

    if not row:
        await query.edit_message_text("⚠️ Action expired or already resolved.", reply_markup=None)
        return

    payload = json.loads(row["payload"])
    repo_path = payload["repo_path"]
    repo_name = payload["repo_name"]

    from jobs.telegram.pending import mark_done

    if action == "skip":
        mark_done(pending_id)
        await query.edit_message_text(f"Skipped — {repo_name} left as-is.", reply_markup=None)
        return

    import subprocess

    def _run_git(args):
        return subprocess.run(["git"] + args, cwd=repo_path, capture_output=True, text=True)

    rebase = await asyncio.to_thread(_run_git, ["pull", "--rebase"])

    if rebase.returncode != 0:
        await asyncio.to_thread(_run_git, ["rebase", "--abort"])
        mark_done(pending_id)
        await query.edit_message_text(
            f"❌ {repo_name} has a real conflict — can't auto-resolve. "
            f"SSH in: cd {repo_path}, git status",
            reply_markup=None,
        )
        return

    push = await asyncio.to_thread(_run_git, ["push", "origin", "main"])
    mark_done(pending_id)

    if push.returncode != 0:
        await query.edit_message_text(
            f"⚠️ {repo_name} rebased but push failed:\n{push.stderr.strip()[:300]}",
            reply_markup=None,
        )
        return

    await query.edit_message_text(f"✅ {repo_name} synced and pushed", reply_markup=None)


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    if not _AUTHORIZED_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set in .env")

    init_db()
    init_fb_db()
    init_email_db()
    pass  # email intake runs as standalone cron
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
    app.add_handler(CallbackQueryHandler(handle_devloop_callback,         pattern=r"^devloop_"))
    app.add_handler(CallbackQueryHandler(handle_git_sync_callback,        pattern=r"^gs_"))
    app.add_handler(CallbackQueryHandler(handle_merge_conflict_callback,  pattern=r"^(merge_old_|merge_new_|skip_|different_)\d+$"))
    app.add_handler(CallbackQueryHandler(handle_benchmark_callback, pattern=r"^bench_(update|ignore):\d+$"))
    app.add_handler(CallbackQueryHandler(handle_member_conflict_callback, pattern=r"^mc_"))
    app.add_handler(CallbackQueryHandler(handle_batch_update_callback, pattern=r"^bu_"))
    app.add_handler(CallbackQueryHandler(handle_command_callback, pattern=r"^cmd_"))
    app.add_handler(CallbackQueryHandler(handle_vault_callback,   pattern=r"^vault_"))
    app.add_handler(CallbackQueryHandler(handle_acquire_callback, pattern=r"^acquire_"))
    app.add_handler(CallbackQueryHandler(handle_reject_callback, pattern=r"^reject:"))
    app.add_handler(CallbackQueryHandler(handle_room_callback, pattern=r"^room_(?:approve|deny):"))
    app.add_handler(CallbackQueryHandler(handle_meeting_pattern_callback, pattern=r"^mtp_(approve|reject):"))
    app.add_handler(CallbackQueryHandler(handle_facebook_image_callback, pattern=r"^fb_img_(?:approve|regen|discard):"))
    app.add_handler(CallbackQueryHandler(handle_facebook_callback, pattern=r"^fb_"))
    app.add_handler(CallbackQueryHandler(handle_email_triage_callback, pattern=r"^et_"))
    app.add_handler(CallbackQueryHandler(handle_carrier_callback, pattern=r"^carrier_"))
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





