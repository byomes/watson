import sqlite3
import os

DB_PATH = os.path.expanduser("~/watson/data/watson.db")


def run():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    # people — create with full spec schema
    conn.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            email        TEXT,
            phone        TEXT,
            relationship TEXT,
            notes        TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Safely add columns that older schema versions may be missing
    existing = {row[1] for row in conn.execute("PRAGMA table_info(people)").fetchall()}
    for col, defn in [("relationship", "TEXT"), ("notes", "TEXT")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE people ADD COLUMN {col} {defn}")

    # congregation
    conn.execute("""
        CREATE TABLE IF NOT EXISTS congregation (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            email           TEXT,
            phone           TEXT,
            status          TEXT,
            campus          TEXT,
            notes           TEXT,
            prayer_requests TEXT,
            follow_up       TEXT,
            first_seen      DATETIME,
            last_seen       DATETIME,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    print("people and congregation tables ready.")


if __name__ == "__main__":
    run()
