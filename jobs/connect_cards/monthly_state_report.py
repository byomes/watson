"""
Monthly State of the Church report — quantitative attendance consistency and
full-month engagement numbers, emailed to Bill only.

Complements (does not replace) jobs/connect_cards/state_of_church.py, which is
weekly (Thu 4pm), LLM-narrative-synthesized, with rolling averages, seasonal
caveats, and benchmark comparisons. This job is monthly and purely quantitative
— no LLM call.

Schema used (confirmed via .schema before build):
  attendance:     member_id, service_date, campus, card_id — one row per
                  member per Sunday attended; service_date IS the Sunday date
                  (verified against June 2026: rows fall on 06-07/14/21/28,
                  all Sundays). Duplicate (member_id, service_date) rows exist
                  (multiple cards same Sunday) — every query here dedupes via
                  SELECT DISTINCT before counting "attendance instances".
  members:        active member set = active = 1 AND (member_status IS NULL
                  OR member_status = 'active') — same filter state_of_church.py
                  and missed_report.py use; excluded from every bucket and
                  denominator below.
  connect_cards:  is_first_visit, campus, prayer_request, next_steps — reused
                  directly via monthly_engagement_report.py's query helpers
                  rather than recomputed, so the two reports never drift.

Sundays in the report month come from the calendar (not distinct attendance
dates), so a Sunday with zero attendance (e.g. service canceled) still counts
toward the denominator.

Cron (1st of month, 6am — after monthly_engagement_report.py's 5am slot):
  0 6 1 * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/connect_cards/monthly_state_report.py \
    >> /home/billyomes/watson/logs/monthly_state_report.log 2>&1

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_state_report
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_state_report --month 2026-06
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_state_report --to bill.yomes@gmail.com
  PYTHONPATH=/home/billyomes/watson python -m jobs.connect_cards.monthly_state_report --dry-run
"""

import argparse
import calendar
import logging
import os
import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from jobs.connect_cards.reports import _CSS, _wrap, _conn
from jobs.connect_cards.monthly_engagement_report import (
    _month_bounds, _month_label, _prev_month, _default_report_month,
    _tracking_started_by, _earliest_service_date,
    _count_cards, _campus_breakdown, _type_breakdown,
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


# ── Attendance helpers ───────────────────────────────────────────────────────────

def _sundays_in_month(year: int, month: int) -> list[date]:
    last_day = calendar.monthrange(year, month)[1]
    return [
        date(year, month, d)
        for d in range(1, last_day + 1)
        if date(year, month, d).weekday() == 6
    ]


def _active_member_ids(conn) -> list[int]:
    return [r[0] for r in conn.execute(f"SELECT id FROM members WHERE {_ACTIVE_FILTER}").fetchall()]


def _distinct_attendance(conn, start: str, end: str) -> list[tuple[int, str]]:
    """(member_id, service_date) pairs, deduped, restricted to active members."""
    rows = conn.execute(
        f"""
        SELECT DISTINCT a.member_id, a.service_date
        FROM attendance a
        JOIN members m ON m.id = a.member_id
        WHERE a.service_date BETWEEN ? AND ?
          AND m.{_ACTIVE_FILTER}
        """,
        (start, end),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _headline_rate(conn, year: int, month: int, active_count: int) -> tuple[int, int, float]:
    """Returns (instances, num_sundays, pct)."""
    start, end = _month_bounds(year, month)
    sundays = {d.isoformat() for d in _sundays_in_month(year, month)}
    rows = _distinct_attendance(conn, start, end)
    instances = sum(1 for _, sd in rows if sd in sundays)
    num_sundays = len(sundays)
    denom = active_count * num_sundays
    pct = (instances / denom * 100) if denom else 0.0
    return instances, num_sundays, pct


_BUCKET_ORDER = ["All weeks", "Most weeks", "About half", "Occasional", "None"]
_BUCKET_RANGES = {
    "All weeks":  (100, 100),
    "Most weeks": (75, 99),
    "About half": (50, 74),
    "Occasional": (1, 49),
    "None":       (0, 0),
}


def _consistency_buckets(conn, year: int, month: int, active_ids: list[int]) -> list[dict]:
    num_sundays = len(_sundays_in_month(year, month))
    if not active_ids or num_sundays == 0:
        return []

    sunday_set = {d.isoformat() for d in _sundays_in_month(year, month)}
    start, end = _month_bounds(year, month)
    rows = _distinct_attendance(conn, start, end)

    per_member = {mid: 0 for mid in active_ids}
    for mid, sd in rows:
        if sd in sunday_set and mid in per_member:
            per_member[mid] += 1

    counts = {label: 0 for label in _BUCKET_ORDER}
    for attended in per_member.values():
        pct = attended / num_sundays * 100
        for label in _BUCKET_ORDER:
            lo, hi = _BUCKET_RANGES[label]
            if lo <= pct <= hi:
                counts[label] += 1
                break

    total_active = len(active_ids)
    result = []
    for label in _BUCKET_ORDER:
        cnt = counts[label]
        result.append({
            "label": label,
            "count": cnt,
            "pct_of_active": (cnt / total_active * 100) if total_active else 0.0,
        })
    return result


def _ninety_day_trend(conn, year: int, month: int, earliest: str | None) -> list[tuple[date, int]]:
    """Weekly attendance totals for the trailing 13 Sundays through the report
    month's last Sunday, clipped to when tracking actually began."""
    _, end = _month_bounds(year, month)
    end_date = date.fromisoformat(end)
    last_sunday = end_date - timedelta(days=(end_date.weekday() + 1) % 7)
    window_start = last_sunday - timedelta(weeks=12)

    rows = _distinct_attendance(conn, window_start.isoformat(), last_sunday.isoformat())
    sunday_dates = [last_sunday - timedelta(weeks=i) for i in range(13)]
    counts = {d: 0 for d in sunday_dates}
    for _, sd in rows:
        d = date.fromisoformat(sd)
        if d in counts:
            counts[d] += 1

    trend = [(d, counts[d]) for d in sorted(counts) if not earliest or d.isoformat() >= earliest]
    return trend


def _trend_callout(trend: list[tuple[date, int]]) -> str | None:
    if len(trend) < 8:
        return None
    recent = [c for _, c in trend[-4:]]
    prior = [c for _, c in trend[-8:-4]]
    recent_avg = sum(recent) / len(recent)
    prior_avg = sum(prior) / len(prior)
    if prior_avg == 0:
        return None
    delta_pct = (recent_avg - prior_avg) / prior_avg * 100
    if delta_pct > 5:
        return f"Trending up — last 4 weeks averaged {recent_avg:.0f}, up {delta_pct:.0f}% from the 4 weeks before."
    if delta_pct < -5:
        return f"Trending down — last 4 weeks averaged {recent_avg:.0f}, down {abs(delta_pct):.0f}% from the 4 weeks before."
    return f"Flat — last 4 weeks averaged {recent_avg:.0f}, in line with the 4 weeks before."


def _six_month_attendance_trend(conn, year: int, month: int, earliest: str | None) -> list[tuple[str, int]]:
    candidates = []
    y, m = year, month
    for _ in range(6):
        candidates.append((y, m))
        y, m = _prev_month(y, m)
    candidates.reverse()

    result = []
    for y, m in candidates:
        if not _tracking_started_by(earliest, y, m):
            continue
        start, end = _month_bounds(y, m)
        result.append((date(y, m, 1).strftime("%b %Y"), len(_distinct_attendance(conn, start, end))))
    return result


def _dropoff_count(conn, year: int, month: int, active_ids: list[int], earliest: str | None) -> int | None:
    """Active members with 2+ attendances last month but 0 this (report) month."""
    prev_year, prev_month = _prev_month(year, month)
    if not _tracking_started_by(earliest, prev_year, prev_month):
        return None

    active_set = set(active_ids)
    prev_start, prev_end = _month_bounds(prev_year, prev_month)
    cur_start, cur_end = _month_bounds(year, month)

    prev_counts: dict[int, int] = {}
    for mid, _ in _distinct_attendance(conn, prev_start, prev_end):
        if mid in active_set:
            prev_counts[mid] = prev_counts.get(mid, 0) + 1

    cur_attendees = {mid for mid, _ in _distinct_attendance(conn, cur_start, cur_end)}

    return sum(1 for mid, cnt in prev_counts.items() if cnt >= 2 and mid not in cur_attendees)


# ── Report builder ──────────────────────────────────────────────────────────────

def build_report(year: int, month: int) -> tuple[str, str]:
    with _conn() as conn:
        earliest = _earliest_service_date(conn)
        month_label = _month_label(year, month)

        active_ids = _active_member_ids(conn)
        active_count = len(active_ids)

        instances, num_sundays, pct = _headline_rate(conn, year, month, active_count)
        buckets = _consistency_buckets(conn, year, month, active_ids)

        cc_total = _count_cards(conn, year, month)
        cc_campus = _campus_breakdown(conn, year, month) if cc_total else []
        cc_types = _type_breakdown(conn, year, month) if cc_total else None

        ninety_day = _ninety_day_trend(conn, year, month, earliest)
        callout = _trend_callout(ninety_day)
        six_month = _six_month_attendance_trend(conn, year, month, earliest)
        dropoff = _dropoff_count(conn, year, month, active_ids, earliest)

    subject = f"Watson — Monthly State of the Church | {month_label}"

    headline = (
        "<div style='text-align:center;margin:20px 0 28px'>"
        f"<div style='font-size:3em;font-weight:bold;color:#222'>{pct:.0f}%</div>"
        "<div style='font-size:.95em;color:#666;text-transform:uppercase;letter-spacing:.05em'>"
        f"Average Attendance Rate — {month_label}</div>"
        f"<div style='font-size:.85em;color:#888;margin-top:6px'>"
        f"{instances} attendance-instances across {num_sundays} Sunday(s), {active_count} active members</div>"
        "</div>"
    )

    body = headline

    if buckets:
        body += (
            "<h2>Attendance Consistency</h2>"
            "<table><thead><tr><th>Bucket</th><th>Members</th><th>% of Active</th></tr></thead><tbody>"
            + "".join(
                f"<tr><td>{b['label']}</td>"
                f"<td>{b['count']} of {active_count}</td>"
                f"<td>{b['pct_of_active']:.0f}%</td></tr>"
                for b in buckets
            )
            + "</tbody></table>"
        )

    body += "<h2>Connect Card Summary</h2>"
    if cc_total:
        body += f"<p><strong>{cc_total}</strong> connect cards submitted in {month_label}.</p>"
        if cc_campus:
            body += "<div style='margin:8px 0 16px'>" + "".join(
                f"<div class='stat-box'><div class='stat'>{count}</div>"
                f"<div class='stat-label'>{name}</div></div>"
                for name, count in cc_campus
            ) + "</div>"
        if cc_types:
            body += (
                "<table><thead><tr><th>Type</th><th>Count</th></tr></thead><tbody>"
                f"<tr><td>First-time visitor</td><td>{cc_types['first_visit']}</td></tr>"
                f"<tr><td>Returning</td><td>{cc_types['returning']}</td></tr>"
                f"<tr><td>Prayer request submitted</td><td>{cc_types['prayer_request']}</td></tr>"
                f"<tr><td>Next step requested</td><td>{cc_types['next_steps']}</td></tr>"
                "</tbody></table>"
            )
            body += f"<p style='color:#888;font-size:.9em'>New people this month: <strong>{cc_types['first_visit']}</strong></p>"
    else:
        body += f"<p class='empty'>No connect cards submitted in {month_label}.</p>"

    if ninety_day:
        body += (
            "<h2>90-Day Trend</h2>"
            + (f"<p>{callout}</p>" if callout else "")
            + "<table><thead><tr><th>Week of</th><th>Attendance</th></tr></thead><tbody>"
            + "".join(f"<tr><td>{d.strftime('%b %-d')}</td><td>{c}</td></tr>" for d, c in ninety_day)
            + "</tbody></table>"
        )

    if six_month:
        body += (
            "<h2>6-Month Trend</h2>"
            "<table><thead><tr><th>Month</th><th>Attendance</th></tr></thead><tbody>"
            + "".join(f"<tr><td>{label}</td><td>{count}</td></tr>" for label, count in six_month)
            + "</tbody></table>"
        )

    if dropoff is not None:
        body += (
            "<h2>Drop-Off Flag</h2>"
            f"<p><strong>{dropoff}</strong> active member(s) attended 2+ times in "
            f"{_month_label(*_prev_month(year, month))} but 0 times in {month_label}. "
            "See the missed report for names.</p>"
        )

    return subject, _wrap("Monthly State of the Church", month_label, body)


# ── Send ─────────────────────────────────────────────────────────────────────────

def send_report(year: int, month: int, to_override: str | None = None) -> None:
    subject, html = build_report(year, month)
    to = to_override or BILL_EMAIL
    if not to:
        raise RuntimeError("Recipient address is empty — check BILL_EMAIL in .env.")

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
