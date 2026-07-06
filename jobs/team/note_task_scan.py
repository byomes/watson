#!/usr/bin/env python3
"""
jobs/team/note_task_scan.py

Scans shared_notes for implied tasks, deduplicates against existing team_tasks,
and emails Donna a proposed task list for approval.

Cron (Tue/Wed/Thu 7am):
  0 7 * * 2,3,4 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 /home/billyomes/watson/jobs/team/note_task_scan.py >> /home/billyomes/watson/logs/note_task_scan.log 2>&1
"""

import json
import logging
import os
import smtplib
import sqlite3
import uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate

load_dotenv(Path.home() / "watson" / ".env")

log = logging.getLogger(__name__)

DB_PATH = Path.home() / "watson" / "data" / "watson.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:14b"

STATE_KEY_LAST_RUN      = "note_task_scan_last_run"
STATE_KEY_PENDING_BATCH = "note_task_scan_pending_batch"

BILL_CC = "pastorbill@catalyst302.com"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _bootstrap(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_state (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS note_task_proposals (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id       TEXT NOT NULL,
            member_id      INTEGER NOT NULL,
            task_title     TEXT NOT NULL,
            source_note_id INTEGER,
            status         TEXT DEFAULT 'pending',
            created_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    for col in ("assigned_by TEXT", "category TEXT"):
        try:
            conn.execute(f"ALTER TABLE team_tasks ADD COLUMN {col}")
        except Exception:
            pass
    conn.commit()


def _get_state(conn, key):
    row = conn.execute("SELECT value FROM job_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(conn, key, value):
    conn.execute(
        "INSERT INTO job_state (key, value, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value),
    )
    conn.commit()


def _clear_state(conn, key):
    conn.execute("DELETE FROM job_state WHERE key=?", (key,))
    conn.commit()


def _get_donna(conn):
    row = conn.execute(
        "SELECT id, name, email FROM team_members WHERE name LIKE '%Donna%' AND active=1 LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def _get_new_notes(conn, since):
    if since:
        rows = conn.execute(
            "SELECT sn.id, sn.member_id, sn.content, sn.created_at, tm.name AS member_name "
            "FROM shared_notes sn "
            "JOIN team_members tm ON tm.id = sn.member_id "
            "WHERE sn.created_at > ? "
            "ORDER BY sn.created_at ASC",
            (since,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT sn.id, sn.member_id, sn.content, sn.created_at, tm.name AS member_name "
            "FROM shared_notes sn "
            "JOIN team_members tm ON tm.id = sn.member_id "
            "ORDER BY sn.created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Ollama ────────────────────────────────────────────────────────────────────

def _extract_tasks_ollama(member_name, note_content):
    prompt = (
        "You are Watson, Dr. Bill Yomes's assistant. Analyze this leadership note and extract "
        "any implied tasks or action items. Return JSON only, no other text.\n\n"
        "Return:\n"
        "{\n"
        '  "tasks": ["task 1", "task 2"]\n'
        "}\n\n"
        "Return empty array [] if no tasks found. Never return placeholder strings.\n\n"
        f"Note about {member_name}:\n{note_content}"
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            return []
        return [t.strip() for t in tasks if isinstance(t, str) and t.strip() and len(t.strip()) > 3]
    except Exception as exc:
        log.error("Ollama task extraction failed for %s: %s", member_name, exc)
        return []


def _parse_donna_intent(body, pending):
    task_list = "\n".join(f"- {p['task_title']}" for p in pending)
    prompt = (
        "You are Watson. Donna replied to a task proposal email. Determine her intent.\n\n"
        "Original proposed tasks:\n"
        f"{task_list}\n\n"
        "Donna's reply:\n"
        f"{body[:800]}\n\n"
        "Reply ONLY with one of: yes, no, corrections\n"
        "- yes: she approves all tasks as listed\n"
        "- no: she declines the entire batch\n"
        "- corrections: she wants to change, add, or remove specific tasks\n"
        "Return only the single word."
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip().lower()
        first = raw.split()[0] if raw.split() else ""
        if first in ("yes", "no", "corrections"):
            return first
        if any(k in raw for k in ("yes", "approve", "assign all", "looks good", "go ahead")):
            return "yes"
        if any(k in raw for k in ("no", "decline", "skip", "cancel")):
            return "no"
        return "corrections"
    except Exception as exc:
        log.error("Ollama intent classification failed: %s", exc)
        return "corrections"


def _parse_corrections(body, pending):
    original = json.dumps([
        {"member_id": p["member_id"], "task": p["task_title"]} for p in pending
    ])
    prompt = (
        "You are Watson. Donna sent corrections to a task list. Extract the corrected final task list.\n\n"
        f"Original tasks (JSON):\n{original}\n\n"
        f"Donna's corrections:\n{body[:800]}\n\n"
        "Return ONLY valid JSON, no other text:\n"
        '{"tasks": [{"member_id": 1, "task_title": "task text"}, ...]}\n\n'
        "Include all tasks that should be assigned after applying her corrections. "
        "Return empty array if no tasks should be assigned."
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        tasks = data.get("tasks", [])
        return [t for t in tasks if t.get("task_title") and t.get("member_id")]
    except Exception as exc:
        log.error("Ollama corrections parse failed: %s", exc)
        return []


# ── Deduplication ─────────────────────────────────────────────────────────────

def _is_duplicate(conn, member_id, task_title):
    title_lower = task_title.lower()
    rows = conn.execute(
        "SELECT title FROM team_tasks WHERE member_id=? AND status='open'",
        (member_id,),
    ).fetchall()
    for row in rows:
        existing = (row["title"] or "").lower()
        if title_lower in existing or existing in title_lower:
            return True
    return False


# ── Email ─────────────────────────────────────────────────────────────────────

def _send_proposal_email(donna_email, donna_name, proposals_by_member, date_str):
    smtp_user = os.getenv("WATSON_GMAIL_ADDRESS", "")
    smtp_pass = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")
    from_addr = os.getenv("WATSON_FROM_ADDRESS") or smtp_user

    subject = f"Watson — Proposed tasks from leadership notes ({date_str})"

    sections = []
    for member_name, tasks in proposals_by_member.items():
        task_lines = "\n".join(f"- {t}" for t in tasks)
        sections.append(f"{member_name}\n{task_lines}")
    task_block = "\n\n".join(sections)

    first_name = donna_name.split()[0] if donna_name else "Donna"
    body = (
        f"Hi {first_name},\n\n"
        "While reviewing recent leadership notes, I found the following potential tasks. "
        "Please reply with one of the following:\n\n"
        '- "Yes" to assign all tasks as listed\n'
        "- Corrections or edits and I'll adjust accordingly\n"
        '- "No" if you\'d like to skip this batch\n\n'
        "---\n\n"
        f"{task_block}\n\n"
        "---\n\n"
        "Acting on behalf of Dr. Bill Yomes\n"
        "Watson · AI-powered digital assistant"
    )
    body_html = body.replace("\n", "<br>")

    msg = MIMEMultipart("alternative")
    msg["To"]      = donna_email
    msg["CC"]      = BILL_CC
    msg["Subject"] = subject
    msg["From"]    = f"Watson <{from_addr}>"
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(f"<html><body>{body_html}</body></html>", "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(smtp_user, smtp_pass)
        smtp.sendmail(from_addr, [donna_email, BILL_CC], msg.as_string())

    log.info("Proposal email sent to %s (CC: %s)", donna_email, BILL_CC)


# ── Assignment helpers ────────────────────────────────────────────────────────

def _assign_all(conn, batch_id, pending):
    today = datetime.now().strftime("%Y-%m-%d")
    for p in pending:
        conn.execute(
            "INSERT INTO team_tasks (member_id, title, status, source, assigned_by, category) "
            "VALUES (?, ?, 'open', 'note_scan', 'donna', 'catalyst')",
            (p["member_id"], p["task_title"]),
        )
        conn.execute(
            "UPDATE team_members SET last_activity_date=? WHERE id=?",
            (today, p["member_id"]),
        )
    conn.execute(
        "UPDATE note_task_proposals SET status='assigned' WHERE batch_id=?", (batch_id,)
    )
    conn.commit()


def _assign_corrected(conn, batch_id, pending, corrected):
    today = datetime.now().strftime("%Y-%m-%d")
    for item in corrected:
        conn.execute(
            "INSERT INTO team_tasks (member_id, title, status, source, assigned_by, category) "
            "VALUES (?, ?, 'open', 'note_scan', 'donna', 'catalyst')",
            (item["member_id"], item["task_title"]),
        )
        conn.execute(
            "UPDATE team_members SET last_activity_date=? WHERE id=?",
            (today, item["member_id"]),
        )
    # Mark remaining pending rows as declined
    conn.execute(
        "UPDATE note_task_proposals SET status='assigned' "
        "WHERE batch_id=? AND status='pending'",
        (batch_id,),
    )
    conn.commit()


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_telegram(text):
    if vacation_gate("normal", "jobs.team.note_task_scan", text):
        return
    token   = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)


# ── Main scan ─────────────────────────────────────────────────────────────────

def run():
    conn = _conn()
    _bootstrap(conn)

    last_run = _get_state(conn, STATE_KEY_LAST_RUN)
    log.info("Last run: %s", last_run or "never")

    # Don't queue a second batch while one is still pending
    pending_batch = _get_state(conn, STATE_KEY_PENDING_BATCH)
    if pending_batch:
        log.info(
            "Pending batch %s still awaiting Donna's response — skipping scan",
            pending_batch,
        )
        conn.close()
        return

    notes = _get_new_notes(conn, last_run)
    log.info("Found %d new shared note(s) to scan", len(notes))

    if not notes:
        _set_state(conn, STATE_KEY_LAST_RUN, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        conn.close()
        return

    donna = _get_donna(conn)
    if not donna or not donna.get("email"):
        log.error("Cannot find Donna with an email in team_members — aborting")
        conn.close()
        return

    batch_id = str(uuid.uuid4())
    proposals_by_member = {}
    total_proposed = 0
    total_skipped  = 0

    for note in notes:
        member_id   = note["member_id"]
        member_name = note["member_name"]
        note_id     = note["id"]

        tasks = _extract_tasks_ollama(member_name, note["content"])
        if not tasks:
            log.info("No tasks extracted from note %d (%s)", note_id, member_name)
            continue

        for task_title in tasks:
            if _is_duplicate(conn, member_id, task_title):
                log.info("Duplicate skipped for %s: %s", member_name, task_title)
                total_skipped += 1
                continue

            conn.execute(
                "INSERT INTO note_task_proposals "
                "(batch_id, member_id, task_title, source_note_id) VALUES (?, ?, ?, ?)",
                (batch_id, member_id, task_title, note_id),
            )
            proposals_by_member.setdefault(member_name, []).append(task_title)
            total_proposed += 1

    conn.commit()
    log.info(
        "Proposed %d task(s), skipped %d duplicate(s)", total_proposed, total_skipped
    )

    if total_proposed == 0:
        log.info("No new tasks to propose — nothing to email")
        _set_state(conn, STATE_KEY_LAST_RUN, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        conn.close()
        return

    date_str = datetime.now().strftime("%B %d, %Y")
    try:
        _send_proposal_email(donna["email"], donna["name"], proposals_by_member, date_str)
    except Exception as exc:
        log.error("Failed to send proposal email: %s", exc)
        conn.close()
        return

    _set_state(conn, STATE_KEY_PENDING_BATCH, batch_id)
    _set_state(conn, STATE_KEY_LAST_RUN, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    conn.close()
    log.info("Done — batch %s stored, email sent to %s", batch_id, donna["email"])


# ── Reply handler (called by email_intake.py) ─────────────────────────────────

def handle_donna_proposal_reply(body: str) -> None:
    """Process Donna's reply to a task proposal email."""
    conn = _conn()
    _bootstrap(conn)

    batch_id = _get_state(conn, STATE_KEY_PENDING_BATCH)
    if not batch_id:
        log.info("No pending batch — ignoring Donna's reply")
        conn.close()
        return

    rows = conn.execute(
        "SELECT id, member_id, task_title FROM note_task_proposals "
        "WHERE batch_id=? AND status='pending'",
        (batch_id,),
    ).fetchall()
    pending = [dict(r) for r in rows]

    if not pending:
        log.info("No pending proposals in batch %s", batch_id)
        _clear_state(conn, STATE_KEY_PENDING_BATCH)
        conn.close()
        return

    intent = _parse_donna_intent(body, pending)
    log.info("Donna intent: %s", intent)

    if intent == "yes":
        _assign_all(conn, batch_id, pending)
        leader_count = len({p["member_id"] for p in pending})
        _send_telegram(
            f"✅ Donna approved task batch — {len(pending)} task(s) assigned "
            f"across {leader_count} leader(s)."
        )

    elif intent == "no":
        conn.execute(
            "UPDATE note_task_proposals SET status='declined' WHERE batch_id=?",
            (batch_id,),
        )
        conn.commit()
        _send_telegram("Donna declined the task proposal batch.")

    else:
        # Corrections
        corrected = _parse_corrections(body, pending)
        if corrected:
            _assign_corrected(conn, batch_id, pending, corrected)
            leader_count = len({item["member_id"] for item in corrected})
            _send_telegram(
                f"✅ Donna sent corrections — {len(corrected)} task(s) assigned "
                f"across {leader_count} leader(s)."
            )
        else:
            # Corrections parse failed — fall back to assigning original list
            log.warning("Corrections parse returned no tasks — assigning original batch")
            _assign_all(conn, batch_id, pending)
            leader_count = len({p["member_id"] for p in pending})
            _send_telegram(
                f"✅ Donna sent corrections (unclear — assigned as-is) — "
                f"{len(pending)} task(s) across {leader_count} leader(s)."
            )

    _clear_state(conn, STATE_KEY_PENDING_BATCH)
    conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    run()
