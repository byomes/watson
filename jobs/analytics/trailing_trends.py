"""jobs/analytics/trailing_trends.py — 6-month trailing trend data across all
three monthly-web-engagement-report sources: engagement_sheet_metrics,
ga4_engagement_weekly (rolled up to monthly), and connect_card_monthly_rollup.

A single month-over-month delta isn't enough to call something a "trend" —
the same reasoning jobs/connect_cards/state_of_church.py already applies to
attendance (3+ consecutive weeks outside the normal range required before
using language like "decline"). This gives the report's Interpretation &
Recommendations section (jobs/analytics/monthly_web_engagement_report.py)
a real trailing series to check trend language against, instead of a
single month's move.

Standalone module — does not import from monthly_web_engagement_report.py,
since that module imports this one; importing back would be circular.
"""
from __future__ import annotations

import calendar
from datetime import date

_GA4_METRICS = ("totalUsers", "newUsers", "sessions", "screenPageViews", "engagementRate")


# ── Date helpers (small, deliberately not shared with
#    monthly_web_engagement_report.py — see module docstring) ──────────────

def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()


def _trailing_months(year: int, month: int, n: int = 6) -> list[tuple[int, int]]:
    """Last n (year, month) pairs ending at (year, month) inclusive, oldest first."""
    months = [(year, month)]
    for _ in range(n - 1):
        months.append(_prev_month(*months[-1]))
    return list(reversed(months))


def _direction_and_streak(series: list) -> tuple[str, int]:
    """Direction + length of the consecutive same-direction run ending at the
    most recent value, ignoring gaps (None entries). `series` is oldest-first."""
    vals = [v for v in series if v is not None]
    if len(vals) < 2:
        return "Flat", 0

    deltas = [1 if b > a else (-1 if b < a else 0) for a, b in zip(vals, vals[1:])]

    streak = 0
    last_sign = None
    for d in reversed(deltas):
        if d == 0:
            break
        if last_sign is None:
            last_sign = d
            streak = 1
        elif d == last_sign:
            streak += 1
        else:
            break

    if last_sign is None:
        return "Flat", 0
    return ("Growing" if last_sign > 0 else "Declining"), streak


# ── Per-source series builders ──────────────────────────────────────────────

def _connect_card_series(conn, months: list[tuple[int, int]]) -> dict:
    n = len(months)
    campuses: dict[str, list] = {}
    totals: list = [None] * n
    for i, (y, m) in enumerate(months):
        start, _ = _month_bounds(y, m)
        rows = conn.execute(
            "SELECT campus, count FROM connect_card_monthly_rollup WHERE month = ?",
            (start,),
        ).fetchall()
        if rows:
            totals[i] = sum(r["count"] for r in rows)
        for r in rows:
            campuses.setdefault(r["campus"], [None] * n)[i] = r["count"]

    direction, streak = _direction_and_streak(totals)
    return {
        "total": totals,
        "by_campus": campuses,
        "direction": direction,
        "consecutive_months": streak,
    }


def _ga4_monthly_totals(conn, year: int, month: int) -> dict:
    """Sums summable GA4 metrics across the month's weeks, and computes a
    sessions-weighted engagementRate — same weighting logic as
    monthly_web_engagement_report.py's own weekly->monthly rollups, since
    engagementRate is a ratio and can't just be added across weeks."""
    start, end = _month_bounds(year, month)
    weeks = [
        r["week_start"] for r in conn.execute(
            "SELECT DISTINCT week_start FROM ga4_engagement_weekly WHERE week_start BETWEEN ? AND ?",
            (start, end),
        ).fetchall()
    ]
    if not weeks:
        return {}

    placeholders = ",".join("?" * len(weeks))
    rows = conn.execute(
        f"""SELECT week_start, metric_name, value FROM ga4_engagement_weekly
            WHERE dimension_type = 'total' AND week_start IN ({placeholders})""",
        weeks,
    ).fetchall()

    by_week: dict[str, dict] = {}
    for r in rows:
        by_week.setdefault(r["week_start"], {})[r["metric_name"]] = r["value"]

    totals = {"totalUsers": 0.0, "newUsers": 0.0, "sessions": 0.0, "screenPageViews": 0.0}
    engagement_weighted = 0.0
    session_weight = 0.0
    for week_vals in by_week.values():
        for k in totals:
            totals[k] += week_vals.get(k) or 0.0
        sessions = week_vals.get("sessions") or 0.0
        session_weight += sessions
        engagement_weighted += (week_vals.get("engagementRate") or 0.0) * sessions
    totals["engagementRate"] = (engagement_weighted / session_weight) if session_weight else None
    return totals


def _ga4_series(conn, months: list[tuple[int, int]]) -> dict:
    n = len(months)
    series = {k: [None] * n for k in _GA4_METRICS}
    for i, (y, m) in enumerate(months):
        totals = _ga4_monthly_totals(conn, y, m)
        for k in _GA4_METRICS:
            if totals.get(k) is not None:
                series[k][i] = totals[k]

    directions = {}
    for k in _GA4_METRICS:
        direction, streak = _direction_and_streak(series[k])
        directions[k] = {"direction": direction, "consecutive_months": streak}
    return {"series": series, "directions": directions}


def _sheet_series(conn, months: list[tuple[int, int]]) -> dict:
    n = len(months)
    series: dict[tuple, list] = {}
    for i, (y, m) in enumerate(months):
        start, _ = _month_bounds(y, m)
        rows = conn.execute(
            "SELECT section, metric_label, value_numeric, is_flagged FROM engagement_sheet_metrics WHERE month = ?",
            (start,),
        ).fetchall()
        for r in rows:
            key = (r["section"], r["metric_label"])
            if key not in series:
                series[key] = [None] * n
            if not r["is_flagged"] and r["value_numeric"] is not None:
                series[key][i] = r["value_numeric"]

    directions = {}
    for key, vals in series.items():
        direction, streak = _direction_and_streak(vals)
        directions[key] = {"direction": direction, "consecutive_months": streak}
    return {"series": series, "directions": directions}


# ── Public API ───────────────────────────────────────────────────────────────

def build_trailing_trend(conn, year: int, month: int, n_months: int = 6) -> dict:
    """6-month trailing trend across engagement_sheet_metrics,
    ga4_engagement_weekly (rolled to monthly), and connect_card_monthly_rollup,
    ending at (year, month) inclusive. `conn` is an already-open watson.db
    connection with sqlite3.Row row_factory — reused, not opened here."""
    months = _trailing_months(year, month, n_months)
    return {
        "months": [f"{y}-{m:02d}" for y, m in months],
        "connect_cards": _connect_card_series(conn, months),
        "ga4": _ga4_series(conn, months),
        "sheet": _sheet_series(conn, months),
    }


def _fmt_series(vals: list, pct: bool = False) -> str:
    def f(v):
        if v is None:
            return "—"
        return f"{v * 100:.1f}%" if pct else f"{v:,.0f}"
    return ", ".join(f(v) for v in vals)


def trailing_trend_summary_text(trend: dict) -> str:
    """Condensed plain-text rendering for the Ollama synthesis prompt — one
    line per metric, oldest-to-newest values plus the consecutive-same-
    direction streak, so the model can check its own trend claims against
    real data instead of asserting off a single month's move."""
    lines = [f"Trailing months (oldest to newest): {', '.join(trend['months'])}"]

    cc = trend["connect_cards"]
    lines.append(
        f"CONNECT CARDS TOTAL: {_fmt_series(cc['total'])} — "
        f"{cc['direction']} ({cc['consecutive_months']} consecutive months)"
    )
    for campus, vals in cc["by_campus"].items():
        lines.append(f"  {campus}: {_fmt_series(vals)}")

    ga4 = trend["ga4"]
    for metric in _GA4_METRICS:
        vals = ga4["series"][metric]
        d = ga4["directions"][metric]
        lines.append(
            f"GA4 {metric}: {_fmt_series(vals, pct=(metric == 'engagementRate'))} — "
            f"{d['direction']} ({d['consecutive_months']} consecutive months)"
        )

    sheet = trend["sheet"]
    for (section, label), vals in sheet["series"].items():
        d = sheet["directions"][(section, label)]
        lines.append(
            f"SHEET {section} / {label}: {_fmt_series(vals)} — "
            f"{d['direction']} ({d['consecutive_months']} consecutive months)"
        )

    return "\n".join(lines)
