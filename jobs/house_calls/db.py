"""jobs/house_calls/db.py — DB helpers for the funeral home house-call log.

A "house call" is a paid callout from the funeral home Bill works for
part-time. He texts Watson the family's last name when he's called out;
Watson logs it here. Once a month, jobs/house_calls/monthly_report.py emails
every not-yet-reported call to Bill's boss so he can be paid, then marks
those rows reported. Rows are never deleted — reported_at is the only state
change, so a late-logged call from a prior period is never lost or silently
skipped, and nothing can be double-reported.
"""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import DB_PATH

NY = ZoneInfo("America/New_York")


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS house_calls (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                family_last_name  TEXT NOT NULL,
                call_date         TEXT NOT NULL,
                notes             TEXT,
                reported_at       TEXT,
                created_at        TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


def add_house_call(family_last_name: str, call_date: str | None = None, notes: str | None = None) -> int:
    call_date = call_date or datetime.now(NY).date().isoformat()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO house_calls (family_last_name, call_date, notes) VALUES (?, ?, ?)",
            (family_last_name, call_date, notes),
        )
        return cur.lastrowid


def count_unreported() -> int:
    with conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM house_calls WHERE reported_at IS NULL"
        ).fetchone()[0]


def unreported_calls() -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT id, family_last_name, call_date, notes FROM house_calls "
            "WHERE reported_at IS NULL ORDER BY call_date, id"
        ).fetchall()


def mark_reported(ids: list[int]) -> None:
    if not ids:
        return
    now = datetime.now(NY).isoformat()
    with conn() as c:
        c.executemany(
            "UPDATE house_calls SET reported_at = ? WHERE id = ?",
            [(now, i) for i in ids],
        )
