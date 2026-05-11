import sqlite3
from config.settings import DB_PATH


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate():
    with get_connection() as conn:
        # ── items table ────────────────────────────────────────────────────
        existing = {row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
        if "score" not in existing:
            conn.execute("ALTER TABLE items ADD COLUMN score INTEGER")
        if "featured_date" not in existing:
            conn.execute("ALTER TABLE items ADD COLUMN featured_date TEXT")

        # ── voice_notes table ──────────────────────────────────────────────
        vn_cols = {row[1] for row in conn.execute("PRAGMA table_info(voice_notes)").fetchall()}
        if "created_at" not in vn_cols:
            conn.execute("ALTER TABLE voice_notes ADD COLUMN created_at TEXT")

        # ── briefing_items table ───────────────────────────────────────────
        bi_cols = {row[1] for row in conn.execute("PRAGMA table_info(briefing_items)").fetchall()}
        if "published_at" not in bi_cols:
            conn.execute("ALTER TABLE briefing_items ADD COLUMN published_at TEXT")
        if "date_unknown" not in bi_cols:
            conn.execute(
                "ALTER TABLE briefing_items ADD COLUMN date_unknown INTEGER NOT NULL DEFAULT 0"
            )
        if "reject_reason" not in bi_cols:
            conn.execute("ALTER TABLE briefing_items ADD COLUMN reject_reason TEXT")

        # ── research_archive table ─────────────────────────────────────────
        ra_cols = {row[1] for row in conn.execute("PRAGMA table_info(research_archive)").fetchall()}
        if "published_at" not in ra_cols:
            conn.execute("ALTER TABLE research_archive ADD COLUMN published_at TEXT")
        if "date_unknown" not in ra_cols:
            conn.execute(
                "ALTER TABLE research_archive ADD COLUMN date_unknown INTEGER NOT NULL DEFAULT 0"
            )

        # ── rejection_patterns table ───────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rejection_patterns (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT,
                keyword     TEXT,
                reason      TEXT,
                count       INTEGER NOT NULL DEFAULT 1,
                last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS research_archive (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT NOT NULL,
                url          TEXT NOT NULL UNIQUE,
                summary      TEXT,
                source_name  TEXT NOT NULL,
                source_type  TEXT NOT NULL DEFAULT 'article',
                priority     INTEGER NOT NULL DEFAULT 3,
                published_at TEXT,
                date_unknown INTEGER NOT NULL DEFAULT 0,
                fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS briefing_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT NOT NULL,
                url          TEXT NOT NULL UNIQUE,
                summary      TEXT,
                source_name  TEXT NOT NULL,
                source_type  TEXT NOT NULL DEFAULT 'article',
                priority     INTEGER NOT NULL DEFAULT 3,
                score        INTEGER,
                published_at TEXT,
                date_unknown INTEGER NOT NULL DEFAULT 0,
                fetched_at    TEXT NOT NULL DEFAULT (datetime('now')),
                dismissed     INTEGER NOT NULL DEFAULT 0,
                reject_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS items (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name   TEXT NOT NULL,
                source_type   TEXT NOT NULL CHECK(source_type IN ('article', 'podcast', 'publication', 'journal')),
                title         TEXT NOT NULL,
                url           TEXT,
                summary       TEXT,
                published_date TEXT,
                fetched_date  TEXT NOT NULL DEFAULT (datetime('now')),
                status        TEXT NOT NULL DEFAULT 'new'
                                  CHECK(status IN ('new', 'sent_to_broadcaster', 'archived', 'dismissed')),
                score         INTEGER,
                featured_date TEXT
            );

            CREATE TABLE IF NOT EXISTS thought_library (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type  TEXT NOT NULL CHECK(content_type IN ('transcript', 'voice_note', 'bible_study', 'sermon')),
                title         TEXT NOT NULL,
                body          TEXT,
                tags          TEXT,
                bible_passage TEXT,
                date_created  TEXT,
                date_indexed  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS research_library (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type  TEXT NOT NULL CHECK(content_type IN ('article', 'podcast', 'publication', 'journal', 'book_physical', 'book_digital')),
                title         TEXT NOT NULL,
                url           TEXT,
                author        TEXT,
                summary       TEXT,
                source_name   TEXT,
                tags          TEXT,
                date_published TEXT,
                date_indexed  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS voice_notes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                transcript    TEXT,
                tags          TEXT,
                theme         TEXT,
                date_captured TEXT NOT NULL DEFAULT (datetime('now')),
                status        TEXT NOT NULL DEFAULT 'new'
                                  CHECK(status IN ('new', 'reviewed'))
            );

            CREATE TABLE IF NOT EXISTS reading_list (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT NOT NULL,
                url           TEXT,
                source_name   TEXT,
                source_type   TEXT,
                summary       TEXT,
                date_added    TEXT NOT NULL DEFAULT (datetime('now')),
                status        TEXT NOT NULL DEFAULT 'unread'
                                  CHECK(status IN ('unread', 'reading', 'finished'))
            );

            CREATE TABLE IF NOT EXISTS rejection_patterns (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT,
                keyword     TEXT,
                reason      TEXT,
                count       INTEGER NOT NULL DEFAULT 1,
                last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
    _migrate()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
