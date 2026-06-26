"""
state_of_church.py — Weekly State of the Church report.

Queries congregation.db, synthesizes via Ollama (qwen2.5:14b),
and emails an HTML pastoral digest to pastorbill@catalyst302.com.

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
from email.mime.multipart import MIMEMultipart
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
        WHERE m.active = 1
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
        "Based on this week's church data, write exactly one cohesive 2-3 paragraph pastoral synthesis "
        "for Dr. Bill. Be concise, pastoral, and direct. Note spiritual momentum, areas of concern, and "
        "who may need attention. Do not include a summary paragraph at the end. Do not repeat yourself. "
        "Do not include a 'Watson\\'s Read:' label or any other label inside the text.\n\n"
        f"{condensed}\n\n"
        "Begin writing now:"
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


# ── HTML builder ───────────────────────────────────────────────────────────────

def _html_section_header(title: str) -> str:
    return (
        f'<h2 style="margin:28px 0 0;font-size:12px;font-weight:700;color:#1a1a1a;'
        f'text-transform:uppercase;letter-spacing:0.8px;padding-bottom:8px;'
        f'border-bottom:2px solid #1a1a1a;">{title}</h2>'
    )


def _build_html(
    monday: date,
    this_sunday: date,
    last_sunday: date,
    this_att: list[dict],
    last_att: list[dict],
    visitors: list[str],
    prayers: list[dict],
    followups: list[dict],
    missing: list[dict],
    synthesis: str | None,
) -> str:
    last_by_campus = {r["campus"]: r["count"] for r in last_att}
    this_total = sum(r["count"] for r in this_att)
    last_total = sum(r["count"] for r in last_att)
    date_label = monday.strftime("%B %d, %Y")
    last_label = last_sunday.strftime("%b %d")

    # ── Watson's Read callout ──────────────────────────────────────────────────
    if synthesis:
        # Wrap paragraphs split by double-newline
        paras = [p.strip() for p in synthesis.split("\n\n") if p.strip()]
        synthesis_html = "".join(
            f'<p style="margin:0 0 10px;font-size:14px;color:#2c3e50;line-height:1.7;">{p}</p>'
            for p in paras
        )
    else:
        synthesis_html = (
            '<p style="margin:0;font-size:14px;color:#888;font-style:italic;">'
            "Synthesis unavailable — Ollama did not respond in time.</p>"
        )

    synthesis_block = f"""
    <div style="margin:20px 0 8px;padding:20px 24px;background:#eef4fb;border-left:4px solid #4a7eb5;border-radius:0 4px 4px 0;">
      <p style="margin:0 0 12px;font-size:10px;font-weight:700;color:#4a7eb5;text-transform:uppercase;letter-spacing:1px;">Watson's Read</p>
      {synthesis_html}
    </div>"""

    # ── Attendance rows ────────────────────────────────────────────────────────
    if this_att:
        att_rows = ""
        for r in this_att:
            campus = r["campus"]
            count  = r["count"]
            prev   = last_by_campus.get(campus, 0)
            diff   = count - prev
            sign   = "+" if diff >= 0 else ""
            color  = "#2e7d32" if diff >= 0 else "#c62828"
            att_rows += f"""
        <tr>
          <td style="padding:10px 0;font-size:15px;color:#333;border-bottom:1px solid #f0f0f0;">{campus}</td>
          <td style="padding:10px 0;font-size:22px;font-weight:700;color:#1a1a1a;text-align:right;border-bottom:1px solid #f0f0f0;">{count}</td>
          <td style="padding:10px 0;font-size:13px;color:{color};text-align:right;border-bottom:1px solid #f0f0f0;padding-left:12px;">{sign}{diff} vs {last_label}</td>
        </tr>"""

        diff_total = this_total - last_total
        sign_total = "+" if diff_total >= 0 else ""
        color_total = "#2e7d32" if diff_total >= 0 else "#c62828"
        att_rows += f"""
        <tr>
          <td style="padding:12px 0 0;font-size:15px;font-weight:700;color:#1a1a1a;">Total</td>
          <td style="padding:12px 0 0;font-size:24px;font-weight:700;color:#1a1a1a;text-align:right;">{this_total}</td>
          <td style="padding:12px 0 0;font-size:13px;color:{color_total};text-align:right;padding-left:12px;">{sign_total}{diff_total} vs last week</td>
        </tr>"""

        att_block = f'<table style="width:100%;border-collapse:collapse;">{att_rows}</table>'
    else:
        att_block = (
            f'<p style="margin:12px 0 0;font-size:14px;color:#555;">No attendance recorded for '
            f'{this_sunday.strftime("%b %d")}. Last week ({last_label}): {last_total}</p>'
        )

    # ── First-time visitors ────────────────────────────────────────────────────
    if visitors:
        visitor_items = "".join(
            f'<li style="padding:4px 0;font-size:14px;color:#333;">{name}</li>'
            for name in visitors
        )
        visitor_block = f'<ul style="margin:12px 0 0;padding-left:18px;">{visitor_items}</ul>'
    else:
        visitor_block = '<p style="margin:12px 0 0;font-size:14px;color:#888;font-style:italic;">None this week.</p>'

    # ── Prayer requests ────────────────────────────────────────────────────────
    if prayers:
        prayer_items = ""
        for i, pr in enumerate(prayers):
            border = "" if i == len(prayers) - 1 else "border-bottom:1px solid #f0f0f0;"
            prayer_items += f"""
        <div style="padding:12px 0;{border}">
          <span style="font-size:14px;font-weight:700;color:#1a1a1a;">{pr['name']}</span>
          <span style="font-size:14px;color:#444;display:block;margin-top:3px;line-height:1.5;">{pr['request_text'].strip()}</span>
        </div>"""
        prayer_block = f'<div style="margin-top:12px;">{prayer_items}</div>'
    else:
        prayer_block = '<p style="margin:12px 0 0;font-size:14px;color:#888;font-style:italic;">None this week.</p>'

    # ── Open follow-ups ────────────────────────────────────────────────────────
    if followups:
        fu_items = ""
        for i, fu in enumerate(followups):
            note = (fu["note"] or "").strip()[:150]
            border = "" if i == len(followups) - 1 else "border-bottom:1px solid #f0f0f0;"
            fu_items += f"""
        <div style="padding:10px 0;{border}">
          <span style="font-size:14px;font-weight:700;color:#1a1a1a;">{fu['name']}</span>
          <span style="font-size:13px;color:#555;display:block;margin-top:2px;">{note}</span>
        </div>"""
        fu_block = f'<div style="margin-top:12px;">{fu_items}</div>'
    else:
        fu_block = '<p style="margin:12px 0 0;font-size:14px;color:#888;font-style:italic;">None open.</p>'

    # ── Members not seen ───────────────────────────────────────────────────────
    if missing:
        missing_items = ""
        for i, m in enumerate(missing):
            campus   = m["campus_preference"] or "—"
            raw_last = m["last_seen"]
            last_display = ("last seen " + date.fromisoformat(raw_last).strftime("%b %-d, %Y")) if raw_last else "never"
            border = "" if i == len(missing) - 1 else "border-bottom:1px solid #f0f0f0;"
            missing_items += f"""
        <div style="padding:10px 0;{border}">
          <span style="font-size:14px;font-weight:700;color:#1a1a1a;">{m['name']}</span>
          <span style="font-size:12px;color:#888;margin-left:8px;">{campus} {last_display}</span>
        </div>"""
        missing_block = f'<div style="margin-top:12px;">{missing_items}</div>'
    else:
        missing_block = '<p style="margin:12px 0 0;font-size:14px;color:#2e7d32;">All members seen within the past 14 days.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:20px 0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:4px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08);">

    <!-- Watson header -->
    <div style="padding:14px 32px;border-bottom:1px solid #ebebeb;">
      <p style="margin:0;font-size:10px;color:#aaa;letter-spacing:0.8px;text-transform:uppercase;">Watson &nbsp;/&nbsp; Office of Dr. Bill Yomes</p>
    </div>

    <!-- Title -->
    <div style="padding:28px 32px 0;">
      <h1 style="margin:0;font-size:28px;font-weight:700;color:#1a1a1a;line-height:1.2;">State of the Church</h1>
      <p style="margin:6px 0 0;font-size:15px;color:#666;">Week of {date_label}</p>
    </div>

    <!-- Watson's Read — FIRST -->
    <div style="padding:0 32px;">
      {synthesis_block}
    </div>

    <!-- Main content -->
    <div style="padding:0 32px 32px;">

      <!-- Attendance -->
      {_html_section_header(f"Attendance")}
      {att_block}

      <!-- First-Time Visitors -->
      {_html_section_header(f"First-Time Visitors")}
      {visitor_block}

      <!-- Prayer Requests -->
      {_html_section_header(f"Prayer Requests This Week ({len(prayers)})")}
      {prayer_block}

      <!-- Open Follow-Ups -->
      {_html_section_header(f"Open Follow-Ups ({len(followups)})")}
      {fu_block}

      <!-- Members Not Seen -->
      {_html_section_header(f"Members Not Seen in 14+ Days ({len(missing)})")}
      {missing_block}

    </div>

    <!-- Footer -->
    <div style="padding:18px 32px;border-top:1px solid #ebebeb;background:#fafafa;">
      <p style="margin:0;font-size:11px;color:#bbb;text-align:center;">Watson &nbsp;/&nbsp; AI-powered digital assistant &nbsp;/&nbsp; Office of Dr. Bill Yomes</p>
    </div>

  </div>
</body>
</html>"""


# ── Plain-text fallback ────────────────────────────────────────────────────────

def _build_plain(
    monday: date,
    this_sunday: date,
    last_sunday: date,
    this_att: list[dict],
    last_att: list[dict],
    visitors: list[str],
    prayers: list[dict],
    followups: list[dict],
    missing: list[dict],
    synthesis: str | None,
) -> str:
    last_by_campus = {r["campus"]: r["count"] for r in last_att}
    this_total = sum(r["count"] for r in this_att)
    last_total = sum(r["count"] for r in last_att)
    last_label = last_sunday.strftime("%b %d")

    lines = [
        "STATE OF THE CHURCH",
        f"Week of {monday.strftime('%B %d, %Y')}",
        "Report generated by Watson",
        "",
        "WATSON'S READ",
        "-" * 52,
    ]
    lines.append(synthesis if synthesis else "(Synthesis unavailable — Ollama did not respond in time.)")

    lines += ["", "ATTENDANCE", "-" * 52]
    if this_att:
        for r in this_att:
            campus = r["campus"]
            count  = r["count"]
            prev   = last_by_campus.get(campus, 0)
            diff   = count - prev
            sign   = "+" if diff >= 0 else ""
            lines.append(f"  {campus}: {count}  ({sign}{diff} vs {last_label})")
        diff_total = this_total - last_total
        sign = "+" if diff_total >= 0 else ""
        lines.append(f"  TOTAL: {this_total}  ({sign}{diff_total} vs last week)")
    else:
        lines.append(f"  No attendance recorded for {this_sunday.strftime('%b %d')}.")
        lines.append(f"  Last week ({last_label}): {last_total}")

    lines += ["", "FIRST-TIME VISITORS", "-" * 52]
    lines += [f"  - {n}" for n in visitors] if visitors else ["  None this week."]

    lines += ["", f"PRAYER REQUESTS ({len(prayers)})", "-" * 52]
    for pr in prayers:
        lines.append(f"  {pr['name']}: {pr['request_text'].strip()}")
    if not prayers:
        lines.append("  None this week.")

    lines += ["", f"OPEN FOLLOW-UPS ({len(followups)})", "-" * 52]
    for fu in followups:
        note = (fu["note"] or "").strip()[:150]
        lines.append(f"  {fu['name']}: {note}")
    if not followups:
        lines.append("  None open.")

    lines += ["", f"MEMBERS NOT SEEN IN 14+ DAYS ({len(missing)})", "-" * 52]
    for m in missing:
        campus   = m["campus_preference"] or "—"
        raw_last = m["last_seen"]
        last_display = ("last seen " + date.fromisoformat(raw_last).strftime("%b %-d, %Y")) if raw_last else "never"
        lines.append(f"  {m['name']}  (campus: {campus}, {last_display})")
    if not missing:
        lines.append("  All members seen within the past 14 days.")

    lines += ["", "—", "Watson / AI-powered digital assistant / Office of Dr. Bill Yomes"]
    return "\n".join(lines)


# ── Report builder ─────────────────────────────────────────────────────────────

def build_report() -> tuple[str, str, str]:
    """Returns (subject, html_body, plain_body)."""
    this_sunday = most_recent_sunday()
    last_sunday = this_sunday - timedelta(days=7)
    monday      = week_monday()

    subject = f"State of the Church — Week of {monday.strftime('%B %d, %Y')}"

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

    # Build condensed summary for Ollama
    last_by_campus = {r["campus"]: r["count"] for r in last_att}
    this_total = sum(r["count"] for r in this_att)
    last_total = sum(r["count"] for r in last_att)
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

    kwargs = dict(
        monday=monday,
        this_sunday=this_sunday,
        last_sunday=last_sunday,
        this_att=this_att,
        last_att=last_att,
        visitors=visitors,
        prayers=prayers,
        followups=followups,
        missing=missing,
        synthesis=synthesis,
    )
    html  = _build_html(**kwargs)
    plain = _build_plain(**kwargs)

    return subject, html, plain


# ── Send ───────────────────────────────────────────────────────────────────────

def send_report(subject: str, html: str, plain: str) -> None:
    if not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Watson <{FROM_ADDR}>"
    msg["To"]      = TO_ADDR
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
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
        subject, html, plain = build_report()
    except Exception as exc:
        log.error("Failed to build report: %s", exc)
        sys.exit(1)

    print(plain)
    print("\n--- HTML preview (first 500 chars) ---")
    print(html[:500])

    if args.dry_run:
        print(f"\n[dry-run] Would send: {subject!r} → {TO_ADDR}")
        print(f"[dry-run] Content-Type: multipart/alternative (text/plain + text/html)")
        sys.exit(0)

    try:
        send_report(subject, html, plain)
    except Exception as exc:
        log.error("Failed to send email: %s", exc)
        sys.exit(1)
