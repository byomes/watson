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
Bill or Melanie can jot down what they see on the real listing (e.g. "peak
~$9200/wk, May ~$5400/wk") after checking dates themselves.

category (added 2026-09-09, see __init__.py's CATEGORIES) is part of the
uniqueness key, not just a plain column -- the same physical property can
legitimately be discovered under more than one tab (a secluded cabin with
a hot tub is a Mountain AND a Romance candidate), and each tab needs its
own row so review_status/price_note can differ per use.
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/beachhouse.db")

CREATE_BH_LISTINGS = """
CREATE TABLE IF NOT EXISTS bh_listings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    category            TEXT NOT NULL CHECK (category IN ('beach', 'mountain', 'romance')),
    source              TEXT NOT NULL CHECK (source IN ('vrbo', 'airbnb')),
    source_id           TEXT NOT NULL,
    source_url          TEXT NOT NULL,
    name                TEXT NOT NULL,
    city                TEXT,
    state               TEXT,
    bedrooms            INTEGER,
    bathrooms           REAL,
    max_sleeps          INTEGER,
    has_pool            INTEGER NOT NULL DEFAULT 0,
    oceanfront          INTEGER NOT NULL DEFAULT 0,
    hot_tub             INTEGER NOT NULL DEFAULT 0,
    fireplace           INTEGER NOT NULL DEFAULT 0,
    mountain_view       INTEGER NOT NULL DEFAULT 0,
    secluded            INTEGER NOT NULL DEFAULT 0,
    description         TEXT,
    primary_image_url   TEXT,
    price_note          TEXT,
    review_status       TEXT NOT NULL DEFAULT 'new'
                        CHECK (review_status IN ('new', 'saved', 'dismissed')),
    discovered_at       TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, source_id, category)
)
"""

CREATE_INDEX_STATE = "CREATE INDEX IF NOT EXISTS idx_bh_listings_state ON bh_listings(category, state)"

_NEW_AMENITY_COLS = ["hot_tub", "fireplace", "mountain_view", "secluded"]


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_to_categories(conn: sqlite3.Connection) -> None:
    """One-time rebuild for the pre-category schema (2026-09-09): SQLite
    can't ALTER a UNIQUE constraint in place, so the table gets recreated
    under a temp name, all existing rows are copied over tagged
    category='beach' (the only category that existed before this), and the
    old table is dropped. Existing review_status/price_note survive."""
    conn.execute("ALTER TABLE bh_listings RENAME TO bh_listings_old")
    conn.execute(CREATE_BH_LISTINGS)
    conn.execute(
        """INSERT INTO bh_listings (
            category, source, source_id, source_url, name, city, state,
            bedrooms, bathrooms, max_sleeps, has_pool, oceanfront,
            description, primary_image_url, price_note, review_status,
            discovered_at, last_seen_at
        )
        SELECT
            'beach', source, source_id, source_url, name, city, state,
            bedrooms, bathrooms, max_sleeps, has_pool, oceanfront,
            description, primary_image_url, price_note, review_status,
            discovered_at, last_seen_at
        FROM bh_listings_old"""
    )
    conn.execute("DROP TABLE bh_listings_old")


def create_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_connection() as conn:
        conn.execute(CREATE_BH_LISTINGS)

        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(bh_listings)").fetchall()}
        if "price_note" not in existing_cols:
            conn.execute("ALTER TABLE bh_listings ADD COLUMN price_note TEXT")
            existing_cols.add("price_note")
        if "category" not in existing_cols:
            _migrate_to_categories(conn)
        else:
            for col in _NEW_AMENITY_COLS:
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE bh_listings ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")

        conn.execute(CREATE_INDEX_STATE)


if __name__ == "__main__":
    create_tables()
    print(f"beachhouse.db ready at {DB_PATH}")
