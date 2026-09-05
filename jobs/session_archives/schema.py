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

        # source_conversation_uuid: the Claude.ai conversation uuid this archive
        # came from, when it was created by the nightly export importer (jobs/
        # session_archives/claude_export_import.py) rather than the live
        # archive_session MCP tool. Lets repeat nightly exports skip conversations
        # already archived instead of re-importing duplicates. NULL for archives
        # created via the normal "send to watson" MCP path.
        # summary: the short recap text passed to archive_session, persisted here
        # (not just appended to the project's _summary.md) so a later reclassify
        # pass can re-file an archive into a different project's summary file
        # without having to regenerate or guess the recap text.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(session_archives)").fetchall()}
        if "source_conversation_uuid" not in cols:
            conn.execute("ALTER TABLE session_archives ADD COLUMN source_conversation_uuid TEXT")
        if "summary" not in cols:
            conn.execute("ALTER TABLE session_archives ADD COLUMN summary TEXT")

        # superseded_by: set when an archive turns out to be bad (truncated,
        # wrong content, etc.) and a corrected archive replaces it. Archives
        # stay immutable/append-only (no update or delete path) — this just
        # hides the bad one from list_archives/search_archives by default so
        # it stops cluttering retrieval, while keeping it in the DB for
        # audit/history. NULL for every normal archive.
        if "superseded_by" not in cols:
            conn.execute("ALTER TABLE session_archives ADD COLUMN superseded_by INTEGER")

        conn.commit()
    finally:
        if owns_conn:
            conn.close()
