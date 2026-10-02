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

# Singleton row (id=1) for app-wide toggles -- vacation mode and the Friday
# Sabbath silence, added 2026-09-26. sabbath_silence defaults ON since it's
# Bill's standing rule; vacation_mode defaults OFF.
#
# Autoresponder fields (added 2026-10-02, project_backlog id=39 follow-on):
# separate Bill-authored body text for Sabbath vs. vacation, each with its
# own enable toggle independent of the notification-silence toggles above --
# turning on Friday Sabbath silence alone must not start auto-texting
# people. vacation_started_at is stamped whenever vacation_mode flips 0->1
# (see settings.py's set_setting) and is the autoresponder's window key for
# that vacation period -- see jobs/sms/autoresponder.py.
CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS sms_settings (
    id                          INTEGER PRIMARY KEY CHECK (id = 1),
    vacation_mode               INTEGER NOT NULL DEFAULT 0,
    sabbath_silence             INTEGER NOT NULL DEFAULT 1,
    sabbath_autoresponder_on    INTEGER NOT NULL DEFAULT 0,
    sabbath_autoresponder_body  TEXT,
    vacation_autoresponder_on   INTEGER NOT NULL DEFAULT 0,
    vacation_autoresponder_body TEXT,
    vacation_started_at         TEXT,
    updated_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# One row per (phone, window_key) a Sabbath/vacation autoresponse has
# already fired for -- the "once per sender per silence window" dedup.
# window_key is "sabbath:<America/New_York date>" (so it naturally resets
# the next Friday) or "vacation:<vacation_started_at>" (so toggling
# vacation off and back on starts a fresh window, but repeat texts within
# one vacation period only get one reply) -- see autoresponder.py.
CREATE_AUTORESPONDER_LOG = """
CREATE TABLE IF NOT EXISTS sms_autoresponder_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT NOT NULL,
    window_key TEXT NOT NULL,
    thread_id  INTEGER NOT NULL REFERENCES sms_threads(id),
    sent_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(phone, window_key)
);
"""

# Group-text support (added 2026-09-28). One row per participant in a
# thread -- a 1:1 thread just has one row -- so send/fan-out logic (api.py's
# _send_and_record) is a single code path instead of an is_group fork. Every
# thread, old or new, gets backfilled with its one participant row in
# _migrate_columns below, so this table is never empty for a real thread.
CREATE_THREAD_PARTICIPANTS = """
CREATE TABLE IF NOT EXISTS sms_thread_participants (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    INTEGER NOT NULL REFERENCES sms_threads(id),
    phone        TEXT NOT NULL,
    contact_name TEXT,
    member_id    INTEGER,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(thread_id, phone)
);
"""

# Only populated when an outbound send fans out to >1 participant (a group
# thread) -- the 1:1 case keeps using sms_messages.gateway_message_id/status
# directly, unchanged.
CREATE_MESSAGE_RECIPIENTS = """
CREATE TABLE IF NOT EXISTS sms_message_recipients (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id         INTEGER NOT NULL REFERENCES sms_messages(id),
    phone              TEXT NOT NULL,
    gateway_message_id TEXT,
    status             TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Broadcast support (added 2026-09-29, project_backlog id=39 follow-on).
# Bill's own wording goes out verbatim to a Bill-defined group -- Watson
# only resolves who's in the group and fans the send out on schedule (see
# feedback_ai_never_originates_relational_language: no template, no
# merge-field, no rewriting here). Delivery is always individual 1:1 texts
# (jobs/sms/broadcast_sender.py), never a single group-MMS thread, so no
# recipient sees anyone else's number or replies.
#
# A group is "anything Bill defines": filter_json is a dict of dimension ->
# list of values (deacons/teams/roles/campuses), OR'd within and across
# dimensions, ANDed with active_only if set; sms_group_members layers
# explicit manual include/exclude rows on top (jobs/sms/groups.py resolves
# both together). An empty filter with active_only=1 is "Everyone".
CREATE_GROUPS = """
CREATE TABLE IF NOT EXISTS sms_groups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    filter_json  TEXT NOT NULL DEFAULT '{}',
    active_only  INTEGER NOT NULL DEFAULT 1,
    manual_only  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_GROUP_MEMBERS = """
CREATE TABLE IF NOT EXISTS sms_group_members (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id     INTEGER NOT NULL REFERENCES sms_groups(id),
    member_id    INTEGER,
    phone        TEXT NOT NULL,
    contact_name TEXT,
    mode         TEXT NOT NULL CHECK (mode IN ('include', 'exclude')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(group_id, phone)
);
"""

# recipient_count/sent_count/failed_count are snapshotted/updated as the
# broadcast runs so the app can show progress without re-resolving the
# group (which may have since changed) -- see the "preview then confirm"
# design: sms_broadcast_recipients below is the frozen recipient list as of
# confirm time, not a live re-query of the group at send time.
CREATE_BROADCASTS = """
CREATE TABLE IF NOT EXISTS sms_broadcasts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        INTEGER REFERENCES sms_groups(id),
    group_name      TEXT,
    body            TEXT NOT NULL,
    send_at         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'scheduled'
                        CHECK (status IN ('scheduled', 'sending', 'sent', 'failed', 'canceled')),
    recipient_count INTEGER NOT NULL DEFAULT 0,
    sent_count      INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    spread_hours    REAL,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    completed_at    TEXT
);
"""

# send_at (added 2026-09-29) is each recipient's OWN randomized send time,
# not the broadcast's -- computed once at confirm time by
# jobs/sms/broadcast_pacing.py and spread across a window so a broadcast to
# many people trickles out like someone individually texting each of them,
# instead of firing all at once, which is exactly the burst pattern carrier
# anti-spam filters flag a single device for. claimed_at guards against a
# recipient being sent twice if two broadcast_sender.py ticks ever overlap
# (a real possibility now that a tick can take longer than before, thanks
# to the small real-time pacing sleep it also does) -- a plain SELECT-then-
# UPDATE-status race isn't enough on its own, so the send loop claims a row
# via `UPDATE ... WHERE claimed_at IS NULL` and checks the row was actually
# affected before sending.
CREATE_BROADCAST_RECIPIENTS = """
CREATE TABLE IF NOT EXISTS sms_broadcast_recipients (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    broadcast_id       INTEGER NOT NULL REFERENCES sms_broadcasts(id),
    member_id          INTEGER,
    phone              TEXT NOT NULL,
    contact_name       TEXT,
    thread_id          INTEGER,
    status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    gateway_message_id TEXT,
    error              TEXT,
    send_at            TEXT,
    claimed_at         TEXT,
    sent_at            TEXT
);
"""

ALL_TABLES = [
    CREATE_THREADS,
    CREATE_MESSAGES,
    CREATE_TEMPLATES,
    CREATE_HEARTBEAT,
    CREATE_PUSH_SUBSCRIPTIONS,
    CREATE_SCHEDULED_MESSAGES,
    CREATE_SETTINGS,
    CREATE_THREAD_PARTICIPANTS,
    CREATE_MESSAGE_RECIPIENTS,
    CREATE_GROUPS,
    CREATE_GROUP_MEMBERS,
    CREATE_BROADCASTS,
    CREATE_BROADCAST_RECIPIENTS,
    CREATE_AUTORESPONDER_LOG,
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
    if "draft_text" not in thread_cols:
        # Watson-prepped draft text: Bill asks Watson to "prep a text" for a
        # thread, Watson stages it here, the app loads it into the compose
        # box on next open and clears it -- Bill still has to tap send
        # himself, same as picking a saved template. Not a guardrail
        # exception (unlike send-to-self); drafting for any recipient was
        # always allowed.
        conn.execute("ALTER TABLE sms_threads ADD COLUMN draft_text TEXT")
    if "highlight_note" not in thread_cols:
        # Set by jobs/congregation/birthday_daily_alert.py (and available for
        # similar same-day nudges) -- pins the thread to the top of the list
        # with a note, e.g. "Birthday today". Only shown/active while
        # highlight_date matches today (see api.py's _thread_dict), so it
        # fades on its own the next day with no cleanup job needed. Never
        # touches draft_text -- the compose box stays empty for Bill to
        # write his own message.
        conn.execute("ALTER TABLE sms_threads ADD COLUMN highlight_note TEXT")
    if "highlight_date" not in thread_cols:
        conn.execute("ALTER TABLE sms_threads ADD COLUMN highlight_date TEXT")
    if "is_group" not in thread_cols:
        conn.execute("ALTER TABLE sms_threads ADD COLUMN is_group INTEGER NOT NULL DEFAULT 0")
    if "android_thread_id" not in thread_cols:
        # Android's own content://mms-sms thread id, for fast re-lookup once
        # a thread has been seen before. Not UNIQUE -- fast-path hint, not
        # the source of truth (see jobs/sms/adb_inbound.py, which matches on
        # the real participant set instead, since Android's own thread
        # grouping turned out to be gated by a separate "Group messaging"
        # toggle unrelated to who holds the default-SMS role).
        conn.execute("ALTER TABLE sms_threads ADD COLUMN android_thread_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sms_threads_android_thread_id ON sms_threads(android_thread_id)")
    if "group_name" not in thread_cols:
        # Bill-given override for a group thread's display name (e.g. "Elder
        # Board" instead of listing every participant) -- set via PATCH
        # /threads/<id>, always wins over the auto participant-name label
        # when non-empty. Meaningless for a 1:1 thread, left NULL there.
        conn.execute("ALTER TABLE sms_threads ADD COLUMN group_name TEXT")

    settings_cols = {row[1] for row in conn.execute("PRAGMA table_info(sms_settings)").fetchall()}
    if "sabbath_autoresponder_on" not in settings_cols:
        conn.execute("ALTER TABLE sms_settings ADD COLUMN sabbath_autoresponder_on INTEGER NOT NULL DEFAULT 0")
    if "sabbath_autoresponder_body" not in settings_cols:
        conn.execute("ALTER TABLE sms_settings ADD COLUMN sabbath_autoresponder_body TEXT")
    if "vacation_autoresponder_on" not in settings_cols:
        conn.execute("ALTER TABLE sms_settings ADD COLUMN vacation_autoresponder_on INTEGER NOT NULL DEFAULT 0")
    if "vacation_autoresponder_body" not in settings_cols:
        conn.execute("ALTER TABLE sms_settings ADD COLUMN vacation_autoresponder_body TEXT")
    if "vacation_started_at" not in settings_cols:
        conn.execute("ALTER TABLE sms_settings ADD COLUMN vacation_started_at TEXT")

    group_cols = {row[1] for row in conn.execute("PRAGMA table_info(sms_groups)").fetchall()}
    if "manual_only" not in group_cols:
        conn.execute("ALTER TABLE sms_groups ADD COLUMN manual_only INTEGER NOT NULL DEFAULT 0")

    broadcast_cols = {row[1] for row in conn.execute("PRAGMA table_info(sms_broadcasts)").fetchall()}
    if "spread_hours" not in broadcast_cols:
        conn.execute("ALTER TABLE sms_broadcasts ADD COLUMN spread_hours REAL")

    broadcast_recipient_cols = {row[1] for row in conn.execute("PRAGMA table_info(sms_broadcast_recipients)").fetchall()}
    if "send_at" not in broadcast_recipient_cols:
        conn.execute("ALTER TABLE sms_broadcast_recipients ADD COLUMN send_at TEXT")
    if "claimed_at" not in broadcast_recipient_cols:
        conn.execute("ALTER TABLE sms_broadcast_recipients ADD COLUMN claimed_at TEXT")

    message_cols = {row[1] for row in conn.execute("PRAGMA table_info(sms_messages)").fetchall()}
    if "media_url" not in message_cols:
        conn.execute("ALTER TABLE sms_messages ADD COLUMN media_url TEXT")
    if "media_type" not in message_cols:
        conn.execute("ALTER TABLE sms_messages ADD COLUMN media_type TEXT")
    if "status" not in message_cols:
        conn.execute("ALTER TABLE sms_messages ADD COLUMN status TEXT")
    if "sender_phone" not in message_cols:
        # Which participant sent an inbound group-thread message. NULL for
        # outbound rows and for legacy inbound rows ingested before this
        # migration. No sender_name column -- resolved at read time via
        # sms_thread_participants/congregation.db (api.py) so a later
        # contact rename doesn't go stale.
        conn.execute("ALTER TABLE sms_messages ADD COLUMN sender_phone TEXT")

    # One-time backfill: give every pre-existing thread its one participant
    # row, so _send_and_record (api.py) never needs an "empty participants"
    # fallback -- one real code path for 1:1 and group sends alike.
    participants_empty = conn.execute("SELECT COUNT(*) FROM sms_thread_participants").fetchone()[0] == 0
    if participants_empty:
        existing_threads = conn.execute("SELECT id, phone, contact_name, member_id FROM sms_threads").fetchall()
        if existing_threads:
            conn.executemany(
                "INSERT OR IGNORE INTO sms_thread_participants (thread_id, phone, contact_name, member_id) "
                "VALUES (?, ?, ?, ?)",
                [(t[0], t[1], t[2], t[3]) for t in existing_threads],
            )


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

        conn.execute("INSERT OR IGNORE INTO sms_settings (id) VALUES (1)")

        conn.commit()
    finally:
        if owns_conn:
            conn.close()
