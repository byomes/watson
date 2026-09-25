"""jobs/devdispatch/scheduled.py -- hold a Claude Code instruction Bill gives
over Telegram and dispatch it for real at a specific future time.

Built 2026-09-24 because Watson's normal chat path (an LLM reply) can't
actually trigger anything -- when Bill said "at 4:52pm today give the
following instruction to Claude Code: ...", Watson just generated a
conversational reply that looked like it had relayed the instruction, but
never did. This module is the real mechanism: bot.py's pre-check
(_maybe_schedule_claude_job in bot.py) parses the time + instruction and
stores a row here; scheduled_dispatch.py (run every minute via cron) calls
fire_due_jobs(), which hands due rows to
jobs.devdispatch.api._dispatch_claude_code_job -- the same function backing
the dispatch_claude_code_job MCP tool. Dispatched jobs are PR-only, same as
that tool: jobs/devdispatch/poller.py (already running every 2 minutes)
picks up completion and pings Bill/opens the PR -- this module does not
duplicate that.
"""
import logging
import re
from datetime import datetime, timedelta

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from core.vacation import vacation_gate

log = logging.getLogger(__name__)


def _notify(text: str) -> None:
    if vacation_gate("normal", "jobs.devdispatch.scheduled", text):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scheduled_claude_jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_text           TEXT NOT NULL,
    repo                TEXT,
    scheduled_for       TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'dispatched', 'failed', 'cancelled')),
    chat_id             INTEGER,
    claude_code_job_id  INTEGER,
    error               TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    dispatched_at       TEXT
)
"""


def ensure_schema() -> None:
    with get_connection() as conn:
        conn.execute(CREATE_TABLE)


ensure_schema()

# Keyword -> repo. Checked in order, first match wins; extend by hand as new
# projects come up. Falls back to None (caller asks Bill) rather than
# guessing wrong and dispatching against the wrong codebase.
_REPO_KEYWORDS = [
    ("watson-tools", (
        "catalyst db", "catalystdb", "wtsn.me", "deacon app", "deaconapp",
        "micah tasks", "ham prep", "curator", "watson-tools",
        "shepherding report", "comms desk", "comms-desk",
    )),
    ("wcky", (
        "wcky", "blog", "guardrails", "companion guide", "guides page",
        "book launch", "arc program", "writing room",
    )),
    ("watson", (
        "watson dashboard", "telegram bot", "bot.py", "devdispatch",
        "cron job", "scheduler.py", "flask", "congregation.db", "watson.db",
        "jobs/", "backend",
    )),
    ("watson-admin", ("watson-admin",)),
    ("watson-ui", ("watson-ui",)),
    ("fms", ("fms", "faith makes sense")),
    ("bodyrec", ("bodyrec",)),
]


def infer_repo(spec_text: str) -> str | None:
    text = spec_text.lower()
    for repo, keywords in _REPO_KEYWORDS:
        if any(kw in text for kw in keywords):
            return repo
    return None


_TIME_RE = re.compile(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', re.IGNORECASE)


def parse_schedule_time(time_phrase: str, date_word: str | None, now: datetime | None = None) -> datetime | None:
    """Parse a time phrase like '4:52pm' / '9am' plus an optional 'today'/
    'tomorrow' word into a concrete datetime. Returns None if unparseable.
    If no date word is given and the time has already passed today, rolls
    to tomorrow (matching how a person would mean it)."""
    now = now or datetime.now()
    m = _TIME_RE.search(time_phrase.strip())
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = (m.group(3) or "").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    date_word = (date_word or "").strip().lower()
    if date_word == "tomorrow":
        target += timedelta(days=1)
    elif date_word in ("", "today") and target <= now:
        target += timedelta(days=1)
    return target


def create_scheduled_job(spec_text: str, scheduled_for: datetime, chat_id: int, repo: str | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO scheduled_claude_jobs (spec_text, repo, scheduled_for, chat_id)
               VALUES (?, ?, ?, ?)""",
            (spec_text, repo, scheduled_for.strftime("%Y-%m-%d %H:%M:%S"), chat_id),
        )
        return cur.lastrowid


def set_repo(job_id: int, repo: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE scheduled_claude_jobs SET repo = ? WHERE id = ?", (repo, job_id))


def cancel(job_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE scheduled_claude_jobs SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (job_id,),
        )


def _due_rows(now: datetime):
    with get_connection() as conn:
        return conn.execute(
            """SELECT id, spec_text, repo, chat_id FROM scheduled_claude_jobs
               WHERE status = 'pending' AND repo IS NOT NULL
                 AND scheduled_for <= ?
               ORDER BY id""",
            (now.strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchall()


def fire_due_jobs(now: datetime | None = None) -> list[dict]:
    """Dispatch every scheduled job whose time has arrived and which has a
    resolved repo. Rows still waiting on Bill to pick a repo (repo IS NULL)
    are left alone -- they are handled by the scheduled_job_repo pending-
    action reply, not by this poller."""
    from jobs.devdispatch.api import _dispatch_claude_code_job

    now = now or datetime.now()
    results = []
    for row in _due_rows(now):
        job_id, spec_text, repo = row["id"], row["spec_text"], row["repo"]
        result = _dispatch_claude_code_job(spec_text, repo)
        with get_connection() as conn:
            if result.get("status") == "running":
                conn.execute(
                    """UPDATE scheduled_claude_jobs
                       SET status = 'dispatched', claude_code_job_id = ?, dispatched_at = datetime('now')
                       WHERE id = ?""",
                    (result.get("job_id"), job_id),
                )
                try:
                    _notify(f"⏰ Dispatched your scheduled instruction to Claude Code ({repo}, job {result.get('job_id')}). I'll ping you when it opens a PR.")
                except Exception:
                    log.exception("scheduled dispatch: notify failed for job_id=%s", job_id)
            else:
                err = result.get("error", "unknown error")
                conn.execute(
                    "UPDATE scheduled_claude_jobs SET status = 'failed', error = ? WHERE id = ?",
                    (err, job_id),
                )
                try:
                    _notify(f"❌ Couldn't dispatch your scheduled Claude Code instruction ({repo}): {err}")
                except Exception:
                    log.exception("scheduled dispatch: failure-notify failed for job_id=%s", job_id)
        results.append({"id": job_id, "result": result})
    return results
