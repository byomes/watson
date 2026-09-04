"""jobs/dev/resource_sampler.py -- lightweight periodic resource sampler.

Records CPU%, RAM used, disk used/free, and whether the box was actively
running a jobs/*.py process at sample time (and which one) to the
resource_samples table. Feeds jobs/dev/weekly_utilization_report.py, which
aggregates a week of samples so Bill can estimate what a VPS with
equivalent specs would cost. Beelink/Watson scope only -- never samples or
references FMSPC (see WATSON_ARCHITECTURE.md's FMSPC note: it is excluded
from Watson's automated job loop, permanently).

Job attribution is a sampling estimate, not exact accounting: a job that
starts and finishes between two 5-minute samples will never be observed.
It's still useful signal for jobs that run for a meaningful fraction of a
sample interval (backups, KB sync/indexing, report generation, Ollama
calls), which is what actually drives box load -- see core/job_tracker.py
for the exact-duration alternative used by this job and the weekly report
job themselves.

Usage:
  PYTHONPATH=/home/billyomes/watson python jobs/dev/resource_sampler.py

Cron (every 5 minutes):
  */5 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/dev/resource_sampler.py \
    >> /home/billyomes/watson/logs/resource_sampler.log 2>&1
"""
import logging
import os

import psutil

from core.database import get_connection
from core.job_tracker import track_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [resource_sampler] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

_SELF_PID = os.getpid()


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resource_samples (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sampled_at   TEXT NOT NULL DEFAULT (datetime('now')),
                cpu_percent  REAL NOT NULL,
                mem_used_gb  REAL NOT NULL,
                mem_total_gb REAL NOT NULL,
                disk_used_gb REAL NOT NULL,
                disk_free_gb REAL NOT NULL,
                busy         INTEGER NOT NULL DEFAULT 0,
                active_jobs  TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_resource_samples_sampled_at "
            "ON resource_samples (sampled_at)"
        )


def _job_name_from_cmdline(cmdline: list) -> str | None:
    """Best-effort job identity from a process cmdline, e.g.
    ['.../venv/bin/python', '/home/billyomes/watson/jobs/foo/bar.py']
    -> 'foo.bar', or ['...python', '-m', 'jobs.foo.bar'] -> 'foo.bar'.
    Returns None for anything that isn't a jobs/*.py invocation."""
    joined = " ".join(cmdline)
    if "watson/jobs/" in joined:
        for part in cmdline:
            if "watson/jobs/" in part and part.endswith(".py"):
                tail = part.split("watson/jobs/", 1)[1]
                return tail[:-3].replace("/", ".")
    if " -m " in f" {joined} " and "jobs." in joined:
        for part in cmdline:
            if part.startswith("jobs."):
                return part[len("jobs."):]
    return None


def detect_active_jobs() -> list:
    """Scans running processes for jobs/*.py invocations, excluding self."""
    active = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        if proc.info["pid"] == _SELF_PID:
            continue
        cmdline = proc.info.get("cmdline") or []
        if not cmdline:
            continue
        name = _job_name_from_cmdline(cmdline)
        if name and name != "dev.resource_sampler":
            active.append(name)
    return sorted(set(active))


def sample() -> dict:
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    active_jobs = detect_active_jobs()
    return {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "mem_used_gb": round(mem.used / 1e9, 2),
        "mem_total_gb": round(mem.total / 1e9, 2),
        "disk_used_gb": round(disk.used / 1e9, 1),
        "disk_free_gb": round(disk.free / 1e9, 1),
        "busy": 1 if active_jobs else 0,
        "active_jobs": ",".join(active_jobs),
    }


def run() -> None:
    _bootstrap()
    with track_job("dev.resource_sampler"):
        s = sample()
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO resource_samples
                       (cpu_percent, mem_used_gb, mem_total_gb, disk_used_gb,
                        disk_free_gb, busy, active_jobs)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    s["cpu_percent"], s["mem_used_gb"], s["mem_total_gb"],
                    s["disk_used_gb"], s["disk_free_gb"], s["busy"], s["active_jobs"],
                ),
            )
        log.info(
            "cpu=%.1f%% mem=%.2f/%.2fGB disk=%.1f/%.1fGB busy=%s active=%s",
            s["cpu_percent"], s["mem_used_gb"], s["mem_total_gb"],
            s["disk_used_gb"], s["disk_free_gb"], bool(s["busy"]), s["active_jobs"] or "-",
        )


if __name__ == "__main__":
    run()
