"""
state_of_church.py — Weekly State of the Church report.

Queries congregation.db and watson.db, synthesizes via Ollama (qwen2.5:14b),
and emails a plain-text pastoral digest to pastorbill@catalyst302.com.

Cron: Thu 4:00pm
  0 16 * * 4  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.connect_cards.state_of_church >> /home/billyomes/watson/logs/state_of_church.log 2>&1

Usage:
  python -m jobs.connect_cards.state_of_church           # build and send
  python -m jobs.connect_cards.state_of_church --dry-run # print without sending
"""

import argparse
import logging
import os
import smtplib
import sqlite3
import sys
from datetime import date, timedelta
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SMTP_HOST    = "smtp.gmail.com"
SMTP_PORT    = 587
SMTP_USER    = os.getenv("WATSON_GMAIL_ADDRESS", "")
SMTP_PASS    = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")
FROM_ADDR    = os.getenv("WATSON_FROM_ADDRESS") or SMTP_USER

TO_ADDR      = "pastorbill@catalyst302.com"
CONG_DB      = os.path.expanduser("~/watson/data/congregation.db")
WATSON_DB    = os.path.expanduser("~/watson/data/watson.db")
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:14b"
OLLAMA_TIMEOUT = 120


# ── Date helpers ───────────────────────────────────────────────────────────────

def most_recent_sunday() -> date:
    today = date.today()
    return today - timedelta(days=(today.weekday() + 1) % 7)


def week_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


# ── congregation.db ────────────────────────────────────────────────────────────

def _attendance_by_campus(conn: sqlite3.Connection, service_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT campus, COUNT(*) as count FROM attendance WHERE service_date = ? GROUP BY campus ORDER BY campus",
        (service_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def _first_time_visitors(conn: sqlite3.Connection, service_date: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT m.name
        FROM connect_cards cc
        JOIN members m ON m.id = cc.member_id
        WHERE cc.service_date = ? AND cc.is_first_visit = 1
        ORDER BY m.name
        """,
        (service_date,),
    ).fetchall()
    return [r["name"] for r in rows]


def _open_follow_ups(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT m.name, fu.note, fu.created_at
        FROM follow_ups fu
        JOIN members m ON m.id = fu.member_id
        WHERE fu.status = 'open'
        ORDER BY fu.created_at ASC
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def _prayer_requests(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT m.name, pr.request_text
        FROM prayer_requests pr
        JOIN members m ON m.id = pr.member_id
        WHERE date(pr.created_at) >= date('now', '-7 days')
          AND pr.leadership_only != 1
        ORDER BY m.name
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def _members_not_seen(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT m.name, m.campus_preference, MAX(a.service_date) as last_seen
        FROM members m
        LEFT JOIN attendance a ON a.member_id = m.id
        WHERE m.active = 1 AND m.status != 'visitor'
        GROUP BY m.id
        HAVING last_seen IS NULL OR last_seen < date('now', '-14 days')
        ORDER BY last_seen ASC
        LIMIT 25
        """,
    ).fetchall()
    return [dict(r) for r in rows]


# ── watson.db ──────────────────────────────────────────────────────────────────

def _active_tasks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT title, priority, due_date
        FROM tasks
        WHERE status = 'active'
        ORDER BY
            CASE priority
                WHEN '1' THEN 1 WHEN 'high'   THEN 1
                WHEN '2' THEN 2
                WHEN '3' THEN 3 WHEN 'medium' THEN 3
                WHEN '4' THEN 4
                WHEN '5' THEN 5 WHEN 'low'    THEN 5
                ELSE 6
            END,
            due_date ASC
        """,
    ).fetchall()
    return [dict(r) for r in rows]


# ── Ollama ─────────────────────────────────────────────────────────────────────

def _ollama_synthesis(data_text: str) -> str | None:
    system = (
        "You are Watson, the AI-powered digital assistant for the Office of Dr. Bill Yomes, "
        "Senior Pastor of Catalyst Community Church. You assist Dr. Bill with pastoral intelligence "
        "and weekly reporting. You are terse, accurate, and never embellish or speculate."
    )
    prompt = (
        f"Here is this week's church data:\n\n{data_text}\n\n"
        "Write a 2–3 paragraph pastoral synthesis covering: overall church health, "
        "spiritual momentum this week, and who needs Dr. Bill's personal attention. "
        "Be specific — use names where available. Do not use bullet points. "
        "Write in a direct, pastoral voice."
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "system": system, "stream": False},
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip() or None
    except Exception as exc:
        log.warning("Ollama synthesis failed: %s", exc)
        return None


# ── Report builder ─────────────────────────────────────────────────────────────

def build_report() -> tuple[str, str]:
    this_sunday = most_recent_sunday()
    last_sunday = this_sunday - timedelta(days=7)
    monday      = week_monday()

    subject = f"State of the Church — Week of {monday.strftime('%B %d, %Y')}"

    # congregation.db — hard fail if unavailable
    try:
        cong = sqlite3.connect(f"file:{CONG_DB}?mode=ro", uri=True)
        cong.row_factory = sqlite3.Row
    except Exception as exc:
        log.error("congregation.db unavailable: %s", exc)
        raise

    try:
        this_att  = _attendance_by_campus(cong, this_sunday.isoformat())
        last_att  = _attendance_by_campus(cong, last_sunday.isoformat())
        visitors  = _first_time_visitors(cong, this_sunday.isoformat())
        followups = _open_follow_ups(cong)
        prayers   = _prayer_requests(cong)
        missing   = _members_not_seen(cong)
    finally:
        cong.close()

    # watson.db — hard fail if unavailable
    try:
        wdb = sqlite3.connect(f"file:{WATSON_DB}?mode=ro", uri=True)
        wdb.row_factory = sqlite3.Row
    except Exception as exc:
        log.error("watson.db unavailable: %s", exc)
        raise

    try:
        tasks = _active_tasks(wdb)
    finally:
        wdb.close()

    # ── Attendance section ─────────────────────────────────────────────────────
    lines = [
        "STATE OF THE CHURCH",
        f"Week of {monday.strftime('%B %d, %Y')}",
        "Report generated by Watson",
        "",
        "=" * 52,
        "ATTENDANCE",
        "=" * 52,
    ]

    last_by_campus = {r["campus"]: r["count"] for r in last_att}
    this_total = sum(r["count"] for r in this_att)
    last_total = sum(r["count"] for r in last_att)

    if this_att:
        for r in this_att:
            campus = r["campus"]
            count  = r["count"]
            prev   = last_by_campus.get(campus, 0)
            diff   = count - prev
            sign   = "+" if diff >= 0 else ""
            lines.append(f"  {campus}: {count}  ({sign}{diff} vs {last_sunday.strftime('%b %d')})")
        diff_total = this_total - last_total
        sign = "+" if diff_total >= 0 else ""
        lines.append(f"  TOTAL: {this_total}  ({sign}{diff_total} vs last week)")
    else:
        lines.append(f"  No attendance recorded for {this_sunday.strftime('%b %d')}.")
        lines.append(f"  Last week ({last_sunday.strftime('%b %d')}): {last_total}")

    # ── First-time visitors ────────────────────────────────────────────────────
    lines += [
        "",
        "=" * 52,
        "FIRST-TIME VISITORS",
        "=" * 52,
    ]
    if visitors:
        for name in visitors:
            lines.append(f"  - {name}")
    else:
        lines.append("  None this week.")

    # ── Open follow-ups ────────────────────────────────────────────────────────
    lines += [
        "",
        "=" * 52,
        f"OPEN FOLLOW-UPS  ({len(followups)})",
        "=" * 52,
    ]
    if followups:
        for fu in followups:
            note = (fu["note"] or "").strip()[:150]
            lines.append(f"  {fu['name']}: {note}")
    else:
        lines.append("  None open.")

    # ── Prayer requests ────────────────────────────────────────────────────────
    lines += [
        "",
        "=" * 52,
        f"PRAYER REQUESTS THIS WEEK  ({len(prayers)})",
        "=" * 52,
    ]
    if prayers:
        for pr in prayers:
            lines.append(f"  {pr['name']}: {pr['request_text'].strip()}")
    else:
        lines.append("  None this week.")

    # ── Members not seen in 14 days ────────────────────────────────────────────
    lines += [
        "",
        "=" * 52,
        f"MEMBERS NOT SEEN IN 14+ DAYS  ({len(missing)})",
        "=" * 52,
    ]
    if missing:
        for m in missing:
            last   = m["last_seen"] or "never"
            campus = m["campus_preference"] or "—"
            lines.append(f"  {m['name']}  (campus: {campus}, last seen: {last})")
    else:
        lines.append("  All members seen within the past 14 days.")

    # ── Active tasks ───────────────────────────────────────────────────────────
    lines += [
        "",
        "=" * 52,
        f"OPEN TASKS  ({len(tasks)})",
        "=" * 52,
    ]
    if tasks:
        for t in tasks:
            due = t["due_date"] or "no due date"
            lines.append(f"  [{t['priority']}]  {t['title']}  (due: {due})")
    else:
        lines.append("  No active tasks.")

    # ── Ollama synthesis (optional) ────────────────────────────────────────────
    data_for_ollama = "\n".join(lines)
    synthesis = _ollama_synthesis(data_for_ollama)

    lines += [
        "",
        "=" * 52,
        "WATSON'S READ",
        "=" * 52,
    ]
    if synthesis:
        lines.append(synthesis)
    else:
        lines.append("  (Synthesis unavailable — Ollama did not respond in time.)")

    lines += [
        "",
        "—",
        "Watson / AI-powered digital assistant / Office of Dr. Bill Yomes",
    ]

    return subject, "\n".join(lines)


# ── Send ───────────────────────────────────────────────────────────────────────

def send_report(subject: str, body: str) -> None:
    if not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = f"Watson <{FROM_ADDR}>"
    msg["To"]      = TO_ADDR
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(SMTP_USER, [TO_ADDR], msg.as_string())
    log.info("Sent: %r → %s", subject, TO_ADDR)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="State of the Church weekly report.")
    parser.add_argument("--dry-run", action="store_true", help="Print report without sending email")
    args = parser.parse_args()

    log.info("Building State of the Church report...")
    try:
        subject, body = build_report()
    except Exception as exc:
        log.error("Failed to build report: %s", exc)
        sys.exit(1)

    print(body)

    if args.dry_run:
        print(f"\n[dry-run] Would send: {subject!r} → {TO_ADDR}")
        sys.exit(0)

    try:
        send_report(subject, body)
    except Exception as exc:
        log.error("Failed to send email: %s", exc)
        sys.exit(1)
