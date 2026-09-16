"""jobs/dev/fix_log.py — a durable, append-only record of fixes made across
the Watson ecosystem, so Bill can refer back to what changed and why.

Built 2026-09-15 per Bill's ask, right after the fast_path_suggestions
autonomous rewire (see that module's docstring) went live -- with fixes now
shipping with no human review before merge+deploy, he wanted a persistent
place to look back at what actually got changed, not just a Telegram
message that scrolls away. Distinct from bug_tracker (open/resolved bugs
Bill tracks and closes out via the dashboard) -- this is broader: any real
fix or capability added, logged once and never edited, whether it came from
a live coding session, the fast-path simple-fix auto-apply, or the fully
autonomous fast-path dispatch+merge+deploy pipeline.

Read via GET /api/fixes (jobs/dashboard/app.py) -> dashboard's Dev > Fixes
tab, and via bot.py's "what fixes have been made" / "recent fixes" query in
Dr. Bill's own chat.
"""
from core.database import get_connection

_SOURCE_LABELS = {
    "session": "coding session",
    "fast_path_auto": "fast-path auto-apply",
    "fast_path_dispatch": "fast-path auto-dispatch",
}


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fix_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT NOT NULL,
                description  TEXT,
                repo         TEXT NOT NULL DEFAULT 'watson',
                source       TEXT NOT NULL DEFAULT 'session',
                commit_hash  TEXT,
                pr_url       TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


_bootstrap()


def log_fix(title: str, description: str = "", repo: str = "watson", source: str = "session",
            commit_hash: str | None = None, pr_url: str | None = None) -> int:
    """Records one fix. Never call this speculatively -- only once the fix
    is actually live (committed, and for the autonomous path, merged and
    deployed), so every row here is something that really happened."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO fix_log (title, description, repo, source, commit_hash, pr_url) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title.strip(), (description or "").strip() or None, repo, source, commit_hash, pr_url),
        )
        return cur.lastrowid


def recent_fixes(limit: int = 10) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM fix_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def format_fixes_reply(limit: int = 10) -> str:
    """Plain-text Telegram reply for bot.py's 'what fixes have been made'
    query -- see that module's _looks_like_fix_log_request."""
    fixes = recent_fixes(limit)
    if not fixes:
        return "No fixes logged yet."
    lines = [f"Last {len(fixes)} fix{'es' if len(fixes) != 1 else ''}:"]
    for f in fixes:
        when = (f["created_at"] or "")[:16].replace("T", " ")
        src = _SOURCE_LABELS.get(f["source"], f["source"])
        ref = f" ({f['commit_hash'][:7]})" if f.get("commit_hash") else (f" ({f['pr_url']})" if f.get("pr_url") else "")
        lines.append(f"\n• {f['title']}: {f['repo']}, {src}{ref}, {when}")
        if f.get("description"):
            lines.append(f"  {f['description']}")
    return "\n".join(lines)
