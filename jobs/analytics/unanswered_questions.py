"""jobs/analytics/unanswered_questions.py — persistent log of Team Chat
questions Watson could only answer generically (bot.py's
_alert_unanswered_team_question fires the live Telegram ping; this module
is where that same event gets written down for the weekly review job
(jobs/analytics/fast_path_suggestions.py) to mine for new LLM-free
fast-path patterns).

Built 2026-09-08 per Bill's ask right after the deacon-group/birthday
fast-path additions -- the goal is a standing feedback loop: log what
Watson couldn't answer -> once a week, look for repeat shapes -> propose a
concrete pattern-match addition -> Bill approves/rejects over Telegram.
"""
from core.database import get_connection


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS unanswered_questions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                asker_name   TEXT NOT NULL,
                question     TEXT NOT NULL,
                reply        TEXT,
                status       TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'reviewed'
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


_bootstrap()


def log_unanswered(asker_name: str, question: str, reply: str) -> None:
    """Never raises -- called from bot.py's live-alert path, so a logging
    failure must not break the Telegram notification it rides alongside."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO unanswered_questions (asker_name, question, reply) VALUES (?, ?, ?)",
                (asker_name, question, reply),
            )
    except Exception:
        pass


def get_open_since(since_iso: str) -> list[dict]:
    """Open (not yet reviewed) rows logged at or after since_iso, oldest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, asker_name, question, reply, created_at FROM unanswered_questions "
            "WHERE status = 'open' AND created_at >= ? ORDER BY created_at ASC",
            (since_iso,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_reviewed(ids: list[int]) -> None:
    if not ids:
        return
    with get_connection() as conn:
        conn.executemany(
            "UPDATE unanswered_questions SET status = 'reviewed' WHERE id = ?",
            [(i,) for i in ids],
        )
