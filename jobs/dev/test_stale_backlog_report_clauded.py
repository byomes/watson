"""
Unit tests for stale_backlog_report_clauded.py's >60-day filtering logic.

Uses an in-memory SQLite database with a fixture project_backlog table —
never touches the real watson.db.

Run:
  PYTHONPATH=/home/billyomes/watson venv/bin/python -m pytest \
    jobs/dev/test_stale_backlog_report_clauded.py -v
"""
import sqlite3

from jobs.dev.stale_backlog_report_clauded import get_stale_backlog_rows


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE project_backlog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            detail TEXT,
            status TEXT NOT NULL DEFAULT 'planned',
            added_date TEXT NOT NULL DEFAULT (date('now'))
        )
    """)
    return conn


def test_returns_only_rows_older_than_60_days():
    conn = _make_conn()
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) VALUES (?, ?, date('now', '-90 days'))",
        ("Stale item", "old"),
    )
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) VALUES (?, ?, date('now', '-10 days'))",
        ("Fresh item", "new"),
    )
    conn.commit()

    rows = get_stale_backlog_rows(conn)

    assert [r["title"] for r in rows] == ["Stale item"]


def test_row_exactly_60_days_old_is_not_included():
    """Boundary check: added_date < date('now', '-60 days') excludes exactly-60-day rows."""
    conn = _make_conn()
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) VALUES (?, ?, date('now', '-60 days'))",
        ("Exactly 60 days", "boundary"),
    )
    conn.commit()

    rows = get_stale_backlog_rows(conn)

    assert rows == []


def test_row_61_days_old_is_included():
    conn = _make_conn()
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) VALUES (?, ?, date('now', '-61 days'))",
        ("61 days old", "boundary"),
    )
    conn.commit()

    rows = get_stale_backlog_rows(conn)

    assert [r["title"] for r in rows] == ["61 days old"]


def test_no_stale_rows_returns_empty_list():
    conn = _make_conn()
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) VALUES (?, ?, date('now', '-5 days'))",
        ("Brand new", "new"),
    )
    conn.commit()

    assert get_stale_backlog_rows(conn) == []


def test_multiple_stale_rows_ordered_oldest_first():
    conn = _make_conn()
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) VALUES (?, ?, date('now', '-70 days'))",
        ("70 days old", "old"),
    )
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) VALUES (?, ?, date('now', '-200 days'))",
        ("200 days old", "oldest"),
    )
    conn.commit()

    rows = get_stale_backlog_rows(conn)

    assert [r["title"] for r in rows] == ["200 days old", "70 days old"]


if __name__ == "__main__":
    test_returns_only_rows_older_than_60_days()
    test_row_exactly_60_days_old_is_not_included()
    test_row_61_days_old_is_included()
    test_no_stale_rows_returns_empty_list()
    test_multiple_stale_rows_ordered_oldest_first()
    print("All tests passed.")
