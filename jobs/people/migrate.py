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

    # connect_cards
    conn.execute("""
        CREATE TABLE IF NOT EXISTS connect_cards (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            congregation_id       INTEGER,
            first_name            TEXT,
            last_name             TEXT,
            email                 TEXT,
            phone                 TEXT,
            campus                TEXT,
            service_date          DATE,
            is_first_visit        INTEGER DEFAULT 0,
            next_steps            TEXT,
            question_comment      TEXT,
            prayer_request        TEXT,
            prayer_request_public INTEGER DEFAULT 1,
            created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    print("people, congregation, and connect_cards tables ready.")


if __name__ == "__main__":
    run()
