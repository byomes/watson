"""jobs/congregation/shepcheck_login_lockout.py -- per-IP lockout for the
wtsn.me/cat/shepcheck PIN login (shepcheck_web.py's verify_pin route).

Same structure as catalystdb_login_lockout.py (kept as its own copy rather
than a shared import, matching this codebase's convention of not
cross-wiring per-app auth internals). MAX_FAILED_ATTEMPTS=3 -- this PIN
gates real pastoral data (prayer-request text and member contact info),
not a throwaway view. Storage is its own table (shepcheck_login_lockouts)
in congregation.db.

Unlocking is manual, via Telegram -- see bot.py's _handle_unlock_login for
the pattern used by the other lockout modules; wire this one in the same
way if it ever gets used enough to need it.
"""
from datetime import datetime, timezone

MAX_FAILED_ATTEMPTS = 3


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shepcheck_login_lockouts (
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
        "SELECT locked_at FROM shepcheck_login_lockouts WHERE client_ip = ?", (client_ip,)
    ).fetchone()
    return bool(row and row["locked_at"])


def record_success(conn, client_ip: str) -> None:
    _ensure_table(conn)
    conn.execute("DELETE FROM shepcheck_login_lockouts WHERE client_ip = ?", (client_ip,))
    conn.commit()


def record_failure(conn, client_ip: str) -> bool:
    """Same semantics as catalystdb_login_lockout.record_failure -- returns
    True only the moment this attempt first crosses MAX_FAILED_ATTEMPTS."""
    _ensure_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT failed_count, locked_at FROM shepcheck_login_lockouts WHERE client_ip = ?",
        (client_ip,),
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO shepcheck_login_lockouts (client_ip, failed_count, locked_at, last_attempt_at) "
            "VALUES (?, 1, NULL, ?)",
            (client_ip, now),
        )
        conn.commit()
        return False

    if row["locked_at"]:
        conn.execute(
            "UPDATE shepcheck_login_lockouts SET last_attempt_at = ? WHERE client_ip = ?",
            (now, client_ip),
        )
        conn.commit()
        return False

    count = row["failed_count"] + 1
    just_locked = count >= MAX_FAILED_ATTEMPTS
    conn.execute(
        "UPDATE shepcheck_login_lockouts SET failed_count = ?, locked_at = ?, last_attempt_at = ? "
        "WHERE client_ip = ?",
        (count, now if just_locked else None, now, client_ip),
    )
    conn.commit()
    return just_locked


def clear_all(conn) -> int:
    _ensure_table(conn)
    count = conn.execute("SELECT COUNT(*) AS n FROM shepcheck_login_lockouts").fetchone()["n"]
    conn.execute("DELETE FROM shepcheck_login_lockouts")
    conn.commit()
    return count
