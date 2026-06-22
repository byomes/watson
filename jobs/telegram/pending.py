"""Reply-threading: track pending actions keyed by the Telegram message ID Watson sent."""
import json
import logging

from core.database import get_connection

log = logging.getLogger(__name__)


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tg_pending_actions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                type                TEXT NOT NULL,
                telegram_message_id INTEGER NOT NULL,
                payload             TEXT NOT NULL DEFAULT '{}',
                created_at          TEXT NOT NULL DEFAULT (datetime('now')),
                status              TEXT NOT NULL DEFAULT 'pending'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tg_pending_msgid
            ON tg_pending_actions(telegram_message_id, status)
        """)


_bootstrap()


def store_pending_action(action_type: str, telegram_message_id: int, payload: dict | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO tg_pending_actions (type, telegram_message_id, payload)
               VALUES (?, ?, ?)""",
            (action_type, telegram_message_id, json.dumps(payload or {})),
        )
        return cur.lastrowid


def get_pending_by_message_id(telegram_message_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, type, telegram_message_id, payload
               FROM tg_pending_actions
               WHERE telegram_message_id = ? AND status = 'pending'
               ORDER BY id DESC LIMIT 1""",
            (telegram_message_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "type": row["type"],
        "telegram_message_id": row["telegram_message_id"],
        "payload": json.loads(row["payload"]),
    }


def mark_done(pending_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE tg_pending_actions SET status='done' WHERE id=?",
            (pending_id,),
        )


def mark_cancelled(pending_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE tg_pending_actions SET status='cancelled' WHERE id=?",
            (pending_id,),
        )


def store_skill_confirmation(action_type: str, payload: dict) -> int:
    """Store a skill awaiting user confirmation (not keyed by reply message ID)."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO tg_pending_actions (type, telegram_message_id, payload, status)
               VALUES (?, 0, ?, 'awaiting_confirmation')""",
            (action_type, json.dumps(payload or {})),
        )
        return cur.lastrowid


def get_pending_confirmation() -> dict | None:
    """Return the most recent awaiting_confirmation action, or None."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, type, payload FROM tg_pending_actions
               WHERE status = 'awaiting_confirmation'
               ORDER BY id DESC LIMIT 1""",
        ).fetchone()
    if not row:
        return None
    return {"id": row["id"], "type": row["type"], "payload": json.loads(row["payload"])}


def mark_pending_status(pending_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE tg_pending_actions SET status=? WHERE id=?",
            (status, pending_id),
        )
