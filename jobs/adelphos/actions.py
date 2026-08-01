"""jobs/adelphos/actions.py — resolves a pending New Account Security Monitor
alert (Delete/Allow button tap handled in bot.py) against watson.db + Moodle.

Delete is a true deletion (core_user_delete_users), not a suspend — per Bill's
2026-08-01 decision. Because deletion is irreversible, the first Delete tap
only moves the row to 'delete_confirm_pending'; the actual Moodle call fires
on the second (Confirm) tap. Cancel reverts the row to 'pending'.
"""
from datetime import datetime, timezone

from core.database import get_connection
from jobs.adelphos import client


def _get_account(conn, moodle_user_id: int):
    return conn.execute(
        "SELECT * FROM adelphos_new_accounts WHERE moodle_user_id = ?", (moodle_user_id,)
    ).fetchone()


def mark_delete_pending(moodle_user_id: int) -> dict | None:
    """First Delete tap: no Moodle call yet, just flags the row as awaiting confirm."""
    with get_connection() as conn:
        account = _get_account(conn, moodle_user_id)
        if not account:
            return None
        conn.execute(
            "UPDATE adelphos_new_accounts SET status='delete_confirm_pending' WHERE moodle_user_id=?",
            (moodle_user_id,),
        )
        return dict(account)


def cancel_delete(moodle_user_id: int) -> dict | None:
    """Cancel tap: reverts the row to 'pending' so it re-alerts normally."""
    with get_connection() as conn:
        account = _get_account(conn, moodle_user_id)
        if not account:
            return None
        conn.execute(
            "UPDATE adelphos_new_accounts SET status='pending' WHERE moodle_user_id=?",
            (moodle_user_id,),
        )
        return dict(account)


def resolve_delete(moodle_user_id: int) -> dict | None:
    """Confirm tap: hard-deletes the Moodle account and marks the row resolved."""
    client.call("core_user_delete_users", userids=[moodle_user_id])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        account = _get_account(conn, moodle_user_id)
        if not account:
            return None
        conn.execute(
            "UPDATE adelphos_new_accounts SET status='deleted', resolved_at=? WHERE moodle_user_id=?",
            (now, moodle_user_id),
        )
        return dict(account)


def resolve_allow(moodle_user_id: int) -> dict | None:
    """No Moodle call — just marks the account resolved so it won't re-alert."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        account = _get_account(conn, moodle_user_id)
        if not account:
            return None
        conn.execute(
            "UPDATE adelphos_new_accounts SET status='allowed', resolved_at=? WHERE moodle_user_id=?",
            (now, moodle_user_id),
        )
        return dict(account)
