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


def _fmt_date(d: str) -> str:
    """Convert YYYY-MM-DD to Mon D, YYYY. Return as-is if parse fails."""
    try:
        from datetime import datetime
        return datetime.strptime(d, "%Y-%m-%d").strftime("%b %-d, %Y")
    except Exception:
        return d or "—"


_CAMPUS_DISPLAY = {
    "Wilmington": "Wilmington",
    "Online":     "Online",
    "Hybrid":     "Hybrid \U0001f500",
    "Unknown":    "—",
}


def _get_member_campus(member_id: int, db) -> str:
    """
    Classify campus from full attendance history.
    - 3+ Wilmington AND 3+ Online → "Hybrid"
    - Otherwise → whichever appears most
    - No cards → "Unknown"
    """
    rows = db.execute(
        "SELECT campus FROM connect_cards WHERE member_id = ?",
        (member_id,),
    ).fetchall()

    if not rows:
        return "Unknown"

    wilm   = sum(1 for r in rows if r["campus"] == "Wilmington")
    online = sum(1 for r in rows if r["campus"] == "Online")

    if wilm >= 3 and online >= 3:
        result = "Hybrid"
    elif wilm >= online:
        result = "Wilmington"
    else:
        result = "Online"
    print(f"[campus] member={member_id} wilm={wilm} online={online} total={len(rows)} → {result}")
    return result


# ── Section 1: At Risk (3–5 weeks absent) ─────────────────────────────────────

def _build_at_risk_section() -> tuple[str, int]:
    """Members whose last connect card was 21–41 days ago (3–5 weeks)."""
    cutoff_near = _cutoff(21)
    cutoff_far  = _cutoff(42)

    with _conn() as conn:
        rows = conn.execute(
            """
            WITH base AS (
                SELECT m.id, m.name,
                       MAX(
                         COALESCE((SELECT MAX(service_date) FROM connect_cards WHERE member_id = m.id), '1900-01-01'),
                         COALESCE((SELECT MAX(service_date) FROM attendance  WHERE member_id = m.id), '1900-01-01')
                       ) AS last_seen
                FROM members m
                WHERE m.status != 'inactive'
                  AND (m.shepherding_exempt IS NULL OR m.shepherding_exempt = 0)
                  AND (m.member_status IS NULL OR m.member_status NOT IN ('deceased', 'disconnected', 'non_local', 'snowbird'))
                  AND (
                    EXISTS (SELECT 1 FROM connect_cards WHERE member_id = m.id)
                    OR EXISTS (SELECT 1 FROM attendance WHERE member_id = m.id)
                  )
            )
            SELECT id, name, last_seen,
                   CAST((julianday('now') - julianday(last_seen)) / 7 AS INTEGER) AS weeks_absent
            FROM base
            WHERE last_seen < ? AND last_seen > ?
            ORDER BY weeks_absent DESC
            """,
            (cutoff_near, cutoff_far),
        ).fetchall()
        campus_map = {r["id"]: _get_member_campus(r["id"], conn) for r in rows}

    count   = len(rows)
    heading = f"<h2>⚠️ At Risk — Absent 3–5 Weeks ({count})</h2>"

    if not rows:
        return heading + "<p class='empty'>No members in this category.</p>", count

    table_rows = ""
    for r in rows:
        campus = _CAMPUS_DISPLAY.get(campus_map[r["id"]], "—")
        campus_badge = (
            f"<span style='display:inline-block;background:#1e3a5f;color:#7eb8f7;"
            f"font-size:11px;padding:2px 8px;border-radius:4px;margin-top:4px'>{campus}</span>"
        )
        table_rows += (
            f"<tr data-member-id='{r['id']}'>"
            f"<td><strong>{r['name'] or '(no name)'}</strong><br>{campus_badge}</td>"
            f"<td><span style='font-size:12px;color:#aaa'>Last seen: {_fmt_date(r['last_seen'])}</span></td>"
            f"<td><span style='color:#f0c040'>{r['weeks_absent']} wks</span></td>"
            f"</tr>"
        )

    html = (
        heading
        + "<table><thead><tr>"
        + "<th>Name</th><th>Last Seen</th><th>Absent</th>"
        + "</tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return html, count


# ── Section 2: Critical Care (6+ weeks absent) ────────────────────────────────

def _build_critical_section() -> tuple[str, int]:
    """Members whose last connect card was 42+ days ago (6+ weeks)."""
    cutoff = _cutoff(42)

    with _conn() as conn:
        rows = conn.execute(
            """
            WITH base AS (
                SELECT m.id, m.name,
                       MAX(
                         COALESCE((SELECT MAX(service_date) FROM connect_cards WHERE member_id = m.id), '1900-01-01'),
                         COALESCE((SELECT MAX(service_date) FROM attendance  WHERE member_id = m.id), '1900-01-01')
                       ) AS last_seen,
                       (
                         SELECT COUNT(*) FROM (
                           SELECT service_date FROM connect_cards WHERE member_id = m.id
                           UNION
                           SELECT service_date FROM attendance WHERE member_id = m.id
                         )
                       ) AS visit_count
                FROM members m
                WHERE m.status != 'inactive'
                  AND (m.shepherding_exempt IS NULL OR m.shepherding_exempt = 0)
                  AND (m.member_status IS NULL OR m.member_status NOT IN ('deceased', 'disconnected', 'non_local', 'snowbird'))
                  AND (
                    EXISTS (SELECT 1 FROM connect_cards WHERE member_id = m.id)
                    OR EXISTS (SELECT 1 FROM attendance WHERE member_id = m.id)
                  )
            )
            SELECT id, name, last_seen,
                   CAST((julianday('now') - julianday(last_seen)) / 7 AS INTEGER) AS weeks_absent
            FROM base
            WHERE last_seen <= ? AND visit_count >= 3
            ORDER BY weeks_absent DESC
            """,
            (cutoff,),
        ).fetchall()
        campus_map = {r["id"]: _get_member_campus(r["id"], conn) for r in rows}

    count   = len(rows)
    heading = (
        f"<h2 style='color:#c0392b'>\U0001f534 Critical Care — Absent 6+ Weeks ({count})</h2>"
        f"<p style='color:#c0392b;font-size:.85em;margin-top:-8px'>"
        f"These members require immediate pastoral attention.</p>"
    )

    if not rows:
        return heading + "<p class='empty'>No members in this category.</p>", count

    table_rows = ""
    for r in rows:
        name_cell = (
            f"<strong>{r['name'] or '(no name)'}</strong>"
            f" <span style='color:#ff6b6b;font-size:11px'>&#9679; Critical</span>"
        )
        campus = _CAMPUS_DISPLAY.get(campus_map[r["id"]], "—")
        campus_badge = (
            f"<span style='display:inline-block;background:#1e3a5f;color:#7eb8f7;"
            f"font-size:11px;padding:2px 8px;border-radius:4px;margin-top:4px'>{campus}</span>"
        )
        table_rows += (
            f"<tr data-member-id='{r['id']}'>"
            f"<td>{name_cell}<br>{campus_badge}</td>"
            f"<td><span style='font-size:12px;color:#aaa'>Last seen: {_fmt_date(r['last_seen'])}</span></td>"
            f"<td><span style='color:#ff6b6b;font-weight:bold'>{r['weeks_absent']} wks</span></td>"
            f"</tr>"
        )

    html = (
        heading
        + "<table><thead><tr>"
        + "<th>Name</th><th>Last Seen</th><th>Absent</th>"
        + "</tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return html, count


# ── Section 3: First-Time Visitors Not Followed Up ─────────────────────────────

def _build_visitors_section() -> tuple[str, int]:
    """Members with exactly one connect card, visited within the last 21 days."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.name, m.email, m.phone, cc.service_date AS visit_date,
                   CAST((julianday('now') - julianday(cc.service_date)) / 7 AS INTEGER)
                       AS weeks_since
            FROM members m
            JOIN connect_cards cc ON cc.member_id = m.id
            WHERE (m.shepherding_exempt IS NULL OR m.shepherding_exempt = 0)
              AND (m.member_status IS NULL OR m.member_status NOT IN ('deceased', 'disconnected', 'non_local', 'snowbird'))
            GROUP BY m.id
            HAVING COUNT(cc.id) = 1
              AND MAX(cc.service_date) >= date('now', '-21 days')
            ORDER BY cc.service_date DESC
            """,
        ).fetchall()
        campus_map = {r["id"]: _get_member_campus(r["id"], conn) for r in rows}

    count   = len(rows)
    heading = f"<h2>👋 First-Time Visitors — Last 3 Weeks ({count})</h2>"

    if not rows:
        return (
            heading + "<p class='empty'>No first-time visitors in the last 3 weeks.</p>",
            count,
        )

    table_rows = ""
    for r in rows:
        campus = _CAMPUS_DISPLAY.get(campus_map[r["id"]], "—")
        campus_badge = (
            f"<span style='display:inline-block;background:#1e3a5f;color:#7eb8f7;"
            f"font-size:11px;padding:2px 8px;border-radius:4px;margin-top:4px'>{campus}</span>"
        )
        table_rows += (
            f"<tr data-member-id='{r['id']}'>"
            f"<td><strong>{r['name'] or '(no name)'}</strong><br>{campus_badge}</td>"
            f"<td><span style='font-size:12px;color:#aaa'>Visited: {_fmt_date(r['visit_date'])}</span></td>"
            f"<td><span style='color:#f0c040'>{r['weeks_since']} wks ago</span></td>"
            f"</tr>"
        )

    html = (
        heading
        + "<table><thead><tr>"
        + "<th>Name</th><th>Visit Date</th><th>Weeks Ago</th>"
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
            "<th>Name</th><th>Date</th><th>Contact</th>"
            "</tr></thead><tbody>"
        )
        for r in step_rows:
            contact = r["email"] or r["phone"] or "—"
            campus = _CAMPUS_DISPLAY.get(r["campus"], r["campus"] or "—")
            campus_badge = (
                f"<span style='display:inline-block;background:#1e3a5f;color:#7eb8f7;"
                f"font-size:11px;padding:2px 8px;border-radius:4px;margin-top:4px'>{campus}</span>"
            )
            parts.append(
                f"<tr>"
                f"<td><strong>{r['name'] or '(no name)'}</strong><br>{campus_badge}</td>"
                f"<td><span style='font-size:12px;color:#aaa'>{_fmt_date(r['date'])}</span></td>"
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

    s1_html, _  = _build_at_risk_section()
    s2_html, _  = _build_critical_section()
    s3_html, _  = _build_visitors_section()
    s4_html, _  = _build_next_steps_section()
    s5_html, _  = _build_prayer_section()

    body = s1_html + s2_html + s3_html + s4_html + s5_html
    return subject, _wrap("Shepherding Report", today, body)


def telegram_shepherding_summary() -> str:
    """Return a short plain-text summary for Telegram."""
    today = _today()

    _, at_risk   = _build_at_risk_section()
    _, critical  = _build_critical_section()
    _, visitors  = _build_visitors_section()
    _, steps     = _build_next_steps_section()
    _, prayers   = _build_prayer_section()

    return (
        f"\U0001f4cb Shepherding Report — {today}\n"
        f"\U0001f534 Critical Care (6+ weeks): {critical} people\n"
        f"⚠️ At Risk (3–5 weeks): {at_risk} people\n"
        f"\U0001f44b First-time visitors (last 3 weeks): {visitors}\n"
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
