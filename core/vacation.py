"""Vacation Mode: suppress non-critical Telegram sends without touching job logic.

Manual on/off toggle only — no dates, no auto-resume. When on, jobs keep
running exactly as scheduled; only Telegram notifications tagged "normal"
are suppressed. Anything tagged "system_failure" always sends.
"""
import logging

from core.database import get_connection

log = logging.getLogger(__name__)


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vacation_suppressed_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                source     TEXT NOT NULL,
                message    TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES ('vacation_mode', 'off')"
        )


_bootstrap()


def is_vacation_mode() -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = 'vacation_mode'"
        ).fetchone()
    return bool(row) and row["value"] == "on"


def set_vacation_mode(on: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO system_settings (key, value, updated_at)
               VALUES ('vacation_mode', ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            ("on" if on else "off",),
        )


def vacation_gate(priority: str = "normal", source: str = "", message: str = "") -> bool:
    """Returns True if this Telegram send should be suppressed.

    priority "system_failure" always returns False (never suppressed).
    priority "normal" is suppressed while vacation mode is on, and logged
    to vacation_suppressed_log so nothing is silently lost.
    """
    if priority != "normal":
        return False
    if not is_vacation_mode():
        return False
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO vacation_suppressed_log (source, message) VALUES (?, ?)",
            (source or "unknown", (message or "")[:500]),
        )
    log.info("vacation mode: suppressed telegram send from %s", source or "unknown")
    return True
