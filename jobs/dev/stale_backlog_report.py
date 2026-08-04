"""jobs/dev/stale_backlog_report.py — Deterministic (non-LLM) report of
project_backlog rows whose "Added" date is more than 60 days old.

Prints title + added date, one per line, oldest first. Read-only — no writes
to project_backlog or any other table.

Run: PYTHONPATH=/home/billyomes/watson venv/bin/python jobs/dev/stale_backlog_report.py
"""
from core.database import get_connection

STALE_DAYS = 60


def get_stale_backlog_items() -> list[dict]:
    """Return project_backlog rows with added_date more than STALE_DAYS old,
    oldest first. Each row is a dict with 'title' and 'added_date'."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT title, added_date FROM project_backlog "
            "WHERE added_date < date('now', ?) "
            "ORDER BY added_date ASC",
            (f"-{STALE_DAYS} days",),
        ).fetchall()
    return [{"title": r["title"], "added_date": r["added_date"]} for r in rows]


def main() -> None:
    items = get_stale_backlog_items()
    if not items:
        print(f"No backlog items older than {STALE_DAYS} days.")
        return

    print(f"Stale backlog items (added more than {STALE_DAYS} days ago):")
    for item in items:
        print(f"  {item['title']} — added {item['added_date']}")


if __name__ == "__main__":
    main()
