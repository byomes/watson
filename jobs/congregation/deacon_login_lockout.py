"""jobs/congregation/deacon_login_lockout.py — per-IP lockout for the
wtsn.me/cat/deaconapp PIN login (deacons_web.py's verify_pin route).

Added 2026-09-16 after a security review found the 4-digit PIN had no
rate limit at all: 10,000 combinations, unthrottled, brute-forceable in
minutes against a login that guards the whole congregation's contact
info and pastoral notes. Locks by client IP (not globally, and not
per-deacon) since a PIN attempt doesn't identify which deacon is
logging in until it actually matches one — see verify_pin's docstring.
Storage is congregation.db (via the caller's _conn(), same DB as
deacon_pins/members) since that's where the PIN data itself lives.

Unlocking is manual, via Telegram: any onboarded leader can clear every
current lockout by asking Watson (see bot.py's
_looks_like_unlock_login_request / _handle_unlock_login), the same
"no allowlist, every onboarded leader" default Bill set for family
relationship marking. Clearing a lockout doesn't grant access by
itself — it only resets the failed-attempt counter, so a script still
guessing PINs locks right back out after MAX_FAILED_ATTEMPTS more tries.
"""
from datetime import datetime, timezone

MAX_FAILED_ATTEMPTS = 5


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deacon_login_lockouts (
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
        "SELECT locked_at FROM deacon_login_lockouts WHERE client_ip = ?", (client_ip,)
    ).fetchone()
    return bool(row and row["locked_at"])


def record_success(conn, client_ip: str) -> None:
    """A matched PIN clears that IP's failure history entirely -- the
    limit is 5 *consecutive* failures, not 5 ever."""
    _ensure_table(conn)
    conn.execute("DELETE FROM deacon_login_lockouts WHERE client_ip = ?", (client_ip,))
    conn.commit()


def record_failure(conn, client_ip: str) -> bool:
    """Increments client_ip's consecutive-failure count, locking it once
    it reaches MAX_FAILED_ATTEMPTS. Returns True only the moment it first
    crosses that threshold (so the caller can fire a one-time Telegram
    alert) -- every later attempt against an already-locked IP just
    updates last_attempt_at and returns False, so a script hammering a
    locked IP doesn't re-alert on every try."""
    _ensure_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT failed_count, locked_at FROM deacon_login_lockouts WHERE client_ip = ?",
        (client_ip,),
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO deacon_login_lockouts (client_ip, failed_count, locked_at, last_attempt_at) "
            "VALUES (?, 1, NULL, ?)",
            (client_ip, now),
        )
        conn.commit()
        return False

    if row["locked_at"]:
        conn.execute(
            "UPDATE deacon_login_lockouts SET last_attempt_at = ? WHERE client_ip = ?",
            (now, client_ip),
        )
        conn.commit()
        return False

    count = row["failed_count"] + 1
    just_locked = count >= MAX_FAILED_ATTEMPTS
    conn.execute(
        "UPDATE deacon_login_lockouts SET failed_count = ?, locked_at = ?, last_attempt_at = ? "
        "WHERE client_ip = ?",
        (count, now if just_locked else None, now, client_ip),
    )
    conn.commit()
    return just_locked


def clear_all(conn) -> int:
    """Clears every current lockout (and any in-progress failure count
    below the threshold) -- returns how many rows were removed. No
    per-IP picker in the Telegram flow: this app has a handful of users,
    clearing everything is the point of the "unlock" ask, and a still-
    guessing attacker just locks back out after MAX_FAILED_ATTEMPTS more
    tries, so over-clearing costs nothing."""
    _ensure_table(conn)
    count = conn.execute("SELECT COUNT(*) AS n FROM deacon_login_lockouts").fetchone()["n"]
    conn.execute("DELETE FROM deacon_login_lockouts")
    conn.commit()
    return count
