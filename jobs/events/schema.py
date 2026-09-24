"""jobs/events/schema.py — event registration tracking, layered onto the
existing church_events table (owned by jobs/dashboard/app.py's _bootstrap()).

Adds:
  church_events.tracking_active — 1 while new signups should auto-attach to
    this event (email detection and the team-chat Q&A both filter on this),
    0 once Bill considers the event closed out.
  church_events.event_time — free-text time/time-range ("6:00pm - 8:00pm"),
    separate from attendance_notes (post-event session notes, a different
    thing) -- added 2026-09-17 for the Telegram new-event-notice path
    (bot.py's _handle_new_event_notice), which only requires a name; date
    and time are optional and may arrive later as a follow-up message.
  church_events.created_by / creator_notified — the leader name from the
    Telegram new-event-notice path (bot.py's _handle_new_event_notice) and
    whether they've been sent the "first registration matched" Telegram
    confirmation yet -- added 2026-09-18 so the person who just told
    Watson to track an event (almost always followed by a test signup)
    hears back once it actually works, without pinging them again on every
    later real registrant. See jobs/events/signup_detect.py.
  church_events.rsvp_tracking — added 2026-09-24 for the annual Servant
    Leaders Banquet. 0 (default) means signup emails for this event are
    handled the old way by jobs/events/signup_detect.py (every registration
    email = one attendee, no decline concept). 1 means the event's RSVP
    form can reply either yes or no, and jobs/events/banquet_rsvp.py (not
    signup_detect.py) owns its incoming emails -- see that file for why
    this needed a separate handler instead of extending signup_detect.py.
  event_registrations — one row per registrant/signup for an event.
    rsvp_status / child_count (added 2026-09-24, both nullable/optional --
    ONLY meaningful for rsvp_tracking=1 events) -- see
    jobs/events/banquet_rsvp.py's module docstring for what they mean and
    why child_count is tracked as its own column instead of folded into
    num_tickets.
"""
import sqlite3

from config.settings import DB_PATH


def create_tables() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("ALTER TABLE church_events ADD COLUMN tracking_active INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE church_events ADD COLUMN event_time TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE church_events ADD COLUMN created_by TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE church_events ADD COLUMN creator_notified INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE church_events ADD COLUMN rsvp_tracking INTEGER NOT NULL DEFAULT 0")
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
    try:
        conn.execute("ALTER TABLE event_registrations ADD COLUMN rsvp_status TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE event_registrations ADD COLUMN child_count INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_event_registrations_event_id
        ON event_registrations(event_id)
    """)
    conn.commit()
    conn.close()
