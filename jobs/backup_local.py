#!/usr/bin/env python3
"""
Watson nightly local backup to the external 2TB HDD via restic.
Fast/versioned local recovery leg — NOT the offsite disaster leg (that's
jobs/backup.py / OneDrive). Runs independently, full scope, from live source
paths.

Backs up:
  - Consistency-safe snapshots of the four DBs: data/watson.db,
    data/congregation.db, data/donors.db, data/curator.db
  - The whole data/ tree (includes data/chroma), config/, .env, memory/,
    kb/documents — full parity with the OneDrive leg's data coverage,
    which this leg previously only partially matched (data/chroma only)
  - ~/.ssh, ~/.config/rclone/rclone.conf, a crontab snapshot
  - CODE_REPO_SOURCES: every actively-developed byomes repo on this box
    (2026-08-22 addition) — full working trees, including uncommitted
    changes and .git history, so the machine can be recreated from this
    drive alone even if something was never pushed to GitHub. Excludes
    node_modules/venv/.next/dist/build/__pycache__ (regenerate via a
    normal install step on restore, not worth the backup size/time).

~/.ssh and rclone.conf are intentionally local-only (not part of the
OneDrive leg, jobs/backup.py) — rclone.conf holds the credential to
OneDrive itself, so uploading it to OneDrive in plaintext would be
circular and risky. The crontab snapshot goes to both legs.

Retention: 14 daily, 8 weekly, 6 monthly (restic forget --prune).
"""
import os
import subprocess
import tempfile
from datetime import datetime

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.retry import run_with_retry
from core.vacation import vacation_gate

# Retry budget shared by every retry-eligible subprocess op: ~10 min of real
# elapsed time, exponential backoff 5s → 60s. See core/retry.py.
RETRY_BUDGET_SECONDS = 600

WATSON_DIR = "/home/billyomes/watson"
HOME_DIR = os.path.expanduser("~")
BACKUP_MOUNT = "/mnt/family-storage"
# This drive is dual-purpose: family NAS storage + Watson's local backups (and
# possibly other Watson needs later). RESTIC_REPO deliberately lives inside a
# chmod 700 `watson/` subfolder (owned by billyomes only) rather than at the
# mount root, so nothing else on the family share can read or write it.
RESTIC_REPO = f"{BACKUP_MOUNT}/watson/restic-repo"
LOG = f"{WATSON_DIR}/logs/backup_local.log"

RESTIC_PASSWORD = os.getenv("RESTIC_PASSWORD")

DB_NAMES = ["watson.db", "congregation.db", "donors.db", "curator.db"]

DIR_SOURCES = [
    f"{WATSON_DIR}/data",
    f"{WATSON_DIR}/config",
    f"{WATSON_DIR}/.env",
    f"{WATSON_DIR}/memory",
    f"{WATSON_DIR}/kb/documents",
    f"{HOME_DIR}/.ssh",
    f"{HOME_DIR}/.config/rclone/rclone.conf",
]

# Every actively-developed byomes repo on this box — added 2026-08-22 so a
# dead Beelink doesn't mean losing anything that was never pushed to GitHub
# (uncommitted work, unpushed commits/branches, local-only worktrees).
# Deliberately excludes third-party clones (e.g. gutendex) and known-stale
# artifacts (old git-filter-repo experiment dirs, aider-test).
CODE_REPO_SOURCES = [
    f"{HOME_DIR}/watson",
    f"{HOME_DIR}/wcky",
    f"{HOME_DIR}/watson-admin",
    f"{HOME_DIR}/watson-ui",
    f"{HOME_DIR}/watson-docs-sync",
    f"{HOME_DIR}/comms-desk",
    f"{HOME_DIR}/comms-assets",
    f"{HOME_DIR}/curator",
    f"{HOME_DIR}/bodyrec",
    f"{HOME_DIR}/fms",
]

# Regenerable via a normal install step on restore (npm/pip install, next
# build) — excluded to keep the nightly backup fast and small. Matched by
# restic against any path component, at any depth, in any repo above.
CODE_EXCLUDES = [
    "node_modules", "venv", ".venv", ".next", "dist", "build", "__pycache__",
]


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def _send_telegram(text):
    if vacation_gate("system_failure", "jobs.backup_local", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
        timeout=10,
    )


def _restic_env():
    env = os.environ.copy()
    env["RESTIC_PASSWORD"] = RESTIC_PASSWORD or ""
    return env


def _snapshot_db(db_name, tmp_dir):
    src = f"{WATSON_DIR}/data/{db_name}"
    dst = f"{tmp_dir}/{db_name}"
    # Two complementary layers of lock resilience (see report / bug_tracker #60):
    #   1. Passive: `.timeout 30000` makes the sqlite3 CLI (whose default
    #      busy_timeout is 0) wait up to 30s *within a single attempt* for a
    #      concurrent writer's lock to clear — cheap, no process churn for the
    #      sub-second writes that cause almost all contention.
    #   2. Active: run_with_retry re-invokes the whole command with backoff if a
    #      full 30s passive wait still ends in "database is locked", up to the
    #      ~10 min budget. Covers the rare case where a lock outlives one wait.
    result = run_with_retry(
        ["sqlite3", src, "-cmd", ".timeout 30000", f".backup {dst}"],
        budget_seconds=RETRY_BUDGET_SECONDS,
        description=f"sqlite3 .backup {db_name}",
        log=log,
    )
    return dst, result


def _snapshot_crontab(tmp_dir):
    """Best-effort — a missing crontab shouldn't block the rest of the backup."""
    dst = f"{tmp_dir}/crontab.txt"
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log(f"WARNING: crontab -l failed, skipping crontab snapshot: {result.stderr.strip()}")
        return None
    with open(dst, "w") as f:
        f.write(result.stdout)
    return dst


def run_backup():
    log("=== Watson local backup started ===")

    if not os.path.ismount(BACKUP_MOUNT):
        msg = f"Backup HDD not mounted at {BACKUP_MOUNT} — aborting local backup"
        log(f"ERROR: {msg}")
        _send_telegram(f"❌ {msg}")
        return

    if not RESTIC_PASSWORD:
        msg = "RESTIC_PASSWORD not set in .env — aborting local backup"
        log(f"ERROR: {msg}")
        _send_telegram(f"❌ {msg}")
        return

    errors = []

    with tempfile.TemporaryDirectory(prefix="watson-backup-") as tmp_dir:
        db_paths = []
        for db_name in DB_NAMES:
            log(f"Snapshotting {db_name}...")
            dst, result = _snapshot_db(db_name, tmp_dir)
            if result.returncode != 0:
                log(f"ERROR on {db_name} snapshot: {result.stderr}")
                errors.append(db_name)
            else:
                db_paths.append(dst)
                log(f"OK: {db_name} snapshot")

        log("Snapshotting crontab...")
        crontab_path = _snapshot_crontab(tmp_dir)
        if crontab_path:
            db_paths.append(crontab_path)
            log("OK: crontab snapshot")

        sources = db_paths + DIR_SOURCES + CODE_REPO_SOURCES
        exclude_args = [arg for pattern in CODE_EXCLUDES for arg in ("--exclude", pattern)]

        log("Running restic backup...")
        result = run_with_retry(
            ["restic", "-r", RESTIC_REPO, "backup"] + exclude_args + sources,
            budget_seconds=RETRY_BUDGET_SECONDS,
            description="restic backup",
            log=log,
            env=_restic_env(),
        )
        if result.returncode != 0:
            log(f"ERROR on restic backup: {result.stderr}")
            errors.append("restic-backup")
        else:
            log("OK: restic backup")

    if "restic-backup" not in errors:
        log("Running restic forget --prune...")
        result = run_with_retry(
            [
                "restic", "-r", RESTIC_REPO, "forget", "--prune",
                "--keep-daily", "14",
                "--keep-weekly", "8",
                "--keep-monthly", "6",
            ],
            budget_seconds=RETRY_BUDGET_SECONDS,
            description="restic forget --prune",
            log=log,
            env=_restic_env(),
        )
        if result.returncode != 0:
            log(f"ERROR on restic forget --prune: {result.stderr}")
            errors.append("restic-forget-prune")
        else:
            log("OK: restic forget --prune")

    if errors:
        log(f"=== Local backup completed WITH ERRORS: {errors} ===")
        _send_telegram(
            f"❌ Local (restic) backup failed — check logs\n\nFailed steps: {', '.join(errors)}"
        )
    else:
        log("=== Local backup completed successfully ===")


if __name__ == "__main__":
    run_backup()
