"""jobs/kb/schema.py — Schema for KB export download-link tokens
(kb_export_links). See jobs/kb/export_link.py and jobs/kb/api.py's
GET /kb/download/<token> route.
"""
from core.database import get_connection

CREATE_EXPORT_LINKS = """
CREATE TABLE IF NOT EXISTS kb_export_links (
    token       TEXT PRIMARY KEY,
    zip_path    TEXT NOT NULL,
    query       TEXT NOT NULL,
    caption     TEXT,
    used        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL
);
"""


def create_tables(conn=None) -> None:
    """Idempotent — CREATE TABLE IF NOT EXISTS."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        conn.execute(CREATE_EXPORT_LINKS)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
