#!/usr/bin/env python3
"""
pre_meeting.py — Send a team member brief to Telegram 30 minutes before
any calendar event whose title contains a team member's name.

Cron: */5 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/team/pre_meeting.py >> /home/billyomes/watson/logs/team_pre_meeting.log 2>&1
"""
import logging
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from jobs.gcal.gcal_service import get_service
from core.vacation import vacation_gate

BASE_DIR  = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

WATSON_DB   = BASE_DIR / "data" / "watson.db"
LOG_PATH    = BASE_DIR / "logs" / "team_pre_meeting.log"
CALENDAR_ID = "bill.yomes@gmail.com"
NY          = ZoneInfo("America/New_York")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("WATSON_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")   or os.getenv("WATSON_CHAT_ID", "")

log = logging.getLogger(__name__)


def _send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.team.pre_meeting", text):
        return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    ).raise_for_status()


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


def _already_briefed(conn: sqlite3.Connection, member_id: int) -> bool:
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT id FROM team_meetings WHERE member_id=? AND date=?",
        (member_id, today)
    ).fetchone()
    if row:
        return True
    # Also check a briefed_team_events dedup table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS briefed_team_events (
            event_id   TEXT PRIMARY KEY,
            briefed_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return False


def _already_event_briefed(conn: sqlite3.Connection, event_id: str) -> bool:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS briefed_team_events (
            event_id   TEXT PRIMARY KEY,
            briefed_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn.execute(
        "SELECT 1 FROM briefed_team_events WHERE event_id=?", (event_id,)
    ).fetchone() is not None


def _mark_event_briefed(conn: sqlite3.Connection, event_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO briefed_team_events (event_id) VALUES (?)", (event_id,))
    conn.commit()


def _load_team_members(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, role, ministry FROM team_members WHERE active=1"
    ).fetchall()
    return [dict(r) for r in rows]


def _match_member_in_title(title: str, members: list[dict]) -> dict | None:
    title_lower = title.lower()
    words = re.findall(r"[a-z]+", title_lower)
    for member in members:
        name_parts = member["name"].lower().split()
        first = name_parts[0] if name_parts else ""
        last  = name_parts[-1] if len(name_parts) > 1 else ""
        if first and last and first in words and last in words:
            return member
        if first and len(first) > 3 and first in words:
            return member
    return None


def _load_profile(conn: sqlite3.Connection, member_id: int) -> dict:
    objectives = conn.execute(
        "SELECT title FROM team_objectives WHERE member_id=? ORDER BY created_at DESC",
        (member_id,)
    ).fetchall()
    goals = conn.execute(
        "SELECT title, target_date, status FROM team_goals WHERE member_id=? AND status='active' ORDER BY target_date ASC",
        (member_id,)
    ).fetchall()
    tasks = conn.execute(
        "SELECT title, due_date FROM team_tasks WHERE member_id=? AND status='open' ORDER BY due_date ASC",
        (member_id,)
    ).fetchall()
    last_meeting = conn.execute(
        "SELECT date, SUBSTR(summary,1,400) AS summary FROM team_meetings "
        "WHERE member_id=? ORDER BY date DESC LIMIT 1",
        (member_id,)
    ).fetchone()
    unanswered = conn.execute(
        "SELECT COUNT(*) FROM team_messages WHERE member_id=? AND direction='out' AND replied_at IS NULL",
        (member_id,)
    ).fetchone()[0]
    return {
        "objectives": [r["title"] for r in objectives],
        "goals":      [dict(r) for r in goals],
        "tasks":      [dict(r) for r in tasks],
        "last_meeting": dict(last_meeting) if last_meeting else None,
        "unanswered": unanswered,
    }


def _build_brief(member: dict, profile: dict) -> str:
    today = date.today().isoformat()
    tasks       = profile["tasks"]
    overdue     = [t for t in tasks if t["due_date"] and t["due_date"] < today]
    not_overdue = [t for t in tasks if not t["due_date"] or t["due_date"] >= today]

    lines = [
        f"📋 <b>Pre-meeting brief — {member['name']}</b> ({member.get('role','')}, {member.get('ministry','')})",
    ]

    if profile["objectives"]:
        lines.append("\n🎯 <b>Objectives:</b>")
        for obj in profile["objectives"]:
            lines.append(f"  • {obj}")

    if profile["goals"]:
        lines.append("\n📌 <b>Active goals:</b>")
        for g in profile["goals"]:
            td = f" (by {g['target_date']})" if g.get("target_date") else ""
            lines.append(f"  • {g['title']}{td}")

    task_count = len(tasks)
    lines.append(f"\n✅ <b>Open tasks ({task_count}):</b>")
    if overdue:
        for t in overdue:
            lines.append(f"  🔴 {t['title']} (due {t['due_date']})")
    for t in not_overdue:
        due = f" (due {t['due_date']})" if t.get("due_date") else ""
        lines.append(f"  • {t['title']}{due}")
    if not tasks:
        lines.append("  None")

    if profile["last_meeting"]:
        lm = profile["last_meeting"]
        lines.append(f"\n📝 <b>Last meeting ({lm['date']}):</b>")
        lines.append(f"  {(lm['summary'] or '').strip()}")
    else:
        lines.append("\n📝 <b>Last meeting:</b> None on file")

    ua = profile["unanswered"]
    lines.append(f"\n📬 <b>Unanswered emails:</b> {ua if ua else 'None'}")

    return "\n".join(lines)


def run() -> None:
    now         = datetime.now(NY)
    window_open = now + timedelta(minutes=25)
    window_close= now + timedelta(minutes=35)

    events = _get_upcoming_events()
    log.info("Fetched %d event(s) in next 60 minutes.", len(events))

    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row
    members = _load_team_members(conn)

    if not members:
        log.info("No active team members — nothing to match.")
        conn.close()
        return

    try:
        for event in events:
            title = event.get("summary", "")
            if not title:
                continue

            start_str = event["start"].get("dateTime")
            if not start_str:
                continue
            start_dt = datetime.fromisoformat(start_str).astimezone(NY)
            if not (window_open <= start_dt <= window_close):
                continue

            event_id = event["id"]
            if _already_event_briefed(conn, event_id):
                log.info("Already briefed event %s — skipping.", event_id)
                continue

            member = _match_member_in_title(title, members)
            if not member:
                continue

            log.info("Matched team member %s in event '%s'.", member["name"], title)
            profile = _load_profile(conn, member["id"])
            brief   = _build_brief(member, profile)

            try:
                _send_telegram(brief)
                _mark_event_briefed(conn, event_id)
                log.info("Team brief sent for %s.", member["name"])
            except Exception as exc:
                log.error("Failed to send team brief for %s: %s", member["name"], exc)
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
