import sqlite3
from datetime import datetime
from config.settings import DB_PATH

def init_email_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            summary TEXT,
            url TEXT,
            status TEXT DEFAULT 'queued',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_in_draft TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_to_email_queue(title, summary, url):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        """INSERT INTO email_queue (title, summary, url, status)
           VALUES (?, ?, ?, 'queued')""",
        (title, summary, url)
    )
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id
