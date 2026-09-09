"""jobs/house_calls/db.py — DB helpers for the funeral home house-call log.

A "house call" is a paid callout from the funeral home Bill works for
part-time (his boss: Jim), at a flat RATE_PER_CALL each. He texts Watson the
family's last name when he's called out; Watson logs it here with the
current date/time. Once a month, jobs/house_calls/monthly_report.py emails
every not-yet-reported call to Jim so Bill can be paid, then marks those
rows reported. Rows are never deleted — reported_at is the only state
change, so a late-logged call from a prior period is never lost or silently
skipped, and nothing can be double-reported. amount is captured per-row at
log time (not computed from RATE_PER_CALL at report time) so a future rate
change never rewrites the pay owed on an already-logged call.
"""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import DB_PATH

NY = ZoneInfo("America/New_York")

RATE_PER_CALL = 100.00


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with conn() as c:
        # Migration from the original date-only schema (call_date TEXT NOT
        # NULL, no time or amount column) — call_date's NOT NULL constraint
        # can't be relaxed with ALTER TABLE ADD COLUMN, so rename the old
        # table aside, create the current schema fresh, and copy any rows
        # forward (converting call_date to a midnight called_at) before
        # dropping the old one.
        cols = {row[1] for row in c.execute("PRAGMA table_info(house_calls)").fetchall()}
        if "call_date" in cols:
            c.execute("ALTER TABLE house_calls RENAME TO house_calls_legacy_call_date")

        c.execute("""
            CREATE TABLE IF NOT EXISTS house_calls (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                family_last_name  TEXT NOT NULL,
                called_at         TEXT NOT NULL,
                amount            REAL NOT NULL DEFAULT 100.00,
                notes             TEXT,
                reported_at       TEXT,
                created_at        TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        legacy = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='house_calls_legacy_call_date'"
        ).fetchone()
        if legacy:
            legacy_cols = {row[1] for row in c.execute("PRAGMA table_info(house_calls_legacy_call_date)").fetchall()}
            called_at_src = "called_at" if "called_at" in legacy_cols else "NULL"
            amount_src = "amount" if "amount" in legacy_cols else "NULL"
            c.execute(
                f"""
                INSERT INTO house_calls (id, family_last_name, called_at, amount, notes, reported_at, created_at)
                SELECT id, family_last_name,
                       COALESCE({called_at_src}, call_date || ' 00:00'),
                       COALESCE({amount_src}, {RATE_PER_CALL}),
                       notes, reported_at, created_at
                FROM house_calls_legacy_call_date
                """
            )
            c.execute("DROP TABLE house_calls_legacy_call_date")


def add_house_call(family_last_name: str, called_at: str | None = None,
                    amount: float | None = None, notes: str | None = None) -> int:
    called_at = called_at or datetime.now(NY).strftime("%Y-%m-%d %H:%M")
    amount = RATE_PER_CALL if amount is None else amount
    with conn() as c:
        cur = c.execute(
            "INSERT INTO house_calls (family_last_name, called_at, amount, notes) VALUES (?, ?, ?, ?)",
            (family_last_name, called_at, amount, notes),
        )
        return cur.lastrowid


def count_unreported() -> int:
    with conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM house_calls WHERE reported_at IS NULL"
        ).fetchone()[0]


def unreported_total() -> float:
    with conn() as c:
        return c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM house_calls WHERE reported_at IS NULL"
        ).fetchone()[0]


def unreported_calls() -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT id, family_last_name, called_at, amount, notes FROM house_calls "
            "WHERE reported_at IS NULL ORDER BY called_at, id"
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
