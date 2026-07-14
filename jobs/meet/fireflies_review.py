"""
fireflies_review.py — Fireflies.ai -> elder meeting review pipeline.

Triggered by POST /api/fireflies/webhook (jobs/dashboard/app.py) on
payload["event"] == "Transcription completed" (unconfirmed real value —
see TODO in app.py's webhook route), or manually via the `fireflies:
<meeting_id>` Telegram directive (bot/bot.py). Both callers run the same
process_meeting() pipeline: fetch the transcript, check whether it's an
elders meeting, get structured content (summary points + action items
grouped by owner) via Ollama, render it into the HTML template in
jobs/meet/templates/elder_review.py, email Bill a live preview, and send
Bill a Telegram approval message (reply-threaded via tg_pending_actions,
dispatched in bot.py) before emailing the elders tagged in Member
Management.

Env vars (~/watson/.env):
  FIREFLIES_API_KEY        Bearer token for api.fireflies.ai/graphql
  FIREFLIES_WEBHOOK_SECRET HMAC-SHA256 secret for webhook signature verification
"""
import json
import logging
import os
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv

from config.settings import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate
from jobs.meet.templates.elder_review import render_elder_review_email, render_elder_review_plain
from jobs.telegram.pending import store_pending_action

load_dotenv(os.path.expanduser("~/watson/.env"))

log = logging.getLogger(__name__)

FIREFLIES_API_KEY     = os.getenv("FIREFLIES_API_KEY", "")
FIREFLIES_GRAPHQL_URL = "https://api.fireflies.ai/graphql"

CONG_DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

BILL_PREVIEW_EMAIL = "pastorbill@catalyst302.com"

# SMTP creds — same source as jobs/email_job/gmail.py's send_as_watson(), but
# sent via a direct MIMEMultipart build (matching jobs/connect_cards/
# state_of_church.py's send_report()) instead of calling send_as_watson()
# itself: send_as_watson() does a "\n" -> "<br>" replace on its body and
# wraps the result in a SECOND <html><body> shell, which would double-wrap
# and mangle a fully pre-rendered HTML template like the one this module
# sends.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("WATSON_GMAIL_ADDRESS", "")
SMTP_PASS = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")
FROM_ADDR = os.getenv("WATSON_FROM_ADDRESS") or SMTP_USER

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


def _format_meeting_date(raw_date) -> str:
    """Human-readable date for subject lines/emails. Fireflies' GraphQL API is
    documented to return `date` as a Unix millisecond epoch timestamp, not a
    date string — previously this was passed straight through, showing a raw
    number like "1752500000000" in subjects and Telegram messages. Handles
    epoch (ms or s) and ISO-string dates defensively, since the real format
    hasn't been confirmed against a live non-test payload yet."""
    if raw_date is None or raw_date == "":
        return "Unknown date"
    try:
        num = float(raw_date)
        if num > 1e12:  # milliseconds
            num /= 1000
        return datetime.fromtimestamp(num, tz=timezone.utc).astimezone().strftime("%B %-d, %Y")
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    try:
        return datetime.fromisoformat(str(raw_date)).strftime("%B %-d, %Y")
    except ValueError:
        log.warning("Could not parse meeting date %r; using raw value.", raw_date)
        return str(raw_date)


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


_STRUCTURED_JSON_INSTRUCTIONS = (
    "You are drafting structured content for an elders meeting review email for "
    "Catalyst Community Church leadership. Return ONLY a JSON object — no prose, "
    "no markdown code fences, nothing before or after the JSON — matching exactly "
    "this shape:\n"
    '{"summary_points": ["point 1", "point 2", ...], '
    '"action_items": [{"owner": "Name or Unassigned", "items": ["item 1", "item 2"]}]}\n\n'
    "Guidelines: summary_points should be 3-6 concise bullet points capturing key "
    "discussion and decisions. Group action items by the person responsible; use "
    '"Unassigned" for items with no clear owner. Keep item text concise and actionable.'
)


def _build_structured_prompt(transcript_data: dict) -> str:
    return (
        f"{_STRUCTURED_JSON_INSTRUCTIONS}\n\n"
        f"Meeting title: {transcript_data.get('title', '')}\n"
        f"Attendees: {', '.join(transcript_data.get('attendees') or [])}\n"
        f"Summary: {transcript_data.get('overview', '')}\n"
        f"Action items (raw): {transcript_data.get('action_items', '')}\n\n"
        f"Full transcript:\n{transcript_data.get('transcript', '')[:8000]}"
    )


def _parse_structured_json(raw: str) -> dict | None:
    """Extract and validate {"summary_points": [...], "action_items": [...]}
    from Ollama's raw response. Returns None on any parse/shape failure —
    callers decide whether to retry or fall back."""
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end])
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    summary_points = data.get("summary_points")
    action_items = data.get("action_items")
    if not isinstance(summary_points, list) or not isinstance(action_items, list):
        return None
    if not all(isinstance(g, dict) and isinstance(g.get("items"), list) for g in action_items):
        return None

    return {
        "summary_points": [str(p) for p in summary_points],
        "action_items": [
            {"owner": str(g.get("owner") or "Unassigned"), "items": [str(i) for i in g.get("items", [])]}
            for g in action_items
        ],
    }


def _fallback_structured_content(transcript_data: dict) -> dict:
    """Basic, clearly-marked version built directly from the raw transcript
    data, used when Ollama's structured JSON fails to parse even after a
    retry — see render_elder_review_email()'s fallback banner."""
    overview = (transcript_data.get("overview") or "").strip()
    summary_points = [overview] if overview else []
    raw_action_items = (transcript_data.get("action_items") or "").strip()
    action_items = (
        [{"owner": "Unassigned", "items": [raw_action_items]}] if raw_action_items else []
    )
    return {"summary_points": summary_points, "action_items": action_items, "fallback": True}


def draft_review_email(transcript_data: dict) -> dict:
    """Get structured content (summary points + action items grouped by
    owner) for a meeting via Ollama — NOT final formatted text/HTML. Python
    (jobs/meet/templates/elder_review.py) owns turning this into the actual
    email, so formatting stays 100% consistent regardless of what Ollama
    produces. Retries once on malformed JSON, then falls back to a basic,
    clearly-marked version built straight from the raw transcript data."""
    prompt = _build_structured_prompt(transcript_data)

    structured = None
    for attempt in (1, 2):
        try:
            raw = _ollama_generate(prompt)
        except Exception as exc:
            log.warning("draft_review_email Ollama call failed (attempt %d): %s", attempt, exc)
            break
        structured = _parse_structured_json(raw)
        if structured is not None:
            break
        log.warning("draft_review_email got malformed JSON (attempt %d): %r", attempt, raw[:300])

    if structured is None:
        structured = _fallback_structured_content(transcript_data)
    else:
        structured["fallback"] = False

    structured["title"] = transcript_data.get("title", "") or "Elders Meeting"
    structured["date_display"] = _format_meeting_date(transcript_data.get("date"))
    return structured


def _send_html_email(to: str, subject: str, html: str, plain: str) -> None:
    if not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Watson <{FROM_ADDR}>"
    msg["To"]      = to
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(FROM_ADDR, [to], msg.as_string())


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


def _save_pending(meeting_id: str, title: str, meeting_date: str, structured: dict,
                   elders: list[tuple[str, str]]) -> int:
    """`draft` now stores the structured-content JSON (not final text) so
    resolve_send_by_id() can re-render the exact same content, with the
    PREVIEW banner removed, on approval."""
    _init_table()
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO fireflies_review_pending
               (meeting_id, title, meeting_date, draft, recipients)
               VALUES (?, ?, ?, ?, ?)""",
            (meeting_id, title, meeting_date, json.dumps(structured), json.dumps(elders)),
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
    is_elders_meeting() check → structured content via Ollama → render HTML
    → email Bill a live preview → Telegram approval prompt with resolved
    recipient list. Returns {"ok": bool, "msg": str}."""
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

    structured = draft_review_email(transcript_data)
    date_display = structured["date_display"]

    preview_subject, preview_html = render_elder_review_email(structured, preview=True)
    preview_plain = render_elder_review_plain(structured)
    try:
        _send_html_email(BILL_PREVIEW_EMAIL, preview_subject, preview_html, preview_plain)
    except Exception as exc:
        msg = f"Could not send preview email for meeting_id={meeting_id}: {exc}"
        log.error(msg)
        return {"ok": False, "msg": msg}

    recipient_lines = "\n".join(f"• {name} <{email}>" for name, email in elders)
    text = (
        f"📋 Elders Meeting Review draft ready — {title} ({date_display})\n\n"
        f"Preview email sent to {BILL_PREVIEW_EMAIL} — check it, then reply:\n\n"
        f"Recipients ({len(elders)}):\n{recipient_lines}\n\n"
        f"• go — send to all recipients above\n"
        f"• cancel — discard, no emails sent"
    )

    result = _send_telegram(text)
    tg_msg_id = result.get("message_id") if result else None
    if not tg_msg_id:
        msg = f"Could not send Telegram approval message for meeting_id={meeting_id}."
        log.error(msg)
        return {"ok": False, "msg": msg}

    record_id = _save_pending(meeting_id, title, date_display, structured, elders)
    try:
        store_pending_action("fireflies_review", tg_msg_id, {"record_id": record_id})
    except Exception as exc:
        log.warning("Failed to store tg_pending_action for fireflies review: %s", exc)

    return {
        "ok": True,
        "msg": f"Draft sent for approval — {title} ({date_display}). Preview emailed to {BILL_PREVIEW_EMAIL}.",
    }


# ── Resolution functions (called from bot.py reply-threading dispatch) ──────

def resolve_send_by_id(record_id: int) -> dict:
    """Render the same structured content again (no PREVIEW banner) and send
    it to every resolved elder recipient."""
    record = _get_pending_by_id(record_id)
    if not record:
        return {"ok": False, "msg": "No pending Fireflies review found."}

    elders = json.loads(record["recipients"] or "[]")
    if not elders:
        _mark_resolved(record["id"], "sent_none")
        return {"ok": False, "msg": "No recipients on this review — nothing sent."}

    try:
        structured = json.loads(record["draft"] or "{}")
    except (json.JSONDecodeError, TypeError):
        _mark_resolved(record["id"], "send_failed")
        return {"ok": False, "msg": "Could not read the saved draft — nothing sent."}

    subject, html = render_elder_review_email(structured, preview=False)
    plain = render_elder_review_plain(structured)

    sent, failed = 0, []
    for name, email in elders:
        try:
            _send_html_email(email, subject, html, plain)
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
