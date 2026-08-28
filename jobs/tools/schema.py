"""jobs/tools/schema.py — public_tools registry table for the wtsn.me
public-tools pathway.

See notes/wtsn-me-public-tools-spec.md for the architecture this
implements: one shared watson-tools Next.js app on Vercel, category/slug
routes (e.g. wtsn.me/cat/connect — different areas of life get their own
top-level category, e.g. cat/ for Catalyst, writing/, fms/, adelphos/),
this table as the single source of truth for what exists and whether it's
live.
"""
from core.database import get_connection

CREATE_PUBLIC_TOOLS = """
CREATE TABLE IF NOT EXISTS public_tools (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    category                  TEXT NOT NULL,
    slug                      TEXT NOT NULL,
    title                     TEXT NOT NULL,
    tool_type                 TEXT NOT NULL CHECK (tool_type IN ('redirect', 'page', 'custom')),
    target_url                TEXT,
    body_text                 TEXT,
    status                    TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'live')),
    created_at                TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                TEXT NOT NULL DEFAULT (datetime('now')),
    first_deploy_confirmed_at TEXT,
    UNIQUE (category, slug)
)
"""


def create_tables():
    with get_connection() as conn:
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(public_tools)").fetchall()}
        if existing_cols and "category" not in existing_cols:
            # Pre-category schema (slug-only UNIQUE) from the first build,
            # 2026-08-27. Safe to recreate outright ONLY because the live
            # table has never held more than a since-deleted throwaway test
            # row (phase4-test) — guarded on row count, never a blind drop.
            row_count = conn.execute("SELECT COUNT(*) FROM public_tools").fetchone()[0]
            if row_count == 0:
                conn.execute("DROP TABLE public_tools")
            else:
                raise RuntimeError(
                    "public_tools has rows under the pre-category schema — "
                    "write a real migration instead of dropping data"
                )
        conn.execute(CREATE_PUBLIC_TOOLS)
