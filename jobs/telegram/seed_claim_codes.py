"""seed_claim_codes.py -- one-time leader onboarding: generate Telegram
claim codes and deep links for the given names.

Usage:
  python3 jobs/telegram/seed_claim_codes.py "Donna Redman" "Jim Bouchat" "Bill Crook"
  python3 jobs/telegram/seed_claim_codes.py "Some Other Leader"

Looks up each name in people (exact match, case-insensitive), creating the
row if it doesn't exist yet, generates an unguessable claim code via the
`secrets` module (this code is a bearer token binding a real chat_id to a
person's identity, not just a unique id), and prints a t.me deep link for
Bill to hand-deliver by whatever channel is easiest (text, email, in
person). Not a scheduled job -- run manually, once per new leader.

Requires jobs/people/migrate_telegram_claim_code.py to have been run first.
"""
import os
import secrets
import sqlite3
import string
import sys

DB_PATH = os.path.expanduser("~/watson/data/watson.db")

_ALPHABET = string.ascii_lowercase + string.digits  # base36, subset of
# Telegram's allowed /start payload alphabet [A-Za-z0-9_-]
_CODE_LENGTH = 8
_BOT_USERNAME = "wckyWatsonbot"


def _generate_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


def seed_claim_code(name: str) -> str:
    """Look up/create the person, assign a fresh claim code, return the deep link."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        person_id = row["id"] if row else None
        if person_id is None:
            cur = conn.execute("INSERT INTO people (name) VALUES (?)", (name,))
            person_id = cur.lastrowid

        while True:
            code = _generate_code()
            collision = conn.execute(
                "SELECT 1 FROM people WHERE telegram_claim_code = ?", (code,)
            ).fetchone()
            if not collision:
                break

        conn.execute(
            "UPDATE people SET telegram_claim_code = ? WHERE id = ?",
            (code, person_id),
        )
        conn.commit()
    finally:
        conn.close()

    return f"https://t.me/{_BOT_USERNAME}?start={code}"


if __name__ == "__main__":
    names = sys.argv[1:] or ["Donna Redman", "Jim Bouchat", "Bill Crook"]
    for n in names:
        link = seed_claim_code(n)
        print(f"{n}: {link}")
