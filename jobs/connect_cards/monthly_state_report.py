"""
Monthly State of the Church report — person-focused engagement view, emailed
to Bill, Bill Crook, and Jim Bouchat. Bill's address comes from BILL_EMAIL in
.env; Bill Crook's and Jim Bouchat's are looked up by name from
congregation.db members on every send (never hardcoded) so the list stays
correct if their contact info changes. A name with no member record, or a
member record with no email on file, logs a warning and is skipped rather
than failing the whole send.

v2: reorganized around people, not transaction counts (supersedes the
card/attendance-count framing from commit 51ced82). Every section answers
"how many people," not "how many events." Complements (does not replace)
jobs/connect_cards/state_of_church.py, which is weekly (Thu 4pm),
LLM-narrative-synthesized, with rolling averages, seasonal caveats, and
benchmark comparisons. This job is monthly and purely quantitative — no LLM
call.

Schema used (confirmed via .schema before build):
  attendance:     member_id, service_date, campus — one row per member per
                  Sunday attended. Duplicate (member_id, service_date) rows
                  exist (multiple cards same Sunday) — every query dedupes
                  via SELECT DISTINCT before counting.
  members:        active member set = active = 1 AND (member_status IS NULL
                  OR member_status = 'active') — same filter state_of_church.py
                  / missed_report.py use. campus_preference (Wilmington/
                  Online/Hybrid) and first_visit_date (tenure — populated for
                  156/158 members) both usable directly.
  next_steps:     member_id, card_id, step, date, created_at — 47 rows, step
                  is categorical (baptism/catalyst_partner/follow_jesus/
                  grow_faith/ministry_team/small_group), `date` is the service
                  date the ask was logged. Usable directly for section 5.
  follow_ups:     member_id, card_id, note, status — table exists but has
                  ZERO rows across the entire dataset (checked, not just this
                  month) — no follow-up completion has ever been recorded
                  through it. Section 5 (next_steps -> completed follow_ups)
                  is therefore omitted entirely rather than reported as a
                  misleading "0% completion" — there's no tracking mechanism
                  populated, not a real zero.
  connect_cards:  is_first_visit exists but is 0 for all 1862+ rows across
                  the full dataset (every month, every campus) — the flag is
                  never set at intake. Reported as-is (0) for consistency
                  with monthly_engagement_report.py's identical figure, not
                  silently swapped for a different proxy field.

Engagement tiers (this month's distinct-Sunday attendance count, any campus,
active members only): 3+ = Highly engaged, 2 = Partially engaged, 1 = Not
engaged, 0 = Disengaged.

Cron (1st of month, 3am — runs independently of monthly_engagement_report.py's
5am slot; this job recomputes everything itself and does not reuse its totals):
  0 3 1 * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/connect_cards/monthly_state_report.py \
    >> /home/billyomes/watson/logs/monthly_state_report.log 2>&1

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_state_report
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_state_report --month 2026-06
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_state_report --to pastorbill@catalyst302.com
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_state_report --dry-run
"""

import argparse
import logging
import os
import re
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from jobs.connect_cards.reports import _CSS, _wrap, _conn
from jobs.connect_cards.monthly_engagement_report import (
    _month_bounds, _month_label, _prev_month, _default_report_month,
    _tracking_started_by, _earliest_service_date,
)
from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monthly_state_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")

BILL_EMAIL = os.getenv("BILL_EMAIL", "")

_ACTIVE_FILTER = "active = 1 AND (member_status IS NULL OR member_status = 'active')"
_NON_ACTIVE_STATUSES = ("disconnected", "non_local", "snowbird")

_TIER_ORDER = ["Highly engaged", "Partially engaged", "Not engaged", "Disengaged"]
_TIER_DISPLAY = {
    "Highly engaged":    "Highly engaged (3+)",
    "Partially engaged": "Partially engaged (2)",
    "Not engaged":       "Not engaged (1)",
    "Disengaged":        "Disengaged (0)",
}


def _tier(attended: int) -> str:
    if attended >= 3:
        return "Highly engaged"
    if attended == 2:
        return "Partially engaged"
    if attended == 1:
        return "Not engaged"
    return "Disengaged"


# ── Population ──────────────────────────────────────────────────────────────────

def _active_members(conn) -> list[dict]:
    rows = conn.execute(
        f"SELECT id, name, campus_preference, first_visit_date FROM members WHERE {_ACTIVE_FILTER}"
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "campus": r[2] or "(unspecified)", "first_visit_date": r[3]}
        for r in rows
    ]


def _non_active_counts(conn) -> list[tuple[str, int]]:
    rows = conn.execute(
        f"""
        SELECT member_status, COUNT(*) FROM members
        WHERE active = 1 AND member_status IN ({",".join("?" * len(_NON_ACTIVE_STATUSES))})
        GROUP BY member_status
        """,
        _NON_ACTIVE_STATUSES,
    ).fetchall()
    found = {r[0]: r[1] for r in rows}
    return [(status, found.get(status, 0)) for status in _NON_ACTIVE_STATUSES]


def _campus_snapshot(active_members: list[dict]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for m in active_members:
        counts[m["campus"]] = counts.get(m["campus"], 0) + 1
    return sorted(counts.items())


# ── Attendance / tiers ───────────────────────────────────────────────────────────

def _member_attendance_counts(conn, start: str, end: str, member_ids: set[int]) -> dict[int, int]:
    """Distinct-Sunday attendance count per member within [start, end], for the
    given member id set. Members with zero attendance are included as 0."""
    counts = {mid: 0 for mid in member_ids}
    if not member_ids:
        return counts
    rows = conn.execute(
        """
        SELECT DISTINCT member_id, service_date FROM attendance
        WHERE service_date BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchall()
    for mid, _ in rows:
        if mid in counts:
            counts[mid] += 1
    return counts


def _last_attended_map(conn, member_ids: set[int]) -> dict[int, str]:
    if not member_ids:
        return {}
    placeholders = ",".join("?" * len(member_ids))
    rows = conn.execute(
        f"SELECT member_id, MAX(service_date) FROM attendance WHERE member_id IN ({placeholders}) GROUP BY member_id",
        tuple(member_ids),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _tier_distribution(counts: dict[int, int]) -> list[dict]:
    tiers = {label: 0 for label in _TIER_ORDER}
    for attended in counts.values():
        tiers[_tier(attended)] += 1
    total = len(counts)
    return [
        {"label": label, "count": tiers[label], "pct": (tiers[label] / total * 100) if total else 0.0}
        for label in _TIER_ORDER
    ]


def _new_this_month_ids(active_members: list[dict], year: int, month: int) -> set[int]:
    start, end = _month_bounds(year, month)
    return {
        m["id"] for m in active_members
        if m["first_visit_date"] and start <= m["first_visit_date"] <= end
    }


def _tier_movement(conn, year: int, month: int, active_members: list[dict], earliest: str | None) -> dict | None:
    prev_year, prev_month = _prev_month(year, month)
    if not _tracking_started_by(earliest, prev_year, prev_month):
        return None

    new_ids = _new_this_month_ids(active_members, year, month)
    comparable_ids = {m["id"] for m in active_members} - new_ids
    if not comparable_ids:
        return {"up": 0, "down": 0, "new_disengaged": [], "new_members": len(new_ids)}

    cur_start, cur_end = _month_bounds(year, month)
    prev_start, prev_end = _month_bounds(prev_year, prev_month)

    cur_counts = _member_attendance_counts(conn, cur_start, cur_end, comparable_ids)
    prev_counts = _member_attendance_counts(conn, prev_start, prev_end, comparable_ids)

    tier_rank = {label: i for i, label in enumerate(_TIER_ORDER)}  # 0=best .. 3=worst
    up = down = 0
    new_disengaged_ids = []
    for mid in comparable_ids:
        cur_t, prev_t = _tier(cur_counts[mid]), _tier(prev_counts[mid])
        if tier_rank[cur_t] < tier_rank[prev_t]:
            up += 1
        elif tier_rank[cur_t] > tier_rank[prev_t]:
            down += 1
        if cur_t == "Disengaged" and prev_counts[mid] >= 2:
            new_disengaged_ids.append(mid)

    last_attended = _last_attended_map(conn, set(new_disengaged_ids))
    members_by_id = {m["id"]: m for m in active_members}
    new_disengaged = sorted(
        (
            {
                "name": members_by_id[mid]["name"],
                "campus": members_by_id[mid]["campus"],
                "last_attended": last_attended.get(mid),
            }
            for mid in new_disengaged_ids
        ),
        key=lambda r: r["name"] or "",
    )

    return {"up": up, "down": down, "new_disengaged": new_disengaged, "new_members": len(new_ids)}


def _six_month_highly_engaged_trend(conn, year: int, month: int, active_members: list[dict], earliest: str | None) -> list[tuple[str, float]]:
    candidates = []
    y, m = year, month
    for _ in range(6):
        candidates.append((y, m))
        y, m = _prev_month(y, m)
    candidates.reverse()

    active_ids = {m["id"] for m in active_members}
    result = []
    for y, m in candidates:
        if not _tracking_started_by(earliest, y, m):
            continue
        start, end = _month_bounds(y, m)
        counts = _member_attendance_counts(conn, start, end, active_ids)
        highly = sum(1 for c in counts.values() if _tier(c) == "Highly engaged")
        pct = (highly / len(active_ids) * 100) if active_ids else 0.0
        result.append((f"{_month_label(y, m)[:3]} {y}", pct))
    return result


# ── New people funnel ────────────────────────────────────────────────────────────

def _first_time_visitor_count(conn, year: int, month: int) -> int:
    start, end = _month_bounds(year, month)
    return conn.execute(
        "SELECT COALESCE(SUM(is_first_visit), 0) FROM connect_cards WHERE service_date BETWEEN ? AND ?",
        (start, end),
    ).fetchone()[0]


def _return_rate(conn, year: int, month: int) -> tuple[int, int] | None:
    """Of last month's first-time-visitor cards, how many of those members
    attended at least once since. Returns (returned, total) or None if last
    month had zero first-time visitors to measure."""
    prev_year, prev_month = _prev_month(year, month)
    prev_start, prev_end = _month_bounds(prev_year, prev_month)
    cur_start, _ = _month_bounds(year, month)

    visitor_ids = {
        r[0] for r in conn.execute(
            """
            SELECT DISTINCT member_id FROM connect_cards
            WHERE service_date BETWEEN ? AND ? AND is_first_visit = 1
            """,
            (prev_start, prev_end),
        ).fetchall()
    }
    if not visitor_ids:
        return None

    placeholders = ",".join("?" * len(visitor_ids))
    returned = conn.execute(
        f"""
        SELECT COUNT(DISTINCT member_id) FROM attendance
        WHERE service_date >= ? AND member_id IN ({placeholders})
        """,
        (cur_start, *visitor_ids),
    ).fetchone()[0]
    return returned, len(visitor_ids)


# ── Follow-up completion ────────────────────────────────────────────────────────

def _next_steps_this_month(conn, year: int, month: int) -> int:
    start, end = _month_bounds(year, month)
    return conn.execute(
        "SELECT COUNT(*) FROM next_steps WHERE date BETWEEN ? AND ?",
        (start, end),
    ).fetchone()[0]


def _follow_ups_table_has_data(conn) -> bool:
    return conn.execute("SELECT COUNT(*) FROM follow_ups").fetchone()[0] > 0


# ── Report builder ──────────────────────────────────────────────────────────────

def build_report(year: int, month: int) -> tuple[str, str]:
    with _conn() as conn:
        earliest = _earliest_service_date(conn)
        month_label = _month_label(year, month)

        active_members = _active_members(conn)
        active_ids = {m["id"] for m in active_members}
        active_count = len(active_members)
        non_active = _non_active_counts(conn)
        campus_snapshot = _campus_snapshot(active_members)

        start, end = _month_bounds(year, month)
        cur_counts = _member_attendance_counts(conn, start, end, active_ids)
        tiers = _tier_distribution(cur_counts)

        movement = _tier_movement(conn, year, month, active_members, earliest)
        six_month = _six_month_highly_engaged_trend(conn, year, month, active_members, earliest)

        new_visitors = _first_time_visitor_count(conn, year, month)
        return_rate = _return_rate(conn, year, month)

        next_steps_count = _next_steps_this_month(conn, year, month)
        follow_ups_tracked = _follow_ups_table_has_data(conn)

    subject = f"Watson — Monthly State of the Church | {month_label}"

    body = (
        "<div style='text-align:center;margin:20px 0 28px'>"
        f"<div style='font-size:3em;font-weight:bold;color:#222'>{active_count}</div>"
        "<div style='font-size:.95em;color:#666;text-transform:uppercase;letter-spacing:.05em'>"
        f"Active Members — {month_label}</div>"
        "</div>"
    )

    body += "<h2>Population Snapshot</h2>"
    if campus_snapshot:
        body += "<div style='margin:8px 0 16px'>" + "".join(
            f"<div class='stat-box'><div class='stat'>{count}</div>"
            f"<div class='stat-label'>{name}</div></div>"
            for name, count in campus_snapshot
        ) + "</div>"
    non_active_line = ", ".join(f"{status}: {count}" for status, count in non_active)
    body += f"<p style='color:#888;font-size:.9em'>Non-active (excluded above): {non_active_line}</p>"

    body += (
        "<h2>Engagement Tiers</h2>"
        "<table><thead><tr><th>Tier</th><th>Members</th><th>% of Active</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td>{_TIER_DISPLAY[t['label']]}</td><td>{t['count']} of {active_count}</td><td>{t['pct']:.0f}%</td></tr>"
            for t in tiers
        )
        + "</tbody></table>"
    )

    body += "<h2>Tier Movement vs. Last Month</h2>"
    if movement is not None:
        new_disengaged = movement["new_disengaged"]
        body += (
            f"<p>{movement['up']} member(s) moved up a tier, {movement['down']} moved down.</p>"
            f"<p><strong>{len(new_disengaged)}</strong> newly {_TIER_DISPLAY['Disengaged']} after being "
            f"{_TIER_DISPLAY['Partially engaged']} or {_TIER_DISPLAY['Highly engaged']} last month — "
            "the group worth a look.</p>"
        )
        if new_disengaged:
            body += (
                "<table><thead><tr><th>Name</th><th>Campus</th><th>Last Attended</th></tr></thead><tbody>"
                + "".join(
                    f"<tr><td>{r['name'] or '(no name)'}</td><td>{r['campus']}</td>"
                    f"<td>{r['last_attended'] or '—'}</td></tr>"
                    for r in new_disengaged
                )
                + "</tbody></table>"
            )
        if movement["new_members"]:
            body += f"<p style='color:#888;font-size:.9em'>{movement['new_members']} new active member(s) this month, excluded from the comparison above (no prior-month tier).</p>"
    else:
        body += "<p class='empty'>Not enough history yet to compare against last month.</p>"

    body += "<h2>New People Funnel</h2>"
    body += f"<p>First-time visitors this month: <strong>{new_visitors}</strong></p>"
    if return_rate is not None:
        returned, total = return_rate
        pct = (returned / total * 100) if total else 0.0
        body += (
            f"<p>Of {total} first-time visitor(s) from {_month_label(*_prev_month(year, month))}, "
            f"<strong>{returned}</strong> ({pct:.0f}%) have attended since.</p>"
        )
    else:
        body += "<p class='empty'>No first-time visitors last month to measure a return rate against.</p>"

    body += "<h2>Follow-Up Completion</h2>"
    if follow_ups_tracked:
        body += f"<p>{next_steps_count} next-step request(s) logged in {month_label}.</p>"
    else:
        body += (
            f"<p>{next_steps_count} next-step request(s) logged in {month_label}.</p>"
            "<p class='empty'>No follow-up completion data available — the follow_ups table "
            "has no records tracked yet.</p>"
        )

    if six_month:
        body += (
            "<h2>6-Month Engagement Trend</h2>"
            f"<p style='color:#888;font-size:.85em'>% of active membership in the "
            f"{_TIER_DISPLAY['Highly engaged']} tier</p>"
            f"<table><thead><tr><th>Month</th><th>% {_TIER_DISPLAY['Highly engaged']}</th></tr></thead><tbody>"
            + "".join(f"<tr><td>{label}</td><td>{pct:.0f}%</td></tr>" for label, pct in six_month)
            + "</tbody></table>"
        )

    return subject, _wrap("Monthly State of the Church", month_label, body)


# ── Send ─────────────────────────────────────────────────────────────────────────

_EXTRA_RECIPIENT_NAMES = ("Bill Crook", "Jim Bouchat")


def _lookup_member_email(conn, name: str) -> str | None:
    row = conn.execute(
        "SELECT email FROM members WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if row is None:
        log.warning("Recipient lookup: no member found named %r — skipping.", name)
        return None
    email = (row[0] or "").strip()
    if not email:
        log.warning("Recipient lookup: %r found but has no email on file — skipping.", name)
        return None
    return email


def _resolve_recipients(conn) -> list[str]:
    recipients = []
    if BILL_EMAIL:
        recipients.append(BILL_EMAIL)
    else:
        log.warning("BILL_EMAIL not set in .env — skipping Bill.")
    for name in _EXTRA_RECIPIENT_NAMES:
        email = _lookup_member_email(conn, name)
        if email:
            recipients.append(email)
    return recipients


def send_report(year: int, month: int, to_override: str | None = None) -> None:
    subject, html = build_report(year, month)

    if to_override:
        recipients = [to_override]
    else:
        with _conn() as conn:
            recipients = _resolve_recipients(conn)
        if not recipients:
            raise RuntimeError("No recipients resolved — check BILL_EMAIL and congregation.db member records.")

    text_fallback = re.sub(r"<[^>]+>", "", html)
    for to in recipients:
        result = send_email(
            to_email=to, to_name="", subject=subject,
            text_body=text_fallback, html_body=html, include_signature=False,
        )
        if not result["success"]:
            raise RuntimeError(f"Brevo send to {to} failed: {result['error']}")
        log.info("Sent %r to %s", subject, to)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the monthly State of the Church report.")
    parser.add_argument("--month",    default=None, help="Report month as YYYY-MM; defaults to the prior calendar month (America/New_York)")
    parser.add_argument("--to",       default=None, help="Override recipient email (defaults to BILL_EMAIL)")
    parser.add_argument("--dry-run",  action="store_true", help="Print subject/HTML without sending")
    args = parser.parse_args()

    if args.month:
        y, m = args.month.split("-")
        report_year, report_month = int(y), int(m)
    else:
        report_year, report_month = _default_report_month()

    if args.dry_run:
        subj, html_body = build_report(report_year, report_month)
        print(f"Subject: {subj}\n")
        print(html_body)
    else:
        send_report(report_year, report_month, to_override=args.to)
