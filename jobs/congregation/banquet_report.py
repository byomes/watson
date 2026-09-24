"""jobs/congregation/banquet_report.py — reporting for the annual Servant
Leaders Banquet (Bill's 2026-09-24 request): RSVP status against the
active-servant invite roster, and the service-awards reference list Donna
uses at the banquet to see who's up for a length-of-service pin.

church_events / event_registrations live in data/watson.db; team_memberships,
started_serving_date and service_pin_notes live in data/congregation.db --
these two files are never joined in a single SQL query anywhere in this
codebase (see jobs/analytics/data_chat.py's module docstring), so the RSVP
vs. roster cross-reference below is done in Python across two connections,
same pattern as jobs/events/matching.py's read-only member lookups.
"""
import os
import sqlite3
from datetime import date

from config.settings import DB_PATH

CONG_DB = os.path.expanduser("~/watson/data/congregation.db")


def _cong_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{CONG_DB}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _watson_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def find_rsvp_tracking_event(name_hint: str = "banquet") -> dict | None:
    """Most recent tracking_active=1, rsvp_tracking=1 event whose name
    contains name_hint -- looked up by name rather than a hardcoded id so
    this keeps working if the event row is ever recreated."""
    conn = _watson_conn()
    row = conn.execute(
        "SELECT id, event_name, start_date FROM church_events "
        "WHERE rsvp_tracking = 1 AND tracking_active = 1 AND event_name LIKE ? "
        "ORDER BY id DESC LIMIT 1",
        (f"%{name_hint}%",),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def rsvp_status_report(event_id: int) -> dict:
    """Cross-references team_memberships (active=1, deduped by member) as
    the invite roster against event_registrations for this event.

    Returns yes/no/no-response name lists plus adult (food) and child
    (paid childcare) headcount totals -- kept as two separate numbers per
    Bill's 2026-09-24 note that childcare is a different variable, never
    combined into one total."""
    cong = _cong_conn()
    invitees = cong.execute(
        """SELECT DISTINCT m.id AS member_id, m.name
           FROM team_memberships tm JOIN members m ON m.id = tm.member_id
           WHERE tm.active = 1"""
    ).fetchall()
    cong.close()

    wconn = _watson_conn()
    regs = wconn.execute(
        "SELECT member_id, first_name, last_name, rsvp_status, num_tickets, child_count "
        "FROM event_registrations WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    wconn.close()

    by_member = {r["member_id"]: r for r in regs if r["member_id"]}
    # A registration with no member_id (a spouse/guest, or a first-time
    # visitor with no congregation.db record) still counts toward totals
    # but can't be cross-checked against the invite roster.
    unmatched = [r for r in regs if not r["member_id"]]

    yes, no, no_response = [], [], []
    total_adults = 0
    total_children = 0

    for inv in invitees:
        reg = by_member.get(inv["member_id"])
        if reg is None:
            no_response.append(inv["name"])
        elif reg["rsvp_status"] == "no":
            no.append(inv["name"])
        else:
            yes.append(inv["name"])
            total_adults += reg["num_tickets"] or 1
            total_children += reg["child_count"] or 0

    for reg in unmatched:
        name = f"{reg['first_name'] or ''} {reg['last_name'] or ''}".strip() or "(unknown)"
        if reg["rsvp_status"] == "no":
            no.append(name)
        else:
            yes.append(name)
            total_adults += reg["num_tickets"] or 1
            total_children += reg["child_count"] or 0

    return {
        "yes": sorted(yes),
        "no": sorted(no),
        "no_response": sorted(no_response),
        "total_adults": total_adults,
        "total_children": total_children,
        "invited_count": len(invitees),
    }


def format_rsvp_summary(event_name: str, report: dict) -> str:
    lines = [
        f"\U0001F37D️ {event_name} — RSVP status",
        f"Invited (active servants): {report['invited_count']}",
        f"Yes: {len(report['yes'])}  |  No: {len(report['no'])}  |  "
        f"No response yet: {len(report['no_response'])}",
        f"Food headcount (adults): {report['total_adults']}",
        f"Childcare headcount: {report['total_children']}",
    ]
    if report["no_response"]:
        preview = ", ".join(report["no_response"][:15])
        remaining = len(report["no_response"]) - 15
        more = f" (+{remaining} more)" if remaining > 0 else ""
        lines.append(f"\nStill haven't responded: {preview}{more}")
    return "\n".join(lines)


def build_service_awards_pdf(path: str, event_name: str = "Servant Leaders Banquet") -> str:
    """Grouped by team, sorted within each team by years of service
    descending (longest-serving first), with each person's pin/award
    history alongside so Donna can see at a glance who's due for a new
    pin. Members with no started_serving_date on file sort to the bottom
    of their team rather than being dropped."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    conn = _cong_conn()
    rows = conn.execute(
        """SELECT tm.team_name, m.name, m.started_serving_date, m.service_pin_notes
           FROM team_memberships tm JOIN members m ON m.id = tm.member_id
           WHERE tm.active = 1
           ORDER BY tm.team_name, m.name"""
    ).fetchall()
    conn.close()

    today = date.today()
    by_team: dict[str, list[tuple[str, float | None, str, str]]] = {}
    for r in rows:
        years = None
        started = r["started_serving_date"] or ""
        if started:
            try:
                sd = date.fromisoformat(started[:10])
                years = round((today - sd).days / 365.25, 1)
            except ValueError:
                years = None
        by_team.setdefault(r["team_name"], []).append(
            (r["name"], years, started, r["service_pin_notes"] or "")
        )

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    story = [
        Paragraph(f"{event_name} — Service Awards Reference", styles["Title"]),
        Paragraph(
            f"Generated {today.isoformat()} — sorted by years of service within each team",
            styles["Normal"],
        ),
        Spacer(1, 0.25 * inch),
    ]

    for team_name in sorted(by_team.keys()):
        members = by_team[team_name]
        members.sort(key=lambda m: (m[1] is None, -(m[1] or 0)))
        story.append(Paragraph(team_name, styles["Heading2"]))
        data = [["Name", "Years Served", "Started", "Pins / Awards Received"]]
        for name, years, started, pins in members:
            data.append([
                name,
                f"{years}" if years is not None else "—",
                started or "—",
                pins or "—",
            ])
        table = Table(
            data,
            colWidths=[1.7 * inch, 0.9 * inch, 0.9 * inch, 2.5 * inch],
            repeatRows=1,
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(table)
        story.append(Spacer(1, 0.2 * inch))

    doc.build(story)
    return path
