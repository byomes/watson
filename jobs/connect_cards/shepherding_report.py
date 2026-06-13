"""
Shepherding Report — weekly pastoral care digest for Dr. Bill.

Sections:
  1. Absent 3+ Weeks
  2. Absent 6+ Weeks (Critical Care)
  3. First-Time Visitors Not Followed Up (3–8 week window)
  4. Next Steps Raised (Last 30 Days)
  5. Prayer Requests (Last 7 Days)

Cron (Monday 6:00am, after connect cards reports at 5am):
  0 6 * * 1  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 -m jobs.connect_cards.shepherding_report

Usage:
  python3 -m jobs.connect_cards.shepherding_report
"""

import os
import smtplib
from collections import defaultdict
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

from jobs.connect_cards.reports import _CSS, _wrap, _conn

load_dotenv(os.path.expanduser("~/watson/.env"))


def _ensure_schema():
    try:
        with _conn() as db:
            db.execute(
                "ALTER TABLE members ADD COLUMN shepherding_exempt INTEGER NOT NULL DEFAULT 0"
            )
    except Exception:
        pass


_ensure_schema()

DB_PATH    = os.path.expanduser("~/watson/data/congregation.db")
SMTP_HOST  = "smtp.gmail.com"
SMTP_PORT  = 587
SMTP_USER  = os.getenv("WATSON_GMAIL_ADDRESS", "")
SMTP_PASS  = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")
FROM_ADDR  = os.getenv("WATSON_FROM_ADDRESS") or SMTP_USER
BILL_EMAIL = os.getenv("BILL_EMAIL", "bill.yomes@gmail.com")

_STEP_NAMES = {
    "follow_jesus":     "Follow Jesus",
    "baptism":          "Baptism",
    "grow_faith":       "Grow in Faith",
    "catalyst_partner": "Catalyst Partner",
    "small_group":      "Small Group",
    "ministry_team":    "Ministry Team",
}

_ALERT_STYLE = (
    "display:inline-block;padding:2px 7px;border-radius:3px;"
    "font-size:.78em;font-weight:bold;background:#fde8e8;color:#c0392b"
)


def _today() -> str:
    return date.today().isoformat()


def _cutoff(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


# ── Section 1 & 2: Absent Members ─────────────────────────────────────────────

def _build_absent_section(days: int, is_critical: bool) -> tuple[str, int]:
    """Build HTML for absent members. days=21 → 3+ weeks, days=42 → 6+ weeks."""
    cutoff = _cutoff(days)
    weeks  = days // 7

    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.name, m.campus_preference,
                   MAX(cc.service_date) AS last_seen,
                   CAST((julianday('now') - julianday(MAX(cc.service_date))) / 7 AS INTEGER)
                       AS weeks_absent
            FROM members m
            JOIN connect_cards cc ON cc.member_id = m.id
            WHERE m.status != 'inactive'
              AND (m.shepherding_exempt IS NULL OR m.shepherding_exempt = 0)
            GROUP BY m.id
            HAVING MAX(cc.service_date) < ?
            ORDER BY weeks_absent DESC
            """,
            (cutoff,),
        ).fetchall()

    count = len(rows)

    if is_critical:
        heading = (
            f"<h2 style='color:#c0392b'>&#128308; Critical Care — Absent 6+ Weeks ({count})</h2>"
            f"<p style='color:#c0392b;font-size:.85em;margin-top:-8px'>"
            f"These members require immediate pastoral attention.</p>"
        )
    else:
        heading = f"<h2>Absent {weeks}+ Weeks ({count})</h2>"

    if not rows:
        return heading + "<p class='empty'>No members in this category.</p>", count

    table_rows = ""
    for r in rows:
        name_cell = f"<strong>{r['name'] or '(no name)'}</strong>"
        if is_critical:
            name_cell = f"<span style='{_ALERT_STYLE}'>Critical</span>&nbsp; " + name_cell
        table_rows += (
            f"<tr data-member-id='{r['id']}'>"
            f"<td>{name_cell}</td>"
            f"<td>{r['campus_preference'] or '—'}</td>"
            f"<td>{r['last_seen'] or '—'}</td>"
            f"<td>{r['weeks_absent']} wks</td>"
            f"</tr>"
        )

    html = (
        heading
        + "<table><thead><tr>"
        + "<th>Name</th><th>Campus</th><th>Last Seen</th><th>Absent</th>"
        + "</tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return html, count


# ── Section 3: First-Time Visitors Not Followed Up ─────────────────────────────

def _build_visitors_section() -> tuple[str, int]:
    """Visitors with only one card, dated 3–8 weeks ago (21–56 days)."""
    cutoff_near = _cutoff(21)   # 3 weeks ago (upper bound — they came at least 3 wks ago)
    cutoff_far  = _cutoff(56)   # 8 weeks ago (lower bound — not older than 8 wks)

    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.name, m.email, m.phone, cc.campus,
                   cc.service_date AS visit_date,
                   CAST((julianday('now') - julianday(cc.service_date)) / 7 AS INTEGER)
                       AS weeks_since
            FROM follow_ups fu
            JOIN members m  ON m.id  = fu.member_id
            JOIN connect_cards cc ON cc.id = fu.card_id
            WHERE fu.note = 'First-time visitor'
              AND cc.service_date <= ?
              AND cc.service_date >= ?
              AND (SELECT COUNT(*) FROM connect_cards WHERE member_id = m.id) = 1
              AND (m.shepherding_exempt IS NULL OR m.shepherding_exempt = 0)
            ORDER BY cc.service_date
            """,
            (cutoff_near, cutoff_far),
        ).fetchall()

    count   = len(rows)
    heading = f"<h2>First-Time Visitors Needing Follow-Up ({count})</h2>"

    if not rows:
        return (
            heading + "<p class='empty'>No first-time visitors in the 3–8 week window.</p>",
            count,
        )

    table_rows = ""
    for r in rows:
        contact = ""
        if r["email"]:
            contact += f"<a href='mailto:{r['email']}'>{r['email']}</a>"
        if r["phone"]:
            contact += ("<br>" if contact else "") + r["phone"]
        table_rows += (
            f"<tr data-member-id='{r['id']}'>"
            f"<td><strong>{r['name'] or '(no name)'}</strong></td>"
            f"<td><small>{contact or '—'}</small></td>"
            f"<td>{r['campus'] or '—'}</td>"
            f"<td>{r['visit_date']}</td>"
            f"<td>{r['weeks_since']} wks</td>"
            f"</tr>"
        )

    html = (
        heading
        + "<table><thead><tr>"
        + "<th>Name</th><th>Contact</th><th>Campus</th><th>Visit Date</th><th>Weeks Ago</th>"
        + "</tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return html, count


# ── Section 4: Next Steps (Last 30 Days) ──────────────────────────────────────

def _build_next_steps_section() -> tuple[str, int]:
    cutoff = _cutoff(30)

    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT ns.step, m.name, cc.campus, ns.date, m.email, m.phone
            FROM next_steps ns
            JOIN members m  ON m.id  = ns.member_id
            JOIN connect_cards cc ON cc.id = ns.card_id
            WHERE ns.date >= ?
            ORDER BY ns.step, m.name
            """,
            (cutoff,),
        ).fetchall()

    count   = len(rows)
    heading = f"<h2>Next Steps — Last 30 Days ({count})</h2>"

    if not rows:
        return heading + "<p class='empty'>No next steps in the last 30 days.</p>", count

    by_step: dict = defaultdict(list)
    for r in rows:
        by_step[r["step"]].append(r)

    parts = [heading]
    for step_key, step_name in _STEP_NAMES.items():
        step_rows = by_step.get(step_key, [])
        if not step_rows:
            continue
        parts.append(
            f"<h3 style='font-size:.9em;text-transform:uppercase;letter-spacing:.04em;"
            f"color:#555;margin:16px 0 6px'>{step_name} ({len(step_rows)})</h3>"
        )
        parts.append(
            "<table><thead><tr>"
            "<th>Name</th><th>Campus</th><th>Date</th><th>Contact</th>"
            "</tr></thead><tbody>"
        )
        for r in step_rows:
            contact = r["email"] or r["phone"] or "—"
            parts.append(
                f"<tr>"
                f"<td>{r['name'] or '(no name)'}</td>"
                f"<td>{r['campus'] or '—'}</td>"
                f"<td>{r['date']}</td>"
                f"<td><small>{contact}</small></td>"
                f"</tr>"
            )
        parts.append("</tbody></table>")

    return "".join(parts), count


# ── Section 5: Prayer Requests (Last 7 Days) ──────────────────────────────────

def _build_prayer_section() -> tuple[str, int]:
    cutoff = _cutoff(7)

    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT pr.request_text, pr.leadership_only, m.name, cc.campus
            FROM prayer_requests pr
            JOIN members m  ON m.id  = pr.member_id
            JOIN connect_cards cc ON cc.id = pr.card_id
            WHERE pr.date >= ?
            ORDER BY pr.leadership_only DESC, m.name
            """,
            (cutoff,),
        ).fetchall()

    count   = len(rows)
    heading = f"<h2>Prayer Requests — Last 7 Days ({count})</h2>"

    if not rows:
        return heading + "<p class='empty'>No prayer requests in the last 7 days.</p>", count

    table_rows = ""
    for r in rows:
        if r["leadership_only"]:
            display_name  = r["name"] or "(no name)"
            privacy_badge = "<span class='badge private'>Leadership Only</span>"
        else:
            parts = (r["name"] or "").split()
            display_name  = parts[0] if parts else "(no name)"
            if len(parts) > 1:
                display_name += f" {parts[-1][0]}."
            privacy_badge = "<span class='badge public'>Public</span>"

        campus_badge = f"<span class='badge campus'>{r['campus'] or '—'}</span>"
        table_rows += (
            f"<tr>"
            f"<td><strong>{display_name}</strong><br>{campus_badge}</td>"
            f"<td>{privacy_badge}</td>"
            f"<td><div class='note'>{r['request_text']}</div></td>"
            f"</tr>"
        )

    html = (
        heading
        + "<table><thead><tr>"
        + "<th>Person</th><th>Visibility</th><th>Prayer Request</th>"
        + "</tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return html, count


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_shepherding_report() -> tuple[str, str]:
    """Return (subject, html) for the full shepherding report."""
    today   = _today()
    subject = f"Shepherding Report — {today}"

    s1_html, _  = _build_absent_section(21, is_critical=False)
    s2_html, _  = _build_absent_section(42, is_critical=True)
    s3_html, _  = _build_visitors_section()
    s4_html, _  = _build_next_steps_section()
    s5_html, _  = _build_prayer_section()

    body = s1_html + s2_html + s3_html + s4_html + s5_html
    return subject, _wrap("Shepherding Report", today, body)


def telegram_shepherding_summary() -> str:
    """Return a short plain-text summary for Telegram."""
    today = _today()

    _, absent_3  = _build_absent_section(21, is_critical=False)
    _, absent_6  = _build_absent_section(42, is_critical=True)
    _, visitors  = _build_visitors_section()
    _, steps     = _build_next_steps_section()
    _, prayers   = _build_prayer_section()

    absent_3_5 = absent_3 - absent_6

    return (
        f"\U0001f4cb Shepherding Report — {today}\n"
        f"\U0001f534 Absent 6+ weeks: {absent_6} people\n"
        f"\U0001f7e1 Absent 3–5 weeks: {absent_3_5} people\n"
        f"\U0001f44b Visitors needing follow-up: {visitors}\n"
        f"✋ Next steps this month: {steps}\n"
        f"\U0001f64f Prayer requests this week: {prayers}\n"
        f"\nFull report sent to your email."
    )


def send_shepherding_report() -> None:
    """Generate and email the shepherding report to Bill."""
    if not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")
    if not BILL_EMAIL:
        raise RuntimeError("BILL_EMAIL must be set.")

    subject, html = generate_shepherding_report()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Watson <{FROM_ADDR}>"
    msg["To"]      = BILL_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(FROM_ADDR, [BILL_EMAIL], msg.as_string())

    print(f"Sent: {subject!r} → {BILL_EMAIL}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import requests
    from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID

    print("Generating and sending shepherding report...")
    send_shepherding_report()

    summary = telegram_shepherding_summary()
    print(summary)

    if WATSON_BOT_TOKEN and WATSON_CHAT_ID:
        resp = requests.post(
            f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
            json={"chat_id": WATSON_CHAT_ID, "text": summary},
            timeout=10,
        )
        if resp.ok:
            print("Telegram summary sent.")
        else:
            print(f"Telegram failed: {resp.text}")
    else:
        print("Telegram not configured — skipping.")
