import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "watson" / "data" / "congregation.db"


TABLES = {
    "members": """
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            campus_preference TEXT,
            first_visit_date TEXT,
            status TEXT DEFAULT 'visitor',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """,
    "connect_cards": """
        CREATE TABLE IF NOT EXISTS connect_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER REFERENCES members(id),
            service_date TEXT NOT NULL,
            campus TEXT NOT NULL,
            raw_text TEXT,
            processed_at TEXT DEFAULT (datetime('now')),
            email_id TEXT
        )
    """,
    "attendance": """
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER REFERENCES members(id),
            service_date TEXT NOT NULL,
            campus TEXT NOT NULL,
            card_id INTEGER REFERENCES connect_cards(id),
            created_at TEXT DEFAULT (datetime('now'))
        )
    """,
    "follow_ups": """
        CREATE TABLE IF NOT EXISTS follow_ups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER REFERENCES members(id),
            card_id INTEGER REFERENCES connect_cards(id),
            note TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """,
    "prayer_requests": """
        CREATE TABLE IF NOT EXISTS prayer_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER REFERENCES members(id),
            card_id INTEGER REFERENCES connect_cards(id),
            request_text TEXT NOT NULL,
            date TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """,
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    existed = DB_PATH.exists()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if not existed:
        print(f"Created database: {DB_PATH}")
    else:
        print(f"Using existing database: {DB_PATH}")

    existing = {
        row[0]
        for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    for table_name, ddl in TABLES.items():
        cursor.execute(ddl)
        if table_name in existing:
            print(f"  [exists]  {table_name}")
        else:
            print(f"  [created] {table_name}")

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    init_db()
