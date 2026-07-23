import os
import shutil
import sqlite3
from datetime import datetime

DB = os.path.expanduser("~/watson/data/watson.db")

backup_path = f"{DB}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(DB, backup_path)
print(f"Backed up watson.db to {backup_path}")

conn = sqlite3.connect(DB)
conn.execute("""
    CREATE TABLE IF NOT EXISTS branded_links (
        slug TEXT PRIMARY KEY,
        destination TEXT NOT NULL,
        clicks INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        notes TEXT
    )
""")

now = datetime.utcnow().isoformat()
conn.execute(
    "INSERT OR IGNORE INTO branded_links (slug, destination, clicks, created_at, updated_at, notes) "
    "VALUES (?, ?, 0, ?, ?, ?)",
    ("office", "https://meet.google.com/obi-qsvd-hfs?pli=1", now, now, "Virtual office"),
)
conn.commit()
conn.close()
print("Migration complete.")
