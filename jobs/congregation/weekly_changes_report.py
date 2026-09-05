"""jobs/congregation/weekly_changes_report.py — weekly email summary of
identity/contact-field changes in congregation.db, so Donna can mirror
them into the church's main ChMS. Deliberately excludes everything
connect-card/attendance/prayer/next-step-related -- Bill and Donna already
read every card as it comes in, so that data has no value here; this
report exists purely to catch member records Donna needs to update or add
in the ChMS.

Tracked fields: name, email, phone, address, birthdate. Not deacon
assignment, campus preference, or status -- those are Watson-internal
pastoral/shepherding fields the ChMS doesn't need mirrored.

congregation.db keeps no field-level audit log, and members.updated_at is
useless for this: jobs/connect_cards/intake.py's member-matching bumps it
on nearly every existing attender's row nearly every week (a connect card
is processed even when no contact field actually changed), so "updated_at
in the last 7 days" would just re-surface the connect-card noise this
report is supposed to eliminate. Instead this module keeps its own tiny
snapshot table (member_field_snapshot -- read-modify-write on the five
tracked fields only, created/maintained solely by this job, no other file
touches it) and diffs the current members table against last run's
snapshot. That's what actually answers "what changed" without a real
audit trail elsewhere. First-ever run has no prior snapshot to diff
against, so it silently baselines every existing member and reports zero
profile changes (only members created within the window still show as
New People) -- expected, not a bug.

A member present in last run's snapshot but no longer in the members
table (deleted, almost always via jobs/congregation/duplicate_review.py's
merge) is reported as Removed/Merged, since that's exactly the kind of
thing Donna needs to reconcile in the ChMS too.

Window: the 7 days ending at run time, for New People / Removed only
(created_at >= datetime('now', '-7 days') / snapshot no longer present).
Profile-field changes aren't time-windowed the same way -- they're simply
"differs from the last snapshot," i.e. since the last time this report ran.

Cron (Tuesday 7:30am, ONCE BILL APPROVES the report after reviewing a
--bill-only send -- not installed yet):
  30 7 * * 2 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.weekly_changes_report \
    >> /home/billyomes/watson/logs/weekly_changes_report.log 2>&1

Usage:
  python3 -m jobs.congregation.weekly_changes_report              # sends to BILL_EMAIL + DONNA_EMAIL
  python3 -m jobs.congregation.weekly_changes_report --bill-only  # review mode: BILL_EMAIL only
  python3 -m jobs.congregation.weekly_changes_report --dry-run    # prints the report, sends nothing (snapshot NOT updated)
"""
import argparse
import os
import re
import sqlite3
from datetime import date, timedelta

from dotenv import load_dotenv

from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

BILL_EMAIL = os.getenv("BILL_EMAIL", "")
DONNA_EMAIL = os.getenv("DONNA_EMAIL", "")

_WINDOW_DAYS = 7
_WINDOW_SQL = f"datetime('now', '-{_WINDOW_DAYS} days')"

_TRACKED_FIELDS = ("name", "email", "phone", "address", "birthdate")
_FIELD_LABELS = {"name": "Name", "email": "Email", "phone": "Phone", "address": "Address", "birthdate": "Birthdate"}

_CREATE_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS member_field_snapshot (
    member_id  INTEGER PRIMARY KEY REFERENCES members(id),
    name       TEXT,
    email      TEXT,
    phone      TEXT,
    address    TEXT,
    birthdate  TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
)
"""


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _window_label() -> str:
    end = date.today()
    start = end - timedelta(days=_WINDOW_DAYS)
    return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"


def _norm(v) -> str:
    return (v or "").strip()


def _fetch(conn) -> dict:
    conn.execute(_CREATE_SNAPSHOT)

    new_members = conn.execute(
        f"SELECT id, name, email, phone, address, birthdate FROM members "
        f"WHERE created_at >= {_WINDOW_SQL} ORDER BY name COLLATE NOCASE"
    ).fetchall()
    new_ids = {m["id"] for m in new_members}

    current = {
        m["id"]: m
        for m in conn.execute(
            f"SELECT id, {', '.join(_TRACKED_FIELDS)} FROM members"
        ).fetchall()
    }
    snapshot = {
        s["member_id"]: s
        for s in conn.execute(
            f"SELECT member_id, {', '.join(_TRACKED_FIELDS)} FROM member_field_snapshot"
        ).fetchall()
    }

    changes = []
    for member_id, row in current.items():
        if member_id in new_ids:
            continue  # reported as New, not as a change against a snapshot that never existed
        prior = snapshot.get(member_id)
        if prior is None:
            continue  # never snapshotted before (first run) -- baseline silently, no diff to show
        diffs = []
        for field in _TRACKED_FIELDS:
            before, after = _norm(prior[field]), _norm(row[field])
            if before != after:
                diffs.append((field, before, after))
        if diffs:
            changes.append({"name": row["name"], "diffs": diffs})

    # Snapshot rows whose member no longer exists -- almost always a
    # duplicate_review.py merge deleting the losing record.
    removed_rows = conn.execute(
        f"""
        SELECT member_id, name FROM member_field_snapshot
        WHERE member_id NOT IN (SELECT id FROM members)
        """
    ).fetchall()
    removed = [r["name"] for r in removed_rows]

    return {
        "new_members": new_members,
        "changes": changes,
        "removed": removed,
        "current": current,
    }


def _update_snapshot(conn, current: dict) -> None:
    for member_id, row in current.items():
        conn.execute(
            f"""
            INSERT INTO member_field_snapshot (member_id, {', '.join(_TRACKED_FIELDS)}, updated_at)
            VALUES (?, {', '.join(['?'] * len(_TRACKED_FIELDS))}, datetime('now'))
            ON CONFLICT(member_id) DO UPDATE SET
              {', '.join(f"{f} = excluded.{f}" for f in _TRACKED_FIELDS)},
              updated_at = excluded.updated_at
            """,
            (member_id, *[row[f] for f in _TRACKED_FIELDS]),
        )
    # Prune snapshot rows for members deleted since last run (already
    # captured in `removed` before this runs) so they don't linger forever.
    conn.execute("DELETE FROM member_field_snapshot WHERE member_id NOT IN (SELECT id FROM members)")
    conn.commit()


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
        "New members and profile-field changes (name, email, phone, address, birthdate) "
        "in congregation.db, for mirroring into the church management system. "
        "Connect card, attendance, and prayer request activity is intentionally excluded.</p>",
    ]

    if data["new_members"]:
        items = "".join(
            f"<li><strong>{_esc(m['name'])}</strong> — "
            f"email: {_esc(m['email']) or '—'}, phone: {_esc(m['phone']) or '—'}, "
            f"address: {_esc(m['address']) or '—'}, birthdate: {m['birthdate'] or '—'}</li>"
            for m in data["new_members"]
        )
        parts.append(_section("New People", len(data["new_members"]), f"<ul style='margin:0;padding-left:20px'>{items}</ul>"))
    else:
        parts.append(_section("New People", 0, "<p style='color:#999;margin:0'>None.</p>"))

    if data["changes"]:
        items = []
        for c in data["changes"]:
            field_lines = "; ".join(
                f"{_FIELD_LABELS[f]}: {_esc(before) or '—'} → {_esc(after) or '—'}"
                for f, before, after in c["diffs"]
            )
            items.append(f"<li><strong>{_esc(c['name'])}</strong> — {field_lines}</li>")
        parts.append(_section("Profile Changes", len(data["changes"]), f"<ul style='margin:0;padding-left:20px'>{''.join(items)}</ul>"))
    else:
        parts.append(_section("Profile Changes", 0, "<p style='color:#999;margin:0'>None.</p>"))

    if data["removed"]:
        items = "".join(f"<li>{_esc(n)}</li>" for n in data["removed"])
        parts.append(_section(
            "Removed / Merged",
            len(data["removed"]),
            f"<ul style='margin:0 0 8px;padding-left:20px'>{items}</ul>"
            "<p style='color:#999;font-size:.85em;margin:0'>No longer in Watson's member list -- "
            "almost always because it was merged as a duplicate. Worth checking the ChMS for a matching duplicate.</p>",
        ))

    return "".join(parts)


def build_report_text(data: dict) -> str:
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

        # Only advance the baseline on a real send -- a --dry-run should be
        # re-runnable without silently consuming this week's diff.
        _update_snapshot(conn, data["current"])

    return sent_any


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly congregation.db identity/contact changes report.")
    parser.add_argument("--bill-only", action="store_true", help="Send to BILL_EMAIL only (review mode).")
    parser.add_argument("--dry-run", action="store_true", help="Print the report, send nothing, don't update the snapshot.")
    args = parser.parse_args()
    send_weekly_changes_report(bill_only=args.bill_only, dry_run=args.dry_run)
