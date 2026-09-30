"""jobs/congregation/kids_checkin_apply.py -- the only code path that turns
a kids_household_review_queue row into an actual kids.household_id write.
Called from bot.py's handle_kids_checkin_review_callback (kcr_* taps),
never automatically -- Donna's tap is the human decision every time, same
shape as fluro_apply.py's relationship to notify_donna_fluro_review.py.
"""
import sqlite3
from pathlib import Path

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"


class KidsCheckinApplyError(RuntimeError):
    pass


def _connect():
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _queue_row(conn, queue_id: int):
    row = conn.execute(
        "SELECT q.*, k.first_name, k.last_name FROM kids_household_review_queue q "
        "JOIN kids k ON k.id = q.kid_id WHERE q.id = ?",
        (queue_id,),
    ).fetchone()
    if not row:
        raise KidsCheckinApplyError(f"kids_household_review_queue id {queue_id} not found")
    return row


def approve_household_link(queue_id: int) -> str:
    conn = _connect()
    try:
        row = _queue_row(conn, queue_id)
        if not row["candidate_household_id"]:
            raise KidsCheckinApplyError("no candidate household on this row -- can't approve")
        conn.execute(
            "UPDATE kids SET household_id = ?, updated_at = datetime('now') WHERE id = ?",
            (row["candidate_household_id"], row["kid_id"]),
        )
        kid = conn.execute("SELECT member_id FROM kids WHERE id = ?", (row["kid_id"],)).fetchone()
        if kid["member_id"]:
            conn.execute(
                "UPDATE members SET household_id = ?, updated_at = datetime('now') WHERE id = ?",
                (row["candidate_household_id"], kid["member_id"]),
            )
        conn.execute("UPDATE kids_household_review_queue SET status = 'resolved' WHERE id = ?", (queue_id,))
        conn.commit()
        kid_name = f"{row['first_name']} {row['last_name'] or ''}".strip()
        return f"Linked {kid_name} to household {row['candidate_household_id']}."
    finally:
        conn.close()


def reject_household_link(queue_id: int) -> str:
    conn = _connect()
    try:
        row = _queue_row(conn, queue_id)
        conn.execute("UPDATE kids_household_review_queue SET status = 'resolved' WHERE id = ?", (queue_id,))
        conn.commit()
        kid_name = f"{row['first_name']} {row['last_name'] or ''}".strip()
        return f"Noted -- {kid_name} not linked to that family."
    finally:
        conn.close()


def skip_for_now(queue_id: int) -> str:
    conn = _connect()
    try:
        row = _queue_row(conn, queue_id)
        conn.execute("UPDATE kids_household_review_queue SET status = 'pending' WHERE id = ?", (queue_id,))
        conn.commit()
        kid_name = f"{row['first_name']} {row['last_name'] or ''}".strip()
        return f"Skipped {kid_name} for now -- will ask again."
    finally:
        conn.close()
