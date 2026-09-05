#!/usr/bin/env python3
"""
Watson nightly backup to OneDrive via rclone.
Backs up: data/ (the four core DBs snapshotted via sqlite3 .backup, not
copied live), .env, config/, data/chroma/ (live vector index), kb/documents/,
~/.claude/projects (Claude Code's own session memory, added 2026-08-30),
a crontab snapshot

Deliberately does NOT back up ~/.ssh or ~/.config/rclone/rclone.conf — those
are local-only (jobs/backup_local.py) since rclone.conf holds the credential
to OneDrive itself.
"""
import subprocess
import os
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
REMOTE = "Watson-Backup:Watson-Backup"
LOG = f"{WATSON_DIR}/logs/backup.log"

DB_NAMES = ["watson.db", "congregation.db", "donors.db", "curator.db"]

# (source path, remote path under REMOTE)
TARGETS = [
    (f"{WATSON_DIR}/data", "data"),
    (f"{WATSON_DIR}/config", "config"),
    (f"{WATSON_DIR}/data/chroma", "chroma-live"),
    (f"{WATSON_DIR}/kb/documents", "kb/documents"),
    (f"{HOME_DIR}/.claude/projects", "claude-projects"),
]

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with open(LOG, "a") as f:
        f.write(line + "\n")

def _send_telegram(text):
    if vacation_gate("system_failure", "jobs.backup", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
        timeout=10,
    )

def _backup_dbs(tmp_dir, errors):
    for db_name in DB_NAMES:
        src = f"{WATSON_DIR}/data/{db_name}"
        dst = f"{tmp_dir}/{db_name}"
        log(f"Snapshotting {db_name}...")
        # Two complementary layers of lock resilience (bug_tracker #60): the
        # passive `.timeout 30000` makes the sqlite3 CLI (default busy_timeout=0)
        # wait up to 30s within one attempt for a concurrent writer's lock to
        # clear, and run_with_retry re-invokes the whole command with backoff if
        # a full 30s wait still ends in "database is locked", up to the ~10 min
        # budget.
        result = run_with_retry(
            ["sqlite3", src, "-cmd", ".timeout 30000", f".backup {dst}"],
            budget_seconds=RETRY_BUDGET_SECONDS,
            description=f"sqlite3 .backup {db_name}",
            log=log,
        )
        if result.returncode != 0:
            log(f"ERROR snapshotting {db_name}: {result.stderr}")
            errors.append(db_name)
            continue
        upload = run_with_retry(
            ["rclone", "copyto", dst, f"{REMOTE}/data/{db_name}"],
            budget_seconds=RETRY_BUDGET_SECONDS,
            description=f"rclone copyto {db_name}",
            log=log,
        )
        if upload.returncode != 0:
            log(f"ERROR uploading {db_name}: {upload.stderr}")
            errors.append(db_name)
        else:
            log(f"OK: {db_name}")

def _backup_crontab(tmp_dir, errors):
    """Best-effort — a missing crontab shouldn't block the rest of the backup."""
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log(f"WARNING: crontab -l failed, skipping crontab snapshot: {result.stderr.strip()}")
        return

    dst = f"{tmp_dir}/crontab.txt"
    with open(dst, "w") as f:
        f.write(result.stdout)

    log("Backing up crontab...")
    upload = run_with_retry(
        ["rclone", "copyto", dst, f"{REMOTE}/crontab.txt"],
        budget_seconds=RETRY_BUDGET_SECONDS,
        description="rclone copyto crontab.txt",
        log=log,
    )
    if upload.returncode != 0:
        log(f"ERROR uploading crontab: {upload.stderr}")
        errors.append("crontab")
    else:
        log("OK: crontab")


def run_backup():
    log("=== Watson backup started ===")
    errors = []

    with tempfile.TemporaryDirectory(prefix="watson-backup-") as tmp_dir:
        _backup_dbs(tmp_dir, errors)
        _backup_crontab(tmp_dir, errors)

    for src, remote in TARGETS:
        dst = f"{REMOTE}/{remote}"
        log(f"Backing up {src}...")
        args = ["rclone", "copy", src, dst, "--stats-one-line"]
        if src == f"{WATSON_DIR}/data":
            # DBs are snapshotted separately via sqlite3 .backup above —
            # skip the live files here so we never upload a raw copy.
            for db_name in DB_NAMES:
                args += ["--exclude", db_name]
        result = run_with_retry(
            args,
            budget_seconds=RETRY_BUDGET_SECONDS,
            description=f"rclone copy {src}",
            log=log,
        )
        if result.returncode != 0:
            log(f"ERROR on {src}: {result.stderr}")
            errors.append(src)
        else:
            log(f"OK: {src}")

    # Backup .env
    result = run_with_retry(
        ["rclone", "copyto", f"{WATSON_DIR}/.env", f"{REMOTE}/.env"],
        budget_seconds=RETRY_BUDGET_SECONDS,
        description="rclone copyto .env",
        log=log,
    )
    if result.returncode != 0:
        log(f"ERROR on .env: {result.stderr}")
        errors.append(".env")
    else:
        log("OK: .env")

    if errors:
        log(f"=== Backup completed WITH ERRORS: {errors} ===")
        _send_telegram(
            f"❌ OneDrive backup failed — check rclone auth/logs\n\nFailed targets: {', '.join(errors)}"
        )
    else:
        log("=== Backup completed successfully ===")

if __name__ == "__main__":
    run_backup()
