#!/usr/bin/env python3
"""
migrate.py — Create team management tables in watson.db if they don't exist.

Usage: python jobs/team/migrate.py
"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "watson.db"


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS team_members (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            email      TEXT,
            phone      TEXT,
            role       TEXT,
            ministry   TEXT,
            notes      TEXT,
            source     TEXT DEFAULT 'manual',
            active     INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS team_objectives (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id   INTEGER NOT NULL,
            title       TEXT NOT NULL,
            description TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS team_goals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id    INTEGER NOT NULL,
            objective_id INTEGER,
            title        TEXT NOT NULL,
            target_date  TEXT,
            status       TEXT DEFAULT 'active',
            created_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS team_tasks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id  INTEGER NOT NULL,
            goal_id    INTEGER,
            meeting_id INTEGER,
            title      TEXT NOT NULL,
            due_date   TEXT,
            status     TEXT DEFAULT 'open',
            source     TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS team_meetings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id    INTEGER NOT NULL,
            date         TEXT NOT NULL,
            transcript   TEXT,
            summary      TEXT,
            email_draft  TEXT,
            email_sent   INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS team_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id  INTEGER NOT NULL,
            direction  TEXT NOT NULL,
            subject    TEXT,
            body       TEXT,
            sent_at    TEXT,
            replied_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    print("Migration complete — all team tables created (or already existed).")

    # Verify
    conn = sqlite3.connect(DB_PATH)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'team_%' ORDER BY name"
    ).fetchall()]
    conn.close()
    print(f"Team tables in watson.db: {tables}")


if __name__ == "__main__":
    run()
