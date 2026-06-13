import sqlite3, os

DB = os.path.expanduser("~/watson/data/watson.db")
conn = sqlite3.connect(DB)
cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()]
if "ended_at" not in cols:
    conn.execute("ALTER TABLE chat_sessions ADD COLUMN ended_at TEXT DEFAULT NULL")
    print("Added ended_at to chat_sessions")
if "auto_filed" not in cols:
    conn.execute("ALTER TABLE chat_sessions ADD COLUMN auto_filed INTEGER DEFAULT 0")
    print("Added auto_filed to chat_sessions")
conn.commit()
conn.close()
print("Migration complete.")
