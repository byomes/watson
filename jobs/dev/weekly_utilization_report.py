"""jobs/dev/weekly_utilization_report.py -- weekly Beelink utilization report.

Aggregates the past 7 days of jobs/dev/resource_sampler.py samples (plus
any exact core/job_tracker.py job_runs) into a Telegram message to Bill:
raw CPU/RAM/disk utilization and busy-vs-idle time, so he can estimate what
a VPS with equivalent specs would cost. Beelink/Watson scope only -- never
references FMSPC (see WATSON_ARCHITECTURE.md's FMSPC note).

Deliberately does NOT recommend a VPS tier or price -- just reports raw
numbers accurately. Cost mapping is a separate, later step once real data
exists.

Host spec constants below were read directly off the live Beelink
(`lscpu`, `free -h`, `lspci | grep -i vga`, `ollama ps`) on 2026-09-04.
Update them if the hardware changes -- there's no live-detection value in
re-deriving CPU model/RAM size every run, since those don't change week to
week the way utilization does.

Usage:
  PYTHONPATH=/home/billyomes/watson python jobs/dev/weekly_utilization_report.py

Cron (Monday 9:00am -- clear of Sunday 3pm attendance_link_reminder.py,
Sunday 5pm conflict_report.py, Sunday 6pm campaigns/weekly_digest.py, and
Monday 7am skillbuilder/audit.py):
  0 9 * * 1 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/dev/weekly_utilization_report.py \
    >> /home/billyomes/watson/logs/weekly_utilization_report.log 2>&1
"""
import logging
import os
from collections import Counter

from dotenv import load_dotenv

from core.database import get_connection
from core.job_tracker import track_job
from core.vacation import vacation_gate
from jobs.telegram.send_to_person import send_to_person

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [weekly_utilization_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RECIPIENT_NAME = "Bill Yomes"
SAMPLE_INTERVAL_MINUTES = 5
TOP_N_JOBS = 5

HOST_SPEC = (
    "Intel Core i5-1235U (12th gen, 10 cores / 12 threads), 32GB RAM, "
    "no discrete GPU (integrated Intel Iris Xe) -- Ollama runs 100% CPU "
    "inference, no GPU offload"
)


def _person_id(conn, name: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row["id"] if row else None


def _fetch_samples(conn):
    return conn.execute(
        """SELECT sampled_at, cpu_percent, mem_used_gb, mem_total_gb,
                  disk_used_gb, disk_free_gb, busy, active_jobs
           FROM resource_samples
           WHERE sampled_at >= datetime('now', '-7 days')
           ORDER BY sampled_at ASC"""
    ).fetchall()


def _fetch_job_runs(conn):
    return conn.execute(
        """SELECT job_name, COUNT(*) AS executions, SUM(duration_seconds) AS total_seconds
           FROM job_runs
           WHERE started_at >= datetime('now', '-7 days') AND status != 'running'
           GROUP BY job_name
           ORDER BY total_seconds DESC"""
    ).fetchall()


def build_report() -> str | None:
    with get_connection() as conn:
        samples = _fetch_samples(conn)
        job_runs = _fetch_job_runs(conn)

    if not samples:
        log.warning("No resource_samples in the past 7 days -- skipping report.")
        return None

    n = len(samples)
    avg_cpu = sum(s["cpu_percent"] for s in samples) / n
    peak_cpu = max(s["cpu_percent"] for s in samples)
    avg_mem = sum(s["mem_used_gb"] for s in samples) / n
    peak_mem = max(s["mem_used_gb"] for s in samples)
    mem_total = samples[-1]["mem_total_gb"]
    busy_pct = 100.0 * sum(s["busy"] for s in samples) / n

    disk_start = samples[0]["disk_used_gb"]
    disk_now = samples[-1]["disk_used_gb"]
    disk_free = samples[-1]["disk_free_gb"]
    disk_growth = disk_now - disk_start

    job_seen = Counter()
    for s in samples:
        for name in (s["active_jobs"] or "").split(","):
            name = name.strip()
            if name:
                job_seen[name] += 1
    top_jobs = job_seen.most_common(TOP_N_JOBS)

    lines = [
        "\U0001f4bb Weekly Beelink Utilization Report",
        "",
        f"Host: {HOST_SPEC}",
        "",
        f"CPU: avg {avg_cpu:.1f}%, peak {peak_cpu:.1f}%",
        f"RAM: avg {avg_mem:.1f} / {mem_total:.1f} GB, peak {peak_mem:.1f} GB",
        f"Busy vs idle: {busy_pct:.1f}% of the week actively running a job",
        f"Disk: {disk_now:.1f} GB used, {disk_free:.1f} GB free "
        f"({'+' if disk_growth >= 0 else ''}{disk_growth:.1f} GB this week)",
        "",
        f"Based on {n} samples every {SAMPLE_INTERVAL_MINUTES} min.",
    ]

    if top_jobs:
        lines.append("")
        lines.append(f"Top jobs by estimated active time (sampled, ~{SAMPLE_INTERVAL_MINUTES} min resolution):")
        for name, count in top_jobs:
            est_minutes = count * SAMPLE_INTERVAL_MINUTES
            lines.append(f"  - {name}: ~{est_minutes} min ({count} samples)")

    if job_runs:
        total_execs = sum(r["executions"] for r in job_runs)
        total_secs = sum(r["total_seconds"] or 0 for r in job_runs)
        lines.append("")
        lines.append(
            f"Exact-tracked runs this week: {total_execs} executions, "
            f"{total_secs / 60:.1f} min total wall-clock "
            f"(currently only jobs opted into core.job_tracker -- see its docstring)"
        )

    return "\n".join(lines)


def send_weekly_utilization_report() -> bool:
    text = build_report()
    if text is None:
        return False

    if vacation_gate("normal", "jobs.dev.weekly_utilization_report", text):
        log.info("Vacation mode is on -- weekly utilization report suppressed (logged).")
        return False

    with get_connection() as conn:
        person_id = _person_id(conn, RECIPIENT_NAME)

    if person_id is None:
        log.error("No people row found for %r -- skipped", RECIPIENT_NAME)
        return False

    if send_to_person(person_id, text):
        log.info("Sent weekly utilization report to %s", RECIPIENT_NAME)
        return True

    log.warning("Failed to send weekly utilization report to %s (not onboarded?)", RECIPIENT_NAME)
    return False


def run() -> None:
    with track_job("dev.weekly_utilization_report"):
        send_weekly_utilization_report()


if __name__ == "__main__":
    run()
