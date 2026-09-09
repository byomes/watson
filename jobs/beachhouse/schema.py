"""jobs/beachhouse/schema.py — bh_listings table in the dedicated
~/watson/data/beachhouse.db file (own file, same one-db-per-domain pattern
as servantcare.db/congregation.db/curator.db).

No image table -- unlike servantcare (which mirrors a directory Bill's
ministry already has standing permission to run search over), these
listings come from VRBO/Airbnb. Photos stay hotlinked straight from their
own CDN URLs (primary_image_url) rather than downloaded and re-hosted, to
avoid copying and redistributing another platform's listing photos.

price_note is manual, free-text, editable from the wtsn.me card -- there is
no automated per-date price lookup (VRBO puts a bot-detection challenge on
its pricing step, confirmed live 2026-09-09; see jobs/beachhouse/__init__.py).
Bill or Donna can jot down what they see on the real listing (e.g. "peak
~$9200/wk, May ~$5400/wk") after checking dates themselves.
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/beachhouse.db")

CREATE_BH_LISTINGS = """
CREATE TABLE IF NOT EXISTS bh_listings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL CHECK (source IN ('vrbo', 'airbnb')),
    source_id           TEXT NOT NULL,
    source_url          TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    city                TEXT,
    state               TEXT,
    bedrooms            INTEGER,
    bathrooms           REAL,
    max_sleeps          INTEGER,
    has_pool            INTEGER NOT NULL DEFAULT 0,
    oceanfront          INTEGER NOT NULL DEFAULT 0,
    description         TEXT,
    primary_image_url   TEXT,
    price_note          TEXT,
    review_status       TEXT NOT NULL DEFAULT 'new'
                        CHECK (review_status IN ('new', 'saved', 'dismissed')),
    discovered_at       TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, source_id)
)
"""

CREATE_INDEX_STATE = "CREATE INDEX IF NOT EXISTS idx_bh_listings_state ON bh_listings(state)"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_connection() as conn:
        conn.execute(CREATE_BH_LISTINGS)
        conn.execute(CREATE_INDEX_STATE)
        # price_note added 2026-09-09 after the table already existed live —
        # ALTER TABLE ADD COLUMN, guarded so this stays safe to re-run.
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(bh_listings)").fetchall()}
        if "price_note" not in existing_cols:
            conn.execute("ALTER TABLE bh_listings ADD COLUMN price_note TEXT")


if __name__ == "__main__":
    create_tables()
    print(f"beachhouse.db ready at {DB_PATH}")
