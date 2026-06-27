"""
cleanup.py — Remove dev loop projects older than 7 days.

Deletes rows with status in (paused, failed, delivered) older than 7 days,
their log files, and any staging files.

Cron (Monday 4am):
    PYTHONPATH=/home/billyomes/watson 0 4 * * 1 /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/dev_loop/cleanup.py >> /home/billyomes/watson/logs/devloop_cleanup.log 2>&1
"""
import os
import sqlite3
from pathlib import Path

DB = os.path.expanduser("~/watson/data/watson.db")
LOGS_DIR = os.path.expanduser("~/watson/logs")


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT slug, staging_path
        FROM dev_projects
        WHERE status IN ('paused', 'failed', 'delivered')
          AND updated_at < datetime('now', '-7 days')
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
    conn.close()

    print(f"Dev loop cleanup: {deleted_rows} project(s) removed, "
          f"{deleted_logs} log(s) deleted, {deleted_staging} staging file(s) deleted.")


if __name__ == "__main__":
    main()
