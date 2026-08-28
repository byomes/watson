"""jobs/tools/schema.py — public_tools registry table for the wtsn.me
public-tools pathway.

See notes/wtsn-me-public-tools-spec.md for the architecture this
implements: one shared watson-tools Next.js app on Vercel, slug-based
routes/rows, this table as the single source of truth for what exists and
whether it's live.
"""
from core.database import get_connection

CREATE_PUBLIC_TOOLS = """
CREATE TABLE IF NOT EXISTS public_tools (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                      TEXT NOT NULL UNIQUE,
    title                     TEXT NOT NULL,
    tool_type                 TEXT NOT NULL CHECK (tool_type IN ('redirect', 'page', 'custom')),
    target_url                TEXT,
    body_text                 TEXT,
    status                    TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'live')),
    created_at                TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                TEXT NOT NULL DEFAULT (datetime('now')),
    first_deploy_confirmed_at TEXT
)
"""


def create_tables():
    with get_connection() as conn:
        conn.execute(CREATE_PUBLIC_TOOLS)
