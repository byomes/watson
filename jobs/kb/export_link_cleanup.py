"""
jobs/kb/export_link_cleanup.py — Sweep up expired, unclaimed KB export
download links.

jobs/kb/api.py's GET /kb/download/<token> already deletes the zip and
marks the token used once a link is actually clicked — this job only
catches the case where a link is issued (jobs.kb.export_link.run()) and
then never clicked before its 15-minute expiry, so its temp zip would
otherwise sit in the OS temp dir forever.

Cron (every 30 min):
    PYTHONPATH=/home/billyomes/watson */30 * * * * /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/kb/export_link_cleanup.py >> /home/billyomes/watson/logs/kb_export_link_cleanup.log 2>&1
"""
from pathlib import Path

from core.database import get_connection
from jobs.kb.schema import create_tables


def cleanup() -> dict:
    create_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT token, zip_path FROM kb_export_links "
            "WHERE used = 0 AND expires_at <= datetime('now')"
        ).fetchall()

        deleted_files = 0
        for row in rows:
            zip_path = Path(row["zip_path"])
            if zip_path.exists():
                zip_path.unlink(missing_ok=True)
                deleted_files += 1

        conn.execute("DELETE FROM kb_export_links WHERE used = 0 AND expires_at <= datetime('now')")
        # Also drop rows for links that were already claimed (their zip is
        # already gone — the row itself is just bookkeeping at that point).
        conn.execute("DELETE FROM kb_export_links WHERE used = 1 AND expires_at <= datetime('now')")
        conn.commit()
    finally:
        conn.close()

    return {"expired_rows": len(rows), "deleted_files": deleted_files}


def main():
    result = cleanup()
    print(
        f"KB export link cleanup: {result['expired_rows']} expired row(s) removed, "
        f"{result['deleted_files']} zip file(s) deleted."
    )


if __name__ == "__main__":
    main()
