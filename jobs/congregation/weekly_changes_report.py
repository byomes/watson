"""jobs/congregation/weekly_changes_report.py — weekly email summary of new
rows added to congregation.db, for Bill and Donna to sanity-check what came
through the growing set of /cat/ tools (papercards, attendance, deacons) and
the email-intake pipeline each week.

Scope / honest limitation: congregation.db keeps no field-level audit log.
Editing an existing person's profile (deacon assignment, address, campus
preference, etc. via /cat/deacons or /cat/attendance) updates the row in
place with no before/after history retained anywhere. This report can only
surface NEW rows, grouped by their own created_at/processed_at/detected_at
timestamp -- it is a "what got added" report, not a full change/edit audit.
If Bill wants true field-level edit history later, that needs a real
audit_log table + trigger or app-level logging, which is a separate build.

Prayer requests are deliberately reported as COUNTS ONLY (public vs
leadership-only split, no names or text) -- Donna is not leadership-tier
(see jobs/congregation/deacons_web.py's module docstring on why
leadership-only content is excluded from her tools), and this report isn't
the place leadership-only pastoral content should ever surface just because
it happened to land in the window.

Window: the 7 days ending at run time (created_at/processed_at/detected_at
>= datetime('now', '-7 days')), not calendar-week-aligned -- whatever the
gap since the last run actually was.

Cron (Tuesday 7:30am, ONCE BILL APPROVES the report after reviewing a
--bill-only send -- not installed yet):
  30 7 * * 2 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.weekly_changes_report \
    >> /home/billyomes/watson/logs/weekly_changes_report.log 2>&1

Usage:
  python3 -m jobs.congregation.weekly_changes_report              # sends to BILL_EMAIL + DONNA_EMAIL
  python3 -m jobs.congregation.weekly_changes_report --bill-only  # review mode: BILL_EMAIL only
  python3 -m jobs.congregation.weekly_changes_report --dry-run    # prints the report, sends nothing
"""
import argparse
import os
import sqlite3
from datetime import date, timedelta

from dotenv import load_dotenv

from jobs.connect_cards.shepherding_report import _STEP_NAMES
from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

BILL_EMAIL = os.getenv("BILL_EMAIL", "")
DONNA_EMAIL = os.getenv("DONNA_EMAIL", "")

_WINDOW_DAYS = 7
_WINDOW_SQL = f"datetime('now', '-{_WINDOW_DAYS} days')"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _window_label() -> str:
    end = date.today()
    start = end - timedelta(days=_WINDOW_DAYS)
    return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"


def _fetch(conn) -> dict:
    new_members = conn.execute(
        f"SELECT name, first_visit_date, created_at FROM members "
        f"WHERE created_at >= {_WINDOW_SQL} ORDER BY created_at"
    ).fetchall()

    cards = conn.execute(
        f"""
        SELECT cc.campus, cc.service_date, cc.is_first_visit, cc.raw_text, cc.email_id,
               m.name, cc.processed_at
        FROM connect_cards cc JOIN members m ON m.id = cc.member_id
        WHERE cc.processed_at >= {_WINDOW_SQL}
        ORDER BY cc.processed_at
        """
    ).fetchall()

    manual_attendance = conn.execute(
        f"""
        SELECT m.name, a.service_date, a.campus, a.created_at
        FROM attendance a JOIN members m ON m.id = a.member_id
        WHERE a.created_at >= {_WINDOW_SQL} AND a.card_id IS NULL
        ORDER BY a.created_at
        """
    ).fetchall()

    next_steps = conn.execute(
        f"""
        SELECT ns.step, m.name, ns.created_at
        FROM next_steps ns JOIN members m ON m.id = ns.member_id
        WHERE ns.created_at >= {_WINDOW_SQL}
        ORDER BY ns.created_at
        """
    ).fetchall()

    prayer_total, prayer_leadership = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(leadership_only), 0) FROM prayer_requests "
        f"WHERE created_at >= {_WINDOW_SQL}"
    ).fetchone()

    follow_ups = conn.execute(
        f"""
        SELECT f.note, f.status, m.name, f.created_at
        FROM follow_ups f JOIN members m ON m.id = f.member_id
        WHERE f.created_at >= {_WINDOW_SQL}
        ORDER BY f.created_at
        """
    ).fetchall()

    dup_flags = conn.execute(
        f"""
        SELECT df.reason, df.status, df.member_id_a, df.member_id_b,
               ma.name AS name_a, mb.name AS name_b, df.created_at
        FROM duplicate_flags df
        JOIN members ma ON ma.id = df.member_id_a
        JOIN members mb ON mb.id = df.member_id_b
        WHERE df.created_at >= {_WINDOW_SQL}
        ORDER BY df.created_at
        """
    ).fetchall()

    conflicts = conn.execute(
        f"""
        SELECT conflict_type, existing_name, new_name, new_email, detected_at
        FROM member_conflicts
        WHERE detected_at >= {_WINDOW_SQL}
        ORDER BY detected_at
        """
    ).fetchall()

    pending_dup_total = conn.execute(
        "SELECT COUNT(*) FROM duplicate_flags WHERE status = 'pending'"
    ).fetchone()[0]
    open_followups_total = conn.execute(
        "SELECT COUNT(*) FROM follow_ups WHERE status = 'open'"
    ).fetchone()[0]
    pending_conflicts_total = conn.execute(
        "SELECT COUNT(*) FROM member_conflicts WHERE status = 'pending'"
    ).fetchone()[0]

    return {
        "new_members": new_members,
        "cards": cards,
        "manual_attendance": manual_attendance,
        "next_steps": next_steps,
        "prayer_total": prayer_total,
        "prayer_leadership": prayer_leadership,
        "follow_ups": follow_ups,
        "dup_flags": dup_flags,
        "conflicts": conflicts,
        "pending_dup_total": pending_dup_total,
        "open_followups_total": open_followups_total,
        "pending_conflicts_total": pending_conflicts_total,
    }


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _section(title: str, count: int, rows_html: str) -> str:
    return (
        f"<h3 style='font-size:.95em;text-transform:uppercase;letter-spacing:.04em;"
        f"color:#555;margin:20px 0 8px'>{_esc(title)} ({count})</h3>"
        f"{rows_html}"
    )


def build_report_html(data: dict) -> str:
    parts = [
        f"<h2 style='margin:0 0 4px'>Congregation DB Changes — {_window_label()}</h2>",
        "<p style='color:#777;font-size:.9em;margin:0 0 16px'>"
        "New records added across every /cat/ tool and email intake this week. "
        "congregation.db keeps no field-level edit history, so profile edits "
        "(address, deacon assignment, campus, etc. changed on an existing "
        "person) are not itemized here -- only newly created rows are.</p>",
    ]

    # New members
    if data["new_members"]:
        items = "".join(
            f"<li>{_esc(m['name'])} <span style='color:#999'>(first visit {m['first_visit_date'] or '—'})</span></li>"
            for m in data["new_members"]
        )
        parts.append(_section("New People", len(data["new_members"]), f"<ul style='margin:0;padding-left:20px'>{items}</ul>"))
    else:
        parts.append(_section("New People", 0, "<p style='color:#999;margin:0'>None.</p>"))

    # Connect cards
    cards = data["cards"]
    digital = [c for c in cards if c["raw_text"] or c["email_id"]]
    paper = [c for c in cards if not (c["raw_text"] or c["email_id"])]
    first_timers = [c for c in cards if c["is_first_visit"]]
    if cards:
        summary = f"<p style='margin:0 0 6px;color:#555'>{len(digital)} via the digital form, {len(paper)} entered from paper cards by staff"
        if first_timers:
            summary += f", {len(first_timers)} first-time visitor(s)"
        summary += ".</p>"
        items = "".join(
            f"<li>{_esc(c['name'])} — {_esc(c['campus'])}, {c['service_date']}"
            f"{' 🆕' if c['is_first_visit'] else ''}"
            f"{' <span style=\"color:#999\">(paper card)</span>' if not (c['raw_text'] or c['email_id']) else ''}</li>"
            for c in cards
        )
        parts.append(_section("Connect Cards Logged", len(cards), summary + f"<ul style='margin:0;padding-left:20px'>{items}</ul>"))
    else:
        parts.append(_section("Connect Cards Logged", 0, "<p style='color:#999;margin:0'>None.</p>"))

    # Manual attendance (toggled directly, not tied to a card)
    if data["manual_attendance"]:
        items = "".join(
            f"<li>{_esc(a['name'])} — {_esc(a['campus'])}, {a['service_date']}</li>"
            for a in data["manual_attendance"]
        )
        parts.append(_section(
            "Attendance Marked Directly (no card)",
            len(data["manual_attendance"]),
            f"<ul style='margin:0;padding-left:20px'>{items}</ul>",
        ))

    # Next steps
    if data["next_steps"]:
        by_step: dict[str, int] = {}
        for ns in data["next_steps"]:
            by_step[ns["step"]] = by_step.get(ns["step"], 0) + 1
        summary_items = "".join(
            f"<li>{_STEP_NAMES.get(step, step)}: {count}</li>" for step, count in by_step.items()
        )
        names_items = "".join(f"<li>{_esc(ns['name'])} — {_STEP_NAMES.get(ns['step'], ns['step'])}</li>" for ns in data["next_steps"])
        parts.append(_section(
            "Next Steps Taken",
            len(data["next_steps"]),
            f"<ul style='margin:0 0 8px;padding-left:20px'>{summary_items}</ul>"
            f"<ul style='margin:0;padding-left:20px;color:#555'>{names_items}</ul>",
        ))
    else:
        parts.append(_section("Next Steps Taken", 0, "<p style='color:#999;margin:0'>None.</p>"))

    # Prayer requests — counts only, no names/content
    prayer_note = f"<p style='margin:0'>{data['prayer_total']} submitted ({data['prayer_leadership']} leadership-only). Names and request text intentionally omitted from this report.</p>"
    parts.append(_section("Prayer Requests", data["prayer_total"], prayer_note))

    # Follow-ups
    if data["follow_ups"]:
        items = "".join(
            f"<li>{_esc(f['name'])} — {_esc(f['note'])} <span style='color:#999'>({f['status']})</span></li>"
            for f in data["follow_ups"]
        )
        parts.append(_section("Follow-Ups Logged", len(data["follow_ups"]), f"<ul style='margin:0;padding-left:20px'>{items}</ul>"))
    else:
        parts.append(_section("Follow-Ups Logged", 0, "<p style='color:#999;margin:0'>None.</p>"))

    # Data quality flags
    flag_items = []
    for df in data["dup_flags"]:
        if df["member_id_a"] == df["member_id_b"]:
            flag_items.append(f"<li>Auto-match flagged for review: {_esc(df['name_a'])} ({_esc(df['reason'])})</li>")
        else:
            flag_items.append(f"<li>Possible duplicate: {_esc(df['name_a'])} / {_esc(df['name_b'])} ({_esc(df['reason'])})</li>")
    for c in data["conflicts"]:
        flag_items.append(
            f"<li>Intake conflict ({_esc(c['conflict_type'])}): "
            f"existing {_esc(c['existing_name'])} vs. new {_esc(c['new_name'])} ({_esc(c['new_email'] or '')})</li>"
        )
    dq_count = len(data["dup_flags"]) + len(data["conflicts"])
    dq_html = f"<ul style='margin:0 0 8px;padding-left:20px'>{''.join(flag_items)}</ul>" if flag_items else "<p style='color:#999;margin:0 0 8px'>None raised this week.</p>"
    dq_html += (
        f"<p style='color:#999;font-size:.85em;margin:0'>Standing totals (all-time, not just this week): "
        f"{data['pending_dup_total']} pending duplicate flag(s), "
        f"{data['pending_conflicts_total']} pending intake conflict(s), "
        f"{data['open_followups_total']} open follow-up(s). "
        f"Review at <a href='https://wtsn.me/cat/duplicates'>wtsn.me/cat/duplicates</a>.</p>"
    )
    parts.append(_section("Data Quality Flags Raised", dq_count, dq_html))

    return "".join(parts)


def build_report_text(data: dict) -> str:
    import re
    return re.sub(r"<[^>]+>", "", build_report_html(data)).strip()


def send_weekly_changes_report(bill_only: bool = False, dry_run: bool = False) -> bool:
    with _conn() as conn:
        data = _fetch(conn)

    html = build_report_html(data)
    text = build_report_text(data)
    subject = f"Congregation DB Changes — {_window_label()}"

    if dry_run:
        print(subject)
        print(text)
        return True

    recipients = [(BILL_EMAIL, "Bill")]
    if not bill_only:
        recipients.append((DONNA_EMAIL, "Donna"))

    sent_any = False
    for email_addr, name in recipients:
        if not email_addr:
            print(f"Skipped {name}: no email address configured.")
            continue
        result = send_email(to_email=email_addr, to_name=name, subject=subject, text_body=text, html_body=html)
        if result["success"]:
            print(f"Sent to {name} <{email_addr}>")
            sent_any = True
        else:
            print(f"Failed to send to {name} <{email_addr}>: {result['error']}")

    return sent_any


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly congregation.db changes report.")
    parser.add_argument("--bill-only", action="store_true", help="Send to BILL_EMAIL only (review mode).")
    parser.add_argument("--dry-run", action="store_true", help="Print the report, send nothing.")
    args = parser.parse_args()
    send_weekly_changes_report(bill_only=args.bill_only, dry_run=args.dry_run)
