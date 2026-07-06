#!/usr/bin/env python3
"""
pre_meeting_brief.py — Sends a pastoral brief to Telegram 30 minutes before
any VA: or IP: appointment on bill.yomes@gmail.com calendar.

Cron: */5 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.gcal.pre_meeting_brief >> /home/billyomes/watson/logs/pre_meeting_brief.log 2>&1
"""

import difflib
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from jobs.gcal.gcal_service import get_service
from core.vacation import vacation_gate

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

CONG_DB   = BASE_DIR / "data" / "congregation.db"
WATSON_DB = BASE_DIR / "data" / "watson.db"
LOG_PATH  = BASE_DIR / "logs" / "pre_meeting_brief.log"
CALENDAR_ID = "bill.yomes@gmail.com"
NY = ZoneInfo("America/New_York")
FUZZY_THRESHOLD = 0.75

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("WATSON_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")   or os.getenv("WATSON_CHAT_ID", "")

# Calendar blocks whose titles contain any of these substrings must never trigger a brief.
_BRIEF_BLOCKLIST = [
    "Sermon Completion",
    "Sermon Prep",
    "Deep Work",
    "Sabbath",
    "Block",
    "Hold",
    "Admin",
]

log = logging.getLogger(__name__)


# ── Dedup ─────────────────────────────────────────────────────────────────────

def _ensure_briefed_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS briefed_events (
            event_id   TEXT PRIMARY KEY,
            briefed_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _already_briefed(conn: sqlite3.Connection, event_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM briefed_events WHERE event_id = ?", (event_id,)
    ).fetchone() is not None


def _mark_briefed(conn: sqlite3.Connection, event_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO briefed_events (event_id) VALUES (?)", (event_id,)
    )
    conn.commit()


# ── Recognized meeting prefixes (Bill-approved via scan_meeting_patterns.py) ──

def _ensure_meeting_type_patterns_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meeting_type_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prefix TEXT UNIQUE NOT NULL,
            label TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT
        )
    """)
    conn.commit()


def _get_approved_prefixes(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Load Bill-approved prefixes once per run — (prefix, label) pairs."""
    rows = conn.execute(
        "SELECT prefix, label FROM meeting_type_patterns WHERE status = 'approved'"
    ).fetchall()
    return [(r["prefix"], r["label"] or r["prefix"].rstrip(": ").strip()) for r in rows]


def _match_prefix(summary: str, approved_prefixes: list[tuple[str, str]]) -> tuple[str, str] | None:
    for prefix, label in approved_prefixes:
        if summary.startswith(prefix):
            return prefix, label
    return None


# ── Calendar ──────────────────────────────────────────────────────────────────

def _get_upcoming_events() -> list[dict]:
    svc = get_service()
    now = datetime.now(NY)
    result = svc.events().list(
        calendarId=CALENDAR_ID,
        timeMin=now.isoformat(),
        timeMax=(now + timedelta(minutes=60)).isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return result.get("items", [])


# ── Congregation lookup ───────────────────────────────────────────────────────

def _lookup_member(name: str) -> dict | None:
    if not CONG_DB.exists():
        return None
    conn = sqlite3.connect(CONG_DB)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        "SELECT * FROM members WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    if row:
        member = dict(row)
        member["_match"] = "exact"
        conn.close()
        return member

    all_members = conn.execute(
        "SELECT id, name FROM members WHERE active = 1"
    ).fetchall()
    best_ratio, best_id = 0.0, None
    for mid, mname in all_members:
        ratio = difflib.SequenceMatcher(None, name.lower(), (mname or "").lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best_id = ratio, mid

    if best_id and best_ratio >= FUZZY_THRESHOLD:
        row = conn.execute("SELECT * FROM members WHERE id = ?", (best_id,)).fetchone()
        member = dict(row)
        member["_match"] = f"fuzzy:{best_ratio:.2f}"
        conn.close()
        return member

    conn.close()
    return None


def _get_pastoral_data(member_id: int) -> dict:
    conn = sqlite3.connect(CONG_DB)
    conn.row_factory = sqlite3.Row

    card_row = conn.execute(
        "SELECT service_date FROM connect_cards WHERE member_id = ? ORDER BY service_date DESC LIMIT 1",
        (member_id,),
    ).fetchone()
    att_row = conn.execute(
        "SELECT service_date FROM attendance WHERE member_id = ? ORDER BY service_date DESC LIMIT 1",
        (member_id,),
    ).fetchone()
    dates = [r["service_date"] for r in (card_row, att_row) if r]
    last_seen = max(dates) if dates else None

    prayer_row = conn.execute(
        "SELECT request_text FROM prayer_requests WHERE member_id = ? ORDER BY created_at DESC LIMIT 1",
        (member_id,),
    ).fetchone()

    followup_row = conn.execute(
        "SELECT note FROM follow_ups WHERE member_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 1",
        (member_id,),
    ).fetchone()

    conn.close()
    return {
        "last_seen": last_seen,
        "prayer":    prayer_row["request_text"] if prayer_row else None,
        "followup":  followup_row["note"] if followup_row else None,
    }


def _get_pastoral_note(name: str) -> str | None:
    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT note FROM pastoral_notes
           WHERE LOWER(person_name) LIKE LOWER(?)
           ORDER BY created_at DESC LIMIT 1""",
        (f"%{name}%",),
    ).fetchone()
    conn.close()
    return row["note"] if row else None


# ── Message ───────────────────────────────────────────────────────────────────

def _build_message(prefix: str, guest_name: str, start_dt: datetime,
                   location: str | None, meet_link: str | None,
                   pastoral: dict, pastoral_note: str | None,
                   match_info: str, description: str | None = None) -> str:
    loc_line = meet_link or location or "TBD"
    lines = [
        f"📅 *{prefix}: {guest_name}* in 30 minutes",
        f"🕐 {start_dt.strftime('%-I:%M %p')} — {loc_line}",
    ]
    if description:
        lines += ["", "*Meeting Context:*", description]
    lines += [
        "",
        "*Pastoral Notes:*",
        f"• Last seen: {pastoral.get('last_seen') or 'not on record'}",
        f"• Prayer: {pastoral.get('prayer') or 'none on file'}",
        f"• Follow-up: {pastoral.get('followup') or 'none'}",
        f"• Notes: {pastoral_note or 'none'}",
    ]
    if match_info not in ("exact", "no record"):
        lines.append(f"\n_(name matched: {match_info})_")
    elif match_info == "no record":
        lines.append("\n_(not found in congregation.db)_")
    return "\n".join(lines)


def _send_telegram(text: str, meet_link: str | None) -> None:
    if vacation_gate("normal", "jobs.gcal.pre_meeting_brief", text):
        return
    payload: dict = {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
    }
    if meet_link:
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "Join Google Meet", "url": meet_link}
            ]]
        }
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=10,
    ).raise_for_status()


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    now = datetime.now(NY)
    window_open  = now + timedelta(minutes=25)
    window_close = now + timedelta(minutes=35)

    events = _get_upcoming_events()
    log.info("Fetched %d event(s) in next 60 minutes.", len(events))

    watson_conn = sqlite3.connect(WATSON_DB)
    watson_conn.row_factory = sqlite3.Row
    _ensure_briefed_table(watson_conn)
    _ensure_meeting_type_patterns_table(watson_conn)
    approved_prefixes = _get_approved_prefixes(watson_conn)  # cached once per run

    try:
        for event in events:
            summary = event.get("summary", "")
            match = _match_prefix(summary, approved_prefixes)
            if not match:
                continue
            matched_prefix, prefix = match

            start_str = event["start"].get("dateTime")
            if not start_str:
                continue
            start_dt = datetime.fromisoformat(start_str).astimezone(NY)
            if not (window_open <= start_dt <= window_close):
                continue

            event_id = event["id"]
            if _already_briefed(watson_conn, event_id):
                log.info("Already briefed %s (%s) — skipping.", event_id, summary)
                continue

            guest_name        = summary[len(matched_prefix):].strip()
            event_description = (event.get("description") or "").strip() or None

            if any(blocked.lower() in summary.lower() for blocked in _BRIEF_BLOCKLIST):
                log.info("Blocked event, skipping brief: %s", summary)
                continue

            meet_link = None
            for ep in event.get("conferenceData", {}).get("entryPoints", []):
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri")
                    break
            location = event.get("location") or None

            member     = _lookup_member(guest_name)
            pastoral   = {}
            note       = None
            match_info = "no record"
            if member:
                match_info = member.get("_match", "found")
                pastoral   = _get_pastoral_data(member["id"])
                note       = _get_pastoral_note(guest_name)

            text = _build_message(prefix, guest_name, start_dt, location,
                                  meet_link, pastoral, note, match_info,
                                  event_description)
            try:
                _send_telegram(text, meet_link)
                _mark_briefed(watson_conn, event_id)
                log.info("Brief sent for %s (%s).", guest_name, event_id)
            except Exception as exc:
                log.error("Failed to send brief for %s: %s", guest_name, exc)
    finally:
        watson_conn.close()


if __name__ == "__main__":
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(),
        ],
    )
    run()
