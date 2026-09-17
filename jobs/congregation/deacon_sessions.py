"""jobs/congregation/deacon_sessions.py — opaque per-deacon session
tokens, minted only as a direct side effect of a successful PIN check in
deacons_web.py's verify_pin().

Added 2026-09-17 to close a gap the PIN-lockout work surfaced: every
/api/cat/deacons/* route only ever checked the shared DEACONS_API_KEY,
which proves "this request came from our own Next.js app" (or from
whoever leaked that one static secret) — never "a real deacon actually
logged in." A leaked DEACONS_API_KEY alone was therefore enough for full
read/write access to the whole congregation roster, no PIN required.

Now every protected route also requires an opaque X-Deacon-Session token
(see deacons_web.py's _require_deacon_session). The only way to obtain
one is a successful PIN check — there's no separate "give me a token for
name X" route, so a leaked API key alone still can't produce one. Routes
that need to know WHO is acting (deacon_notes.author_deacon, family_edit's
sender) now take that from the resolved token instead of trusting a
client-supplied name in the request body, closing a second, smaller gap
where anyone with the API key could previously claim to be any deacon.

Storage is congregation.db (same DB as deacon_pins), a `deacon_sessions`
table of opaque token -> deacon_name + expiry. TTL matches
deaconAuth.ts's 30-day cookie (SESSION_TTL_MS) since this token IS the
real session now — the signed cookie on the Next.js side just carries it
around, it doesn't independently grant anything on its own anymore.
"""
import secrets
from datetime import datetime, timedelta, timezone

SESSION_TTL_DAYS = 30


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deacon_sessions (
            token TEXT PRIMARY KEY,
            deacon_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )


def create_token(conn, deacon_name: str) -> str:
    _ensure_table(conn)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    conn.execute(
        "INSERT INTO deacon_sessions (token, deacon_name, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, deacon_name, now.isoformat(), expires.isoformat()),
    )
    conn.commit()
    return token


def resolve(conn, token: str) -> str | None:
    """Returns the deacon_name for a live token, or None if missing/
    expired. Lazily deletes an expired row it happens to find -- no
    separate cron, this table is small and only reads ever notice
    staleness."""
    _ensure_table(conn)
    if not token:
        return None
    row = conn.execute(
        "SELECT deacon_name, expires_at FROM deacon_sessions WHERE token = ?", (token,)
    ).fetchone()
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        conn.execute("DELETE FROM deacon_sessions WHERE token = ?", (token,))
        conn.commit()
        return None
    return row["deacon_name"]


def invalidate(conn, token: str) -> None:
    """Called on logout so a signed-out cookie's token can't still be
    replayed directly against the API for the rest of its 30-day life."""
    _ensure_table(conn)
    conn.execute("DELETE FROM deacon_sessions WHERE token = ?", (token,))
    conn.commit()
