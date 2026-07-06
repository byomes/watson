"""jobs/memory/propose.py — propose and resolve Watson memory updates."""
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests

from core.vacation import vacation_gate

REPO = Path(__file__).resolve().parents[2]
DB_PATH = os.getenv("WATSON_DB", str(REPO / "data" / "watson.db"))
CORE_MD = REPO / "memory" / "core.md"


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _bootstrap(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_proposals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TEXT NOT NULL,
            resolved_at TEXT
        )
    """)
    conn.commit()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def propose_core_update(proposed_change: str) -> int:
    """Send a Telegram proposal and store it in DB. Returns the proposal id."""
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

    msg = f"MEMORY UPDATE PROPOSAL\n\n{proposed_change}\n\nReply APPROVE or REJECT."
    if bot_token and chat_id and not vacation_gate("normal", "jobs.memory.propose", msg):
        try:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": msg},
                timeout=10,
            )
        except Exception:
            pass

    conn = _db()
    _bootstrap(conn)
    cur = conn.execute(
        "INSERT INTO memory_proposals (proposal, status, created_at) VALUES (?, 'pending', ?)",
        (proposed_change, _now()),
    )
    conn.commit()
    proposal_id = cur.lastrowid
    conn.close()
    return proposal_id


def resolve_proposal(proposal_id: int, approved: bool) -> None:
    """Approve or reject a proposal. If approved, appends to core.md and commits."""
    conn = _db()
    _bootstrap(conn)
    status = "approved" if approved else "rejected"
    conn.execute(
        "UPDATE memory_proposals SET status = ?, resolved_at = ? WHERE id = ?",
        (status, _now(), proposal_id),
    )
    conn.commit()

    if approved:
        row = conn.execute(
            "SELECT proposal FROM memory_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row:
            proposal_text = row["proposal"]
            with open(CORE_MD, "a", encoding="utf-8") as f:
                f.write(f"\n{proposal_text}\n")
            subprocess.run(
                ["git", "add", str(CORE_MD)],
                cwd=str(REPO),
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "memory: approved core update"],
                cwd=str(REPO),
                check=True,
            )

    conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 2:
        pid = propose_core_update(sys.argv[1])
        print(f"Proposal stored with id={pid}")
    elif len(sys.argv) == 4 and sys.argv[1] == "resolve":
        resolve_proposal(int(sys.argv[2]), sys.argv[3].lower() == "approve")
        print("Resolved.")
    else:
        print("Usage: propose.py '<change>'")
        print("       propose.py resolve <id> approve|reject")
