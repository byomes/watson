"""
fireflies_review.py — Fireflies.ai -> elder meeting review pipeline.

Triggered by POST /api/fireflies/webhook (jobs/dashboard/app.py) on
payload["event"] == "Transcription completed" (unconfirmed real value —
see TODO in app.py's webhook route), or manually via the `fireflies:
<meeting_id>` Telegram directive (bot/bot.py). Both callers run the same
process_meeting() pipeline: fetch the transcript, check whether it's an
elders meeting, draft a review email via Ollama, and send Bill a Telegram
approval message (reply-threaded via tg_pending_actions, dispatched in
bot.py) before emailing the elders tagged in Member Management.

Env vars (~/watson/.env):
  FIREFLIES_API_KEY        Bearer token for api.fireflies.ai/graphql
  FIREFLIES_WEBHOOK_SECRET HMAC-SHA256 secret for webhook signature verification
"""
import json
import logging
import os
import sqlite3

import requests
from dotenv import load_dotenv

from config.settings import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate
from jobs.email_job.gmail import send_as_watson
from jobs.telegram.pending import store_pending_action

load_dotenv(os.path.expanduser("~/watson/.env"))

log = logging.getLogger(__name__)

FIREFLIES_API_KEY     = os.getenv("FIREFLIES_API_KEY", "")
FIREFLIES_GRAPHQL_URL = "https://api.fireflies.ai/graphql"

CONG_DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

# Matches the qwen2.5:14b call pattern used in jobs/pastoral_notes/handler.py
# and jobs/email_job/draft_email.py.
_OLLAMA_URL   = "http://localhost:11434/api/generate"
_OLLAMA_MODEL = "qwen2.5:14b"

_TRANSCRIPT_QUERY = """
query Transcript($id: String!) {
  transcript(id: $id) {
    title
    date
    participants
    summary {
      overview
      action_items
    }
    sentences {
      speaker_name
      text
    }
  }
}
"""


def fetch_transcript(meeting_id: str) -> dict | None:
    """Pull title, date, attendees, summary, action items, and full transcript
    for a meeting via the Fireflies GraphQL API."""
    if not FIREFLIES_API_KEY:
        log.error("FIREFLIES_API_KEY not set; cannot fetch transcript.")
        return None
    try:
        resp = requests.post(
            FIREFLIES_GRAPHQL_URL,
            json={"query": _TRANSCRIPT_QUERY, "variables": {"id": meeting_id}},
            headers={"Authorization": f"Bearer {FIREFLIES_API_KEY}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            log.error("Fireflies GraphQL error for %s: %s", meeting_id, data["errors"])
            return None
        t = data.get("data", {}).get("transcript")
        if not t:
            log.error("No transcript returned for meeting_id=%s", meeting_id)
            return None
        summary = t.get("summary") or {}
        transcript_text = "\n".join(
            f"{s.get('speaker_name', '')}: {s.get('text', '')}"
            for s in (t.get("sentences") or [])
        )
        return {
            "title":        t.get("title", ""),
            "date":         t.get("date", ""),
            "attendees":    t.get("participants") or [],
            "overview":     summary.get("overview", ""),
            "action_items": summary.get("action_items", ""),
            "transcript":   transcript_text,
        }
    except Exception as exc:
        log.error("fetch_transcript failed for %s: %s", meeting_id, exc)
        return None


def is_elders_meeting(title: str) -> bool:
    """True if title contains 'elder' (case-insensitive). One-line change to widen later."""
    return "elder" in (title or "").lower()


def get_elder_emails() -> list[tuple[str, str]]:
    """Members tagged role='elder' (leadership_roles), member_status='active',
    with a non-blank email."""
    conn = sqlite3.connect(CONG_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT m.name, m.email
            FROM members m
            JOIN leadership_roles lr ON lr.member_id = m.id
            WHERE lr.role = 'elder' AND m.member_status = 'active'
              AND m.email IS NOT NULL AND m.email != ''
            ORDER BY m.name
            """
        ).fetchall()
        return [(r["name"], r["email"]) for r in rows]
    finally:
        conn.close()


def _ollama_generate(prompt: str) -> str:
    # 60s (the timeout used elsewhere for short single-note/article prompts —
    # jobs/pastoral_notes/handler.py, jobs/email_job/draft_email.py) is not
    # enough for a full meeting transcript: qwen2.5:14b on the Beelink's
    # CPU-bound inference is slow on large context (same known slowness
    # documented for qwen2.5-coder:7b in KB search), and this timed out
    # against a real elders meeting transcript. 300s (5 min) gives headroom;
    # if that's still not enough on retest, the next step is chunking/staged
    # summarization rather than raising the timeout further.
    resp = requests.post(
        _OLLAMA_URL,
        json={"model": _OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def draft_review_email(transcript_data: dict) -> str:
    """Draft an elders meeting review email via Ollama. Falls back to a
    templated summary on Ollama failure."""
    prompt = (
        "Write a concise elders meeting review email for a church leadership team. "
        "Include the meeting date, attendees, a summary, key decisions, and action "
        "items with owners if identifiable. Keep it clear and pastoral, not overly formal.\n\n"
        f"Meeting title: {transcript_data.get('title', '')}\n"
        f"Date: {transcript_data.get('date', '')}\n"
        f"Attendees: {', '.join(transcript_data.get('attendees') or [])}\n"
        f"Summary: {transcript_data.get('overview', '')}\n"
        f"Action items: {transcript_data.get('action_items', '')}\n\n"
        f"Full transcript:\n{transcript_data.get('transcript', '')[:8000]}"
    )
    try:
        return _ollama_generate(prompt)
    except Exception as exc:
        log.warning("draft_review_email Ollama call failed: %s", exc)
        attendees = ", ".join(transcript_data.get("attendees") or []) or "Unknown"
        return (
            f"Elders Meeting Review — {transcript_data.get('date', '')}\n\n"
            f"Attendees: {attendees}\n\n"
            f"Summary:\n{transcript_data.get('overview') or '(none)'}\n\n"
            f"Action Items:\n{transcript_data.get('action_items') or '(none)'}"
        )


def _send_telegram(text: str) -> dict | None:
    if vacation_gate("normal", "jobs.meet.fireflies_review", text):
        return None
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not set; skipping notification")
        return None
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("result")
    except Exception as exc:
        log.error("Telegram notification failed: %s", exc)
        return None


# ── Pending-record persistence (watson.db) ──────────────────────────────────

def _get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_table() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fireflies_review_pending (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id   TEXT NOT NULL,
                title        TEXT,
                meeting_date TEXT,
                draft        TEXT,
                recipients   TEXT NOT NULL DEFAULT '[]',
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at  TEXT
            )
        """)


def _save_pending(meeting_id: str, title: str, meeting_date: str, draft: str,
                   elders: list[tuple[str, str]]) -> int:
    _init_table()
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO fireflies_review_pending
               (meeting_id, title, meeting_date, draft, recipients)
               VALUES (?, ?, ?, ?, ?)""",
            (meeting_id, title, meeting_date, draft, json.dumps(elders)),
        )
        return cur.lastrowid


def _get_pending_by_id(record_id: int) -> dict | None:
    _init_table()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM fireflies_review_pending WHERE id=? AND status='pending'",
            (record_id,),
        ).fetchone()
    return dict(row) if row else None


def _mark_resolved(record_id: int, status: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE fireflies_review_pending SET status=?, resolved_at=datetime('now') WHERE id=?",
            (status, record_id),
        )


# ── Entry point (shared by the webhook route and the `fireflies:` Telegram
#    directive — the only difference between those two callers is how
#    meeting_id gets in and what they do with the returned result: the
#    webhook logs it and stays silent on skips; the manual trigger always
#    relays result["msg"] back to Bill, since a silent no-op on an explicit
#    manual request would be confusing) ───────────────────────────────────

def process_meeting(meeting_id: str) -> dict:
    """Run the full pipeline for a single meeting: fetch transcript →
    is_elders_meeting() check → draft review email → Telegram approval
    prompt with resolved recipient list. Returns {"ok": bool, "msg": str}."""
    transcript_data = fetch_transcript(meeting_id)
    if not transcript_data:
        msg = f"Could not fetch transcript for meeting_id={meeting_id}."
        log.error(msg)
        return {"ok": False, "msg": msg}

    title = transcript_data.get("title", "")
    if not is_elders_meeting(title):
        msg = f"Skipped — meeting title {title!r} doesn't look like an elders meeting."
        log.info("Skipping non-elders meeting: %r", title)
        return {"ok": False, "msg": msg}

    elders = get_elder_emails()
    if not elders:
        no_elders_msg = "No members tagged 'elder' found — tag elders in Member Management first."
        _send_telegram(no_elders_msg)
        log.warning("No elder emails found; not sending. meeting_id=%s", meeting_id)
        return {"ok": False, "msg": no_elders_msg}

    draft = draft_review_email(transcript_data)
    recipient_lines = "\n".join(f"• {name} <{email}>" for name, email in elders)

    text = (
        f"📋 Elders Meeting Review — {transcript_data.get('date', '')}\n"
        f"({title})\n\n"
        f"---DRAFT---\n{draft}\n---\n\n"
        f"Recipients:\n{recipient_lines}\n\n"
        f"Reply with:\n"
        f"• go — send to all recipients above\n"
        f"• cancel — discard, no emails sent"
    )

    result = _send_telegram(text)
    tg_msg_id = result.get("message_id") if result else None
    if not tg_msg_id:
        msg = f"Could not send Telegram approval message for meeting_id={meeting_id}."
        log.error(msg)
        return {"ok": False, "msg": msg}

    record_id = _save_pending(meeting_id, title, transcript_data.get("date", ""), draft, elders)
    try:
        store_pending_action("fireflies_review", tg_msg_id, {"record_id": record_id})
    except Exception as exc:
        log.warning("Failed to store tg_pending_action for fireflies review: %s", exc)

    return {"ok": True, "msg": f"Draft sent for approval — {title} ({transcript_data.get('date', '')})."}


# ── Resolution functions (called from bot.py reply-threading dispatch) ──────

def resolve_send_by_id(record_id: int) -> dict:
    """Send the review email to every resolved elder recipient."""
    record = _get_pending_by_id(record_id)
    if not record:
        return {"ok": False, "msg": "No pending Fireflies review found."}

    elders = json.loads(record["recipients"] or "[]")
    if not elders:
        _mark_resolved(record["id"], "sent_none")
        return {"ok": False, "msg": "No recipients on this review — nothing sent."}

    subject = f"Elders Meeting Review — {record['meeting_date']}"
    sent, failed = 0, []
    for name, email in elders:
        try:
            send_as_watson(email, subject, record["draft"] or "")
            sent += 1
        except Exception as exc:
            log.error("Failed to send elders review to %s <%s>: %s", name, email, exc)
            failed.append(name)

    _mark_resolved(record["id"], "sent")
    msg = f"✅ Elders review sent to {sent} recipient(s)."
    if failed:
        msg += f" Failed: {', '.join(failed)}."
    return {"ok": True, "msg": msg}


def resolve_cancel_by_id(record_id: int) -> dict:
    """Discard a pending elders review — no emails sent."""
    record = _get_pending_by_id(record_id)
    if not record:
        return {"ok": False, "msg": None}
    _mark_resolved(record["id"], "discarded")
    log.info("Fireflies review discarded: id=%s meeting_id=%s", record["id"], record["meeting_id"])
    return {"ok": True, "msg": "❌ Elders review discarded — no emails sent."}
