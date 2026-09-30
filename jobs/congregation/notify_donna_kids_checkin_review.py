"""jobs/congregation/notify_donna_kids_checkin_review.py -- sends every
pending row in kids_household_review_queue to Donna via Telegram.

Per Bill's 2026-09-29 directive, a row is never sent the same day it was
queued -- only "tomorrow" (date(created_at) < date('now')), and only
through donna_notify.py's existing 9am-8pm window (which also enforces
[[feedback_no_messages_after_8pm_standing]] -- there is no after-8pm
override for anyone but Bill). Meant to run on a daily 9am+ cron tick, not
right after kids_checkin_import.py.

A row WITH a candidate household (matched via the checking-in guardian's
phone/email against `members`) gets an approve/reject Telegram button --
taps route to bot.py's handle_kids_checkin_review_callback (pattern
^kcr_), which is the only code path that writes kids.household_id. A row
with NO candidate (guardian phone/email matched nobody) is sent as a
plain informational message -- there's no household to confirm, so Donna
handles those through her normal process; it's marked 'sent' immediately
since there's no button tap to wait on.

Usage (cron, not manual -- see memory/CRON.md):
  python3 -m jobs.congregation.notify_donna_kids_checkin_review
"""
import sqlite3
import time
from pathlib import Path

from jobs.telegram.donna_notify import send_buttons_to_donna, send_to_donna

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"


def _connect():
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _candidate_keyboard(queue_id: int):
    return [
        [{"text": "✅ Yes, link them", "callback_data": f"kcr_approve:{queue_id}"}],
        [{"text": "\U0001F6AB Not that family", "callback_data": f"kcr_reject:{queue_id}"}],
        [{"text": "⏭ Skip for now", "callback_data": f"kcr_skip:{queue_id}"}],
    ]


def _household_display(conn, household_id: str) -> str:
    row = conn.execute(
        "SELECT name FROM members WHERE household_id = ? AND household_role IN ('head', 'spouse') "
        "ORDER BY household_role LIMIT 1",
        (household_id,),
    ).fetchone()
    if row:
        return row["name"]
    row = conn.execute("SELECT name FROM members WHERE household_id = ? LIMIT 1", (household_id,)).fetchone()
    return row["name"] if row else household_id


def run() -> dict:
    conn = _connect()
    stats = {"candidate_sent": 0, "no_match_sent": 0}
    try:
        rows = conn.execute(
            "SELECT q.id AS queue_id, q.candidate_household_id, q.match_reason, "
            "       k.first_name, k.last_name "
            "FROM kids_household_review_queue q JOIN kids k ON k.id = q.kid_id "
            "WHERE q.status = 'pending' AND date(q.created_at) < date('now') "
            "ORDER BY q.created_at"
        ).fetchall()

        for row in rows:
            kid_name = f"{row['first_name']} {row['last_name'] or ''}".strip()

            if row["candidate_household_id"]:
                family_name = _household_display(conn, row["candidate_household_id"])
                reason = "parent's phone number" if row["match_reason"] == "guardian_phone" else "parent's email"
                text = (
                    f"Kids Checkin: <b>{kid_name}</b> checked in and Watson matched the {reason} "
                    f"to the <b>{family_name}</b> family. Add {kid_name} to that household?"
                )
                ok = send_buttons_to_donna(text, _candidate_keyboard(row["queue_id"]))
                if ok:
                    # 'sent', not 'resolved' -- only a kcr_approve/reject tap in
                    # bot.py resolves it. Marking it here (rather than leaving
                    # it 'pending') keeps this daily cron from re-sending the
                    # same approve/reject message every day until she taps.
                    conn.execute(
                        "UPDATE kids_household_review_queue SET status = 'sent', sent_at = datetime('now') WHERE id = ?",
                        (row["queue_id"],),
                    )
                    stats["candidate_sent"] += 1
            else:
                text = (
                    f"Kids Checkin: <b>{kid_name}</b> checked in but Watson couldn't match a family "
                    f"(no phone/email match on file). Needs manual lookup."
                )
                ok = send_to_donna(text)
                if ok:
                    conn.execute(
                        "UPDATE kids_household_review_queue SET status = 'sent', sent_at = datetime('now') WHERE id = ?",
                        (row["queue_id"],),
                    )
                    stats["no_match_sent"] += 1

            time.sleep(0.5)

        conn.commit()
    finally:
        conn.close()

    return stats


if __name__ == "__main__":
    result = run()
    print(f"Sent {result['candidate_sent']} candidate-match + {result['no_match_sent']} no-match Kids Checkin reviews to Donna.")
