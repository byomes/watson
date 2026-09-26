"""jobs/sms/schema.py — Schema for Watson SMS (project_backlog id=39): the
1:1 texting channel backed by a dedicated Android phone + android-sms-gateway.

sms_threads / sms_messages / sms_templates / sms_gateway_heartbeat all live
in watson.db, not congregation.db — congregation.db stays the pastoral CRM
of record; sms_threads.member_id is a soft cross-reference into it
(resolved by phone match in jobs/sms/bridge.py), never a foreign key, since
congregation.db is a separate database file.

GUARDRAIL (Bill, 2026-09-26): sms_threads/sms_messages/sms_scheduled_messages
are Bill's own 1:1 pastoral texting log, for Bill's access only — never
surface them to deacons, elders, or staff. Concretely: never add these
tables to jobs/analytics/data_chat.py's "web" domain allowlist (Team
Chat, which leaders use), never wire them into the Deacon App or any
congregation-admin surface, and never grant the SMS web app's PIN to
anyone but Bill. The app-level PIN gate (SMS_APP_PIN) is necessary but
not sufficient on its own — this table-level rule is the backstop.
"""
from core.database import get_connection

CREATE_THREADS = """
CREATE TABLE IF NOT EXISTS sms_threads (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    phone                TEXT NOT NULL UNIQUE,
    contact_name         TEXT,
    member_id            INTEGER,
    last_message_at      TEXT,
    last_message_preview TEXT,
    unread               INTEGER NOT NULL DEFAULT 0,
    state                TEXT NOT NULL DEFAULT 'open',
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS sms_messages (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id          INTEGER NOT NULL REFERENCES sms_threads(id),
    direction          TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    body               TEXT NOT NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    gateway_message_id TEXT
);
"""

CREATE_TEMPLATES = """
CREATE TABLE IF NOT EXISTS sms_templates (
    id         TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    body       TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_HEARTBEAT = """
CREATE TABLE IF NOT EXISTS sms_gateway_heartbeat (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at  TEXT NOT NULL DEFAULT (datetime('now')),
    ok          INTEGER NOT NULL,
    battery_pct INTEGER,
    detail      TEXT
);
"""

CREATE_PUSH_SUBSCRIPTIONS = """
CREATE TABLE IF NOT EXISTS sms_push_subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# send_at is a UTC "YYYY-MM-DD HH:MM:SS" string, same convention as
# reminders.due_datetime -- jobs/sms/scheduled_sender.py compares it
# directly against SQLite's own datetime('now').
CREATE_SCHEDULED_MESSAGES = """
CREATE TABLE IF NOT EXISTS sms_scheduled_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  INTEGER NOT NULL REFERENCES sms_threads(id),
    body       TEXT NOT NULL,
    send_at    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'failed')),
    error      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

ALL_TABLES = [
    CREATE_THREADS,
    CREATE_MESSAGES,
    CREATE_TEMPLATES,
    CREATE_HEARTBEAT,
    CREATE_PUSH_SUBSCRIPTIONS,
    CREATE_SCHEDULED_MESSAGES,
]

# Bill's own wording, drafted during the design conversation (2026-09-25) —
# Watson merges {first_name} into these, never originates new phrasing. See
# the guardrail note in project_backlog id=39.
_SEED_TEMPLATES = [
    (
        "1st_time_guest",
        "1st-time guest",
        "{first_name}! So good to have you with us this week, thank you for "
        "coming. I'd love to help you get plugged in, is there a good time "
        "for a quick call or coffee this week? - Pastor Bill",
    ),
    (
        "2nd_time_guest",
        "2nd-time guest",
        "{first_name}, so glad you came back! Means a lot that you're giving "
        "us a second look. Want to grab lunch after service sometime? I'd "
        "love to answer any questions. - Pastor Bill",
    ),
]


def _migrate_columns(conn) -> None:
    """Idempotent ALTER TABLE ADD COLUMN for tables that predate a feature —
    mirrors core/database.py's _migrate() pattern."""
    thread_cols = {row[1] for row in conn.execute("PRAGMA table_info(sms_threads)").fetchall()}
    if "snoozed_until" not in thread_cols:
        conn.execute("ALTER TABLE sms_threads ADD COLUMN snoozed_until TEXT")
    if "muted" not in thread_cols:
        conn.execute("ALTER TABLE sms_threads ADD COLUMN muted INTEGER NOT NULL DEFAULT 0")

    message_cols = {row[1] for row in conn.execute("PRAGMA table_info(sms_messages)").fetchall()}
    if "media_url" not in message_cols:
        conn.execute("ALTER TABLE sms_messages ADD COLUMN media_url TEXT")
    if "media_type" not in message_cols:
        conn.execute("ALTER TABLE sms_messages ADD COLUMN media_type TEXT")
    if "status" not in message_cols:
        conn.execute("ALTER TABLE sms_messages ADD COLUMN status TEXT")


def create_tables(conn=None) -> None:
    """Idempotent — CREATE TABLE IF NOT EXISTS for all tables, ALTER TABLE
    for columns added after initial release, plus a one-time seed of the two
    starter templates if sms_templates is empty."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        for stmt in ALL_TABLES:
            conn.execute(stmt)

        _migrate_columns(conn)

        count = conn.execute("SELECT COUNT(*) FROM sms_templates").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO sms_templates (id, label, body) VALUES (?, ?, ?)",
                _SEED_TEMPLATES,
            )

        conn.commit()
    finally:
        if owns_conn:
            conn.close()
