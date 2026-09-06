# 20 4 * * 0 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/dev/cron_stagger.py >> /home/billyomes/watson/logs/cron_stagger.log 2>&1
"""Weekly cron-minute rebalancer.

Watson's crontab has grown to dozens of `*/N`-style repeating jobs. When
several jobs share the same cadence with no offset, they all launch in the
same instant every cycle (e.g. nine separate `*/5` jobs firing together at
:00/:05/:10... -- found and manually fixed 2026-09-05, suspected contributor
to bug #21's unresolved transient Ollama slowdown). New jobs get added with
a bare `*/N` by default, so the problem re-forms over time.

This re-derives, from scratch on every run, an even minute-offset spread for
every group of same-cadence jobs (grouped by exact cadence N -- all `*/5`
jobs together, all `*/15` jobs together, etc.) and rewrites only the minute
field of those lines. Line order, comments, and every other schedule
(anything with a non-`*` hour/day/month/weekday, or cadence 1 -- i.e. every
minute, nothing to spread) are left untouched.

Deterministic: the offset assignment depends only on the sorted set of
matching command strings, not on today's date or the current offsets. So a
week where nothing was added/removed/re-cadenced produces zero changes, and
this is safe to run manually at any time with --dry-run to preview.
"""
import argparse
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  # noqa: E402
from core.vacation import vacation_gate  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

WATSON_DIR = Path(__file__).resolve().parents[2]
BACKUP_DIR = WATSON_DIR / "data" / "cron_backups"
BACKUP_RETENTION = 8

BARE_RE = re.compile(r"^\*/(\d+)$")
OFFSET_RE = re.compile(r"^(\d+)-59/(\d+)$")
JOB_PATH_RE = re.compile(r"(jobs/[\w./]+\.py)")
JOB_MODULE_RE = re.compile(r"-m (jobs\.[\w.]+)")


def send_telegram(text):
    if vacation_gate("normal", "jobs.dev.cron_stagger", text):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)


def _job_label(command: str) -> str:
    m = JOB_PATH_RE.search(command)
    if m:
        return m.group(1)
    m = JOB_MODULE_RE.search(command)
    if m:
        return m.group(1)
    return command[:50]


def read_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


def write_crontab(lines: list[str]) -> None:
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)


def cadence_of(minute_field: str):
    m = BARE_RE.match(minute_field)
    if m:
        return int(m.group(1))
    m = OFFSET_RE.match(minute_field)
    if m:
        return int(m.group(2))
    return None


def eligible_cadence(line: str):
    """Return the cadence N for a line we're allowed to re-offset, else None."""
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    parts = line.split(maxsplit=5)
    if len(parts) != 6:
        return None
    minute, hour, dom, month, dow, _command = parts
    if hour != "*" or dom != "*" or month != "*" or dow != "*":
        return None
    n = cadence_of(minute)
    if n is None or n <= 1 or n >= 60:
        return None
    return n


def rebalance(lines: list[str]):
    """Return (new_lines, changed) where changed is a list of (cadence, job_label)."""
    groups: dict[int, list[int]] = {}
    for i, line in enumerate(lines):
        n = eligible_cadence(line)
        if n is not None:
            groups.setdefault(n, []).append(i)

    new_lines = list(lines)
    changed = []

    for n, indices in groups.items():
        # Stable regardless of current offsets or crontab line order.
        indices_sorted = sorted(indices, key=lambda i: lines[i].split(maxsplit=5)[5])
        for slot, i in enumerate(indices_sorted):
            offset = slot % n
            parts = lines[i].split(maxsplit=5)
            new_minute = f"*/{n}" if offset == 0 else f"{offset}-59/{n}"
            if new_minute != parts[0]:
                parts[0] = new_minute
                new_lines[i] = " ".join(parts[:5]) + " " + parts[5]
                changed.append((n, _job_label(parts[5])))

    return new_lines, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="preview changes, don't install")
    args = ap.parse_args()

    lines = read_crontab()
    new_lines, changed = rebalance(lines)

    if not changed:
        log.info("no changes needed -- crontab already well-spread")
        return

    log.info("%d job(s) to re-offset:", len(changed))
    for n, label in changed:
        log.info("  */%d  %s", n, label)

    if args.dry_run:
        log.info("--dry-run: not installing")
        return

    if len(new_lines) != len(lines) or any(
        old.strip() and not new for old, new in zip(lines, new_lines)
    ):
        log.error("sanity check failed on the rebuilt crontab -- aborting, nothing installed")
        return

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"crontab_{stamp}.txt"
    backup_path.write_text("\n".join(lines) + "\n")

    write_crontab(new_lines)

    backups = sorted(BACKUP_DIR.glob("crontab_*.txt"))
    for old in backups[:-BACKUP_RETENTION]:
        old.unlink()

    log.info("installed. backup: %s", backup_path)
    summary = "\n".join(f"  */{n} {label}" for n, label in changed[:10])
    more = f"\n  ...and {len(changed) - 10} more" if len(changed) > 10 else ""
    send_telegram(f"cron_stagger: re-spread {len(changed)} job(s)\n{summary}{more}")


if __name__ == "__main__":
    main()
