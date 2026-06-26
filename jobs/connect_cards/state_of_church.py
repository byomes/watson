"""
state_of_church.py — Weekly State of the Church report.

Queries congregation.db, synthesizes via Ollama (qwen2.5:14b),
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
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:14b"
OLLAMA_TIMEOUT = 180


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


# ── Ollama ─────────────────────────────────────────────────────────────────────

def _ollama_synthesis(condensed: str) -> str | None:
    prompt = (
        "You are Watson, AI assistant to Dr. Bill Yomes, Senior Pastor of Catalyst Community Church "
        "in Wilmington, DE.\n\n"
        "Based on this week's church data, write a 2-3 paragraph pastoral synthesis for Dr. Bill. "
        "Be concise, pastoral, and direct. Note spiritual momentum, areas of concern, and who may "
        "need attention.\n\n"
        f"{condensed}\n\n"
        "Write Watson's Read now:"
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
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

    # ── Ollama synthesis (optional) ────────────────────────────────────────────
    att_parts = []
    for r in this_att:
        campus = r["campus"]
        count  = r["count"]
        prev   = last_by_campus.get(campus, 0)
        diff   = count - prev
        sign   = "+" if diff >= 0 else ""
        att_parts.append(f"{campus} {count} ({sign}{diff})")
    att_total_diff = this_total - last_total
    att_total_sign = "+" if att_total_diff >= 0 else ""

    prayer_names = ", ".join(p["name"].split()[0] for p in prayers) if prayers else "none"
    absent_names = ", ".join(m["name"].split()[0] for m in missing) if missing else "none"

    condensed = (
        f"WEEK OF: {monday.strftime('%B %d, %Y')}\n"
        f"ATTENDANCE: {', '.join(att_parts) or 'no data'}, Total {this_total} ({att_total_sign}{att_total_diff})\n"
        f"FIRST-TIME VISITORS: {len(visitors)}\n"
        f"OPEN FOLLOW-UPS: {len(followups)}\n"
        f"PRAYER REQUESTS: {len(prayers)} requests from: {prayer_names}\n"
        f"MEMBERS NOT SEEN 14+ DAYS: {len(missing)} members: {absent_names}"
    )
    synthesis = _ollama_synthesis(condensed)

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
