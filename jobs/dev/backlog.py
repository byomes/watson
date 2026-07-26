"""jobs/dev/backlog.py — shared project_backlog write path.

Single insert function, called by the Telegram `backlog:` directive (bot.py),
the dashboard terminal `backlog:` prefix (app.py terminal()), and
POST /api/project-backlog (app.py) — one INSERT, not three.
"""
from core.database import get_connection


def parse_directive_text(raw: str) -> tuple[str, str]:
    """Split 'title | summary' on the first '|'. No '|' present -> (raw, '')."""
    if "|" in raw:
        title, summary = raw.split("|", 1)
        return title.strip(), summary.strip()
    return raw.strip(), ""


def create_backlog_item(title: str, summary: str = "", detail: str | None = None) -> int:
    """Insert a project_backlog row with status='planned'. Returns the new row id."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO project_backlog (title, summary, detail, status) VALUES (?, ?, ?, 'planned')",
            (title, summary, detail),
        )
        return cur.lastrowid
