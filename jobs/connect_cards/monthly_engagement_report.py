"""
Monthly connect card engagement report — emails Kaci a monthly submission
count plus context for her Comms tracking spreadsheet.

Connect cards moved off Subsplash's own reporting; Subsplash still forwards
submissions to Gmail and jobs/connect_cards/intake.py still parses them, but
Kaci can no longer see counts in Subsplash directly. This replaces that
visibility with a monthly email covering the prior calendar month.

Schema used directly from connect_cards (confirmed via .schema before build):
  service_date, campus, is_first_visit, prayer_request, next_steps
connect_cards.campus is Wilmington/Online only (cards self-report; "Hybrid"
is a member-level classification in members.campus_preference, not something
a card records) — so the campus breakdown below reads cc.campus directly and
never joins members.

Cron (1st of month, 5am — after intake.py has had all night to catch up on
the last day of the prior month):
  0 5 1 * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/connect_cards/monthly_engagement_report.py \
    >> /home/billyomes/watson/logs/monthly_engagement_report.log 2>&1

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_engagement_report
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_engagement_report --month 2026-06
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_engagement_report --preview
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_engagement_report --dry-run
"""

import argparse
import calendar
import logging
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from jobs.connect_cards.reports import _CSS, _wrap, _conn
from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monthly_engagement_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")

KACI_EMAIL    = os.getenv("KACI_EMAIL", "")
PREVIEW_EMAIL = "bill.yomes@gmail.com"


# ── Date / range helpers ───────────────────────────────────────────────────────

def _default_report_month() -> tuple[int, int]:
    """Prior calendar month, relative to now in America/New_York."""
    today = datetime.now(NY).date()
    return _prev_month(today.year, today.month)


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()


def _month_label(year: int, month: int) -> str:
    return date(year, month, 1).strftime("%B %Y")


# ── Queries ─────────────────────────────────────────────────────────────────────

def _count_cards(conn, year: int, month: int) -> int:
    start, end = _month_bounds(year, month)
    return conn.execute(
        "SELECT COUNT(*) FROM connect_cards WHERE service_date BETWEEN ? AND ?",
        (start, end),
    ).fetchone()[0]


def _earliest_service_date(conn) -> str | None:
    return conn.execute("SELECT MIN(service_date) FROM connect_cards").fetchone()[0]


def _tracking_started_by(earliest: str | None, year: int, month: int) -> bool:
    """True if connect_cards history reaches back to (at least) the start of
    the given month, so a 0 count there is real data, not a tracking gap."""
    if not earliest:
        return False
    start, _ = _month_bounds(year, month)
    return earliest <= start


def _campus_breakdown(conn, year: int, month: int) -> list[tuple[str, int]]:
    start, end = _month_bounds(year, month)
    rows = conn.execute(
        """
        SELECT campus, COUNT(*) FROM connect_cards
        WHERE service_date BETWEEN ? AND ?
        GROUP BY campus ORDER BY campus
        """,
        (start, end),
    ).fetchall()
    return [(r[0] or "(unspecified)", r[1]) for r in rows]


def _type_breakdown(conn, year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)
    row = conn.execute(
        """
        SELECT
            COUNT(*),
            SUM(is_first_visit),
            SUM(CASE WHEN TRIM(COALESCE(prayer_request, '')) != '' THEN 1 ELSE 0 END),
            SUM(CASE WHEN TRIM(COALESCE(next_steps, ''))    != '' THEN 1 ELSE 0 END)
        FROM connect_cards
        WHERE service_date BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()
    total, first_visit, prayer, next_steps = row
    total = total or 0
    first_visit = first_visit or 0
    return {
        "first_visit": first_visit,
        "returning": total - first_visit,
        "prayer_request": prayer or 0,
        "next_steps": next_steps or 0,
    }


def _weekly_trend(conn, year: int, month: int) -> list[tuple[str, int]]:
    start, end = _month_bounds(year, month)
    rows = conn.execute(
        "SELECT service_date FROM connect_cards WHERE service_date BETWEEN ? AND ?",
        (start, end),
    ).fetchall()
    buckets: dict[date, int] = {}
    for (sd,) in rows:
        d = date.fromisoformat(sd)
        sunday = d - timedelta(days=(d.weekday() + 1) % 7)
        buckets[sunday] = buckets.get(sunday, 0) + 1
    weeks = []
    for sunday in sorted(buckets):
        saturday = sunday + timedelta(days=6)
        label = f"{sunday.strftime('%b %-d')}–{saturday.strftime('%b %-d')}"
        weeks.append((label, buckets[sunday]))
    return weeks


def _six_month_trend(conn, year: int, month: int, earliest: str | None) -> list[tuple[str, int]]:
    """Last 6 calendar months through the report month, skipping any month
    before connect_cards tracking began (first-run-ever case)."""
    candidates = []
    y, m = year, month
    for _ in range(6):
        candidates.append((y, m))
        y, m = _prev_month(y, m)
    candidates.reverse()
    return [
        (date(y, m, 1).strftime("%b %Y"), _count_cards(conn, y, m))
        for y, m in candidates
        if _tracking_started_by(earliest, y, m)
    ]


def _pct_change(current: int, previous: int) -> str:
    if previous == 0:
        return "New" if current > 0 else "flat (0 → 0)"
    pct = (current - previous) / previous * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.0f}%"


# ── Report builder ──────────────────────────────────────────────────────────────

def build_report(year: int, month: int) -> tuple[str, str]:
    """Return (subject, html) for the monthly connect card engagement report."""
    with _conn() as conn:
        earliest = _earliest_service_date(conn)
        total = _count_cards(conn, year, month)
        month_label = _month_label(year, month)

        prev_year, prev_month = _prev_month(year, month)
        mom = None
        if _tracking_started_by(earliest, prev_year, prev_month):
            prev_count = _count_cards(conn, prev_year, prev_month)
            mom = (prev_count, _pct_change(total, prev_count))

        yoy_year, yoy_month = year - 1, month
        yoy = None
        if _tracking_started_by(earliest, yoy_year, yoy_month):
            yoy_count = _count_cards(conn, yoy_year, yoy_month)
            yoy = (yoy_count, _pct_change(total, yoy_count))

        six_month = _six_month_trend(conn, year, month, earliest)

        if total > 0:
            campus_rows = _campus_breakdown(conn, year, month)
            types = _type_breakdown(conn, year, month)
            weeks = _weekly_trend(conn, year, month)
        else:
            campus_rows, types, weeks = [], None, []

    subject = f"Watson — Connect Card Engagement | {month_label}"

    headline = (
        "<div style='text-align:center;margin:20px 0 28px'>"
        f"<div style='font-size:3em;font-weight:bold;color:#222'>{total}</div>"
        "<div style='font-size:.95em;color:#666;text-transform:uppercase;letter-spacing:.05em'>"
        f"Total Connect Cards — {month_label}</div>"
        "</div>"
    )

    comparisons = ""
    if mom is not None:
        prev_count, pct = mom
        comparisons += (
            f"<p>Month-over-month: <strong>{total}</strong> vs <strong>{prev_count}</strong> "
            f"({_month_label(prev_year, prev_month)}) — {pct}</p>"
        )
    if yoy is not None:
        yoy_count, pct = yoy
        comparisons += (
            f"<p>Year-over-year: <strong>{total}</strong> vs <strong>{yoy_count}</strong> "
            f"({_month_label(yoy_year, yoy_month)}) — {pct}</p>"
        )

    body = headline + comparisons

    if total == 0:
        body += f"<p class='empty'>No connect cards submitted in {month_label}.</p>"
    else:
        if campus_rows:
            body += "<h2>Campus Breakdown</h2><div style='margin:8px 0 16px'>" + "".join(
                f"<div class='stat-box'><div class='stat'>{count}</div>"
                f"<div class='stat-label'>{name}</div></div>"
                for name, count in campus_rows
            ) + "</div>"

        if weeks:
            body += (
                "<h2>Weekly Trend</h2>"
                "<table><thead><tr><th>Week</th><th>Cards</th></tr></thead><tbody>"
                + "".join(f"<tr><td>{label}</td><td>{count}</td></tr>" for label, count in weeks)
                + "</tbody></table>"
            )

        if types:
            body += (
                "<h2>Type Breakdown</h2>"
                "<table><thead><tr><th>Type</th><th>Count</th></tr></thead><tbody>"
                f"<tr><td>First-time visitor</td><td>{types['first_visit']}</td></tr>"
                f"<tr><td>Returning</td><td>{types['returning']}</td></tr>"
                f"<tr><td>Prayer request submitted</td><td>{types['prayer_request']}</td></tr>"
                f"<tr><td>Next step requested</td><td>{types['next_steps']}</td></tr>"
                "</tbody></table>"
            )

    if six_month:
        body += (
            "<h2>6-Month Trend</h2>"
            "<table><thead><tr><th>Month</th><th>Cards</th></tr></thead><tbody>"
            + "".join(f"<tr><td>{label}</td><td>{count}</td></tr>" for label, count in six_month)
            + "</tbody></table>"
        )

    return subject, _wrap("Connect Card Engagement", month_label, body)


# ── Send ─────────────────────────────────────────────────────────────────────────

def send_report(year: int, month: int, preview: bool = False, to_override: str | None = None) -> None:
    subject, html = build_report(year, month)
    to = to_override or (PREVIEW_EMAIL if preview else KACI_EMAIL)
    if not to:
        raise RuntimeError("Recipient address is empty — check KACI_EMAIL in .env.")
    if preview and not to_override:
        subject = f"[PREVIEW] {subject}"

    text_fallback = re.sub(r"<[^>]+>", "", html)
    result = send_email(
        to_email=to, to_name="", subject=subject,
        text_body=text_fallback, html_body=html, include_signature=False,
    )
    if not result["success"]:
        raise RuntimeError(f"Brevo send to {to} failed: {result['error']}")
    log.info("Sent %r to %s", subject, to)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the monthly connect card engagement report.")
    parser.add_argument("--month",    default=None, help="Report month as YYYY-MM; defaults to the prior calendar month (America/New_York)")
    parser.add_argument("--preview",  action="store_true", help=f"Send to {PREVIEW_EMAIL} instead of Kaci")
    parser.add_argument("--to",       default=None, help="Override recipient email (takes precedence over --preview and the Kaci default)")
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
        send_report(report_year, report_month, preview=args.preview, to_override=args.to)
