#!/usr/bin/env python3
"""
Watson nightly backup to OneDrive via rclone.
Backs up: data/ (the four core DBs snapshotted via sqlite3 .backup, not
copied live), .env, config/, data/chroma/ (live vector index), kb/documents/,
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
from core.vacation import vacation_gate

WATSON_DIR = "/home/billyomes/watson"
REMOTE = "Watson-Backup:Watson-Backup"
LOG = f"{WATSON_DIR}/logs/backup.log"

DB_NAMES = ["watson.db", "congregation.db", "donors.db", "curator.db"]

TARGETS = [
    ("data", "data"),
    ("config", "config"),
    ("data/chroma", "chroma-live"),
    ("kb/documents", "kb/documents"),
]

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
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
        # DBs are in rollback-journal mode (journal_mode=delete), so a
        # concurrent writer holds an exclusive lock that blocks .backup's
        # read lock. Without a busy-timeout the sqlite3 CLI (busy_timeout=0)
        # fails instantly with "database is locked" — which is exactly how
        # watson.db (the busiest DB) failed on 2026-08-08. Give it 30s to
        # wait out the brief sub-second writes instead. (bug_tracker #60)
        result = subprocess.run(
            ["sqlite3", src, "-cmd", ".timeout 30000", f".backup {dst}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log(f"ERROR snapshotting {db_name}: {result.stderr}")
            errors.append(db_name)
            continue
        upload = subprocess.run(
            ["rclone", "copyto", dst, f"{REMOTE}/data/{db_name}"],
            capture_output=True, text=True,
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
    upload = subprocess.run(
        ["rclone", "copyto", dst, f"{REMOTE}/crontab.txt"],
        capture_output=True, text=True,
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

    for local, remote in TARGETS:
        src = f"{WATSON_DIR}/{local}"
        dst = f"{REMOTE}/{remote}"
        log(f"Backing up {local}...")
        args = ["rclone", "copy", src, dst, "--stats-one-line"]
        if local == "data":
            # DBs are snapshotted separately via sqlite3 .backup above —
            # skip the live files here so we never upload a raw copy.
            for db_name in DB_NAMES:
                args += ["--exclude", db_name]
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            log(f"ERROR on {local}: {result.stderr}")
            errors.append(local)
        else:
            log(f"OK: {local}")

    # Backup .env
    result = subprocess.run(
        ["rclone", "copyto", f"{WATSON_DIR}/.env", f"{REMOTE}/.env"],
        capture_output=True, text=True
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
