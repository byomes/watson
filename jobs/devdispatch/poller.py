"""jobs/devdispatch/poller.py — Scheduled poller for devdispatch jobs.

Closes the gap documented in jobs/devdispatch/api.py's module docstring:
check_claude_code_job only cross-references `claude agents --json --all`
and finalizes (commit/push/PR/Telegram) when someone actually calls it —
a job that's never checked sits at 'queued'/'running' indefinitely with
its background session idling. This script exercises that exact same
check on a schedule instead, so Bill gets the "✅ devdispatch job {id}
done — {pr_url}" Telegram message automatically.

Reuses jobs.devdispatch.api._check_claude_code_job directly — it already
owns the claude-agents cross-reference, _finalize_completed_job (commit/
push/PR/Telegram), and failure handling. Nothing here duplicates that
logic; this is purely "which job ids need checking, and don't overlap
with a previous run."

Additive only — dispatch_claude_code_job and check_claude_code_job (the
manual, on-demand path) are untouched and keep working exactly as before.

Cron (every 2 minutes):
  */2 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/devdispatch/poller.py \
    >> /home/billyomes/watson/logs/devdispatch_poller.log 2>&1
"""
import fcntl
import logging
from pathlib import Path

from core.database import get_connection
from jobs.devdispatch.api import _check_claude_code_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [devdispatch.poller] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "data" / ".devdispatch_poller.lock"


def _pending_job_ids() -> list[int]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM claude_code_jobs WHERE status IN ('queued', 'running') ORDER BY id"
        ).fetchall()
        return [row["id"] for row in rows]
    finally:
        conn.close()


def poll() -> None:
    job_ids = _pending_job_ids()
    if not job_ids:
        return

    log.info("checking %d job(s): %s", len(job_ids), job_ids)
    for job_id in job_ids:
        try:
            result = _check_claude_code_job(job_id)
        except Exception as exc:
            log.error("job %d: check failed: %s", job_id, exc)
            continue

        status = result.get("status")
        if status in ("done", "failed"):
            log.info("job %d: transitioned to %s (%s)", job_id, status, result.get("summary"))
        else:
            log.info("job %d: still %s", job_id, status)


def main() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("previous poller run still in progress — skipping this tick")
            return
        try:
            poll()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


if __name__ == "__main__":
    main()
