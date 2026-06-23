"""
Connect card weekly reports — three distinct views of the same dataset.

Normalized schema in congregation.db:
  connect_cards:    id, member_id, service_date, campus, questions_comments, email_id
  members:          id, name, email, phone, first_visit_date
  next_steps:       id, member_id, card_id, step, date
  prayer_requests:  id, member_id, card_id, request_text, date
  attendance:       id, member_id, service_date, campus, card_id
  follow_ups:       id, member_id, card_id, note  (note='First-time visitor' flags first visits)

All queries filter by service_date, never created_at, so late-arriving cards
are always attributed to the correct Sunday regardless of submission time.
"""

import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

_CSS = (
    "body{font-family:Georgia,serif;max-width:620px;margin:0 auto;padding:24px;color:#222;background:#fff}"
    "h1{font-size:1.25em;border-bottom:2px solid #333;padding-bottom:8px;margin-bottom:20px}"
    "h2{font-size:.9em;text-transform:uppercase;letter-spacing:.06em;color:#666;margin:24px 0 8px}"
    "table{width:100%;border-collapse:collapse;font-size:.9em}"
    "th{text-align:left;border-bottom:2px solid #ddd;padding:6px 8px;color:#555;font-size:.82em;text-transform:uppercase;letter-spacing:.04em}"
    "td{border-bottom:1px solid #eee;padding:6px 8px;vertical-align:top}"
    "tr:last-child td{border-bottom:none}"
    ".badge{display:inline-block;padding:2px 7px;border-radius:3px;font-size:.78em;font-weight:bold}"
    ".first{background:#e8f4e8;color:#2a6a2a}"
    ".returning{background:#f0f0f0;color:#555}"
    ".campus{background:#e8eef8;color:#2a4a8a}"
    ".private{background:#fdf0e8;color:#8a4a2a}"
    ".public{background:#e8f4e8;color:#2a6a2a}"
    ".stat{font-size:2em;font-weight:bold;color:#222}"
    ".stat-label{font-size:.8em;color:#888;margin-top:2px}"
    ".stat-box{display:inline-block;text-align:center;padding:12px 20px;border:1px solid #eee;border-radius:4px;margin:4px}"
    ".empty{color:#bbb;font-style:italic;font-size:.9em}"
    ".footer{margin-top:32px;padding-top:12px;border-top:1px solid #eee;font-size:.8em;color:#bbb}"
    ".note{background:#fafafa;border-left:3px solid #ddd;padding:6px 10px;margin:4px 0;font-size:.9em}"
    ".updated-note{background:#fef9e7;border:1px solid #f0c040;border-radius:4px;padding:10px 14px;margin-bottom:16px;font-size:.9em;color:#666}"
)

_UPDATED_BANNER = (
    "<div class='updated-note'>"
    "This is an updated report. Any additions since Monday are included."
    "</div>"
)


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _subject(report_type: str, service_date: str, updated: bool) -> str:
    base = f"Watson — {report_type} | {service_date}"
    return base + (" | Updated" if updated else "")


def _wrap(title: str, subtitle: str, body: str) -> str:
    return (
        f"<html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>"
        f"<h1>{title}</h1>"
        f"<p style='color:#888;font-size:.9em;margin-top:-12px'>{subtitle}</p>"
        f"{body}"
        f"<p class='footer'>Watson connect cards · {subtitle}</p>"
        f"</body></html>"
    )


# ── Bill: next steps + questions/comments ─────────────────────────────────────

_FIELD_LABEL = (
    "font-size:.78em;text-transform:uppercase;letter-spacing:.05em;"
    "color:#555;margin:10px 0 2px"
)

def bill_report(service_date: str, updated: bool = False) -> tuple[str, str]:
    """Return (subject, html) for Bill — next steps and comments."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT m.name, m.email, m.phone, cc.campus, cc.is_first_visit,
                   cc.next_steps, cc.questions_comments
            FROM connect_cards cc
            JOIN members m ON m.id = cc.member_id
            WHERE cc.service_date = ?
              AND (TRIM(COALESCE(cc.next_steps,        '')) != ''
                OR TRIM(COALESCE(cc.questions_comments, '')) != '')
            ORDER BY cc.is_first_visit DESC, m.name
            """,
            (service_date,),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM connect_cards WHERE service_date = ?",
            (service_date,),
        ).fetchone()[0]

    subject = _subject("Next Steps & Comments", service_date, updated)
    banner = _UPDATED_BANNER if updated else ""

    if not rows:
        body = (
            banner
            + f"<p class='empty'>No next steps or comments recorded for {service_date}.</p>"
            + f"<p style='color:#888;font-size:.9em'>Total cards submitted: {total}</p>"
        )
        return subject, _wrap("Next Steps &amp; Comments", service_date, body)

    cards = ""
    for r in rows:
        visit_badge = (
            "<span class='badge first'>First visit</span>"
            if r["is_first_visit"]
            else "<span class='badge returning'>Returning</span>"
        )
        campus_badge = f"<span class='badge campus'>{r['campus'] or ''}</span>"
        email = r["email"] or ""
        contact = ""
        if email:
            contact += f"<a href='mailto:{email}'>{email}</a>"
        if r["phone"]:
            contact += ("<br>" if contact else "") + r["phone"]

        fields_html = ""
        if r["next_steps"]:
            fields_html += (
                f"<p style='{_FIELD_LABEL}'>Next Step</p>"
                f"<div class='note'>{r['next_steps']}</div>"
            )
        if r["questions_comments"]:
            fields_html += (
                f"<p style='{_FIELD_LABEL}'>Question / Comment</p>"
                f"<div class='note'>{r['questions_comments']}</div>"
            )

        cards += (
            f"<div style='border:1px solid #eee;border-radius:4px;padding:14px 16px;margin-bottom:12px'>"
            f"<div><strong>{r['name'] or '(no name)'}</strong> {visit_badge} {campus_badge}</div>"
            f"<div style='font-size:.85em;color:#888;margin-top:2px'>{contact}</div>"
            f"{fields_html}"
            f"</div>"
        )

    body = (
        banner
        + f"<p style='color:#888;font-size:.9em'>Total cards: {total} &nbsp;|&nbsp; "
        f"Showing {len(rows)} with next step, comment, or prayer request</p>"
        + cards
    )
    return subject, _wrap("Next Steps &amp; Comments", service_date, body)


# ── Donna: attendance summary ─────────────────────────────────────────────────

def donna_report(service_date: str, updated: bool = False) -> tuple[str, str]:
    """Return (subject, html) for Donna — attendance counts and breakdown."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT m.name, m.email, m.phone, cc.campus,
                   MAX(fu.id IS NOT NULL) AS is_first_visit
            FROM connect_cards cc
            JOIN members m ON m.id = cc.member_id
            LEFT JOIN follow_ups fu ON fu.card_id = cc.id
                                   AND fu.note = 'First-time visitor'
            WHERE cc.service_date = ?
            GROUP BY cc.id
            ORDER BY cc.campus, is_first_visit DESC, m.name
            """,
            (service_date,),
        ).fetchall()

    subject = _subject("Attendance Report", service_date, updated)
    banner = _UPDATED_BANNER if updated else ""

    total = len(rows)
    first_time = sum(1 for r in rows if r["is_first_visit"])
    returning = total - first_time
    wilmington = sum(1 for r in rows if r["campus"] == "Wilmington")
    online = sum(1 for r in rows if r["campus"] == "Online")

    stats = (
        f"<div style='margin:16px 0'>"
        f"<div class='stat-box'><div class='stat'>{total}</div><div class='stat-label'>Total</div></div>"
        f"<div class='stat-box'><div class='stat'>{first_time}</div><div class='stat-label'>First Visit</div></div>"
        f"<div class='stat-box'><div class='stat'>{returning}</div><div class='stat-label'>Returning</div></div>"
        f"<div class='stat-box'><div class='stat'>{wilmington}</div><div class='stat-label'>Wilmington</div></div>"
        f"<div class='stat-box'><div class='stat'>{online}</div><div class='stat-label'>Online</div></div>"
        f"</div>"
    )

    if not rows:
        body = banner + stats + f"<p class='empty'>No connect cards submitted for {service_date}.</p>"
        return subject, _wrap("Attendance Report", service_date, body)

    table_rows = ""
    for r in rows:
        visit_badge = (
            "<span class='badge first'>First visit</span>"
            if r["is_first_visit"]
            else "<span class='badge returning'>Returning</span>"
        )
        campus_badge = f"<span class='badge campus'>{r['campus'] or '—'}</span>"
        contact = r["email"] or r["phone"] or "—"
        table_rows += (
            f"<tr>"
            f"<td>{r['name'] or '(no name)'}</td>"
            f"<td>{campus_badge}</td>"
            f"<td>{visit_badge}</td>"
            f"<td><small>{contact}</small></td>"
            f"</tr>"
        )

    body = (
        banner
        + stats
        + "<h2>All Submissions</h2>"
        + "<table><thead><tr><th>Name</th><th>Campus</th><th>Visit Type</th><th>Contact</th></tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return subject, _wrap("Attendance Report", service_date, body)


# ── Kaci: prayer requests ─────────────────────────────────────────────────────

def kaci_report(service_date: str, updated: bool = False) -> tuple[str, str]:
    """Return (subject, html) for Kaci — prayer requests."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT m.name, cc.campus, pr.request_text, pr.leadership_only
            FROM connect_cards cc
            JOIN members m ON m.id = cc.member_id
            JOIN prayer_requests pr ON pr.card_id = cc.id
            WHERE cc.service_date = ?
            ORDER BY pr.leadership_only DESC, m.name
            """,
            (service_date,),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM connect_cards WHERE service_date = ?",
            (service_date,),
        ).fetchone()[0]

    subject = _subject("Prayer Requests", service_date, updated)
    banner = _UPDATED_BANNER if updated else ""

    if not rows:
        body = (
            banner
            + f"<p class='empty'>No prayer requests submitted for {service_date}.</p>"
            + f"<p style='color:#888;font-size:.9em'>Total cards submitted: {total}</p>"
        )
        return subject, _wrap("Prayer Requests", service_date, body)

    leadership_count = sum(1 for r in rows if r["leadership_only"])
    public_count = len(rows) - leadership_count

    stats = (
        f"<div style='margin:16px 0'>"
        f"<div class='stat-box'><div class='stat'>{len(rows)}</div><div class='stat-label'>Prayer Requests</div></div>"
        f"<div class='stat-box'><div class='stat'>{public_count}</div><div class='stat-label'>Public</div></div>"
        f"<div class='stat-box'><div class='stat'>{leadership_count}</div><div class='stat-label'>Leadership Only</div></div>"
        f"</div>"
    )

    table_rows = ""
    for r in rows:
        privacy_badge = (
            "<span class='badge private'>Leadership only</span>"
            if r["leadership_only"]
            else "<span class='badge public'>Public</span>"
        )
        campus_badge = f"<span class='badge campus'>{r['campus'] or '—'}</span>"
        table_rows += (
            f"<tr>"
            f"<td><strong>{r['name'] or '(no name)'}</strong><br>{campus_badge}</td>"
            f"<td>{privacy_badge}</td>"
            f"<td><div class='note'>{r['request_text']}</div></td>"
            f"</tr>"
        )

    body = (
        banner
        + stats
        + f"<p style='color:#888;font-size:.9em'>Total cards submitted: {total}</p>"
        + "<table><thead><tr><th>Person</th><th>Visibility</th><th>Prayer Request</th></tr></thead>"
        + f"<tbody>{table_rows}</tbody></table>"
    )
    return subject, _wrap("Prayer Requests", service_date, body)
