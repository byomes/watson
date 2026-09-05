"""Add telegram_claim_code to people, backing Telegram leader onboarding
(jobs/telegram/seed_claim_codes.py, bot.py's /start payload handler,
jobs/telegram/send_to_person.py).

telegram_chat_id already exists on people (unused elsewhere until this
build) and is reused as-is -- this migration only adds the claim-code
column used to bind a leader's real chat_id to their people row via a
one-time /start deep link.

Uniqueness is enforced via a separate index rather than an inline UNIQUE
on the ALTER TABLE -- SQLite's ADD COLUMN rejects a UNIQUE constraint
directly ("Cannot add a UNIQUE column"), and a plain unique index gives
the same guarantee (including allowing multiple NULLs for
not-yet-claimed/never-issued rows).

Usage:
  python3 jobs/people/migrate_telegram_claim_code.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/watson.db")

conn = sqlite3.connect(DB_PATH)
try:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(people)").fetchall()}
    if "telegram_claim_code" not in existing:
        conn.execute("ALTER TABLE people ADD COLUMN telegram_claim_code TEXT")
        print("  [migrated] people.telegram_claim_code")
    else:
        print("  [exists]   people.telegram_claim_code")

    indexes = {row[1] for row in conn.execute("PRAGMA index_list(people)").fetchall()}
    if "idx_people_telegram_claim_code" not in indexes:
        conn.execute(
            "CREATE UNIQUE INDEX idx_people_telegram_claim_code "
            "ON people(telegram_claim_code)"
        )
        print("  [migrated] idx_people_telegram_claim_code")
    else:
        print("  [exists]   idx_people_telegram_claim_code")

    conn.commit()
    print("Done: telegram_claim_code column + unique index ready.")
finally:
    conn.close()
