"""jobs/devdispatch/schema.py — Schema for the MCP Claude Code dispatcher's
job-tracking table (claude_code_jobs). See MCP-Claude-Code-Dispatcher-Spec.md.
"""
from core.database import get_connection

ALLOWED_REPOS = ("watson", "wcky", "watson-admin", "watson-ui", "fms", "bodyrec")

CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS claude_code_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_text    TEXT NOT NULL,
    repo         TEXT NOT NULL CHECK (repo IN ('watson','wcky','watson-admin','watson-ui','fms','bodyrec')),
    branch       TEXT NOT NULL CHECK (branch NOT IN ('main', 'master')),
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'failed', 'expired')),
    pr_url       TEXT,
    log_path     TEXT,
    summary      TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT
);
"""


def create_tables(conn=None) -> None:
    """Idempotent — CREATE TABLE IF NOT EXISTS."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        conn.execute(CREATE_JOBS)

        # cli_session_id: the short id `claude --bg` prints at launch (e.g.
        # "f5593e57"), needed to cross-reference `claude agents --json` —
        # none of the original columns map to it.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(claude_code_jobs)").fetchall()}
        if "cli_session_id" not in cols:
            conn.execute("ALTER TABLE claude_code_jobs ADD COLUMN cli_session_id TEXT")

        conn.commit()
    finally:
        if owns_conn:
            conn.close()
