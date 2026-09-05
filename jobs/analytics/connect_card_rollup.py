"""jobs/analytics/connect_card_rollup.py — Monthly connect-card counts by
campus, from congregation.db's connect_cards table, into
connect_card_monthly_rollup (watson.db).

Schema used directly from connect_cards (confirmed via .schema before build):
  id, member_id, service_date, campus, raw_text, processed_at, email_id,
  questions_comments, prayer_request, next_steps, is_first_visit,
  prayer_request_public
`campus` is always 'Wilmington' or 'Online' (confirmed live — no NULLs, no
other values) — cards self-report; "Hybrid" is a member-level classification
in members.campus_preference, not something a card records (same finding as
jobs/connect_cards/monthly_engagement_report.py).

This table replaces the Sheet's old "Connect Cards/Registration" section as
the source of truth going forward — jobs/analytics/sheet_import.py
deliberately does not parse that section.

Usage:
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.connect_card_rollup
  PYTHONPATH=/home/billyomes/watson python -m jobs.analytics.connect_card_rollup --dry-run
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

from jobs.analytics.schema import create_tables
from core.database import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [connect_card_rollup] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CONG_DB = os.path.expanduser("~/watson/data/congregation.db")


def _cong_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CONG_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _monthly_counts(conn: sqlite3.Connection) -> list[tuple[str, str, int]]:
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m-01', service_date) AS month, campus, COUNT(*) AS cnt
        FROM connect_cards
        WHERE campus IS NOT NULL AND campus != ''
        GROUP BY month, campus
        ORDER BY month, campus
        """
    ).fetchall()
    return [(r["month"], r["campus"], r["cnt"]) for r in rows]


def sync(dry_run: bool = False) -> dict:
    with _cong_conn() as cconn:
        rows = _monthly_counts(cconn)

    computed_at = datetime.now(timezone.utc).isoformat()
    if not dry_run:
        conn = get_connection()
        create_tables(conn)
        try:
            conn.executemany(
                """
                INSERT INTO connect_card_monthly_rollup (month, campus, count, computed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(month, campus) DO UPDATE SET
                    count       = excluded.count,
                    computed_at = excluded.computed_at
                """,
                [(month, campus, cnt, computed_at) for month, campus, cnt in rows],
            )
            conn.commit()
        finally:
            conn.close()

    log.info("Rolled up %d month/campus row(s).", len(rows))
    return {"rows": len(rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Roll up connect_cards into monthly campus counts.")
    parser.add_argument("--dry-run", action="store_true", help="Compute and log without writing to the DB")
    args = parser.parse_args()

    try:
        out = sync(dry_run=args.dry_run)
    except Exception as exc:
        log.error("Rollup failed: %s", exc)
        sys.exit(1)

    print(out)
