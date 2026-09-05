"""Add recipient to telegram_log now that Watson sends to more than Bill
(jobs/telegram/send_to_person.py onboarded leaders, e.g. Jim Bouchat).

Every row logged before this migration was Bill's own chat -- the bot's
_is_authorized gate meant nobody else's inbound/outbound traffic ever
reached telegram_log -- so existing rows backfill to 'Bill'.

Usage:
  python3 jobs/telegram/migrate_recipient.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/watson.db")

conn = sqlite3.connect(DB_PATH)
try:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(telegram_log)").fetchall()}
    if "recipient" not in existing:
        conn.execute("ALTER TABLE telegram_log ADD COLUMN recipient TEXT")
        conn.execute("UPDATE telegram_log SET recipient = 'Bill' WHERE recipient IS NULL")
        print("  [migrated] telegram_log.recipient (backfilled existing rows to 'Bill')")
    else:
        print("  [exists]   telegram_log.recipient")

    conn.commit()
    print("Done: telegram_log.recipient ready.")
finally:
    conn.close()
