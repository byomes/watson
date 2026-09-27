"""jobs/congregation/notify_donna_fluro_review.py -- sends every pending
'conflict' and 'possible_duplicate' row from the latest jobs/congregation/
fluro_pull.py staging pass to Donna via Telegram, one message per contact,
with buttons. Taps route to bot.py's handle_fluro_review_callback
(pattern ^flr_), which calls jobs/congregation/fluro_apply.py -- the only
code path that actually writes a Fluro-sourced change into congregation.db.
See fluro_pull.py's docstring for the full classification rules.

Not on a cron schedule (yet) -- run manually after fluro_pull.py, same
relationship notify_subsplash_fuzzy_review.py has to
import_subsplash_contacts.py:

  python3 -m jobs.congregation.fluro_pull
  python3 -m jobs.congregation.notify_donna_fluro_review
"""
import time

from jobs.congregation.fluro_staging_schema import get_connection
from jobs.telegram.send_to_person import send_buttons_to_person

_DONNA_PERSON_ID = 12  # see bot.py's own copy of this mapping


def _conflict_keyboard(fluro_id: str):
    return [
        [{"text": "✅ Use Fluro's values", "callback_data": f"flr_capply:{fluro_id}"}],
        [{"text": "\U0001F6AB Keep what's on file", "callback_data": f"flr_ckeep:{fluro_id}"}],
        [{"text": "⏭ Skip for now", "callback_data": f"flr_skip:{fluro_id}"}],
    ]


def _duplicate_keyboard(fluro_id: str):
    return [
        [{"text": "✅ Same person, merge", "callback_data": f"flr_dsame:{fluro_id}"}],
        [{"text": "\U0001F195 Different person, add new", "callback_data": f"flr_dnew:{fluro_id}"}],
        [{"text": "⏭ Skip for now", "callback_data": f"flr_skip:{fluro_id}"}],
    ]


def _conflict_text(row) -> str:
    import json

    conflicts = json.loads(row["conflict_fields"] or "{}")
    lines = [f"<b>{row['first_name']} {row['last_name']}</b> -- Fluro data disagrees with what's on file:"]
    for field, vals in conflicts.items():
        lines.append(f"  • {field}: on file “{vals['existing']}” vs Fluro “{vals['fluro']}”")
    return "\n".join(lines)


def _duplicate_text(row) -> str:
    return (
        f"<b>{row['first_name']} {row['last_name']}</b> from Fluro matched an existing member by name only "
        f"(no shared email/phone) -- matched_member_id {row['matched_member_id']}. Same person?"
    )


def run() -> int:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM fluro_contacts WHERE match_status IN ('conflict','possible_duplicate') "
        "AND review_status = 'pending' ORDER BY match_status, last_name"
    ).fetchall()
    conn.close()

    if not rows:
        print("No pending Fluro conflicts/possible-duplicates to send.")
        return 0

    sent = 0
    for row in rows:
        if row["match_status"] == "conflict":
            text, keyboard = _conflict_text(row), _conflict_keyboard(row["fluro_id"])
        else:
            text, keyboard = _duplicate_text(row), _duplicate_keyboard(row["fluro_id"])

        ok = send_buttons_to_person(_DONNA_PERSON_ID, text, keyboard)
        if ok:
            sent += 1
        time.sleep(0.5)

    print(f"Sent {sent}/{len(rows)} Fluro review messages to Donna.")
    return sent


if __name__ == "__main__":
    run()
