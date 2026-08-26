"""jobs/session_archives/schema.py — schema for Claude.ai session archives.

Backs the archive_session MCP tool and the list_archives / search_archives /
get_archive / list_projects / get_project_summary skills (see
jobs/session_archives/storage.py). Full spec: WATSON_ARCHITECTURE.md,
"Session Archives (Claude.ai)".
"""
from core.database import get_connection

CREATE_SESSION_ARCHIVES = """
CREATE TABLE IF NOT EXISTS session_archives (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project          TEXT NOT NULL,
    title            TEXT NOT NULL,
    dir_path         TEXT NOT NULL,
    transcript       TEXT NOT NULL,
    file_count       INTEGER NOT NULL DEFAULT 0,
    secrets_flagged  INTEGER NOT NULL DEFAULT 0,
    secrets_patterns TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# External-content FTS5 index over title/transcript, keyed by session_archives.id.
# Kept in sync manually (INSERT only — archives are immutable/append-only, see
# storage.archive_session) rather than via triggers, since there's exactly one
# write path. Best-effort: some sqlite3 builds lack FTS5, so creation is
# wrapped below and search_archives() falls back to a LIKE query if this
# table doesn't exist.
CREATE_SESSION_ARCHIVES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS session_archives_fts USING fts5(
    title, transcript, content='session_archives', content_rowid='id'
);
"""


def create_tables(conn=None) -> None:
    """Idempotent — CREATE TABLE IF NOT EXISTS."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        conn.execute(CREATE_SESSION_ARCHIVES)
        try:
            conn.execute(CREATE_SESSION_ARCHIVES_FTS)
        except Exception:
            pass
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
