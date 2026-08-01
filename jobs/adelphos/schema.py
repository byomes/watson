"""jobs/adelphos/schema.py — DB schema for the Adelphos Academy New Account
Security Monitor (Priority 1, built 2026-07-31 in response to active
fraudulent-signup abuse on www.adelphosonline.com).
"""
from core.database import get_connection


def create_tables() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS adelphos_new_accounts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                moodle_user_id      INTEGER NOT NULL UNIQUE,
                fullname            TEXT,
                email               TEXT,
                username            TEXT,
                signup_timestamp    INTEGER,
                source_ip           TEXT,
                detected_at         TEXT NOT NULL DEFAULT (datetime('now')),
                status              TEXT NOT NULL DEFAULT 'pending',
                telegram_message_id INTEGER,
                resolved_at         TEXT
            )
        """)


if __name__ == "__main__":
    create_tables()
    print("adelphos_new_accounts table ready.")
