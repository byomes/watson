"""jobs/devdispatch/schema.py — Schema for the MCP Claude Code dispatcher's
job-tracking table (claude_code_jobs) and its OAuth 2.1 authorization-code
shim (devdispatch_oauth_codes / devdispatch_oauth_tokens). See
MCP-Claude-Code-Dispatcher-Spec.md.
"""
from core.database import get_connection

ALLOWED_REPOS = ("watson", "wcky", "watson-admin", "watson-ui", "fms", "bodyrec", "watson-tools")

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

# Short-lived, single-use authorization codes issued by GET .../oauth/authorize
# and redeemed by POST .../oauth/token. code_challenge is always S256 (the
# only method the authorize endpoint accepts), so the method itself isn't
# stored.
CREATE_OAUTH_CODES = """
CREATE TABLE IF NOT EXISTS devdispatch_oauth_codes (
    code            TEXT PRIMARY KEY,
    client_id       TEXT NOT NULL,
    redirect_uri    TEXT NOT NULL,
    code_challenge  TEXT NOT NULL,
    resource        TEXT,
    used            INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL
);
"""

# Opaque bearer tokens issued by the token endpoint. Checked directly against
# this table on every /mcp/devdispatch request — no JWT signing, no refresh
# tokens; single-user shim, kept deliberately simple.
CREATE_OAUTH_TOKENS = """
CREATE TABLE IF NOT EXISTS devdispatch_oauth_tokens (
    access_token   TEXT PRIMARY KEY,
    client_id      TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at     TEXT NOT NULL
);
"""


def create_tables(conn=None) -> None:
    """Idempotent — CREATE TABLE IF NOT EXISTS."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        conn.execute(CREATE_JOBS)
        conn.execute(CREATE_OAUTH_CODES)
        conn.execute(CREATE_OAUTH_TOKENS)

        # cli_session_id: the short id `claude --bg` prints at launch (e.g.
        # "f5593e57"), needed to cross-reference `claude agents --json` —
        # none of the original columns map to it.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(claude_code_jobs)").fetchall()}
        if "cli_session_id" not in cols:
            conn.execute("ALTER TABLE claude_code_jobs ADD COLUMN cli_session_id TEXT")

        # last_progress_step: highest .devdispatch/progress.json "step" the
        # poller has already notified Telegram about, so it only sends a
        # message when the step actually advances. See jobs/devdispatch/poller.py.
        if "last_progress_step" not in cols:
            conn.execute("ALTER TABLE claude_code_jobs ADD COLUMN last_progress_step INTEGER NOT NULL DEFAULT 0")

        # merged_at: set once merge_claude_code_job successfully merges the
        # job's PR into main. NULL means not merged. Merge state is tracked
        # via this column rather than a new `status` value, so the CHECK
        # constraint on `status` is untouched — status stays 'done'.
        if "merged_at" not in cols:
            conn.execute("ALTER TABLE claude_code_jobs ADD COLUMN merged_at TEXT")

        # auto_merge: per Bill's 2026-09-15 explicit direction ("fully
        # autonomous -- write, merge, deploy, no review"), jobs.analytics.
        # fast_path_suggestions dispatches a real Claude Code job for any
        # "needs real logic" gap it finds, with NO Approve/Reject gate.
        # This flag tells jobs/devdispatch/poller.py to call
        # _merge_claude_code_job + deploy the instant the PR is opened,
        # instead of the normal "PR opened, Telegram sent, Bill merges
        # manually" flow that every other dispatch still uses. Scoped to
        # this one trigger on purpose -- see _merge_claude_code_job's own
        # docstring, which this is a deliberate, narrow exception to, not a
        # change to the general devdispatch safety model.
        if "auto_merge" not in cols:
            conn.execute("ALTER TABLE claude_code_jobs ADD COLUMN auto_merge INTEGER NOT NULL DEFAULT 0")

        # source_suggestion_id: the fast_path_suggestions.id that triggered
        # this dispatch, if any -- lets the poller write the outcome back
        # onto that row (status, applied_detail) once merged/deployed or
        # failed, the same way the non-autonomous suggestion flow already
        # records what happened.
        if "source_suggestion_id" not in cols:
            conn.execute("ALTER TABLE claude_code_jobs ADD COLUMN source_suggestion_id INTEGER")

        conn.commit()
    finally:
        if owns_conn:
            conn.close()
