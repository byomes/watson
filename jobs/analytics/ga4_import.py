"""jobs/analytics/ga4_import.py — Weekly GA4 pull (property 509598079) into
ga4_engagement_weekly. Recon: memory/engagement_data_recon_2026-08-20.md.

Bot filtering: every query below applies a `country == "United States"`
dimension filter. This is the deliberate decision from the recon (Singapore/
other foreign traffic confirmed as likely bot contamination — a single
foreign city rivaling total US traffic, `(not set)` city at high volume, and
an anomalously low-engagement "Referral" channel) — US-only, not raw+flagged.
ga4_engagement_weekly.us_filtered is always 1 as a result; the column exists
so a future report can tell at a glance this data was already filtered.

Weekly grain is computed in Python as Monday-start weeks — one
RunReportRequest per (week, shape), not GA4's own `yearWeek` dimension, which
uses GA4's internal week boundary (confirmed via live probe: `yearWeek`
202632 covers 2026-08-02, a Sunday) and can't be trusted to line up with
Monday. Querying per real [Monday, Sunday] date range also means ratio
metrics (engagementRate, bounceRate) are computed correctly by GA4 itself for
that week, rather than being naively averaged from daily pulls after the
fact.

Metrics pulled beyond the Part 3 build-spec lists:
  - `eventCount` for dimension_type='total' — the report's reconciliation
    section (jobs/analytics/monthly_web_engagement_report.py) needs a live
    GA4 number to show side by side with the Sheet's hand-copied "Event
    Count" row, and eventCount is GA4's only equivalent metric for that.
  - `bounceRate` for dimension_type='page' (alongside screenPageViews) — the
    report's "Known Oddities" section needs a page-level bounce rate to flag
    an outlier top page against the report's other top pages (this is the
    exact "/fbc" finding from the recon), which device-level bounceRate
    alone can't answer.
Both confirmed as valid GA4 metric/dimension combinations via live probe
calls before being added here.

Earliest real traffic: 2025-10-21 (recon, confirmed via a live daily
sessions/totalUsers pull spanning back to 2020 with no earlier data) — the
first-run backfill starts at the Monday of that week. Incremental runs only
pull weeks not already stored, plus re-pull the trailing _TRAILING_REFRESH_
WEEKS weeks each time to catch GA4's own late-arriving/adjusted numbers
(existing rows for a re-pulled week are deleted and replaced, not merged).

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.ga4_import
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.ga4_import --dry-run
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Filter, FilterExpression, Metric, OrderBy, RunReportRequest,
)

from jobs.analytics.schema import create_tables
from core.database import get_connection

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ga4_import] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

KEY_FILE = os.getenv(
    "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE",
    os.path.expanduser("~/watson/config/sheets_service_account.json"),
)
PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "509598079")
SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

EARLIEST_TRAFFIC = date(2025, 10, 21)
TRAILING_REFRESH_WEEKS = 3
TOP_PAGES_LIMIT = 15

TOTAL_METRICS = [
    "totalUsers", "sessions", "engagementRate", "screenPageViews",
    "averageSessionDuration", "newUsers", "eventCount",
]
CHANNEL_METRICS = ["sessions", "totalUsers", "engagementRate"]
DEVICE_METRICS = ["sessions", "engagementRate", "bounceRate"]
PAGE_METRIC = "screenPageViews"
# bounceRate is pulled alongside screenPageViews for the page dimension (not
# named in the Part 3 build-spec page-metric list, which only mentions
# screenPageViews) because Part 5's "Known Oddities" section needs a
# page-level bounce rate to flag an outlier top page against the report's
# other top pages — device-level bounceRate alone can't answer that.
# Confirmed pagePath+bounceRate is a valid GA4 dimension/metric combo via a
# live probe before adding it here.
PAGE_METRICS = [PAGE_METRIC, "bounceRate"]


# ── Client / query helpers ──────────────────────────────────────────────────

def _client() -> BetaAnalyticsDataClient:
    creds = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
    return BetaAnalyticsDataClient(credentials=creds)


def _us_filter() -> FilterExpression:
    return FilterExpression(
        filter=Filter(field_name="country", string_filter=Filter.StringFilter(value="United States"))
    )


def _run(client, dims: list[str], metrics: list[str], start: date, end: date,
          limit: int | None = None, order_metric: str | None = None):
    kwargs = dict(
        property=f"properties/{PROPERTY_ID}",
        dimensions=[Dimension(name=d) for d in dims],
        metrics=[Metric(name=m) for m in metrics],
        date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
        dimension_filter=_us_filter(),
    )
    if limit:
        kwargs["limit"] = limit
    if order_metric:
        kwargs["order_bys"] = [OrderBy(metric=OrderBy.MetricOrderBy(metric_name=order_metric), desc=True)]
    return client.run_report(RunReportRequest(**kwargs))


# ── Week math ────────────────────────────────────────────────────────────────

def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _weeks_to_pull(conn) -> list[date]:
    today = date.today()
    current_week_start = _monday_of(today)
    earliest_week_start = _monday_of(EARLIEST_TRAFFIC)

    existing = {
        date.fromisoformat(r[0])
        for r in conn.execute("SELECT DISTINCT week_start FROM ga4_engagement_weekly").fetchall()
    }

    all_weeks = []
    w = earliest_week_start
    while w <= current_week_start:
        all_weeks.append(w)
        w += timedelta(days=7)

    trailing_cutoff = current_week_start - timedelta(weeks=TRAILING_REFRESH_WEEKS - 1)
    return [w for w in all_weeks if w not in existing or w >= trailing_cutoff]


# ── Pull one week ────────────────────────────────────────────────────────────

def _pull_week(client, week_start: date) -> list[tuple]:
    week_end = week_start + timedelta(days=6)
    pulled_at = datetime.now(timezone.utc).isoformat()
    rows: list[tuple] = []

    def add(metric_name, dimension_type, dimension_value, page_title, value):
        rows.append((week_start.isoformat(), metric_name, dimension_type, dimension_value,
                      page_title, value, 1, pulled_at))

    resp = _run(client, [], TOTAL_METRICS, week_start, week_end)
    if resp.rows:
        for name, mv in zip(TOTAL_METRICS, resp.rows[0].metric_values):
            add(name, "total", None, None, float(mv.value))
    else:
        for name in TOTAL_METRICS:
            add(name, "total", None, None, 0.0)

    resp = _run(client, ["sessionDefaultChannelGroup"], CHANNEL_METRICS, week_start, week_end)
    for row in resp.rows:
        channel = row.dimension_values[0].value
        for name, mv in zip(CHANNEL_METRICS, row.metric_values):
            add(name, "channel", channel, None, float(mv.value))

    resp = _run(client, ["deviceCategory"], DEVICE_METRICS, week_start, week_end)
    for row in resp.rows:
        device = row.dimension_values[0].value
        for name, mv in zip(DEVICE_METRICS, row.metric_values):
            add(name, "device", device, None, float(mv.value))

    resp = _run(client, ["pagePath", "pageTitle"], PAGE_METRICS, week_start, week_end,
                limit=TOP_PAGES_LIMIT, order_metric=PAGE_METRIC)
    for row in resp.rows:
        path, title = row.dimension_values[0].value, row.dimension_values[1].value
        for name, mv in zip(PAGE_METRICS, row.metric_values):
            add(name, "page", path, title, float(mv.value))

    return rows


# ── Sync ─────────────────────────────────────────────────────────────────────

def sync(dry_run: bool = False) -> dict:
    conn = get_connection()
    create_tables(conn)
    try:
        weeks = _weeks_to_pull(conn)
        if not weeks:
            log.info("No weeks to pull — up to date.")
            return {"weeks_pulled": 0, "rows": 0}

        client = _client()
        total_rows = 0
        for week_start in weeks:
            rows = _pull_week(client, week_start)
            if dry_run:
                log.info("[dry-run] week %s: %d row(s)", week_start.isoformat(), len(rows))
            else:
                conn.execute("DELETE FROM ga4_engagement_weekly WHERE week_start = ?", (week_start.isoformat(),))
                conn.executemany(
                    """
                    INSERT INTO ga4_engagement_weekly
                        (week_start, metric_name, dimension_type, dimension_value, page_title, value, us_filtered, pulled_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.commit()
            total_rows += len(rows)
            log.info("Pulled week %s: %d row(s)", week_start.isoformat(), len(rows))

        return {"weeks_pulled": len(weeks), "rows": total_rows}
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pull weekly GA4 data (property 509598079) into ga4_engagement_weekly.")
    parser.add_argument("--dry-run", action="store_true", help="Pull and log without writing to the DB")
    args = parser.parse_args()

    try:
        out = sync(dry_run=args.dry_run)
    except Exception as exc:
        log.error("Sync failed: %s", exc)
        sys.exit(1)

    print(out)
