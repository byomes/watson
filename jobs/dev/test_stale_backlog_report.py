"""jobs/dev/test_stale_backlog_report.py — Unit test for
stale_backlog_report.py's query logic against an isolated in-memory
project_backlog table (never touches the real watson.db).

Run: PYTHONPATH=/home/billyomes/watson venv/bin/python -m pytest \
    jobs/dev/test_stale_backlog_report.py -v
"""
import sqlite3
from unittest.mock import patch

from jobs.dev.stale_backlog_report import get_stale_backlog_items


def _fake_connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE project_backlog (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          detail TEXT,
          status TEXT NOT NULL DEFAULT 'planned',
          added_date TEXT NOT NULL DEFAULT (date('now'))
        )
        """
    )
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) "
        "VALUES ('Stale item, 90 days old', '', date('now', '-90 days'))"
    )
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) "
        "VALUES ('Stale item, exactly 61 days old', '', date('now', '-61 days'))"
    )
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) "
        "VALUES ('Fresh item, 10 days old', '', date('now', '-10 days'))"
    )
    conn.execute(
        "INSERT INTO project_backlog (title, summary, added_date) "
        "VALUES ('Boundary item, exactly 60 days old', '', date('now', '-60 days'))"
    )
    conn.commit()
    return conn


class _ConnCtx:
    """Minimal context-manager wrapper matching core.database.get_connection()'s
    `with get_connection() as conn:` usage in the module under test."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc):
        self._conn.close()
        return False


def test_returns_only_items_older_than_60_days():
    conn = _fake_connection()
    with patch(
        "jobs.dev.stale_backlog_report.get_connection",
        return_value=_ConnCtx(conn),
    ):
        items = get_stale_backlog_items()

    titles = [item["title"] for item in items]
    assert "Stale item, 90 days old" in titles
    assert "Stale item, exactly 61 days old" in titles
    assert "Fresh item, 10 days old" not in titles
    assert "Boundary item, exactly 60 days old" not in titles


def test_results_ordered_oldest_first():
    conn = _fake_connection()
    with patch(
        "jobs.dev.stale_backlog_report.get_connection",
        return_value=_ConnCtx(conn),
    ):
        items = get_stale_backlog_items()

    assert items[0]["title"] == "Stale item, 90 days old"
    assert items[1]["title"] == "Stale item, exactly 61 days old"


def test_empty_table_returns_empty_list():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE project_backlog (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          detail TEXT,
          status TEXT NOT NULL DEFAULT 'planned',
          added_date TEXT NOT NULL DEFAULT (date('now'))
        )
        """
    )
    conn.commit()

    with patch(
        "jobs.dev.stale_backlog_report.get_connection",
        return_value=_ConnCtx(conn),
    ):
        items = get_stale_backlog_items()

    assert items == []
