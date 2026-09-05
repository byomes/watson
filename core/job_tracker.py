"""Job execution tracking: start/end wall-clock time per jobs/*.py run.

No general job-execution entrypoint exists in this codebase today -- every
cron line invokes a job script directly (see WATSON_ARCHITECTURE.md's Jobs
Architecture table), and there are 450+ job files, so retrofitting all of
them (or rewriting every live crontab line to route through a wrapper) was
judged too invasive for one change. This module is the reusable mechanism:
new jobs should call track_job() around their run(), and existing jobs can
adopt it incrementally next time they're touched.

Currently wired into jobs/dev/resource_sampler.py and
jobs/dev/weekly_utilization_report.py only. Until broader adoption,
jobs/dev/weekly_utilization_report.py leans on jobs/dev/resource_sampler.py's
sampled process-attribution (resource_samples.active_jobs) as the main
signal for "which jobs are eating wall-clock time," since that already
covers every job without any per-file changes -- see that module's
docstring for the tradeoff (sampled, not exact; short/fast jobs can be
undercounted).
"""
import logging
import time
from contextlib import contextmanager

from core.database import get_connection

log = logging.getLogger(__name__)


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_runs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name         TEXT NOT NULL,
                started_at       TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at         TEXT,
                duration_seconds REAL,
                status           TEXT NOT NULL DEFAULT 'running'
                                     CHECK(status IN ('running', 'success', 'error'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_runs_started_at ON job_runs (started_at)"
        )


_bootstrap()


@contextmanager
def track_job(job_name: str):
    """Logs a job_runs row spanning the wrapped block's start/end.

    Best-effort: a DB failure while logging never breaks the job it's
    wrapping. Exceptions raised inside the block are recorded as status
    'error' and re-raised unchanged.
    """
    start = time.monotonic()
    run_id = None
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO job_runs (job_name) VALUES (?)", (job_name,)
            )
            run_id = cur.lastrowid
    except Exception as exc:
        log.warning("track_job: failed to insert start row for %s: %s", job_name, exc)

    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        duration = time.monotonic() - start
        if run_id is not None:
            try:
                with get_connection() as conn:
                    conn.execute(
                        """UPDATE job_runs SET ended_at = datetime('now'),
                               duration_seconds = ?, status = ? WHERE id = ?""",
                        (duration, status, run_id),
                    )
            except Exception as exc:
                log.warning("track_job: failed to update end row for %s: %s", job_name, exc)
