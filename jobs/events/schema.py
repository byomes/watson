"""jobs/events/schema.py — event registration tracking, layered onto the
existing church_events table (owned by jobs/dashboard/app.py's _bootstrap()).

Adds:
  church_events.tracking_active — 1 while new signups should auto-attach to
    this event (email detection and the team-chat Q&A both filter on this),
    0 once Bill considers the event closed out.
  event_registrations — one row per registrant/signup for an event.
"""
import sqlite3

from config.settings import DB_PATH


def create_tables() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("ALTER TABLE church_events ADD COLUMN tracking_active INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_registrations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id      INTEGER NOT NULL REFERENCES church_events(id),
            first_name    TEXT,
            last_name     TEXT,
            email         TEXT,
            phone         TEXT,
            ticket_type   TEXT,
            ticket_price  TEXT,
            num_tickets   INTEGER NOT NULL DEFAULT 1,
            extra_fields  TEXT,
            member_id     INTEGER,
            source        TEXT NOT NULL DEFAULT 'manual',
            submitted_at  TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_event_registrations_event_id
        ON event_registrations(event_id)
    """)
    conn.commit()
    conn.close()
