"""jobs/dev/command_executor.py — propose shell commands via Telegram; execute on approval."""
import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("WATSON_DB", str(REPO / "data" / "watson.db")))

_BLOCKED = [
    "rm -rf", "rm -r /", "dd if=", "mkfs", ":(){:|:&};:",
    "chmod 777 /", "chown root", "curl | bash", "wget | bash",
    "> /dev/sda", "shred", "fdisk", "parted",
    "cat /etc/shadow", "cat /etc/passwd",
    "git push --force", "git reset --hard origin",
    "DROP TABLE", "DELETE FROM", "TRUNCATE",
    "SECRETS.md", ".env", "watson.key",
]


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS command_proposals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            command     TEXT NOT NULL,
            reason      TEXT DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'pending',
            output      TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            executed_at TEXT
        )
    """)
    conn.commit()
    return conn


def _is_blocked(command: str) -> bool:
    return any(pattern in command for pattern in _BLOCKED)


def _telegram(text: str, reply_markup: dict = None) -> None:
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        return
    payload = {"chat_id": chat_id, "text": text[:4096], "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json=payload,
            timeout=15,
        )
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)


def propose_command(description: str, command: str, reason: str = "") -> int:
    if _is_blocked(command):
        _telegram("❌ Blocked: that command contains a restricted pattern.")
        return -1

    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO command_proposals (description, command, reason) VALUES (?, ?, ?)",
            (description, command, reason or ""),
        )
        proposal_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    reason_text = reason or "Requested by Bill"
    _telegram(
        f"⚙️ Command Proposal\n\n{description}\n\nCommand:\n`{command}`\n\nReason: {reason_text}\n\nApprove to execute on Beelink?",
        reply_markup={
            "inline_keyboard": [[
                {"text": "✅ Run", "callback_data": f"cmd_approve_{proposal_id}"},
                {"text": "❌ Cancel", "callback_data": f"cmd_reject_{proposal_id}"},
            ]]
        },
    )
    log.info("Command proposal #%d: %s", proposal_id, description)
    return proposal_id


def execute_command(proposal_id: int) -> dict:
    import subprocess

    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM command_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"success": False, "output": "", "return_code": -1}

    command = row["command"]

    if _is_blocked(command):
        _telegram(f"🚫 Execution blocked: `{command[:200]}` contains a restricted pattern.")
        conn = _get_connection()
        conn.execute("UPDATE command_proposals SET status='blocked' WHERE id=?", (proposal_id,))
        conn.commit()
        conn.close()
        return {"success": False, "output": "blocked", "return_code": -1}

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(Path.home() / "watson"),
        )
        combined_output = (result.stdout + result.stderr).strip()
        success = result.returncode == 0
        executed_at = datetime.utcnow().isoformat()

        conn = _get_connection()
        conn.execute(
            "UPDATE command_proposals SET status='completed', output=?, executed_at=? WHERE id=?",
            (combined_output[:4000], executed_at, proposal_id),
        )
        conn.commit()
        conn.close()

        prefix = "✓ Command executed" if success else "⚠️ Completed with errors"
        _telegram(f"{prefix}\n\n`{command}`\n\nOutput:\n{combined_output[:2000]}")

        return {"success": success, "output": combined_output, "return_code": result.returncode}

    except subprocess.TimeoutExpired:
        conn = _get_connection()
        conn.execute(
            "UPDATE command_proposals SET status='timeout' WHERE id=?", (proposal_id,)
        )
        conn.commit()
        conn.close()
        _telegram(f"⏱ Command timed out after 60s:\n`{command}`")
        return {"success": False, "output": "timeout", "return_code": -1}

    except Exception as exc:
        log.error("execute_command #%d failed: %s", proposal_id, exc)
        return {"success": False, "output": str(exc), "return_code": -1}


# --- Pre-built proposals ---------------------------------------------------

def restart_dashboard() -> int:
    return propose_command(
        "Restart Watson dashboard",
        "sudo systemctl restart watson-dashboard.service",
        "Reload dashboard after code changes",
    )


def restart_bot() -> int:
    return propose_command(
        "Restart Telegram bot",
        "sudo systemctl restart watson-bot.service",
        "Reload bot after code changes",
    )


def restart_all() -> int:
    return propose_command(
        "Restart all Watson services",
        "sudo systemctl restart watson-dashboard.service && sudo systemctl restart watson-bot.service && sudo systemctl restart watson-people.service",
        "Full Watson restart",
    )


def git_pull() -> int:
    return propose_command(
        "Pull latest Watson code",
        "cd ~/watson && git pull origin main",
        "Sync Beelink with latest GitHub commits",
    )


def run_sync() -> int:
    return propose_command(
        "Run memory sync",
        "PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 -m jobs.memory.sync",
        "Sync memory files to DB",
    )


def check_services() -> int:
    return propose_command(
        "Check service status",
        "systemctl status watson-dashboard watson-bot watson-people --no-pager",
        "Check all Watson services are running",
    )


def disk_usage() -> int:
    return propose_command(
        "Check disk usage",
        "df -h ~ && du -sh ~/watson/",
        "Check available disk space",
    )


def update_packages() -> int:
    return propose_command(
        "Update Watson Python packages",
        "cd ~/watson && source venv/bin/activate && pip install -r requirements.txt --upgrade --break-system-packages",
        "Update all Watson dependencies",
    )


# --- run() entry point ------------------------------------------------------

def run(message: str = None) -> str:
    if not message:
        return "Command executor ready. Tell me what you need done and I'll propose the command for your approval."

    msg = message.lower()

    if any(k in msg for k in ("restart all", "restart watson", "full restart")):
        proposal_id = restart_all()
    elif "restart dashboard" in msg:
        proposal_id = restart_dashboard()
    elif "restart bot" in msg:
        proposal_id = restart_bot()
    elif any(k in msg for k in ("git pull", "pull latest", "pull code", "pull origin")):
        proposal_id = git_pull()
    elif any(k in msg for k in ("run sync", "memory sync")):
        proposal_id = run_sync()
    elif any(k in msg for k in ("check services", "services running", "service status", "are services")):
        proposal_id = check_services()
    elif any(k in msg for k in ("disk space", "disk usage")):
        proposal_id = disk_usage()
    elif any(k in msg for k in ("update packages", "upgrade packages", "update dependencies")):
        proposal_id = update_packages()
    else:
        return "Command executor ready. Tell me what you need done and I'll propose the command for your approval."

    if proposal_id == -1:
        return "Command blocked — contains a restricted pattern."
    return f"Proposed command #{proposal_id}. Check Telegram to approve or cancel."
