import sqlite3

from jobs.thesis_tracker import get_db


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thesis_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pulled_at TEXT NOT NULL,
                window_type TEXT NOT NULL DEFAULT 'rolling_30d',
                window_start TEXT,
                window_end TEXT,
                total_downloads INTEGER,
                total_views INTEGER,
                total_countries INTEGER,
                source_link TEXT,
                raw_json TEXT
            )
        """)
        try:
            conn.execute(
                "ALTER TABLE thesis_snapshots ADD COLUMN window_type TEXT NOT NULL DEFAULT 'rolling_30d'"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thesis_titles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER REFERENCES thesis_snapshots(id),
                title TEXT,
                downloads INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thesis_countries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER REFERENCES thesis_snapshots(id),
                country TEXT,
                downloads INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thesis_institutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER REFERENCES thesis_snapshots(id),
                institution TEXT,
                downloads INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thesis_referrers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER REFERENCES thesis_snapshots(id),
                referrer TEXT,
                downloads INTEGER
            )
        """)


def insert_snapshot(
    pulled_at: str,
    window_start: str | None,
    window_end: str | None,
    total_downloads: int | None,
    total_views: int | None,
    total_countries: int | None,
    source_link: str,
    raw_json: str,
    titles: list[dict],
    countries: list[dict],
    institutions: list[dict],
    referrers: list[dict],
    window_type: str = "rolling_30d",
) -> int:
    """Insert one full snapshot (parent row + breakdown rows). Returns snapshot id."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO thesis_snapshots
               (pulled_at, window_type, window_start, window_end, total_downloads, total_views,
                total_countries, source_link, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pulled_at, window_type, window_start, window_end, total_downloads, total_views,
             total_countries, source_link, raw_json),
        )
        snapshot_id = cur.lastrowid

        for row in titles:
            conn.execute(
                "INSERT INTO thesis_titles (snapshot_id, title, downloads) VALUES (?, ?, ?)",
                (snapshot_id, row.get("title"), row.get("downloads")),
            )
        for row in countries:
            conn.execute(
                "INSERT INTO thesis_countries (snapshot_id, country, downloads) VALUES (?, ?, ?)",
                (snapshot_id, row.get("country"), row.get("downloads")),
            )
        for row in institutions:
            conn.execute(
                "INSERT INTO thesis_institutions (snapshot_id, institution, downloads) VALUES (?, ?, ?)",
                (snapshot_id, row.get("institution"), row.get("downloads")),
            )
        for row in referrers:
            conn.execute(
                "INSERT INTO thesis_referrers (snapshot_id, referrer, downloads) VALUES (?, ?, ?)",
                (snapshot_id, row.get("referrer"), row.get("downloads")),
            )

        return snapshot_id


def get_known_countries() -> set[str]:
    with get_db() as conn:
        rows = conn.execute("SELECT DISTINCT country FROM thesis_countries").fetchall()
    return {row["country"] for row in rows if row["country"]}


def get_known_institutions() -> set[str]:
    with get_db() as conn:
        rows = conn.execute("SELECT DISTINCT institution FROM thesis_institutions").fetchall()
    return {row["institution"] for row in rows if row["institution"]}
