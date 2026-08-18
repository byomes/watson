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

        # previewed_at: added in Phase 2 for the weekly digest job. A separate
        # timestamp column rather than repurposing status='previewed' (already
        # a valid CHECK value) — a row can be both 'edited' and previewed, and
        # overloading status would lose whichever happened first.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(book_launch_sends)").fetchall()}
        if "previewed_at" not in cols:
            conn.execute("ALTER TABLE book_launch_sends ADD COLUMN previewed_at TEXT")

        # image_path: local path to a generated/approved image for this row
        # (Facebook only — Brevo rows leave it NULL). dispatch_facebook_row()
        # reads it and passes it through to facebook_queue.image_path, which
        # facebook_post.py already honors (posts via /photos when set, /feed
        # when not) — this column is what was missing to connect the two.
        if "image_path" not in cols:
            conn.execute("ALTER TABLE book_launch_sends ADD COLUMN image_path TEXT")

        # recipient_mode / recipient_detail: Comms Desk email rows can target a
        # live Brevo list or a hand-picked set of individual contacts, neither
        # of which fits the existing `segment` CHECK (public/general/donor/arc).
        # Rather than rebuild that constraint (SQLite can't ALTER a CHECK in
        # place), these two nullable columns carry the real target and segment
        # is left as a satisfying placeholder ('general') for such rows.
        # recipient_mode: NULL/'segment' (use `segment` as before), 'brevo_list',
        # or 'custom_emails'. recipient_detail: JSON — {"list_id", "list_name"}
        # for brevo_list, {"emails": [{"email","name"}, ...]} for custom_emails.
        # See jobs/campaigns/dispatch.py:resolve_recipients().
        if "recipient_mode" not in cols:
            conn.execute("ALTER TABLE book_launch_sends ADD COLUMN recipient_mode TEXT")
        if "recipient_detail" not in cols:
            conn.execute("ALTER TABLE book_launch_sends ADD COLUMN recipient_detail TEXT")

        # needs_image: Facebook-only flag (0/1). Set when a row is created with
        # image_intent='needs_manual' (Comms Desk composer or Claude.ai batch
        # import) — a human still needs to attach a real photo. Comms Desk
        # badges these on the calendar until AddImageModal clears the flag via
        # the existing edit_send() PUT route.
        if "needs_image" not in cols:
            conn.execute("ALTER TABLE book_launch_sends ADD COLUMN needs_image INTEGER DEFAULT 0")

        # admin_approved_at: temporary extra safety gate for Comms Desk email
        # (platform='brevo', source='comms_desk') rows only — set only via
        # jobs/comms/api.py's admin-only /approve-send route. Kaci/a volunteer
        # marking a row 'ready' (status='approved') is no longer sufficient on
        # its own to make an email eligible for the real Brevo send; see the
        # gate in jobs/campaigns/dispatch.py:send_brevo_row(). Meant to be
        # removed later once Watson's Comms Desk pipeline has earned trust —
        # book-launch campaign Brevo rows (source != 'comms_desk') are
        # unaffected and keep sending on 'approved' alone, same as always.
        if "admin_approved_at" not in cols:
            conn.execute("ALTER TABLE book_launch_sends ADD COLUMN admin_approved_at TEXT")

        conn.commit()
    finally:
        if owns_conn:
            conn.close()
