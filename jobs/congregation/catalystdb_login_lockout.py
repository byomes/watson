"""jobs/congregation/catalystdb_login_lockout.py -- per-IP lockout for the
wtsn.me/cat/catalystdb PIN login (catalystdb_web.py's verify_pin route).

Same structure as deacon_login_lockout.py (kept as its own copy rather than
a shared import, matching this codebase's convention of not cross-wiring
per-app auth internals), but MAX_FAILED_ATTEMPTS=3 rather than 5 -- Bill's
2026-09-23 request -- since this PIN gates full read/write access to every
member field, not just the deacon app's roster view. Storage is its own
table (catalystdb_login_lockouts) in congregation.db.

Unlocking is manual, via Telegram -- see bot.py's _handle_unlock_login,
which clears both this and deacon_login_lockout on the same "unlock
login"/"unlock pin" phrase.
"""
from datetime import datetime, timezone

MAX_FAILED_ATTEMPTS = 3


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalystdb_login_lockouts (
            client_ip TEXT PRIMARY KEY,
            failed_count INTEGER NOT NULL DEFAULT 0,
            locked_at TEXT,
            last_attempt_at TEXT NOT NULL
        )
        """
    )


def is_locked(conn, client_ip: str) -> bool:
    _ensure_table(conn)
    row = conn.execute(
        "SELECT locked_at FROM catalystdb_login_lockouts WHERE client_ip = ?", (client_ip,)
    ).fetchone()
    return bool(row and row["locked_at"])


def record_success(conn, client_ip: str) -> None:
    _ensure_table(conn)
    conn.execute("DELETE FROM catalystdb_login_lockouts WHERE client_ip = ?", (client_ip,))
    conn.commit()


def record_failure(conn, client_ip: str) -> bool:
    """Same semantics as deacon_login_lockout.record_failure -- returns
    True only the moment this attempt first crosses MAX_FAILED_ATTEMPTS."""
    _ensure_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT failed_count, locked_at FROM catalystdb_login_lockouts WHERE client_ip = ?",
        (client_ip,),
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO catalystdb_login_lockouts (client_ip, failed_count, locked_at, last_attempt_at) "
            "VALUES (?, 1, NULL, ?)",
            (client_ip, now),
        )
        conn.commit()
        return False

    if row["locked_at"]:
        conn.execute(
            "UPDATE catalystdb_login_lockouts SET last_attempt_at = ? WHERE client_ip = ?",
            (now, client_ip),
        )
        conn.commit()
        return False

    count = row["failed_count"] + 1
    just_locked = count >= MAX_FAILED_ATTEMPTS
    conn.execute(
        "UPDATE catalystdb_login_lockouts SET failed_count = ?, locked_at = ?, last_attempt_at = ? "
        "WHERE client_ip = ?",
        (count, now if just_locked else None, now, client_ip),
    )
    conn.commit()
    return just_locked


def clear_all(conn) -> int:
    _ensure_table(conn)
    count = conn.execute("SELECT COUNT(*) AS n FROM catalystdb_login_lockouts").fetchone()["n"]
    conn.execute("DELETE FROM catalystdb_login_lockouts")
    conn.commit()
    return count
