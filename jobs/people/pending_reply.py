"""Durable per-asker memory of a directed question Watson is waiting on a
specific reply for -- e.g. "reply with your 4-digit PIN"
(jobs/congregation/pin_collection.py).

Deliberately NOT in-memory like jobs/people/pending_lookup.py or
data_chat.py's _pending_clarifications: those two only work because the
question is asked and the reply is resolved inside the same inbound-
Telegram-message call, in the same process. This module exists for the
opposite case -- the question can be asked from a one-off script running
as a separate process from watson-bot.service (e.g. a batch of Telegram
DMs kicked off from a shell), and the reply only ever surfaces later,
inside the bot process. An in-memory dict populated by that script would
be invisible to the bot and the whole ask would silently go nowhere.

Keyed by asker_name, same identity space compute_team_chat_reply already
uses everywhere else in bot.py. TTL default is long (48h) compared to the
5-minute/300s live-clarification mechanisms above, because this is an
outbound ask nobody replies to instantly, not a resume of the current
back-and-forth."""
import json
import time

from core.database import get_connection

_DEFAULT_TTL_SECONDS = 60 * 60 * 48
_MAX_ENTRIES = 50


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_directed_replies (
                asker_name   TEXT PRIMARY KEY,
                kind         TEXT NOT NULL,
                context_json TEXT NOT NULL DEFAULT '{}',
                expires_at   REAL NOT NULL
            )
            """
        )


_bootstrap()


def ask(asker: str, kind: str, context: dict | None = None, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
    now = time.time()
    with get_connection() as conn:
        conn.execute("DELETE FROM pending_directed_replies WHERE expires_at <= ?", (now,))
        conn.execute(
            """
            INSERT OR REPLACE INTO pending_directed_replies (asker_name, kind, context_json, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (asker, kind, json.dumps(context or {}), now + ttl_seconds),
        )
        count = conn.execute("SELECT COUNT(*) FROM pending_directed_replies").fetchone()[0]
        if count > _MAX_ENTRIES:
            conn.execute(
                """
                DELETE FROM pending_directed_replies WHERE asker_name = (
                    SELECT asker_name FROM pending_directed_replies ORDER BY expires_at ASC LIMIT 1
                )
                """
            )


def peek(asker: str) -> dict | None:
    """Returns {"kind": str, "context": dict} for asker's pending directed
    reply, or None if there isn't one or it's expired (an expired row is
    deleted on read, same as pending_lookup.pop_pending's TTL sweep)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT kind, context_json, expires_at FROM pending_directed_replies WHERE asker_name = ?",
            (asker,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] <= time.time():
            conn.execute("DELETE FROM pending_directed_replies WHERE asker_name = ?", (asker,))
            return None
        return {"kind": row["kind"], "context": json.loads(row["context_json"])}


def clear(asker: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM pending_directed_replies WHERE asker_name = ?", (asker,))
