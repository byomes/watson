"""jobs/campaigns/schema.py — Schema for the reusable book-launch marketing
automation system (book_launch_campaigns / book_launch_sends / book_launch_contacts).
"""
from core.database import get_connection

CREATE_CAMPAIGNS = """
CREATE TABLE IF NOT EXISTS book_launch_campaigns (
    campaign_id     TEXT PRIMARY KEY,
    book_title      TEXT NOT NULL,
    launch_date     TEXT NOT NULL,
    start_date      TEXT NOT NULL,
    framework_weeks INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_SENDS = """
CREATE TABLE IF NOT EXISTS book_launch_sends (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id         TEXT NOT NULL REFERENCES book_launch_campaigns(campaign_id),
    week_number         INTEGER,
    send_date           TEXT NOT NULL,
    platform            TEXT NOT NULL CHECK (platform IN ('facebook', 'brevo')),
    segment             TEXT NOT NULL CHECK (segment IN ('public', 'general', 'donor', 'arc')),
    subject             TEXT,
    body_text           TEXT NOT NULL,
    image_template_type TEXT,
    status              TEXT NOT NULL DEFAULT 'scheduled'
                        CHECK (status IN ('scheduled', 'previewed', 'approved', 'edited', 'sent', 'skipped')),
    telegram_message_id INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at             TEXT
);
"""

CREATE_CONTACTS = """
CREATE TABLE IF NOT EXISTS book_launch_contacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id  TEXT NOT NULL REFERENCES book_launch_campaigns(campaign_id),
    email        TEXT NOT NULL,
    name         TEXT,
    segment      TEXT NOT NULL CHECK (segment IN ('public', 'general', 'donor', 'arc')),
    source       TEXT NOT NULL CHECK (source IN ('kit_export', 'donors_db', 'arc_readers')),
    imported_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

ALL_TABLES = [CREATE_CAMPAIGNS, CREATE_SENDS, CREATE_CONTACTS]


def create_tables(conn=None) -> None:
    """Create all three book_launch_* tables in watson.db (idempotent — CREATE
    TABLE IF NOT EXISTS). Reused by every future book-launch campaign, not just
    this one."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        for stmt in ALL_TABLES:
            conn.execute(stmt)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
