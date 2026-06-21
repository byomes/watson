"""jobs/writing_room/onboard.py — signup alerting, approval/denial, welcome email, Kit tag."""
import hashlib
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import bcrypt
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.writing_room import (
    bootstrap_db, generate_password, generate_username, get_db, send_email, send_telegram,
)

log = logging.getLogger(__name__)

_KIT_API_KEY    = lambda: os.getenv("KIT_API_KEY", "")
_KIT_API_SECRET = lambda: os.getenv("KIT_API_SECRET", "")
_KIT_TAG        = "writing-room-partner"

_ROOM_URL = "https://williamckyomes.com/room"


def alert_new_application(partner_id: int) -> None:
    """Send William the full application via Telegram with Approve/Deny buttons."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM writing_room_partners WHERE id = ?", (partner_id,)
        ).fetchone()
        if not row:
            return

        agreed = "✅ Agreed to participate actively" if row["agreed_to_participate"] else "❌ Did not agree"
        faith_section = f"\nTheir faith:\n\"{row['faith_description']}\"\n" if row["faith_description"] else ""
        text = (
            f"✍️ Writing Room Application\n\n"
            f"Name: {row['name']}\n"
            f"Email: {row['email']}\n\n"
            f"Why they want to join:\n\"{row['why_join']}\"\n"
            f"{faith_section}\n"
            f"{agreed}"
        )
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"room_approve:{partner_id}"},
                {"text": "🚫 Deny",   "callback_data": f"room_deny:{partner_id}"},
            ]]
        }
        send_telegram(text, reply_markup=keyboard)
    finally:
        conn.close()


def process_approval(partner_id: int) -> None:
    """Generate credentials, send welcome email, tag in Kit, mark welcome_sent."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM writing_room_partners WHERE id = ?", (partner_id,)
        ).fetchone()
        if not row or row["welcome_sent"]:
            return

        first_name = row["name"].split()[0]
        username   = generate_username(first_name)
        password   = generate_password()
        pw_hash    = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        joined_at  = datetime.utcnow().isoformat()

        conn.execute(
            "UPDATE writing_room_partners "
            "SET username = ?, password_hash = ?, status = 'active', joined_at = ?, welcome_sent = 1 "
            "WHERE id = ?",
            (username, pw_hash, joined_at, partner_id),
        )
        conn.commit()

        _send_welcome_email(row["email"], first_name, username, password)
        _kit_tag(row["email"], first_name)
        send_telegram(f"✅ {row['name']} is now a Writing Room Partner.")
    finally:
        conn.close()


def process_denial(partner_id: int) -> None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT name FROM writing_room_partners WHERE id = ?", (partner_id,)
        ).fetchone()
        conn.execute(
            "UPDATE writing_room_partners SET status = 'denied' WHERE id = ?", (partner_id,)
        )
        conn.commit()
        name = row["name"] if row else f"#{partner_id}"
        send_telegram(f"🚫 {name} denied.")
    finally:
        conn.close()


def _send_welcome_email(email: str, first_name: str, username: str, password: str) -> None:
    subject = "You're in — Welcome to the Writing Room"
    body = f"""Hi {first_name},

Dr. Bill approved your Writing Room access. Here's everything you need to get in:

  URL: {_ROOM_URL}
  Username: {username}
  Password: {password}

Log in and change your password once you're in.

A few things waiting for you:
- The community board — introduce yourself
- Beta chapters — your feedback shapes the book
- The prayer wall — we pray for each other here
- Write to Dr. Bill — direct line, no filters

Dr. Bill reads everything. This is a real community.

— Watson, on behalf of Dr. Bill Yomes"""
    send_email(email, subject, body)


def _kit_tag(email: str, first_name: str) -> None:
    api_secret = _KIT_API_SECRET()
    api_key    = _KIT_API_KEY()
    if not (api_secret and api_key):
        log.warning("Kit API credentials missing — skipping tag")
        return

    # Get or create tag
    resp = requests.get(
        "https://api.convertkit.com/v3/tags",
        params={"api_key": api_key},
        timeout=10,
    )
    tag_id = None
    if resp.ok:
        for t in resp.json().get("tags", []):
            if t["name"].lower() == _KIT_TAG.lower():
                tag_id = t["id"]
                break

    if not tag_id:
        resp = requests.post(
            "https://api.convertkit.com/v3/tags",
            json={"api_secret": api_secret, "tag": {"name": _KIT_TAG}},
            timeout=10,
        )
        if resp.ok:
            tag_id = resp.json().get("id")

    if not tag_id:
        log.error("Could not get/create Kit tag '%s'", _KIT_TAG)
        return

    requests.post(
        f"https://api.convertkit.com/v3/tags/{tag_id}/subscribe",
        json={"api_secret": api_secret, "first_name": first_name, "email": email},
        timeout=10,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bootstrap_db()
