import json

from core.database import get_connection


def _bootstrap():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id            INTEGER NOT NULL,
                action_type        TEXT NOT NULL,
                params_json        TEXT NOT NULL,
                proposed_slot_json TEXT,
                created_at         TEXT NOT NULL DEFAULT (datetime('now')),
                status             TEXT NOT NULL DEFAULT 'pending'
            )
        """)


_bootstrap()


def save_pending(chat_id: int, action_type: str, params: dict, proposed_slot: dict) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO pending_actions (chat_id, action_type, params_json, proposed_slot_json)
               VALUES (?, ?, ?, ?)""",
            (chat_id, action_type, json.dumps(params), json.dumps(proposed_slot)),
        )
        return cursor.lastrowid


def get_pending(chat_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, action_type, params_json, proposed_slot_json
               FROM pending_actions
               WHERE chat_id = ? AND status = 'pending'
               ORDER BY id DESC LIMIT 1""",
            (chat_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "action_type": row["action_type"],
        "params": json.loads(row["params_json"]),
        "proposed_slot": json.loads(row["proposed_slot_json"] or "{}"),
    }


def confirm_pending(pending_id: int) -> dict | None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE pending_actions SET status = 'confirmed' WHERE id = ?",
            (pending_id,),
        )
        row = conn.execute(
            "SELECT id, action_type, params_json, proposed_slot_json FROM pending_actions WHERE id = ?",
            (pending_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "action_type": row["action_type"],
        "params": json.loads(row["params_json"]),
        "proposed_slot": json.loads(row["proposed_slot_json"] or "{}"),
    }


def cancel_pending(pending_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE pending_actions SET status = 'cancelled' WHERE id = ?",
            (pending_id,),
        )
