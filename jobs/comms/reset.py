"""jobs/comms/reset.py — self-serve password reset for Comms Desk users.

Same token pattern as jobs/writing_room/reset.py, with one difference: the
new password is generated server-side (not user-chosen) and returned once for
display, matching the Comms Desk spec's "new 3-word password generated and
displayed once" flow.
"""
import logging
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.comms import generate_password, get_db
from jobs.email_job.brevo_send import send_email

log = logging.getLogger(__name__)

_RESET_URL   = "https://comms-desk.vercel.app/reset"
_TOKEN_TTL_H = 1


def request_reset(username: str) -> bool:
    """Generate a reset token and email the user. Returns False if username unknown."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, email, display_name FROM comms_users WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            return False  # silent — no enumeration

        token      = secrets.token_urlsafe(32)
        expires_at = (datetime.utcnow() + timedelta(hours=_TOKEN_TTL_H)).isoformat()
        conn.execute(
            "INSERT INTO comms_reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
            (row["id"], token, expires_at),
        )
        conn.commit()

        first_name = row["display_name"].split()[0]
        _send_reset_email(row["email"], first_name, token)
        return True
    finally:
        conn.close()


def validate_token(token: str) -> int | None:
    """Return user_id if token is valid, unexpired, and unused, else None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM comms_reset_tokens WHERE token = ? AND used_at IS NULL",
            (token,),
        ).fetchone()
        if not row:
            return None
        if datetime.utcnow() > datetime.fromisoformat(row["expires_at"]):
            return None
        return row["user_id"]
    finally:
        conn.close()


def confirm_reset(token: str) -> str | None:
    """Generate a new password, store its hash, consume the token.
    Returns the new plaintext password (shown once) or None if token invalid."""
    conn = get_db()
    try:
        user_id = validate_token(token)
        if not user_id:
            return None

        new_password = generate_password()
        pw_hash = generate_password_hash(new_password)
        conn.execute(
            "UPDATE comms_users SET password_hash = ? WHERE id = ?", (pw_hash, user_id)
        )
        conn.execute(
            "UPDATE comms_reset_tokens SET used_at = datetime('now') WHERE token = ?", (token,)
        )
        conn.commit()
        return new_password
    finally:
        conn.close()


def _send_reset_email(email: str, first_name: str, token: str) -> None:
    link    = f"{_RESET_URL}?token={token}"
    subject = "Reset your Comms Desk password"
    body    = (
        f"Hi {first_name},\n\n"
        f"Click here to reset your Comms Desk password:\n{link}\n\n"
        f"This link expires in {_TOKEN_TTL_H} hour.\n\n"
        f"If you didn't request this, ignore this email."
    )
    result = send_email(to_email=email, to_name="", subject=subject, text_body=body)
    if not result["success"]:
        raise RuntimeError(f"Brevo send failed: {result['error']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from jobs.comms import bootstrap_db
    bootstrap_db()
