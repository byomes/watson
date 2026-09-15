"""jobs/congregation/family_report.py -- on-demand "family units with
relationships" report, emailed via Brevo (not Telegram -- the list runs
long enough that a chat message isn't a good format, per Bill's
2026-09-15 request). Restricted to Dr. Bill and Donna Redman: see
bot.py's _FAMILY_REPORT_ALLOWLIST / _handle_general wiring for who can
trigger it and how.

Groups active members by household_id, listing each household's head/
spouse/children under a friendly label derived from the head (or spouse,
if no head) surname. Also lists households where a role backfill is
still needed -- surfaced by the 2026-09-15 blank-household_role sweep
([[project_congregation_duplicates]]-adjacent cleanup) -- since Bill and
Donna are exactly the people who can act on it (mark_spouse/mark_child
via the deacon app or Telegram)."""
import sqlite3
from pathlib import Path

from jobs.email_job.brevo_send import send_email

DB_PATH = Path.home() / "watson" / "data" / "congregation.db"

_ROLE_LABEL = {"head": "Head", "spouse": "Spouse", "child": "Child"}
_ROLE_SORT = {"head": 0, "spouse": 1, "child": 2}


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _household_label(members: list[dict]) -> str:
    lead = next((m for m in members if m["household_role"] in ("head", "spouse")), members[0])
    surname = lead["name"].split()[-1]
    return f"The {surname} Family"


def _load_households(conn) -> list[list[dict]]:
    rows = conn.execute(
        """
        SELECT id, name, household_id, household_role
        FROM members
        WHERE active = 1 AND household_id IS NOT NULL
        ORDER BY household_id
        """
    ).fetchall()

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["household_id"], []).append(dict(r))

    households = [members for members in groups.values() if len(members) >= 2]
    households.sort(key=lambda members: _household_label(members))
    for members in households:
        members.sort(key=lambda m: (_ROLE_SORT.get(m["household_role"], 3), m["name"]))
    return households


def build_report() -> tuple[str, str]:
    """Returns (text_body, html_body)."""
    with _conn() as conn:
        households = _load_households(conn)

    complete = [h for h in households if all(m["household_role"] for m in h)]
    needs_review = [h for h in households if not all(m["household_role"] for m in h)]

    text_lines = ["FAMILY UNITS", ""]
    html_lines = ['<h2 style="margin-bottom:4px;">Family Units</h2>']

    for members in complete:
        label = _household_label(members)
        text_lines.append(label)
        html_lines.append(f'<p style="margin:16px 0 4px;"><b>{label}</b></p><ul style="margin:0;">')
        by_role: dict[str, list[str]] = {}
        for m in members:
            by_role.setdefault(m["household_role"], []).append(m["name"])
        for role in ("head", "spouse", "child"):
            names = by_role.get(role)
            if not names:
                continue
            label_word = _ROLE_LABEL[role] if len(names) == 1 else _ROLE_LABEL[role] + "ren" if role == "child" else _ROLE_LABEL[role] + "s"
            text_lines.append(f"  {label_word}: {', '.join(names)}")
            html_lines.append(f"<li>{label_word}: {', '.join(names)}</li>")
        text_lines.append("")
        html_lines.append("</ul>")

    if needs_review:
        text_lines.append("")
        text_lines.append("NEEDS A ROLE ASSIGNED (grouped together, but who's head/spouse/child isn't marked)")
        html_lines.append('<h2 style="margin:24px 0 4px;">Needs a Role Assigned</h2>')
        html_lines.append('<p style="margin:0 0 8px;color:#666;">Grouped together in the same household, but who\'s head/spouse/child isn\'t marked yet.</p>')
        for members in needs_review:
            names = ", ".join(m["name"] for m in members)
            text_lines.append(f"  {names}")
            html_lines.append(f"<p style='margin:4px 0;'>{names}</p>")

    text_body = "\n".join(text_lines).rstrip() + "\n"
    html_body = "\n".join(html_lines)
    return text_body, html_body


_RECIPIENT_LOOKUP = {
    "Bill Yomes": "Pastor Bill Yomes",
    "Donna Redman": "Donna Redman",
}


def resolve_recipient(sender_name: str) -> tuple[str, str] | None:
    """(email, display_name) for an allowed requester, by exact name match
    in congregation.db, or None if not found. Kept as a live lookup (not a
    hardcoded email) so it never goes stale if either person's email
    changes on file."""
    query_name = _RECIPIENT_LOOKUP.get(sender_name)
    if not query_name:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT name, email FROM members WHERE name = ? AND email IS NOT NULL AND email != ''",
            (query_name,),
        ).fetchone()
    return (row["email"], row["name"]) if row else None


def send_family_report(sender_name: str) -> tuple[bool, str]:
    recipient = resolve_recipient(sender_name)
    if not recipient:
        return False, f"I don't have an email on file to send the family report to for {sender_name}."

    to_email, to_name = recipient
    text_body, html_body = build_report()
    result = send_email(
        to_email=to_email,
        to_name=to_name,
        subject="Family Units Report",
        text_body=text_body,
        html_body=html_body,
        tags=["family_report"],
    )
    if not result["success"]:
        return False, f"Sorry, that email failed to send: {result['error']}"
    return True, f"Sent the family report to {to_email}."
