"""jobs/dev/stale_backlog_report_clauded.py — Print project_backlog rows
older than 60 days.

Deterministic, non-LLM: queries project_backlog for rows where added_date
is more than 60 days old and prints title + added date to stdout, most
overdue first.

Run: PYTHONPATH=/home/billyomes/watson venv/bin/python jobs/dev/stale_backlog_report_clauded.py
"""
from core.database import get_connection


def get_stale_backlog_rows(conn):
    return conn.execute(
        "SELECT title, added_date FROM project_backlog "
        "WHERE added_date < date('now', '-60 days') "
        "ORDER BY added_date ASC"
    ).fetchall()


def main() -> None:
    conn = get_connection()
    try:
        rows = get_stale_backlog_rows(conn)
    finally:
        conn.close()

    if not rows:
        print("No stale backlog items (nothing older than 60 days).")
        return

    for row in rows:
        print(f"{row['title']} — added {row['added_date']}")


if __name__ == "__main__":
    main()
