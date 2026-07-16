"""Add benchmark_sources table to watson.db.

Tracks candidate research sources found by jobs/research/benchmark_check.py
(weekly Serper.dev scan for new church-attendance benchmark research) pending
Bill's Telegram approval before anything gets appended to
memory/projects/benchmarks.md.

Usage:
  python3 jobs/research/migrate_benchmark_sources.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/watson.db")

conn = sqlite3.connect(DB_PATH)
try:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS benchmark_sources (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          url TEXT UNIQUE NOT NULL,
          title TEXT,
          source_name TEXT,
          date_found TEXT NOT NULL,
          summary TEXT,
          status TEXT DEFAULT 'pending',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    print("Done: benchmark_sources ready.")
except Exception as e:
    print(f"Migration error: {e}")
finally:
    conn.close()
