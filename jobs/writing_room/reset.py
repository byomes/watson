"""jobs/writing_room/reset.py — password reset token generation and email."""
import logging
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.writing_room import bootstrap_db, get_db, send_email

log = logging.getLogger(__name__)

_RESET_URL   = "https://williamckyomes.com/room/reset"
_TOKEN_TTL_H = 1


def request_reset(email: str) -> bool:
    """Generate a reset token and email the partner. Returns False if email unknown."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, name FROM writing_room_partners WHERE email = ? AND status = 'active'",
            (email,),
        ).fetchone()
        if not row:
            return False  # silent — no enumeration

        token      = secrets.token_urlsafe(32)
        expires_at = (datetime.utcnow() + timedelta(hours=_TOKEN_TTL_H)).isoformat()
        conn.execute(
            "INSERT INTO writing_room_reset_tokens (partner_id, token, expires_at) VALUES (?, ?, ?)",
            (row["id"], token, expires_at),
        )
        conn.commit()

        first_name = row["name"].split()[0]
        _send_reset_email(email, first_name, token)
        return True
    finally:
        conn.close()


def validate_token(token: str) -> int | None:
    """Return partner_id if token is valid and unexpired, else None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM writing_room_reset_tokens WHERE token = ? AND used = 0",
            (token,),
        ).fetchone()
        if not row:
            return None
        if datetime.utcnow() > datetime.fromisoformat(row["expires_at"]):
            return None
        return row["partner_id"]
    finally:
        conn.close()


def confirm_reset(token: str, new_password: str) -> bool:
    """Hash and store new password, consume token. Returns True on success."""
    conn = get_db()
    try:
        partner_id = validate_token(token)
        if not partner_id:
            return False

        pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "UPDATE writing_room_partners SET password_hash = ? WHERE id = ?",
            (pw_hash, partner_id),
        )
        conn.execute(
            "UPDATE writing_room_reset_tokens SET used = 1 WHERE token = ?", (token,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _send_reset_email(email: str, first_name: str, token: str) -> None:
    link    = f"{_RESET_URL}?token={token}"
    subject = "Reset your Writing Room password"
    body    = (
        f"Hi {first_name},\n\n"
        f"Click here to reset your password:\n{link}\n\n"
        f"This link expires in {_TOKEN_TTL_H} hour.\n\n"
        f"If you didn't request this, ignore this email.\n\n"
        f"— Watson"
    )
    send_email(email, subject, body)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bootstrap_db()
