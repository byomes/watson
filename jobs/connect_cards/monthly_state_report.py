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

"Wilmington Headcount — Historical Context" (added 2026-09-01, per Bill):
this month's average weekly Wilmington headcount from wilmington_headcounts
(jobs/gsheets/headcount_sync.py, sourced from the Catalyst Count Tracking
Google Sheet, back to 2023) against this year's YTD average and the same
calendar month's average across the past 3 years -- separate from the
existing "Wilmington Headcount Gap" section above it, which measures
connect-card tracking accuracy (a different question from "is this month
high or low for us historically"). Averages whichever prior years actually
have synced data rather than requiring all 3.

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

import requests
from dotenv import load_dotenv

from jobs.connect_cards.reports import _CSS, _wrap, _conn
from jobs.connect_cards.monthly_engagement_report import (
    _month_bounds, _month_label, _prev_month, _default_report_month,
    _tracking_started_by, _earliest_service_date,
)
from jobs.email_job.brevo_send import send_email
from core.ollama_context import size_num_ctx
from core.ollama_lock import heavy_ollama_call
from core.vacation import vacation_gate
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monthly_state_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")

BILL_EMAIL = os.getenv("BILL_EMAIL", "")

BENCHMARKS_DOC = os.path.expanduser("~/watson/memory/projects/benchmarks.md")
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"
# 600s + one retry + a warm-up ping fired before the DB work, same fix and
# same reasoning as jobs/analytics/monthly_web_engagement_report.py's
# INTERP_OLLAMA_TIMEOUT (see that file's comment) -- this model isn't kept
# warm the way jobs/intent/keep_warm.py keeps gemma3:4b warm for live chat,
# so a cold 4.7GB CPU load eats into the same budget as generation. Fixed
# there 2026-09-01 after the old 240s timeout silently dropped that
# report's interpretation section; using the proven value here from the
# start rather than waiting to hit the same failure.
OLLAMA_TIMEOUT = 600

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


# ── Wilmington headcount gap ──────────────────────────────────────────────────────

def _month_headcount_gap(conn, year: int, month: int) -> dict | None:
    """Actual Wilmington headcount total vs. card-derived total, for the subset
    of Sundays that have a synced headcount row this month (jobs/gsheets/
    headcount_sync.py). None if no Sundays synced yet — caller omits the month
    rather than treating it as zero (same principle as the weekly report)."""
    start, end = _month_bounds(year, month)
    hc_rows = conn.execute(
        "SELECT date, headcount FROM wilmington_headcounts WHERE date BETWEEN ? AND ?",
        (start, end),
    ).fetchall()
    if not hc_rows:
        return None
    dates = [r[0] for r in hc_rows]
    actual_total = sum(r[1] for r in hc_rows)
    placeholders = ",".join("?" * len(dates))
    card_total = conn.execute(
        f"SELECT COUNT(*) FROM attendance WHERE campus = 'Wilmington' AND service_date IN ({placeholders})",
        dates,
    ).fetchone()[0]
    gap = actual_total - card_total
    gap_pct = (gap / actual_total * 100) if actual_total else 0.0
    return {"weeks": len(dates), "actual": actual_total, "cards": card_total, "gap": gap, "gap_pct": gap_pct}


def _six_month_headcount_gap_trend(conn, year: int, month: int) -> list[tuple[str, float]]:
    candidates = []
    y, m = year, month
    for _ in range(6):
        candidates.append((y, m))
        y, m = _prev_month(y, m)
    candidates.reverse()

    result = []
    for y, m in candidates:
        gap_info = _month_headcount_gap(conn, y, m)
        if gap_info is None:
            continue
        result.append((f"{_month_label(y, m)[:3]} {y}", gap_info["gap_pct"]))
    return result


def _gap_trend_direction(trend: list[tuple[str, float]]) -> str:
    """Improving = gap % shrinking (connect cards capturing more of actual
    attendance), Worsening = gap % growing, Flat = neither."""
    if len(trend) < 2:
        return "Flat"
    recent = trend[-1][1]
    earlier = [pct for _, pct in trend[:-1]]
    earlier_avg = sum(earlier) / len(earlier)
    if earlier_avg == 0:
        return "Flat"
    if recent < earlier_avg * 0.97:
        return "Improving"
    if recent > earlier_avg * 1.03:
        return "Worsening"
    return "Flat"


def _headcount_values(conn, start: str, end: str) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT headcount FROM wilmington_headcounts WHERE date BETWEEN ? AND ?", (start, end),
    ).fetchall()]


def _year_to_date_headcount_avg(conn, year: int, through_month: int) -> tuple[float, int] | None:
    """Average weekly Wilmington headcount from Jan 1 through the end of
    `through_month`, same year. None if nothing synced yet."""
    _, end = _month_bounds(year, through_month)
    values = _headcount_values(conn, f"{year}-01-01", end)
    return (sum(values) / len(values), len(values)) if values else None


def _three_year_same_month_headcount_avg(conn, year: int, month: int) -> tuple[float, int] | None:
    """Average weekly Wilmington headcount for this same calendar month
    across the previous 3 years. Averages whichever of those years actually
    have synced data rather than requiring all 3 -- headcount_sync.py's
    earliest tab is 2023, so a report for an early-year month may only have
    1-2 prior years available yet. None if none of the 3 years have any."""
    values: list[int] = []
    years_with_data = 0
    for offset in (1, 2, 3):
        y = year - offset
        start, end = _month_bounds(y, month)
        year_values = _headcount_values(conn, start, end)
        if year_values:
            years_with_data += 1
            values.extend(year_values)
    return (sum(values) / len(values), years_with_data) if values else None


def _headcount_historical_context(conn, year: int, month: int) -> dict | None:
    """This month's average weekly Wilmington headcount against YTD (same
    year) and the same-month average across the past 3 years -- per Bill's
    2026-09-01 request to give elders multi-year context on the Sheet's
    real headcount number, on top of the month-over-month gap-tracking
    above. None if this month itself has no synced headcount data."""
    start, end = _month_bounds(year, month)
    this_month_values = _headcount_values(conn, start, end)
    if not this_month_values:
        return None
    ytd = _year_to_date_headcount_avg(conn, year, month)
    three_year = _three_year_same_month_headcount_avg(conn, year, month)
    return {
        "this_month_avg": sum(this_month_values) / len(this_month_values),
        "weeks": len(this_month_values),
        "ytd_avg": ytd[0] if ytd else None,
        "ytd_weeks": ytd[1] if ytd else 0,
        "three_year_avg": three_year[0] if three_year else None,
        "three_year_years": three_year[1] if three_year else 0,
    }


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


# ── Elders' framing overview (Ollama synthesis) ─────────────────────────────────

def _load_benchmarks_doc() -> str:
    try:
        with open(BENCHMARKS_DOC, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as exc:
        log.warning("benchmarks.md unavailable: %s", exc)
        return ""


def _condensed_state_text(
    month_label: str, attended_count: int, active_count: int,
    campus_snapshot: list[tuple[str, int]], non_active: list[tuple[str, int]],
    headcount_gap: dict | None, six_month_gap: list[tuple[str, float]], gap_direction: str,
    historical_context: dict | None, tiers: list[dict], movement: dict | None,
    new_visitors: int, return_rate: tuple[int, int] | None, next_steps_count: int,
    follow_ups_tracked: bool, six_month: list[tuple[str, float]],
) -> str:
    """Plain-text condensation of everything build_report() already computed,
    fed to the Ollama prompt below -- built directly from the same data
    dicts/lists used for the HTML sections rather than stripping HTML from
    them, so there's one source of truth for the numbers and no HTML-artifact
    risk in what the model reads."""
    lines = [
        f"MONTH: {month_label}",
        f"Active members who attended at least once: {attended_count} of {active_count} active members.",
    ]
    if campus_snapshot:
        lines.append("Campus breakdown: " + ", ".join(f"{name}: {count}" for name, count in campus_snapshot))
    if non_active:
        lines.append("Non-active (excluded from active count): " + ", ".join(f"{s}: {c}" for s, c in non_active))

    if headcount_gap:
        lines.append(
            f"Wilmington headcount this month: {headcount_gap['actual']} across {headcount_gap['weeks']} "
            f"synced Sunday(s), vs {headcount_gap['cards']} connect-card-derived attendance for those same "
            f"Sundays -- gap {headcount_gap['gap']} ({headcount_gap['gap_pct']:.0f}%)."
        )
    if len(six_month_gap) >= 2:
        lines.append(
            f"Headcount gap trend, last {len(six_month_gap)} synced month(s): {gap_direction} -- "
            + ", ".join(f"{label}: {pct:.0f}%" for label, pct in six_month_gap)
        )

    if historical_context:
        hc = historical_context
        lines.append(f"Average weekly Wilmington headcount this month: {hc['this_month_avg']:.0f}.")
        if hc["ytd_avg"] is not None:
            delta_pct = (hc["this_month_avg"] - hc["ytd_avg"]) / hc["ytd_avg"] * 100 if hc["ytd_avg"] else 0.0
            lines.append(
                f"Year-to-date average: {hc['ytd_avg']:.0f} ({hc['ytd_weeks']} Sundays) -- this month is "
                f"{delta_pct:+.0f}% vs. YTD."
            )
        if hc["three_year_avg"] is not None:
            delta_pct = (hc["this_month_avg"] - hc["three_year_avg"]) / hc["three_year_avg"] * 100 if hc["three_year_avg"] else 0.0
            lines.append(
                f"Same calendar month's average across the past {hc['three_year_years']} year(s): "
                f"{hc['three_year_avg']:.0f} -- this month is {delta_pct:+.0f}% vs. that historical average."
            )
        else:
            lines.append("No prior-year data yet for this calendar month -- no 3-year comparison available.")

    lines.append(
        "Engagement tiers: " + ", ".join(f"{_TIER_DISPLAY[t['label']]}: {t['count']} ({t['pct']:.0f}%)" for t in tiers)
    )

    if movement:
        lines.append(
            f"Tier movement vs. last month: {movement['up']} moved up a tier, {movement['down']} moved down, "
            f"{len(movement['new_disengaged'])} newly disengaged after being partially or highly engaged last month."
        )
        if movement["new_members"]:
            lines.append(f"{movement['new_members']} new active member(s) this month (excluded from movement comparison).")
    else:
        lines.append("Not enough history yet to compare tier movement to last month.")

    lines.append(f"First-time visitors this month: {new_visitors}.")
    if return_rate is not None:
        returned, total = return_rate
        pct = (returned / total * 100) if total else 0.0
        lines.append(f"Of {total} first-time visitor(s) from last month, {returned} ({pct:.0f}%) have returned since.")

    lines.append(f"Next-step requests logged this month: {next_steps_count}.")
    if not follow_ups_tracked:
        lines.append("No follow-up completion tracking data exists yet (follow_ups table has no records).")

    if six_month:
        lines.append(
            "6-month trend, % of active membership Highly Engaged: "
            + ", ".join(f"{label}: {pct:.0f}%" for label, pct in six_month)
        )

    return "\n".join(lines)


def _warm_up_ollama() -> None:
    """Best-effort head start on loading qwen2.5:7b, fired before the DB
    work in build_report() below -- see OLLAMA_TIMEOUT's comment."""
    try:
        requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": "hi", "stream": False}, timeout=30)
    except Exception as exc:
        log.info("Ollama warm-up ping failed (non-fatal): %s", exc)


def _ollama_synthesis(month_label: str, condensed: str, benchmarks_context: str) -> str | None:
    prompt = (
        "You are Watson, AI assistant to Dr. Bill Yomes, Senior Pastor of Catalyst Community Church "
        "in Wilmington, DE, with both a Wilmington campus and an Online campus. Write the elders' "
        f"framing overview for the Monthly State of the Church report for {month_label} -- the goal is "
        "to help the elders read this month's numbers in context, not just see them cold.\n\n"
        "Reference context -- national church attendance benchmarks and how to apply them (use this to "
        "judge whether this month's local numbers reflect normal variance, a seasonal pattern, or an "
        "actual trend for a congregation our size; never quote it verbatim):\n"
        f"{benchmarks_context or '(no benchmark research logged yet -- proceed without it)'}\n\n"
        "Write exactly one cohesive 3-4 paragraph pastoral synthesis, following these rules:\n"
        "a. Anchor every specific claim to a number from THIS MONTH'S DATA below -- no generic "
        "commentary without a figure behind it.\n"
        "b. Only use words like 'trend', 'growth', or 'decline' if the data below shows 3 or more "
        "consecutive months moving the same direction (see the headcount gap trend and 6-month "
        "engagement trend figures). A single month above or below the YTD or 3-year average is a data "
        "point, not a trend -- say so plainly if that's the case.\n"
        "c. Use the YTD and 3-year same-month average comparisons below as the primary local historical "
        "frame -- that's specifically what distinguishes 'is this month unusual for OUR church' from 'is "
        "this month unusual nationally'. Address both explicitly, and don't conflate them.\n"
        "d. If this month falls in a known seasonal window per the benchmarks context (summer, a major "
        "holiday), lead with that before any other read on the numbers.\n"
        "e. Report plainly. Only add interpretive language when it's grounded in the benchmarks context "
        "or a real sustained local pattern above; otherwise describe, don't diagnose.\n"
        "f. Comment on engagement tier health (the Highly/Partially/Not engaged/Disengaged distribution "
        "and this month's movement) and note whether the disengaged group is worth pastoral attention, "
        "without naming individuals -- names are already listed in the Tier Movement section below this "
        "overview.\n\n"
        f"THIS MONTH'S DATA:\n{condensed}\n\n"
        "Do not include a summary paragraph at the end, a 'Watson's Read:' label, or any other label. "
        "You must respond in English only. Begin writing now:"
    )
    for attempt in (1, 2):
        try:
            # bug_tracker #118/#121: runs monthly at 3am (see cron), inside this
            # codebase's established quiet-hours cluster, but keep_warm.py's
            # every-4-minute cadence has no time-of-day exception -- the busy
            # lock still matters even here.
            with heavy_ollama_call("connect_cards.monthly_state_report"):
                resp = requests.post(
                    OLLAMA_URL,
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        # bug_tracker #118: same benchmarks-doc + condensed-data
                        # pattern as jobs/connect_cards/state_of_church.py, richer
                        # here (month-over-month history, tier movement) -- sized
                        # with headroom so it doesn't silently start truncating
                        # against Ollama's ~4096 default num_ctx.
                        "options": {"num_ctx": size_num_ctx(prompt)},
                    },
                    timeout=OLLAMA_TIMEOUT,
                )
                resp.raise_for_status()
                return resp.json().get("response", "").strip() or None
        except Exception as exc:
            log.warning("Ollama synthesis failed (attempt %d/2): %s", attempt, exc)
    return None


def _alert_synthesis_failed(month_label: str) -> None:
    text = (
        f"⚠️ Monthly State of the Church ({month_label}): the elders' framing overview failed to "
        "generate after 2 attempts (Ollama timeout or error) -- the report still sent with the data "
        "sections intact, just without the synthesis at the top. Check logs/monthly_state_report.log."
    )
    log.error(text)
    if vacation_gate("system_failure", "jobs.connect_cards.monthly_state_report", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Failed to send synthesis-failure Telegram alert: %s", exc)


def _render_synthesis_html(text: str) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    return "".join(f'<p style="margin:0 0 10px">{p}</p>' for p in paragraphs)


# ── Report builder ──────────────────────────────────────────────────────────────

def build_report(year: int, month: int) -> tuple[str, str]:
    _warm_up_ollama()
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

        headcount_gap = _month_headcount_gap(conn, year, month)
        six_month_gap = _six_month_headcount_gap_trend(conn, year, month)
        gap_direction = _gap_trend_direction(six_month_gap)
        historical_context = _headcount_historical_context(conn, year, month)

        new_visitors = _first_time_visitor_count(conn, year, month)
        return_rate = _return_rate(conn, year, month)

        next_steps_count = _next_steps_this_month(conn, year, month)
        follow_ups_tracked = _follow_ups_table_has_data(conn)

    subject = f"Watson — Monthly State of the Church | {month_label}"

    attended_count = sum(1 for c in cur_counts.values() if c >= 1)

    condensed = _condensed_state_text(
        month_label=month_label, attended_count=attended_count, active_count=active_count,
        campus_snapshot=campus_snapshot, non_active=non_active,
        headcount_gap=headcount_gap, six_month_gap=six_month_gap, gap_direction=gap_direction,
        historical_context=historical_context, tiers=tiers, movement=movement,
        new_visitors=new_visitors, return_rate=return_rate, next_steps_count=next_steps_count,
        follow_ups_tracked=follow_ups_tracked, six_month=six_month,
    )
    synthesis = _ollama_synthesis(month_label, condensed, _load_benchmarks_doc())
    if synthesis:
        synthesis_html = _render_synthesis_html(synthesis)
    else:
        synthesis_html = (
            "<p class='empty'>Framing overview unavailable this month — Ollama didn't respond in "
            "time after two attempts. The data below is unaffected; Bill's been alerted to check "
            "the logs.</p>"
        )
        _alert_synthesis_failed(month_label)

    body = (
        "<div style='text-align:center;margin:20px 0 28px'>"
        f"<div style='font-size:3em;font-weight:bold;color:#222'>{attended_count} "
        f"<span style='font-size:.5em;font-weight:normal;color:#888'>of {active_count}</span></div>"
        "<div style='font-size:.95em;color:#666;text-transform:uppercase;letter-spacing:.05em'>"
        f"Active Members Attended — {month_label}</div>"
        "</div>"
    )

    # Framing overview leads, ahead of the raw data -- same principle as
    # jobs/analytics/monthly_web_engagement_report.py's 2026-09-01 reorder:
    # give the elders a read on the numbers before the numbers themselves.
    body += "<h2>Elders' Framing Overview</h2>" + synthesis_html
    body += "<h2 style='margin-top:28px;padding-top:14px;border-top:2px solid #ddd'>The Data</h2>"

    body += "<h3 style='margin:14px 0 4px'>Population Snapshot</h3>"
    if campus_snapshot:
        body += "<div style='margin:8px 0 16px'>" + "".join(
            f"<div class='stat-box'><div class='stat'>{count}</div>"
            f"<div class='stat-label'>{name}</div></div>"
            for name, count in campus_snapshot
        ) + "</div>"
    non_active_line = ", ".join(f"{status}: {count}" for status, count in non_active)
    body += f"<p style='color:#888;font-size:.9em'>Non-active (excluded above): {non_active_line}</p>"

    body += "<h3 style='margin:14px 0 4px'>Wilmington Headcount Gap</h3>"
    if headcount_gap is not None:
        body += (
            f"<p>Actual Wilmington headcount this month: <strong>{headcount_gap['actual']}</strong> "
            f"across {headcount_gap['weeks']} synced Sunday(s), vs. <strong>{headcount_gap['cards']}</strong> "
            f"connect-card-derived attendance for those same Sundays — gap: "
            f"<strong>{headcount_gap['gap']}</strong> ({headcount_gap['gap_pct']:.0f}%).</p>"
            "<p style='color:#888;font-size:.85em'>This is an aggregate coverage check, not a "
            "person-level correction — the headcount has no names attached, so it can show "
            "<em>that</em> Wilmington attendance is undercounted and by how much, but not "
            "<em>which</em> specific members are the ones missing from connect-card attendance.</p>"
        )
        if len(six_month_gap) >= 2:
            body += (
                f"<p>Gap trend (last {len(six_month_gap)} synced month(s)): <strong>{gap_direction}</strong></p>"
                "<table><thead><tr><th>Month</th><th>Gap %</th></tr></thead><tbody>"
                + "".join(f"<tr><td>{label}</td><td>{pct:.0f}%</td></tr>" for label, pct in six_month_gap)
                + "</tbody></table>"
            )
    else:
        body += "<p class='empty'>No headcount data synced yet this month.</p>"

    body += "<h3 style='margin:14px 0 4px'>Wilmington Headcount — Historical Context</h3>"
    if historical_context is not None:
        hc = historical_context
        parts = [
            f"<p>Average weekly Wilmington headcount this month: "
            f"<strong>{hc['this_month_avg']:.0f}</strong> (across {hc['weeks']} synced Sunday(s)).</p>"
        ]
        if hc["ytd_avg"] is not None:
            delta = hc["this_month_avg"] - hc["ytd_avg"]
            delta_pct = (delta / hc["ytd_avg"] * 100) if hc["ytd_avg"] else 0.0
            parts.append(
                f"<p>Vs. {year} year-to-date average (<strong>{hc['ytd_avg']:.0f}</strong>, "
                f"{hc['ytd_weeks']} Sunday(s)): "
                f"<strong>{'+' if delta >= 0 else ''}{delta:.0f}</strong> ({delta_pct:+.0f}%).</p>"
            )
        if hc["three_year_avg"] is not None:
            delta = hc["this_month_avg"] - hc["three_year_avg"]
            delta_pct = (delta / hc["three_year_avg"] * 100) if hc["three_year_avg"] else 0.0
            years_note = (
                f"{hc['three_year_years']} prior year(s)" if hc["three_year_years"] < 3
                else "the past 3 years"
            )
            parts.append(
                f"<p>Vs. {_month_label(year, month).split()[0]} average across {years_note} "
                f"(<strong>{hc['three_year_avg']:.0f}</strong>): "
                f"<strong>{'+' if delta >= 0 else ''}{delta:.0f}</strong> ({delta_pct:+.0f}%).</p>"
            )
        else:
            parts.append(
                "<p style='color:#888;font-size:.85em'>No prior-year data synced yet for this "
                "calendar month — 3-year comparison unavailable.</p>"
            )
        body += "".join(parts)
    else:
        body += "<p class='empty'>No headcount data synced yet this month.</p>"

    body += (
        "<h3 style='margin:14px 0 4px'>Engagement Tiers</h3>"
        "<table><thead><tr><th>Tier</th><th>Members</th><th>% of Active</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td>{_TIER_DISPLAY[t['label']]}</td><td>{t['count']} of {active_count}</td><td>{t['pct']:.0f}%</td></tr>"
            for t in tiers
        )
        + "</tbody></table>"
    )

    body += "<h3 style='margin:14px 0 4px'>Tier Movement vs. Last Month</h3>"
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

    body += "<h3 style='margin:14px 0 4px'>New People Funnel</h3>"
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

    body += "<h3 style='margin:14px 0 4px'>Follow-Up Completion</h3>"
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
            "<h3 style='margin:14px 0 4px'>6-Month Engagement Trend</h3>"
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
