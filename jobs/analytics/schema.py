"""jobs/analytics/schema.py — Schema for the monthly church engagement report
(engagement_sheet_metrics / ga4_engagement_weekly / connect_card_monthly_rollup),
built in watson.db per memory/engagement_data_recon_2026-08-20.md.
"""
from core.database import get_connection

CREATE_SHEET_METRICS = """
CREATE TABLE IF NOT EXISTS engagement_sheet_metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tab           TEXT NOT NULL,
    section       TEXT NOT NULL,
    metric_label  TEXT NOT NULL,
    month         TEXT NOT NULL,
    value_numeric REAL,
    value_raw     TEXT NOT NULL,
    is_flagged    INTEGER NOT NULL DEFAULT 0,
    synced_at     TEXT NOT NULL,
    UNIQUE(tab, section, metric_label, month)
);
"""

# dimension_value is nullable (NULL for dimension_type='total') and SQLite
# treats each NULL as distinct for UNIQUE-constraint purposes, so this table
# has no UNIQUE constraint — jobs/analytics/ga4_import.py dedupes on rerun by
# deleting all rows for a week_start before re-inserting that week's pull.
CREATE_GA4_WEEKLY = """
CREATE TABLE IF NOT EXISTS ga4_engagement_weekly (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start      TEXT NOT NULL,
    metric_name     TEXT NOT NULL,
    dimension_type  TEXT NOT NULL CHECK (dimension_type IN ('total', 'channel', 'device', 'page')),
    dimension_value TEXT,
    page_title      TEXT,
    value           REAL NOT NULL,
    us_filtered     INTEGER NOT NULL DEFAULT 1,
    pulled_at       TEXT NOT NULL
);
"""

CREATE_GA4_WEEKLY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_ga4_engagement_weekly_week_start
    ON ga4_engagement_weekly(week_start);
"""

CREATE_CONNECT_CARD_ROLLUP = """
CREATE TABLE IF NOT EXISTS connect_card_monthly_rollup (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    month       TEXT NOT NULL,
    campus      TEXT NOT NULL,
    count       INTEGER NOT NULL,
    computed_at TEXT NOT NULL,
    UNIQUE(month, campus)
);
"""

ALL_TABLES = [
    CREATE_SHEET_METRICS,
    CREATE_GA4_WEEKLY,
    CREATE_GA4_WEEKLY_INDEX,
    CREATE_CONNECT_CARD_ROLLUP,
]


def create_tables(conn=None) -> None:
    """Create all engagement-report tables in watson.db (idempotent — CREATE
    TABLE/INDEX IF NOT EXISTS)."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        for stmt in ALL_TABLES:
            conn.execute(stmt)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


if __name__ == "__main__":
    create_tables()
    print("engagement_sheet_metrics / ga4_engagement_weekly / connect_card_monthly_rollup ready.")
