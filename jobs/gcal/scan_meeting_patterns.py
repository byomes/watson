#!/usr/bin/env python3
"""
scan_meeting_patterns.py — Scans the next 14 days of calendar events for
meeting-title prefixes not yet recognized by pre_meeting_brief.py, and asks
Dr. Bill via Telegram whether each new prefix should trigger pre-meeting
briefs going forward.

Cron: 30 6 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.gcal.scan_meeting_patterns >> /home/billyomes/watson/logs/scan_meeting_patterns.log 2>&1
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import sqlite3

import requests
from dotenv import load_dotenv

from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID
from core.vacation import vacation_gate
from jobs.gcal.gcal_service import get_service
from jobs.gcal.pre_meeting_brief import _BRIEF_BLOCKLIST

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

WATSON_DB   = BASE_DIR / "data" / "watson.db"
LOG_PATH    = BASE_DIR / "logs" / "scan_meeting_patterns.log"
CALENDAR_ID = "bill.yomes@gmail.com"
NY = ZoneInfo("America/New_York")
SCAN_DAYS = 14

log = logging.getLogger(__name__)


# ── DB ──────────────────────────────────────────────────────────────────────

def _ensure_table(conn: sqlite3.Connection) -> None:
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


def _known_prefixes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT prefix FROM meeting_type_patterns").fetchall()
    return {r[0] for r in rows}


# ── Calendar ──────────────────────────────────────────────────────────────────

def _get_upcoming_events() -> list[dict]:
    svc = get_service()
    now = datetime.now(NY)
    result = svc.events().list(
        calendarId=CALENDAR_ID,
        timeMin=now.isoformat(),
        timeMax=(now + timedelta(days=SCAN_DAYS)).isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return result.get("items", [])


def _extract_prefix(summary: str) -> str | None:
    if ": " not in summary:
        return None
    return summary.split(": ", 1)[0] + ": "


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_prompt(prefix: str, summary: str, pattern_id: int) -> None:
    if vacation_gate("normal", "jobs.gcal.scan_meeting_patterns", summary):
        return
    text = (
        f"New meeting title pattern found: '{prefix}' "
        f"(e.g. from event '{summary}'). "
        f"Treat this as a meeting requiring a pre-meeting brief?"
    )
    payload = {
        "chat_id": WATSON_CHAT_ID,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "Approve", "callback_data": f"mtp_approve:{pattern_id}"},
                {"text": "Reject",  "callback_data": f"mtp_reject:{pattern_id}"},
            ]]
        },
    }
    requests.post(
        f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=10,
    ).raise_for_status()


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.error("WATSON_BOT_TOKEN and WATSON_CHAT_ID must be set.")
        return

    events = _get_upcoming_events()
    log.info("Fetched %d event(s) in next %d days.", len(events), SCAN_DAYS)

    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)

    try:
        known = _known_prefixes(conn)
        seen_this_run: set[str] = set()

        for event in events:
            summary = (event.get("summary") or "").strip()
            if not summary:
                continue
            if any(blocked.lower() in summary.lower() for blocked in _BRIEF_BLOCKLIST):
                continue

            prefix = _extract_prefix(summary)
            if not prefix:
                continue
            if prefix in known or prefix in seen_this_run:
                continue

            seen_this_run.add(prefix)
            cur = conn.execute(
                "INSERT INTO meeting_type_patterns (prefix, status) VALUES (?, 'pending')",
                (prefix,),
            )
            conn.commit()
            pattern_id = cur.lastrowid

            try:
                _send_prompt(prefix, summary, pattern_id)
                log.info("Sent approval prompt for new prefix %r (id=%d).", prefix, pattern_id)
            except Exception as exc:
                log.error("Failed to send prompt for prefix %r: %s", prefix, exc)
    finally:
        conn.close()


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
