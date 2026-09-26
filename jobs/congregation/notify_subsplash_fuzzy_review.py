"""
One-off notifier: sends each pending duplicate_flags row created by
jobs/congregation/import_subsplash_contacts.py (reason='subsplash_import_fuzzy')
as its own Telegram message with the same merge/alias/separate/skip buttons
as the standing dupf_ review flow (bot/bot.py's handle_dup_flag_callback).
No new bot.py code needed -- taps route to the already-registered
CallbackQueryHandler(pattern=r"^dupf_(merge|alias|sep|skip):") since the
callback_data format matches exactly.

Usage:
  python3 jobs/congregation/notify_subsplash_fuzzy_review.py
"""
import os
import time

import requests
from dotenv import load_dotenv

from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID
from jobs.congregation.duplicate_review import DB_PATH, _conn, _member_summary

load_dotenv(os.path.expanduser("~/watson/.env"))

REASON = "subsplash_import_fuzzy"


def _keyboard(flag_id, keep, other):
    return {
        "inline_keyboard": [
            [{"text": f"✅ Merge, keep {keep['name']}", "callback_data": f"dupf_merge:{flag_id}:{keep['id']}:{other['id']}"}],
            [{"text": f"\U0001F517 Merge + save alias, keep {keep['name']}", "callback_data": f"dupf_alias:{flag_id}:{keep['id']}:{other['id']}"}],
            [{"text": f"\U0001F504 Merge, keep {other['name']} instead", "callback_data": f"dupf_merge:{flag_id}:{other['id']}:{keep['id']}"}],
            [
                {"text": "\U0001F645 Separate people", "callback_data": f"dupf_sep:{flag_id}"},
                {"text": "⏭ Skip for now", "callback_data": f"dupf_skip:{flag_id}"},
            ],
        ]
    }


def _line(m):
    extra = m["email"] or m["phone"] or ""
    return f"<b>{m['name']}</b>, id {m['id']}" + (f", {extra}" if extra else "")


def _send(text, keyboard):
    payload = {"chat_id": WATSON_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = keyboard
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        raise RuntimeError(f"Telegram sendMessage failed: {resp.status_code} {resp.text}") from None


def run():
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        raise SystemExit("WATSON_BOT_TOKEN and WATSON_CHAT_ID must be set.")

    with _conn() as conn:
        flags = conn.execute(
            "SELECT id, member_id_a, member_id_b FROM duplicate_flags "
            "WHERE reason = ? AND status = 'pending' ORDER BY id",
            (REASON,),
        ).fetchall()

    if not flags:
        print("No pending subsplash_import_fuzzy flags to send.")
        return

    _send(f"\U0001F50E {len(flags)} contacts from today's Subsplash import need a quick check, matched an existing person by name only, no shared email/phone. One at a time below.", None)
    time.sleep(1)

    for f in flags:
        with _conn() as conn:
            a = _member_summary(conn, f["member_id_a"])
            b = _member_summary(conn, f["member_id_b"])
        if a.get("deleted") or b.get("deleted"):
            continue
        # New CSV-sourced row is member_id_a; recommend keeping the pre-existing record (b).
        keep, other = b, a
        text = f"{_line(keep)}\n{_line(other)} (new, from today's import)\n\nSame person?"
        _send(text, _keyboard(f["id"], keep, other))
        time.sleep(0.5)

    print(f"Sent {len(flags)} review messages.")


if __name__ == "__main__":
    run()
