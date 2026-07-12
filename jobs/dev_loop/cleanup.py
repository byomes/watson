"""
cleanup.py — Remove stale dev loop projects.

Retention policy:
  - delivered, failed, stopped: delete after 48 hours
  - paused: delete after 7 days
  - running: never deleted automatically

  'running' is excluded from auto-purge because Watson can't tell a
  crashed loop from a legitimately long-running one apart from that
  status alone — only Bill can make that call. flag_stuck_running()
  below is the manual-review safety net for that gap: it surfaces
  'running' rows stalled past STUCK_RUNNING_HOURS via a single Telegram
  digest, but never changes status, deletes rows, or touches staging
  directories.

Cron (Monday 4am):
    PYTHONPATH=/home/billyomes/watson 0 4 * * 1 /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/dev_loop/cleanup.py >> /home/billyomes/watson/logs/devloop_cleanup.log 2>&1
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate

load_dotenv(os.path.expanduser("~/watson/.env"))

DB = os.path.expanduser("~/watson/data/watson.db")
LOGS_DIR = os.path.expanduser("~/watson/logs")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STUCK_RUNNING_HOURS = 24


def _send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.dev_loop.cleanup", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        print(f"[cleanup] Telegram send failed: {exc}")


def flag_stuck_running(conn: sqlite3.Connection) -> None:
    """Read-only alert for 'running' rows stalled past STUCK_RUNNING_HOURS.

    Does not change status, delete rows, or touch staging directories —
    see the module docstring for why 'running' stays out of the purge
    query above.
    """
    rows = conn.execute(
        """
        SELECT slug, title, created_at, updated_at
        FROM dev_projects
        WHERE status = 'running'
        AND updated_at < datetime('now', ?)
        """,
        (f"-{STUCK_RUNNING_HOURS} hours",),
    ).fetchall()

    if not rows:
        return

    now = datetime.utcnow()
    lines = [f"⚠️ Dev Loop: {len(rows)} project(s) stuck in 'running' past {STUCK_RUNNING_HOURS}h:"]
    for row in rows:
        updated = datetime.strptime(row["updated_at"], "%Y-%m-%d %H:%M:%S")
        stalled = now - updated
        lines.append(
            f"- {row['slug']} ({row['title']}): created {row['created_at']}, "
            f"stalled {stalled.days}d {stalled.seconds // 3600}h"
        )
    _send_telegram("\n".join(lines))


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT slug, staging_path
        FROM dev_projects
        WHERE (
            status IN ('delivered', 'failed', 'stopped')
            AND updated_at < datetime('now', '-48 hours')
        ) OR (
            status = 'paused'
            AND updated_at < datetime('now', '-7 days')
        )
        """
    ).fetchall()

    deleted_rows = 0
    deleted_logs = 0
    deleted_staging = 0

    for row in rows:
        slug = row["slug"]
        staging_path = row["staging_path"]

        log_file = os.path.join(LOGS_DIR, f"devloop-{slug}.log")
        if os.path.exists(log_file):
            os.unlink(log_file)
            deleted_logs += 1

        if staging_path:
            staging = Path(staging_path)
            for f in ("main.py", "spec.md"):
                p = staging / f
                if p.exists():
                    p.unlink()
                    deleted_staging += 1
            try:
                staging.rmdir()
            except OSError:
                pass

        conn.execute("DELETE FROM dev_projects WHERE slug = ?", (slug,))
        deleted_rows += 1

    conn.commit()

    flag_stuck_running(conn)

    conn.close()

    print(f"Dev loop cleanup: {deleted_rows} project(s) removed, "
          f"{deleted_logs} log(s) deleted, {deleted_staging} staging file(s) deleted.")


if __name__ == "__main__":
    main()
