"""jobs/skills/logins.py — vault-aware login lookup for chat (dashboard + Telegram)."""
import sqlite3

DB = "/home/billyomes/watson/data/watson.db"


def handle(message: str) -> str:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT locked FROM vault_status WHERE id = 1").fetchone()
        if row and row["locked"]:
            return "Login vault is currently locked. Unlock via Telegram."
        challenge_row = conn.execute(
            "SELECT id, challenge FROM login_challenges ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
        if not challenge_row:
            return "No challenge questions configured for the login vault."
        return f"Challenge: {challenge_row['challenge']}"
    finally:
        conn.close()
