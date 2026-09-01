"""jobs/analytics/monthly_web_engagement_report.py — Monthly church web/
digital engagement report, combining the Catalyst Tracking Sheet, GA4
(property 509598079), and congregation.db's connect_cards table.

Named monthly_web_engagement_report.py specifically — NOT
monthly_engagement_report.py, which is jobs/connect_cards/
monthly_engagement_report.py, an unrelated existing report to Kaci.

Runs the three import jobs (sheet_import, ga4_import, connect_card_rollup)
first, then builds and sends an HTML email to Bill only (BILL_EMAIL in
.env). Adding Kaci or other recipients is a follow-up decision once this is
confirmed working — not done here.

Report sections, in send order (reordered 2026-09-01 per Bill: the numbers-
first version left the team without help deciphering them — the written
analysis now leads, everything else follows under one "The Data" break):

  1. Interpretation & Recommendations — an Ollama (qwen2.5:7b) synthesis
     grounded in this month's data sections below, a 6-month trailing trend
     across all three sources (jobs/analytics/trailing_trends.py — "trend"
     language requires 3+ consecutive months in the same direction),
     church_events for the report month, and
     memory/projects/web_engagement_benchmarks.md for peer-benchmark
     context. Never resolves the reconciliation mismatch (section 4 below)
     — flags it, same as that section itself. Structured under three fixed
     sub-headers (Growing Visitors & Connect Cards / Social Reach / Email
     Engagement) so no one channel dominates just because it swung the most
     this month. Generation gets a 600s timeout, one retry, and a head-start
     warm-up ping (_warm_up_interpretation_model()) fired before the DB
     work below — qwen2.5:7b isn't kept warm the way jobs/intent/
     keep_warm.py keeps gemma3:4b warm for live chat, so a cold 4.7GB
     CPU load previously ate into the same budget as generation and blew
     the old 240s timeout outright (confirmed via the August 2026 send
     log). On ultimate failure the report still sends with the data
     sections intact, with a plain "unavailable this month" note in place
     of this section, and Bill gets a Telegram alert to go check the logs.
  2. Sheet's Social/App/Email/Acquisition metrics for the month, with
     month-over-month deltas.
  3. GA4 weekly trend for the month — totals per week, channel/device
     monthly summary (sessions/users summed across weeks, engagementRate/
     bounceRate weighted by sessions since they're ratios, not summable),
     and top pages with real titles.
  4. Connect card counts from congregation.db for the month, by campus.
  5. Reconciliation — the Sheet's hand-copied "Active Web Users / New Web
     Users / Avg Engagement Time / Event Count" rows next to GA4's live
     totalUsers/newUsers/averageSessionDuration/eventCount for the same
     month, explicitly labeled as two different sources. No attempt to
     explain or resolve a mismatch — both numbers are just shown.
  6. Known-oddity flags carried forward, not hidden: any TBD/unparseable
     Sheet cells for the month (is_flagged=1 rows), and any top page whose
     bounceRate is unusually high relative to this report's other top pages
     (device-level bounceRate is the only bounceRate GA4 gives us — flagged
     against the month's device-level average as a proxy, not a per-page
     bounceRate GA4 doesn't expose at the page dimension here).

Cron (1st of month, early morning — same slot pattern as
jobs/connect_cards/monthly_state_report.py):
  0 4 1 * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/analytics/monthly_web_engagement_report.py \
    >> /home/billyomes/watson/logs/monthly_web_engagement_report.log 2>&1

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.monthly_web_engagement_report
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.monthly_web_engagement_report --month 2026-06
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.monthly_web_engagement_report --dry-run
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.monthly_web_engagement_report --skip-import --dry-run
"""

import argparse
import calendar
import logging
import os
import re
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from jobs.connect_cards.reports import _CSS
from jobs.email_job.brevo_send import send_email
from jobs.analytics import sheet_import, ga4_import, connect_card_rollup, trailing_trends
from core.database import get_connection
from core.vacation import vacation_gate
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monthly_web_engagement_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
BILL_EMAIL = os.getenv("BILL_EMAIL", "")

WEB_BENCHMARKS_DOC = os.path.expanduser("~/watson/memory/projects/web_engagement_benchmarks.md")
INTERP_OLLAMA_URL = "http://localhost:11434/api/generate"
INTERP_OLLAMA_MODEL = "qwen2.5:7b"
# 2026-09-01: the August report's real prompt (full sheet/GA4/reconciliation/
# trend/benchmark context in, a 3-header structured synthesis out) blew past
# the previous 240s timeout -- confirmed via logs, not a model failure, a
# budget-too-small failure. qwen2.5:7b also isn't kept warm the way
# jobs/intent/keep_warm.py keeps gemma3:4b warm (that model serves live chat
# intent classification; this one only runs once a month), so a cold 4.7GB
# CPU load eats into the same budget as generation. This is a monthly batch
# job with no one waiting synchronously, so there's no real cost to a
# generous timeout -- 600s, plus one retry (_ollama_interpretation below),
# plus _warm_up_interpretation_model() firing early in send_report() so the
# model has a head start loading during the sheet/GA4/connect-card syncs
# that run first anyway.
INTERP_OLLAMA_TIMEOUT = 600

# Sections/labels shown in report section 1 (everything the sheet parses
# except Top Page Views, which gets its own layout).
_SHEET_METRIC_SECTIONS = ["Social Media", "Catalyt App Engagement", "E Mails/Website", "Aquisitions"]

# Sheet label -> GA4 total metric_name, for the reconciliation section.
_RECONCILE_PAIRS = [
    ("Active Web Users", "totalUsers", "Active Web Users (GA4: totalUsers)"),
    ("New Web Users", "newUsers", "New Web Users (GA4: newUsers)"),
    ("Avg Engagement Time (seconds)", "averageSessionDuration", "Avg Engagement Time — sec (GA4: averageSessionDuration)"),
    ("Event Count", "eventCount", "Event Count (GA4: eventCount)"),
]
# The sheet's own label text drifted slightly between tabs ("Avg Engagement
# Time (seconds)" in 2025 vs "Avg Engagement Time (sec)" in 2026) — matched
# by trying every known alias, not by exact string, so this survives that
# drift without hardcoding a specific tab.
_SHEET_LABEL_ALIASES = {
    "Avg Engagement Time (seconds)": ["Avg Engagement Time (seconds)", "Avg Engagement Time (sec)"],
}

BOUNCE_RATE_FLAG_THRESHOLD = 1.75  # a top page's bounceRate >= 75% above the report's own top-page average is flagged as a real outlier, not noise


def _wrap(title: str, subtitle: str, body: str) -> str:
    """Local wrap, not jobs.connect_cards.reports._wrap — that helper's
    footer is hardcoded 'Watson connect cards', wrong for this report. Reuses
    the shared _CSS constant only."""
    return (
        f"<html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>"
        f"<h1>{title}</h1>"
        f"<p style='color:#888;font-size:.9em;margin-top:-12px'>{subtitle}</p>"
        f"{body}"
        f"<p class='footer'>Watson · {subtitle}</p>"
        f"</body></html>"
    )


# ── Date / range helpers ─────────────────────────────────────────────────────

def _default_report_month() -> tuple[int, int]:
    today = datetime.now(NY).date()
    return _prev_month(today.year, today.month)


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()


def _month_label(year: int, month: int) -> str:
    return date(year, month, 1).strftime("%B %Y")


def _fmt(value, pct: bool = False) -> str:
    if value is None:
        return "—"
    if pct:
        return f"{value * 100:.1f}%"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{value:,.0f}"


# ── Section 1: Sheet metrics + MoM deltas ────────────────────────────────────

def _sheet_metrics_for_month(conn, month: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT section, metric_label, value_numeric, value_raw, is_flagged
        FROM engagement_sheet_metrics
        WHERE month = ? AND section IN ({})
        ORDER BY section, metric_label
        """.format(",".join("?" * len(_SHEET_METRIC_SECTIONS))),
        (month, *_SHEET_METRIC_SECTIONS),
    ).fetchall()
    return [dict(r) for r in rows]


def _sheet_value_lookup(conn, month: str) -> dict[tuple[str, str], dict]:
    rows = conn.execute(
        "SELECT section, metric_label, value_numeric, value_raw, is_flagged FROM engagement_sheet_metrics WHERE month = ?",
        (month,),
    ).fetchall()
    return {(r["section"], r["metric_label"]): dict(r) for r in rows}


def _is_percent_metric(section: str, label: str) -> bool:
    return section == "Aquisitions" or label.startswith("Top Page")


def _sheet_metrics_section_html(conn, year: int, month: int) -> str:
    start, _ = _month_bounds(year, month)
    prev_year, prev_month = _prev_month(year, month)
    prev_start, _ = _month_bounds(prev_year, prev_month)

    current = _sheet_metrics_for_month(conn, start)
    prev_lookup = _sheet_value_lookup(conn, prev_start)

    if not current:
        return "<p class='empty'>No Sheet metrics found for this month (not yet filled in on the tracking sheet).</p>"

    html = ""
    for section in _SHEET_METRIC_SECTIONS:
        section_rows = [r for r in current if r["section"] == section]
        if not section_rows:
            continue
        html += f"<h3 style='margin:14px 0 4px;font-size:.95em'>{section}</h3>"
        html += "<table><thead><tr><th>Metric</th><th>Value</th><th>vs. Prior Month</th></tr></thead><tbody>"
        for r in section_rows:
            is_pct = _is_percent_metric(section, r["metric_label"])
            if r["is_flagged"]:
                value_str = f"<span title='Unparseable — original: {r['value_raw']}'>⚠ {r['value_raw']}</span>"
            else:
                value_str = _fmt(r["value_numeric"], pct=is_pct)

            prev = prev_lookup.get((section, r["metric_label"]))
            delta_str = "—"
            if prev and not prev["is_flagged"] and not r["is_flagged"] and prev["value_numeric"] is not None and r["value_numeric"] is not None:
                delta = r["value_numeric"] - prev["value_numeric"]
                if is_pct:
                    delta_str = f"{'+' if delta >= 0 else ''}{delta * 100:.1f}pp"
                else:
                    delta_str = f"{'+' if delta >= 0 else ''}{delta:,.0f}"

            html += f"<tr><td>{r['metric_label']}</td><td>{value_str}</td><td>{delta_str}</td></tr>"
        html += "</tbody></table>"

    return html


# ── Section 2: GA4 weekly trend ──────────────────────────────────────────────

def _ga4_weeks_in_month(conn, year: int, month: int) -> list[str]:
    start, end = _month_bounds(year, month)
    rows = conn.execute(
        "SELECT DISTINCT week_start FROM ga4_engagement_weekly WHERE week_start BETWEEN ? AND ? ORDER BY week_start",
        (start, end),
    ).fetchall()
    return [r["week_start"] for r in rows]


def _ga4_totals_by_week(conn, weeks: list[str]) -> list[dict]:
    if not weeks:
        return []
    placeholders = ",".join("?" * len(weeks))
    rows = conn.execute(
        f"""
        SELECT week_start, metric_name, value FROM ga4_engagement_weekly
        WHERE dimension_type = 'total' AND week_start IN ({placeholders})
        """,
        weeks,
    ).fetchall()
    by_week: dict[str, dict] = {}
    for r in rows:
        by_week.setdefault(r["week_start"], {})[r["metric_name"]] = r["value"]
    return [{"week_start": w, **by_week.get(w, {})} for w in weeks]


def _ga4_weighted_breakdown(conn, weeks: list[str], dimension_type: str, weight_metric: str = "sessions") -> list[dict]:
    """Monthly summary for channel/device breakdowns: sums summable metrics
    (sessions, totalUsers), and computes a sessions-weighted average for
    ratio metrics (engagementRate, bounceRate) since those can't just be
    added across weeks."""
    if not weeks:
        return []
    placeholders = ",".join("?" * len(weeks))
    rows = conn.execute(
        f"""
        SELECT week_start, dimension_value, metric_name, value FROM ga4_engagement_weekly
        WHERE dimension_type = ? AND week_start IN ({placeholders})
        """,
        (dimension_type, *weeks),
    ).fetchall()

    per_dim: dict[str, dict[str, dict[str, float]]] = {}
    for r in rows:
        per_dim.setdefault(r["dimension_value"], {}).setdefault(r["week_start"], {})[r["metric_name"]] = r["value"]

    result = []
    for dim_value, by_week in per_dim.items():
        summed: dict[str, float] = {}
        weight_total = 0.0
        ratio_accum: dict[str, float] = {}
        for week_vals in by_week.values():
            w = week_vals.get(weight_metric, 0.0)
            weight_total += w
            for name, val in week_vals.items():
                if name == weight_metric or name in ("sessions", "totalUsers"):
                    summed[name] = summed.get(name, 0.0) + val
                else:
                    ratio_accum[name] = ratio_accum.get(name, 0.0) + val * w
        row = {"dimension_value": dim_value, **summed}
        for name, accum in ratio_accum.items():
            row[name] = (accum / weight_total) if weight_total else 0.0
        result.append(row)

    result.sort(key=lambda r: r.get(weight_metric, 0.0), reverse=True)
    return result


def _ga4_top_pages(conn, weeks: list[str], limit: int = 15) -> list[dict]:
    """Top pages by total views across the month's weeks, each with a
    views-weighted average bounceRate (bounceRate isn't summable across
    weeks — it's a ratio)."""
    if not weeks:
        return []
    placeholders = ",".join("?" * len(weeks))
    rows = conn.execute(
        f"""
        SELECT week_start, dimension_value AS path, page_title, metric_name, value
        FROM ga4_engagement_weekly
        WHERE dimension_type = 'page' AND week_start IN ({placeholders})
        """,
        weeks,
    ).fetchall()

    # per_page[path] = {"page_title", "views_by_week": {week: views}, "bounce_by_week": {week: rate}}
    per_page: dict[str, dict] = {}
    for r in rows:
        entry = per_page.setdefault(r["path"], {"page_title": None, "views_by_week": {}, "bounce_by_week": {}})
        if r["page_title"]:
            entry["page_title"] = r["page_title"]
        if r["metric_name"] == "screenPageViews":
            entry["views_by_week"][r["week_start"]] = r["value"]
        elif r["metric_name"] == "bounceRate":
            entry["bounce_by_week"][r["week_start"]] = r["value"]

    result = []
    for path, entry in per_page.items():
        views = sum(entry["views_by_week"].values())
        weight_total = sum(entry["views_by_week"].get(w, 0.0) for w in entry["bounce_by_week"])
        bounce_rate = (
            sum(rate * entry["views_by_week"].get(w, 0.0) for w, rate in entry["bounce_by_week"].items()) / weight_total
            if weight_total else None
        )
        result.append({"path": path, "page_title": entry["page_title"], "views": views, "bounce_rate": bounce_rate})

    result.sort(key=lambda e: e["views"], reverse=True)
    return result[:limit]


def _ga4_section_html(conn, year: int, month: int) -> tuple[str, list[str], list[dict]]:
    """Returns (html, weeks, top_pages) — top_pages is reused by the
    known-oddities section for the page-level bounce-rate flag."""
    weeks = _ga4_weeks_in_month(conn, year, month)
    if not weeks:
        return "<p class='empty'>No GA4 data synced for this month.</p>", [], []

    html = "<h3 style='margin:14px 0 4px;font-size:.95em'>Weekly Totals (US only)</h3>"
    html += (
        "<table><thead><tr><th>Week of</th><th>Users</th><th>New Users</th>"
        "<th>Sessions</th><th>Engagement Rate</th><th>Page Views</th><th>Avg Session (sec)</th></tr></thead><tbody>"
    )
    for w in _ga4_totals_by_week(conn, weeks):
        html += (
            f"<tr><td>{w['week_start']}</td><td>{_fmt(w.get('totalUsers'))}</td>"
            f"<td>{_fmt(w.get('newUsers'))}</td><td>{_fmt(w.get('sessions'))}</td>"
            f"<td>{_fmt(w.get('engagementRate'), pct=True)}</td><td>{_fmt(w.get('screenPageViews'))}</td>"
            f"<td>{_fmt(w.get('averageSessionDuration'))}</td></tr>"
        )
    html += "</tbody></table>"

    channel = _ga4_weighted_breakdown(conn, weeks, "channel")
    html += "<h3 style='margin:14px 0 4px;font-size:.95em'>Channel (month total, US only)</h3>"
    html += "<table><thead><tr><th>Channel</th><th>Sessions</th><th>Users</th><th>Engagement Rate</th></tr></thead><tbody>"
    for c in channel:
        html += (
            f"<tr><td>{c['dimension_value']}</td><td>{_fmt(c.get('sessions'))}</td>"
            f"<td>{_fmt(c.get('totalUsers'))}</td><td>{_fmt(c.get('engagementRate'), pct=True)}</td></tr>"
        )
    html += "</tbody></table>"

    device = _ga4_weighted_breakdown(conn, weeks, "device")
    html += "<h3 style='margin:14px 0 4px;font-size:.95em'>Device (month total, US only)</h3>"
    html += "<table><thead><tr><th>Device</th><th>Sessions</th><th>Engagement Rate</th><th>Bounce Rate</th></tr></thead><tbody>"
    for d in device:
        html += (
            f"<tr><td>{d['dimension_value']}</td><td>{_fmt(d.get('sessions'))}</td>"
            f"<td>{_fmt(d.get('engagementRate'), pct=True)}</td><td>{_fmt(d.get('bounceRate'), pct=True)}</td></tr>"
        )
    html += "</tbody></table>"

    pages = _ga4_top_pages(conn, weeks)
    html += "<h3 style='margin:14px 0 4px;font-size:.95em'>Top Pages (month total, US only)</h3>"
    html += "<table><thead><tr><th>Page</th><th>Path</th><th>Views</th><th>Bounce Rate</th></tr></thead><tbody>"
    for p in pages:
        html += (
            f"<tr><td>{p['page_title'] or '(untitled)'}</td><td>{p['path']}</td>"
            f"<td>{_fmt(p['views'])}</td><td>{_fmt(p['bounce_rate'], pct=True)}</td></tr>"
        )
    html += "</tbody></table>"

    return html, weeks, pages


# ── Section 3: Connect cards ──────────────────────────────────────────────────

def _connect_card_section_html(conn, year: int, month: int) -> str:
    start, _ = _month_bounds(year, month)
    rows = conn.execute(
        "SELECT campus, count FROM connect_card_monthly_rollup WHERE month = ? ORDER BY campus",
        (start,),
    ).fetchall()
    if not rows:
        return "<p class='empty'>No connect card rollup data for this month.</p>"
    total = sum(r["count"] for r in rows)
    html = "<div style='margin:8px 0 16px'>"
    for r in rows:
        html += f"<div class='stat-box'><div class='stat'>{r['count']}</div><div class='stat-label'>{r['campus']}</div></div>"
    html += f"<div class='stat-box'><div class='stat'>{total}</div><div class='stat-label'>Total</div></div>"
    html += "</div>"
    return html


# ── Section 4: Reconciliation ─────────────────────────────────────────────────

def _sheet_label_lookup(sheet_by_key: dict[tuple[str, str], dict], label: str) -> dict | None:
    for alias in _SHEET_LABEL_ALIASES.get(label, [label]):
        row = sheet_by_key.get(("E Mails/Website", alias))
        if row:
            return row
    return None


def _reconciliation_html(conn, year: int, month: int) -> str:
    start, _ = _month_bounds(year, month)
    sheet_by_key = _sheet_value_lookup(conn, start)

    weeks = _ga4_weeks_in_month(conn, year, month)
    # sum users/newUsers/eventCount across weeks; average averageSessionDuration (unweighted — no session count stored per week for a proper weighted avg here)
    ga4_month: dict[str, float] = {}
    if weeks:
        rows = conn.execute(
            f"""
            SELECT metric_name, value FROM ga4_engagement_weekly
            WHERE dimension_type = 'total' AND week_start IN ({",".join("?" * len(weeks))})
            """,
            weeks,
        ).fetchall()
        by_metric: dict[str, list[float]] = {}
        for r in rows:
            by_metric.setdefault(r["metric_name"], []).append(r["value"])
        for name in ("totalUsers", "newUsers", "eventCount"):
            ga4_month[name] = sum(by_metric.get(name, []))
        if by_metric.get("averageSessionDuration"):
            ga4_month["averageSessionDuration"] = sum(by_metric["averageSessionDuration"]) / len(by_metric["averageSessionDuration"])

    html = (
        "<p style='color:#888;font-size:.85em'>Two different sources for the same underlying "
        "concept — shown side by side, not merged. A mismatch is expected (the Sheet is a "
        "hand-copied snapshot; GA4 numbers here are a live pull) and is not resolved below.</p>"
    )
    html += "<table><thead><tr><th>Metric</th><th>Sheet (hand-copied)</th><th>GA4 (live, US only)</th></tr></thead><tbody>"
    for sheet_label, ga4_metric, display in _RECONCILE_PAIRS:
        sheet_row = _sheet_label_lookup(sheet_by_key, sheet_label)
        if sheet_row is None:
            sheet_str = "—"
        elif sheet_row["is_flagged"]:
            sheet_str = f"⚠ {sheet_row['value_raw']}"
        else:
            sheet_str = _fmt(sheet_row["value_numeric"])
        ga4_str = _fmt(ga4_month.get(ga4_metric)) if weeks else "—"
        html += f"<tr><td>{display}</td><td>{sheet_str}</td><td>{ga4_str}</td></tr>"
    html += "</tbody></table>"
    return html


# ── Interpretation & Recommendations (Ollama synthesis) ──────────────────────

def _load_web_benchmarks_doc() -> str:
    try:
        with open(WEB_BENCHMARKS_DOC, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as exc:
        log.warning("web_engagement_benchmarks.md unavailable: %s", exc)
        return ""


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _events_for_month(conn, year: int, month: int) -> list[dict]:
    """church_events (watson.db) with any date inside the report month —
    same table/query shape as state_of_church.py's _special_events(), just
    windowed to the report month instead of the past 14 days."""
    start, end = _month_bounds(year, month)
    rows = conn.execute(
        """
        SELECT event_name, start_date, end_date, attendance_notes
        FROM church_events
        WHERE start_date BETWEEN ? AND ?
           OR (end_date IS NOT NULL AND end_date BETWEEN ? AND ?)
        ORDER BY start_date
        """,
        (start, end, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def _events_text(events: list[dict]) -> str:
    if not events:
        return "none"
    parts = []
    for e in events:
        date_range = e["start_date"]
        if e["end_date"] and e["end_date"] != e["start_date"]:
            date_range = f"{e['start_date']} to {e['end_date']}"
        note = (e["attendance_notes"] or "").strip()
        parts.append(f"{e['event_name']} ({date_range})" + (f" — {note}" if note else ""))
    return "; ".join(parts)


def _ollama_interpretation(
    month_label: str,
    sheet_text: str,
    ga4_text: str,
    connect_text: str,
    reconciliation_text: str,
    oddities_text: str,
    trailing_text: str,
    events_text: str,
    benchmarks_context: str,
) -> str | None:
    prompt = (
        "You are Watson, AI assistant to Dr. Bill Yomes, Senior Pastor of Catalyst Community "
        "Church. Write the 'Interpretation & Recommendations' section for the monthly web/digital "
        f"engagement report for {month_label}, following these rules exactly:\n\n"
        "a. Only use 'trend' or similar language about a metric if the 6-MONTH TRAILING DATA below "
        "shows 3 or more consecutive months moving in the same direction for that metric. A single "
        "month's move is noise — say so plainly if that's the case, do not call it a trend.\n"
        "b. The RECONCILIATION data below shows two different numbers for the same concept from two "
        "different sources (a hand-copied Sheet and a live GA4 pull). Never resolve, average, or pick "
        "a winner between them — if they disagree, flag it as worth checking with whoever maintains "
        "the Sheet, and move on. Do not state which one is 'correct'.\n"
        "c. Every recommendation must cite a specific number, page, or channel from THIS MONTH'S DATA "
        "below — no generic advice like 'post more' or 'engage your audience' without a number "
        "behind it.\n"
        "d. If a spike or dip in this month's data lines up with an entry in CHURCH EVENTS THIS "
        "MONTH below, lead your interpretation with that connection before any other analysis.\n"
        "e. Reference context — digital/church-communications benchmarks (use only to judge whether "
        "this month's numbers are normal or notable relative to peer congregations, never quote it "
        "verbatim):\n"
        f"{benchmarks_context or '(no benchmark research logged yet — proceed without it)'}\n\n"
        "Structure your response under exactly these three headers, in this order, and no others — "
        "put every recommendation inside the relevant one of these three sections, never in a "
        "separate 'Recommendations' section or a fourth header. Each section needs at least one "
        "paragraph of interpretation AND at least one concrete, numbered recommendation. This is "
        "deliberate so no single channel dominates just because it had the biggest swing this "
        "month:\n"
        "### Growing Visitors & Connect Cards\n"
        "### Social Reach\n"
        "### Email Engagement\n\n"
        f"CHURCH EVENTS THIS MONTH: {events_text}\n\n"
        f"6-MONTH TRAILING DATA:\n{trailing_text}\n\n"
        f"THIS MONTH'S SHEET METRICS:\n{sheet_text}\n\n"
        f"THIS MONTH'S GA4 DATA:\n{ga4_text}\n\n"
        f"THIS MONTH'S CONNECT CARDS:\n{connect_text}\n\n"
        f"RECONCILIATION (Sheet vs GA4, do not resolve):\n{reconciliation_text}\n\n"
        f"KNOWN ODDITIES / FLAGGED CAVEATS:\n{oddities_text}\n\n"
        "You must respond in English only. Do not use any other language. Begin writing now:"
    )
    for attempt in (1, 2):
        try:
            resp = requests.post(
                INTERP_OLLAMA_URL,
                json={"model": INTERP_OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=INTERP_OLLAMA_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip() or None
        except Exception as exc:
            log.warning("Ollama interpretation synthesis failed (attempt %d/2): %s", attempt, exc)
    return None


def _warm_up_interpretation_model() -> None:
    """Best-effort head start on loading qwen2.5:7b into memory, fired
    before the sheet/GA4/connect-card syncs below so the ~5-30s cold load
    (see INTERP_OLLAMA_TIMEOUT comment) overlaps with that ~10s of unrelated
    work instead of eating into the interpretation call's own budget.
    Failure here is not fatal -- _ollama_interpretation's own retry covers
    a still-cold model."""
    try:
        requests.post(
            INTERP_OLLAMA_URL,
            json={"model": INTERP_OLLAMA_MODEL, "prompt": "hi", "stream": False},
            timeout=30,
        )
    except Exception as exc:
        log.info("Interpretation model warm-up ping failed (non-fatal): %s", exc)


def _alert_interpretation_failed(month_label: str) -> None:
    text = (
        f"⚠️ Monthly Web Engagement Report ({month_label}): the Interpretation & "
        "Recommendations section failed to generate after 2 attempts (Ollama "
        "timeout or error) -- the report still sent with the data sections, just "
        "without the written analysis at the top. Check logs/monthly_web_engagement_report.log."
    )
    log.error(text)
    if vacation_gate("system_failure", "jobs.analytics.monthly_web_engagement_report", text):
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
        log.warning("Failed to send interpretation-failure Telegram alert: %s", exc)


def _render_interpretation_html(text: str) -> str:
    """Lightweight markdown-lite renderer: '#'-prefixed lines become the
    three required sub-headers, blank-line-separated text becomes
    paragraphs. Avoids requiring a strict output format from Ollama."""
    html_parts = []
    para_buf: list[str] = []

    def flush():
        if para_buf:
            html_parts.append(
                f'<p style="margin:0 0 10px">{" ".join(para_buf)}</p>'
            )
            para_buf.clear()

    for line in text.strip().split("\n"):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        header_match = re.match(r"^#{1,3}\s*(.+)$", stripped)
        if header_match:
            flush()
            html_parts.append(f"<h3 style='margin:14px 0 4px;font-size:.95em'>{header_match.group(1)}</h3>")
            continue
        para_buf.append(stripped)
    flush()

    return "".join(html_parts)


# ── Section 5: Known oddities ─────────────────────────────────────────────────

def _oddities_html(conn, year: int, month: int, top_pages: list[dict]) -> str:
    start, _ = _month_bounds(year, month)
    flagged = conn.execute(
        "SELECT section, metric_label, value_raw FROM engagement_sheet_metrics WHERE month = ? AND is_flagged = 1",
        (start,),
    ).fetchall()

    items = []
    for r in flagged:
        items.append(f"<li>Sheet — <strong>{r['section']} / {r['metric_label']}</strong>: unparseable value {r['value_raw']!r}, stored as flagged, not zeroed.</li>")

    rates = [p["bounce_rate"] for p in top_pages if p.get("bounce_rate") is not None]
    if rates:
        avg_rate = sum(rates) / len(rates)
        for p in top_pages:
            rate = p.get("bounce_rate")
            if rate is not None and avg_rate > 0 and rate >= avg_rate * BOUNCE_RATE_FLAG_THRESHOLD:
                items.append(
                    f"<li>GA4 — <strong>{p['page_title'] or p['path']}</strong> ({p['path']}) bounce rate "
                    f"({rate * 100:.1f}%) is notably higher than this report's other top pages "
                    f"(avg {avg_rate * 100:.1f}%).</li>"
                )

    if not items:
        return "<p class='empty'>No flagged Sheet values or bounce-rate outliers this month.</p>"
    return "<ul>" + "".join(items) + "</ul>"


# ── Report builder ─────────────────────────────────────────────────────────────

def build_report(year: int, month: int) -> tuple[str, str]:
    month_label = _month_label(year, month)
    _warm_up_interpretation_model()
    conn = get_connection()
    try:
        sheet_html = _sheet_metrics_section_html(conn, year, month)
        ga4_html, _weeks, top_pages = _ga4_section_html(conn, year, month)
        connect_html = _connect_card_section_html(conn, year, month)
        reconciliation_html = _reconciliation_html(conn, year, month)
        oddities_html = _oddities_html(conn, year, month, top_pages)

        events = _events_for_month(conn, year, month)
        trend = trailing_trends.build_trailing_trend(conn, year, month)
    finally:
        conn.close()

    interpretation = _ollama_interpretation(
        month_label=month_label,
        sheet_text=_strip_html(sheet_html),
        ga4_text=_strip_html(ga4_html),
        connect_text=_strip_html(connect_html),
        reconciliation_text=_strip_html(reconciliation_html),
        oddities_text=_strip_html(oddities_html),
        trailing_text=trailing_trends.trailing_trend_summary_text(trend),
        events_text=_events_text(events),
        benchmarks_context=_load_web_benchmarks_doc(),
    )
    if interpretation:
        interpretation_html = _render_interpretation_html(interpretation)
    else:
        interpretation_html = (
            "<p class='empty'>Interpretation unavailable this month — Ollama didn't respond in "
            "time after two attempts. The data below is unaffected; Bill's been alerted to check "
            "the logs.</p>"
        )
        _alert_interpretation_failed(month_label)

    subject = f"Watson — Monthly Web Engagement Report | {month_label}"

    # Interpretation leads the email -- the written read on what the numbers
    # mean and what to do about it, before any raw data. Per Bill's
    # 2026-09-01 request: the numbers-first version left the team without
    # help deciphering them. "The Data" below is a deliberate section break,
    # not just another <h2>, so it's visually clear everything past that
    # point is reference material for the analysis above it.
    body = "<h2>Interpretation & Recommendations</h2>" + interpretation_html
    body += "<h2 style='margin-top:28px;padding-top:14px;border-top:2px solid #ddd'>The Data</h2>"
    body += "<h3 style='margin:14px 0 4px'>Sheet Metrics — Social / App / Email / Acquisitions</h3>" + sheet_html
    body += "<h3 style='margin:14px 0 4px'>GA4 Web Trend</h3>" + ga4_html
    body += "<h3 style='margin:14px 0 4px'>Connect Cards</h3>" + connect_html
    body += "<h3 style='margin:14px 0 4px'>Reconciliation — Sheet vs. GA4</h3>" + reconciliation_html
    body += "<h3 style='margin:14px 0 4px'>Known Oddities</h3>" + oddities_html

    return subject, _wrap("Monthly Web Engagement Report", month_label, body)


def send_report(year: int, month: int, to_override: str | None = None) -> None:
    subject, html = build_report(year, month)
    to = to_override or BILL_EMAIL
    if not to:
        raise RuntimeError("No recipient — BILL_EMAIL not set in .env and no --to override given.")

    text_fallback = re.sub(r"<[^>]+>", "", html)
    result = send_email(
        to_email=to, to_name="", subject=subject,
        text_body=text_fallback, html_body=html, include_signature=False,
    )
    if not result["success"]:
        raise RuntimeError(f"Brevo send to {to} failed: {result['error']}")
    log.info("Sent %r to %s", subject, to)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build and send the monthly web engagement report.")
    parser.add_argument("--month", default=None, help="Report month as YYYY-MM; defaults to the prior calendar month (America/New_York)")
    parser.add_argument("--to", default=None, help="Override recipient email (defaults to BILL_EMAIL)")
    parser.add_argument("--dry-run", action="store_true", help="Print subject/HTML without sending")
    parser.add_argument("--skip-import", action="store_true", help="Skip running the sheet/GA4/connect-card import jobs first (use already-synced data)")
    args = parser.parse_args()

    if args.month:
        y, m = args.month.split("-")
        report_year, report_month = int(y), int(m)
    else:
        report_year, report_month = _default_report_month()

    if not args.skip_import:
        log.info("Running sheet_import...")
        sheet_import.sync()
        log.info("Running ga4_import...")
        ga4_import.sync()
        log.info("Running connect_card_rollup...")
        connect_card_rollup.sync()

    if args.dry_run:
        subj, html_body = build_report(report_year, report_month)
        print(f"Subject: {subj}\n")
        print(html_body)
    else:
        try:
            send_report(report_year, report_month, to_override=args.to)
        except Exception as exc:
            log.error("Send failed: %s", exc)
            sys.exit(1)
