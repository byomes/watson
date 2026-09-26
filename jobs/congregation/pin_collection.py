"""Deacon App PIN self-service collection over Telegram team chat.

Used by jobs/congregation/collect_deacon_pins.py (the one-off trigger that
DMs each target person and registers a jobs.people.pending_reply "ask")
and by bot.py's compute_team_chat_reply (which resolves the reply once it
arrives, via _dispatch_pending_directed_reply -- see bot.py for the
fall-through-vs-reask wiring).

TARGETS is a hardcoded one-off list for these 6 people, not a general
person-identity-linking system: congregation.db (members.id, for
leadership_roles) and watson.db (people.id, for telegram_chat_id) are two
separate SQLite files with no automatic cross-DB join, so this is the
simplest correct thing for a handful of named people."""
import os
import sqlite3

from jobs.congregation.deacon_pin_auth import hash_pin, pin_in_use

CONGREGATION_DB = os.path.expanduser("~/watson/data/congregation.db")

TARGETS: dict[str, dict] = {
    "Melanie Yomes": {"member_id": 72, "person_id": 2},
    "Tyler McCauley": {"member_id": 76, "person_id": 470},
    "Donna Redman": {"member_id": 320, "person_id": 12},
    "Kaci Gravatt": {"member_id": 14, "person_id": 13},
    "Lucie Hale": {"member_id": 102, "person_id": 332},
    "Tara Mathena": {"member_id": 236, "person_id": 450},
}

# Melanie and Tyler already have a leadership_roles 'staff' row; these four
# don't yet, though Bill confirmed 2026-09-17 they are staff too.
NEEDS_STAFF_ROLE = {"Donna Redman", "Kaci Gravatt", "Lucie Hale", "Tara Mathena"}

PROMPT_TEMPLATE = (
    "Hi {first_name} — Bill asked me to have you pick your own 4-digit PIN for the "
    "Deacon App login. Reply with any 4 digits (e.g. 1234). If someone else already "
    "has that PIN I'll ask you to pick a different one."
)


def looks_like_attempt(text: str) -> bool:
    """Broad on purpose -- this only decides fall-through (let an unrelated
    message go to normal routing) vs. handle (treat it as a PIN attempt,
    even a malformed one worth re-asking about)."""
    candidate = (text or "").strip().replace(" ", "").replace("-", "")
    return bool(candidate) and candidate.isdigit() and len(candidate) <= 6


def handle_reply(asker_name: str, text: str, context: dict) -> str:
    from jobs.people import pending_reply

    candidate = (text or "").strip().replace(" ", "").replace("-", "")

    if not (candidate.isdigit() and len(candidate) == 4):
        return "That needs to be exactly 4 digits -- reply with 4 digits, like 1234."

    if pin_in_use(candidate, exclude_deacon_name=asker_name):
        return "That PIN's already taken by someone else -- reply with a different 4 digits."

    conn = sqlite3.connect(CONGREGATION_DB)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO deacon_pins (deacon_name, pin_hash) VALUES (?, ?)",
            (asker_name, hash_pin(candidate)),
        )
        conn.commit()
    finally:
        conn.close()

    pending_reply.clear(asker_name)
    _notify_bill(f"✅ {asker_name} set their Deacon App PIN.")
    return "Got it, your PIN is set. Thanks!"


def _notify_bill(text: str) -> None:
    from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    from core.vacation import vacation_gate
    import requests

    if vacation_gate("normal", "pin_collection._notify_bill", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception:
        pass
