"""
fireflies_review.py — Fireflies.ai -> elder meeting review pipeline.

Triggered by POST /api/fireflies/webhook (jobs/dashboard/app.py) on
payload["event"] == "Transcription completed" (unconfirmed real value —
see TODO in app.py's webhook route), or manually via the `fireflies:
<meeting_id>` Telegram directive (bot/bot.py). Both callers run the same
process_meeting() pipeline: fetch the transcript, check whether it's an
elders meeting, get structured content (summary points + per-item action
items with a fuzzy-matched owner guess) via Ollama, save it to
meeting_reviews / meeting_review_action_items (watson.db) with
status='pending_review', and notify Bill via Telegram with a dashboard
review link — jobs/dashboard/app.py's /meet/review/<id> page.

Fireflies frequently misattributes action items (confirmed: Bill's own
tasks were dropped and reassigned to other elders in a real test), so
nothing reaches elders without Bill reviewing and correcting assignments
in the dashboard first. This replaces the earlier Telegram go/cancel draft
approval — send_html_email() and get_elder_emails() below are now called
from app.py's /api/meet/review/<id>/preview and /send routes, not from
this module.

Env vars (~/watson/.env):
  FIREFLIES_API_KEY        Bearer token for api.fireflies.ai/graphql
  FIREFLIES_WEBHOOK_SECRET HMAC-SHA256 secret for webhook signature verification
"""
import json
import logging
import os
import re
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate

load_dotenv(os.path.expanduser("~/watson/.env"))

log = logging.getLogger(__name__)

FIREFLIES_API_KEY     = os.getenv("FIREFLIES_API_KEY", "")
FIREFLIES_GRAPHQL_URL = "https://api.fireflies.ai/graphql"

CONG_DB_PATH = os.path.expanduser("~/watson/data/congregation.db")
WATSON_DB_PATH = os.path.expanduser("~/watson/data/watson.db")

WATSON_API_URL = os.getenv("WATSON_API_URL", "https://watson.tail0243ff.ts.net")
BILL_PREVIEW_EMAIL = "pastorbill@catalyst302.com"

# SMTP creds — same source as jobs/email_job/gmail.py's send_as_watson(), but
# sent via a direct MIMEMultipart build (matching jobs/connect_cards/
# state_of_church.py's send_report()) instead of calling send_as_watson()
# itself: send_as_watson() does a "\n" -> "<br>" replace on its body and
# wraps the result in a SECOND <html><body> shell, which would double-wrap
# and mangle a fully pre-rendered HTML template. Used by app.py's
# /api/meet/review/<id>/preview and /send routes.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("WATSON_GMAIL_ADDRESS", "")
SMTP_PASS = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")
FROM_ADDR = os.getenv("WATSON_FROM_ADDRESS") or SMTP_USER

# Matches the qwen2.5:7b call pattern used in jobs/pastoral_notes/handler.py
# and jobs/email_job/draft_email.py — qwen2.5:14b was retired from the
# Beelink's automated job loop (too heavy, starved concurrent Ollama calls).
_OLLAMA_URL   = "http://localhost:11434/api/generate"
_OLLAMA_MODEL = "qwen2.5:7b"

# Fuzzy-match tuning for pre-filling owner_member_id from Fireflies' owner
# guess, against the fixed 8-person ELDER_REVIEW_OWNERS pool. Verified by
# hand against realistic guesses (bare first names, "Dr Bill", "Pastor
# Bill", Fireflies' actual "Boucher"/"Bouchar" misspellings of Jim Bouchat):
# fuzz.partial_ratio scores a bare "Bill" as an exact 100/100 tie between
# Dr. Bill Yomes and Bill Crook — genuinely ambiguous, not a bug — so a
# minimum score alone isn't safe. _OWNER_MATCH_MIN_GAP requires the best
# candidate to clearly beat the runner-up before auto-matching; a close
# call is left Unassigned for Bill to pick, which is the safe failure mode
# for exactly the kind of misattribution (Bill's own tasks going to the
# wrong person) this whole feature exists to prevent. This is only a
# pre-fill suggestion either way — Bill can override anything in the
# dashboard review UI.
_OWNER_MATCH_THRESHOLD = 75
_OWNER_MATCH_MIN_GAP = 10

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
    with a non-blank email. Reused by app.py's /api/meet/review/<id>/send."""
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


def get_active_members() -> list[dict]:
    """All active congregation.db members (id, name). No longer used for the
    elder-review owner dropdown/fuzzy-match (see get_review_owners()) — kept
    intact in case anything else needs the full active-member list."""
    conn = sqlite3.connect(CONG_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name FROM members WHERE member_status = 'active' ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# The only 8 people the elder-meeting review dashboard may assign action
# items to. Fireflies frequently misattributes items, so the assignable
# pool is deliberately restricted to a small, curated list rather than
# every active congregation.db member. Resolved by hand against
# team_members and congregation.db's members table (team_members preferred
# where a person has both — that's also what jobs/dashboard/app.py's
# meet_review_send() task auto-creation needs): Dr. Bill, Donna, Melanie,
# Tyler, Lucie, and Tara all have a team_members row; Bill Crook and Jim
# Bouchat exist only in congregation.db. Jim Bouchat is frequently
# misspelled "Boucher"/"Bouchar" by Fireflies' transcription — this list
# uses the correct congregation.db spelling regardless of what a transcript
# calls him.
#
# "id" is only unique WITHIN this 8-entry list, not globally — it's a
# team_members id for 6 entries and a congregation.db members id for 2
# (no table-discriminator column on meeting_review_action_items.
# owner_member_id). The assertion below guarantees that stays collision-free
# for this specific list; if a future edit to ELDER_REVIEW_OWNERS ever
# introduces a same-id collision across the two tables, this fails loudly
# at import time instead of silently resolving the wrong person.
ELDER_REVIEW_OWNERS = [
    {"display_name": "Dr. Bill Yomes", "table": "team_members", "id": 12},
    {"display_name": "Donna Redman",   "table": "team_members", "id": 2},
    {"display_name": "Melanie Yomes",  "table": "team_members", "id": 4},
    {"display_name": "Tyler McCauley", "table": "team_members", "id": 5},
    {"display_name": "Lucie Hale",     "table": "team_members", "id": 6},
    {"display_name": "Tara Mathena",   "table": "team_members", "id": 7},
    {"display_name": "Bill Crook",     "table": "members",      "id": 96},
    {"display_name": "Jim Bouchat",    "table": "members",      "id": 3},
]
assert len({o["id"] for o in ELDER_REVIEW_OWNERS}) == len(ELDER_REVIEW_OWNERS), \
    "ELDER_REVIEW_OWNERS has a same-id collision across team_members/congregation.db"


def get_review_owners() -> list[dict]:
    """The fixed 8-person owner list for the elder-review dropdown and
    fuzzy-match pre-fill, shaped as {"id", "name"} for drop-in
    compatibility with the old get_active_members()-shaped callers."""
    return [{"id": o["id"], "name": o["display_name"]} for o in ELDER_REVIEW_OWNERS]


def get_member_name(member_id: int | None) -> str | None:
    """Look up a single congregation.db member's name by id — used to
    render the final owner name for an action item whose owner_member_id
    was set (fuzzy-matched or picked by Bill in the dashboard)."""
    if not member_id:
        return None
    conn = sqlite3.connect(CONG_DB_PATH)
    try:
        row = conn.execute("SELECT name FROM members WHERE id = ?", (member_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _match_owner_member_id(owner_guess: str, candidates: list[dict]) -> int | None:
    """Best-effort match of Fireflies' owner guess against the fixed
    ELDER_REVIEW_OWNERS candidate pool (see get_review_owners()). Uses
    fuzz.partial_ratio rather than token_sort_ratio: transcripts usually
    give a bare first name or informal address ("Bill", "Pastor Bill"), and
    partial_ratio (best matching substring) is far better calibrated for a
    short guess against "First Last"-shaped candidate names —
    token_sort_ratio scored several genuinely correct single-first-name
    matches (Donna, Tyler, Tara, Jim) below any usable threshold when this
    was checked by hand. Requires both a minimum score and a minimum gap
    over the runner-up (see _OWNER_MATCH_MIN_GAP) — always just a pre-fill
    suggestion, Bill can override anything in the dashboard review UI."""
    guess = (owner_guess or "").strip()
    if not guess or guess.lower() == "unassigned":
        return None
    scored = sorted(
        ((fuzz.partial_ratio(guess, c["name"]), c["id"]) for c in candidates),
        reverse=True,
    )
    if not scored:
        return None
    best_score, best_id = scored[0]
    if best_score < _OWNER_MATCH_THRESHOLD:
        return None
    if len(scored) > 1 and (best_score - scored[1][0]) < _OWNER_MATCH_MIN_GAP:
        return None
    return best_id


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
    # enough for a full meeting transcript: qwen2.5:7b on the Beelink's
    # CPU-bound inference is slow on large context (same known slowness
    # documented for qwen2.5-coder:7b in KB search). 300s (5 min) gives
    # headroom; if that's still not enough, the next step is chunking/staged
    # summarization rather than raising the timeout further.
    resp = requests.post(
        _OLLAMA_URL,
        json={"model": _OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


# Fireflies' own summary.action_items field is NOT a JSON list/array (that
# was the working assumption before checking a real payload) — it's a
# single markdown-ish string, already grouped by owner via a bold header
# line per person, one action item per line below it, e.g.:
#   **Jim Boucher**
#   Send the sabbatical plan document to Chipwood for review (05:32)
#   Follow up with Donna to confirm school supply needs (11:09)
#
#   **Bill Crook**
#   Contact the company that installed the sanctuary screens (18:21)
# Confirmed against a real elders meeting (meeting_id
# 01KXGCN2PA0R4QXN7W4800FXY9): 15 items across 3 groups, matching Fireflies'
# own recap count exactly. _parse_fireflies_action_items() below splits this
# into individual {"owner_guess", "text"} items with a plain regex — no
# Ollama involved — and is used both as the fallback path (see
# _fallback_structured_content()) and as the raw material fed to Ollama
# (see _build_structured_prompt()), replacing the old approach of sending
# the full raw transcript and asking Ollama to re-derive this structure
# from scratch.
_OWNER_HEADER_RE = re.compile(r"^\*\*(.+?)\*\*$")


def _parse_fireflies_action_items(raw: str) -> list[dict]:
    """Split Fireflies' action_items text into individual
    {"owner_guess", "text"} items. Text before the first bold header (if
    any) is attributed to "Unassigned" rather than dropped."""
    raw = (raw or "").strip()
    if not raw:
        return []
    items: list[dict] = []
    current_owner = "Unassigned"
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _OWNER_HEADER_RE.match(line)
        if m:
            current_owner = m.group(1).strip()
            continue
        items.append({"owner_guess": current_owner, "text": line})
    return items


_STRUCTURED_JSON_INSTRUCTIONS = (
    "You are drafting structured content for an elders meeting review for "
    "Catalyst Community Church leadership. Return ONLY a JSON object — no prose, "
    "no markdown code fences, nothing before or after the JSON — matching exactly "
    "this shape:\n"
    '{"summary_points": ["point 1", "point 2", ...], '
    '"action_items": [{"owner_guess": "Name or Unassigned", "text": "item text"}]}\n\n'
    "Guidelines: summary_points should be 3-6 concise bullet points capturing key "
    "discussion and decisions, based on the meeting summary below. The action items "
    "below are already grouped by owner under a bold name — convert each one into "
    "its own action_items entry, using the name above it as owner_guess (or "
    "\"Unassigned\" for items with no name above them). Keep item text as-is, one "
    "action_items entry per item — do not merge or group them."
)


def _build_structured_prompt(transcript_data: dict) -> str:
    # Feeds Ollama Fireflies' own condensed overview + action_items text
    # (already organized, ~2-3k chars combined on a real meeting) instead
    # of the full raw transcript (~37k chars on that same meeting, capped
    # at 8000 here previously). Every real production run timed out at
    # both 60s and 300s sending the raw-transcript version — confirmed via
    # journalctl, always a plain socket read timeout, never actual
    # malformed JSON — because asking a CPU-bound 14B model to both
    # comprehend raw dialogue AND derive structure from it in one call is
    # much slower than asking it to reformat text Fireflies has already
    # organized. Ollama's job here is reformatting, not comprehension.
    overview = transcript_data.get("overview", "") or "(none provided)"
    action_items_raw = transcript_data.get("action_items", "") or "(none provided)"
    return (
        f"{_STRUCTURED_JSON_INSTRUCTIONS}\n\n"
        f"Meeting title: {transcript_data.get('title', '')}\n"
        f"Attendees: {', '.join(transcript_data.get('attendees') or []) or '(not provided)'}\n\n"
        f"Meeting summary:\n{overview}\n\n"
        f"Action items (grouped by owner):\n{action_items_raw}"
    )


def _build_retry_prompt(transcript_data: dict) -> str:
    """Second-attempt prompt — shorter and more blunt than the primary
    prompt, per the fix spec: a different, simpler prompt on retry rather
    than repeating the same one verbatim. Same source content (overview +
    action_items), since that content itself isn't the problem."""
    overview = transcript_data.get("overview", "") or "(none provided)"
    action_items_raw = transcript_data.get("action_items", "") or "(none provided)"
    return (
        "Return ONLY this JSON — no explanation, no markdown fences, nothing else:\n"
        '{"summary_points": ["..."], "action_items": [{"owner_guess": "...", "text": "..."}]}\n\n'
        f"Turn this summary into 3-6 summary_points bullets:\n{overview}\n\n"
        "Turn each line below into one action_items entry — use the bold name "
        f"above each group as owner_guess:\n{action_items_raw}"
    )


def _parse_structured_json(raw: str) -> dict | None:
    """Extract and validate {"summary_points": [...], "action_items":
    [{"owner_guess": ..., "text": ...}]} from Ollama's raw response. Returns
    None on any parse/shape failure — callers decide whether to retry or
    fall back."""
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
    if not all(isinstance(it, dict) and it.get("text") for it in action_items):
        return None

    return {
        "summary_points": [str(p) for p in summary_points],
        "action_items": [
            {"owner_guess": str(it.get("owner_guess") or "Unassigned"), "text": str(it.get("text"))}
            for it in action_items
        ],
    }


def _fallback_structured_content(transcript_data: dict) -> dict:
    """Basic version built directly from Fireflies' own data, used when
    Ollama's structured JSON fails entirely (both attempts) — never silent,
    so a marker note is prepended to summary_points and logged. Critically,
    this must never collapse all action items into one entry: parses
    Fireflies' action_items text into individual items via
    _parse_fireflies_action_items() rather than dumping the whole string
    into a single {"owner_guess": "Unassigned", "text": <everything>} blob,
    so Bill always gets real, individually reviewable items — even in the
    worst case where Ollama is completely unavailable."""
    overview = (transcript_data.get("overview") or "").strip()
    summary_points = ["[Auto-formatting failed — review this draft carefully]"]
    if overview:
        summary_points.append(overview)
    action_items = _parse_fireflies_action_items(transcript_data.get("action_items"))
    return {"summary_points": summary_points, "action_items": action_items}


def draft_review_email(transcript_data: dict) -> dict:
    """Get structured content (summary points + per-item action items with
    an owner guess) for a meeting via Ollama — NOT final formatted text/
    HTML. Tries a primary prompt, then a shorter/blunter retry prompt on
    ANY failure (a request exception/timeout, or a response that doesn't
    parse as valid structured JSON) — the previous version only retried on
    malformed JSON and gave up immediately on a timeout, which was the
    actual failure mode in every real run so far. Falls back to a basic,
    clearly-marked version built straight from Fireflies' own data if both
    attempts fail. Bill reviews and can edit any of this in the dashboard
    before anything is sent."""
    prompts = [_build_structured_prompt(transcript_data), _build_retry_prompt(transcript_data)]

    structured = None
    for attempt, prompt in enumerate(prompts, start=1):
        try:
            raw = _ollama_generate(prompt)
        except Exception as exc:
            log.warning("draft_review_email Ollama call failed (attempt %d): %s", attempt, exc)
            continue
        structured = _parse_structured_json(raw)
        if structured is not None:
            break
        log.warning("draft_review_email got malformed JSON (attempt %d): %r", attempt, raw[:300])

    if structured is None:
        structured = _fallback_structured_content(transcript_data)

    structured["title"] = transcript_data.get("title", "") or "Elders Meeting"
    structured["date_display"] = _format_meeting_date(transcript_data.get("date"))
    return structured


def send_html_email(to: str, subject: str, html: str, plain: str) -> None:
    """Send a fully pre-rendered HTML email. Called from app.py's
    /api/meet/review/<id>/preview and /send routes."""
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


# ── meeting_reviews / meeting_review_action_items persistence (watson.db) ───

def _get_conn():
    conn = sqlite3.connect(WATSON_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_tables() -> None:
    """Safety-net self-bootstrap matching this codebase's usual defensive
    style — the real onboarding path is jobs/meet/migrate_meeting_reviews.py
    (with a watson.db backup), but CREATE TABLE IF NOT EXISTS here means a
    fresh environment that skipped the migration still works."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meeting_reviews (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                fireflies_meeting_id TEXT NOT NULL,
                title                TEXT,
                meeting_date         TEXT,
                summary_text         TEXT,
                status               TEXT NOT NULL DEFAULT 'pending_review',
                created_at           TEXT NOT NULL DEFAULT (datetime('now')),
                sent_at              TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meeting_review_action_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id       INTEGER NOT NULL REFERENCES meeting_reviews(id),
                owner_text      TEXT,
                owner_member_id INTEGER,
                item_text       TEXT NOT NULL,
                sort_order      INTEGER NOT NULL DEFAULT 0
            )
        """)


def _save_review(meeting_id: str, structured: dict, review_owners: list[dict]) -> int:
    _init_tables()
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO meeting_reviews
               (fireflies_meeting_id, title, meeting_date, summary_text, status)
               VALUES (?, ?, ?, ?, 'pending_review')""",
            (
                meeting_id,
                structured.get("title", ""),
                structured.get("date_display", ""),
                "\n".join(structured.get("summary_points") or []),
            ),
        )
        review_id = cur.lastrowid
        for i, item in enumerate(structured.get("action_items") or []):
            owner_guess = item.get("owner_guess") or "Unassigned"
            owner_member_id = _match_owner_member_id(owner_guess, review_owners)
            conn.execute(
                """INSERT INTO meeting_review_action_items
                   (review_id, owner_text, owner_member_id, item_text, sort_order)
                   VALUES (?, ?, ?, ?, ?)""",
                (review_id, owner_guess, owner_member_id, item.get("text", ""), i),
            )
        return review_id


# ── Entry point (shared by the webhook route and the `fireflies:` Telegram
#    directive — the only difference between those two callers is how
#    meeting_id gets in and what they do with the returned result: the
#    webhook logs it and stays silent on skips; the manual trigger always
#    relays result["msg"] back to Bill, since a silent no-op on an explicit
#    manual request would be confusing) ───────────────────────────────────

def process_meeting(meeting_id: str) -> dict:
    """Run the full pipeline for a single meeting: fetch transcript →
    is_elders_meeting() check → structured content via Ollama → fuzzy-match
    each action item's owner → save as a pending_review record → Telegram
    notice with a dashboard review link. Returns {"ok": bool, "msg": str}.
    Nothing is emailed to anyone here — that only happens from the
    dashboard review page, after Bill has reviewed and corrected
    assignments."""
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

    review_owners = get_review_owners()
    review_id = _save_review(meeting_id, structured, review_owners)

    n_items = len(structured.get("action_items") or [])
    dashboard_url = f"{WATSON_API_URL}/meet/review/{review_id}"
    text = (
        f"📋 Meeting review ready: {title} ({date_display}) — "
        f"{n_items} action item{'s' if n_items != 1 else ''} need your review.\n{dashboard_url}"
    )
    _send_telegram(text)

    return {
        "ok": True,
        "msg": f"Review saved — {n_items} action item(s) to review. {dashboard_url}",
    }
