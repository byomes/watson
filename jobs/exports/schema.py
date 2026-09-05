"""jobs/exports/schema.py — Schema for the general file-export download-link
mechanism (file_export_links). See jobs/exports/export_link.py and
jobs/exports/api.py's GET /export/download/<token> route.

Generalizes jobs/kb/schema.py's kb_export_links table beyond KB zip
exports -- kept as a separate table/module rather than merged into the KB
one, since KB's shape (query/caption) doesn't generalize cleanly and the
KB mechanism is left untouched by this addition (see the 2026-09-05
watson-review file-export-link proposal, open question #4).
"""
from core.database import get_connection

CREATE_EXPORT_LINKS = """
CREATE TABLE IF NOT EXISTS file_export_links (
    token       TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    filename    TEXT NOT NULL,
    caption     TEXT,
    sanitized   INTEGER NOT NULL DEFAULT 0,
    redactions  TEXT,
    used        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    used_at     TEXT,
    expires_at  TEXT NOT NULL
);
"""


def create_tables(conn=None) -> None:
    """Idempotent -- CREATE TABLE IF NOT EXISTS."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        conn.execute(CREATE_EXPORT_LINKS)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
