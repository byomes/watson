"""jobs/exports/export_link_cleanup.py — Sweep up expired, unclaimed file
export links. Mirrors jobs/kb/export_link_cleanup.py exactly, targeting
file_export_links instead of kb_export_links.

jobs/exports/api.py's GET /export/download/<token> already deletes the
staged file and marks the token used once a link is actually clicked --
this job only catches the case where a link is issued
(jobs.exports.export_link.create_export_link()) and then never clicked
before its expiry, so its staged copy would otherwise sit in the OS temp
dir forever.

Cron (every 30 min):
    PYTHONPATH=/home/billyomes/watson */30 * * * * /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/exports/export_link_cleanup.py >> /home/billyomes/watson/logs/export_link_cleanup.log 2>&1
"""
from pathlib import Path

from core.database import get_connection
from jobs.exports.schema import create_tables


def cleanup() -> dict:
    create_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT token, file_path FROM file_export_links "
            "WHERE used = 0 AND expires_at <= datetime('now')"
        ).fetchall()

        deleted_files = 0
        for row in rows:
            file_path = Path(row["file_path"])
            if file_path.exists():
                file_path.unlink(missing_ok=True)
                deleted_files += 1

        conn.execute("DELETE FROM file_export_links WHERE used = 0 AND expires_at <= datetime('now')")
        # Also drop rows for links that were already claimed (their staged
        # file is already gone -- the row itself is just bookkeeping).
        conn.execute("DELETE FROM file_export_links WHERE used = 1 AND expires_at <= datetime('now')")
        conn.commit()
    finally:
        conn.close()

    return {"expired_rows": len(rows), "deleted_files": deleted_files}


def main():
    result = cleanup()
    print(
        f"Export link cleanup: {result['expired_rows']} expired row(s) removed, "
        f"{result['deleted_files']} file(s) deleted."
    )


if __name__ == "__main__":
    main()
