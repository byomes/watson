"""jobs/analytics/unanswered_questions.py — persistent log of Team Chat
questions Watson had to hand off to an LLM instead of a free pattern-match
(bot.py's _alert_unanswered_team_question fires the live Telegram ping for
genuinely unanswered ones; this module is where that event gets written
down, alongside successful-but-Claude-costing questions synced in from
core.claude_tier's spend log, for the nightly review job
(jobs/analytics/fast_path_suggestions.py) to mine for new LLM-free
fast-path patterns).

Built 2026-09-08 per Bill's ask right after the deacon-group/birthday
fast-path additions -- the goal is a standing feedback loop: log what
Watson couldn't (or could only expensively) answer -> nightly, look for
repeat shapes -> propose a concrete pattern-match addition -> Bill
approves/rejects over Telegram.

Widened 2026-09-09 per Bill's ask (Jim Bouchat starting to use Team Chat to
organize the deacons, generating a steady stream of "Watson called Claude
for help" pings): the original design only ever saw questions Watson
answered generically (on_topic=False in jobs.analytics.data_chat) --
that's a small minority of what's actually costing API spend, since
data_chat's answer_data_question() tries Claude FIRST for every on-topic
attendance/web/events question that its free cdb_query pattern-match layer
doesn't already catch (see core/claude_tier.py's call_claude() docstring).
sync_claude_answered_questions() below closes that gap by also pulling
successfully-answered-but-Claude-billed questions straight from
claude_tier_spend_log, so the nightly review sees the real, full picture
of what's driving spend -- not just the failures.
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
        cols = {row[1] for row in conn.execute("PRAGMA table_info(unanswered_questions)").fetchall()}
        if "source" not in cols:
            # 'unanswered' = genuinely off-topic/declined (original design);
            # 'claude_call' = on-topic and answered, but only by paying for
            # a Claude call -- see sync_claude_answered_questions() below.
            conn.execute(
                "ALTER TABLE unanswered_questions ADD COLUMN source TEXT NOT NULL DEFAULT 'unanswered'"
            )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


_bootstrap()

_WATERMARK_KEY = "claude_call_review_last_spend_id"


def log_unanswered(asker_name: str, question: str, reply: str) -> None:
    """Never raises -- called from bot.py's live-alert path, so a logging
    failure must not break the Telegram notification it rides alongside."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO unanswered_questions (asker_name, question, reply, source) "
                "VALUES (?, ?, ?, 'unanswered')",
                (asker_name, question, reply),
            )
    except Exception:
        pass


def sync_claude_answered_questions() -> int:
    """Pull newly-logged jobs.analytics.data_chat rows out of
    core.claude_tier's claude_tier_spend_log and add them to the same
    review queue as genuinely-unanswered questions (source='claude_call').
    Tracks a watermark (highest spend-log id seen) in system_settings so
    each run only picks up rows logged since the last sync. Never raises --
    called at the top of the nightly review run; a sync failure shouldn't
    block that run from still reviewing whatever's already queued.
    Returns the number of rows added."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM system_settings WHERE key = ?", (_WATERMARK_KEY,)
            ).fetchone()
            last_id = int(row["value"]) if row else 0

            new_rows = conn.execute(
                "SELECT id, person, trigger_message FROM claude_tier_spend_log "
                "WHERE job_name = 'analytics.data_chat' AND id > ? AND trigger_message != '' "
                "ORDER BY id ASC",
                (last_id,),
            ).fetchall()
            if not new_rows:
                return 0

            conn.executemany(
                "INSERT INTO unanswered_questions (asker_name, question, reply, source) "
                "VALUES (?, ?, NULL, 'claude_call')",
                [(r["person"], r["trigger_message"]) for r in new_rows],
            )
            max_id = max(r["id"] for r in new_rows)
            conn.execute(
                """INSERT INTO system_settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (_WATERMARK_KEY, str(max_id)),
            )
            return len(new_rows)
    except Exception:
        return 0


def get_open_since(since_iso: str) -> list[dict]:
    """Open (not yet reviewed) rows logged at or after since_iso, oldest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, asker_name, question, reply, source, created_at FROM unanswered_questions "
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
