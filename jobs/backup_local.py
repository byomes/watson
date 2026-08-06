#!/usr/bin/env python3
"""
Watson nightly local backup to the external 2TB HDD via restic.
Fast/versioned local recovery leg — NOT the offsite disaster leg (that's
jobs/backup.py / OneDrive). Runs independently, full scope, from live source
paths.

Backs up (consistency-safe snapshots for the four DBs): data/watson.db,
data/congregation.db, data/donors.db, data/curator.db, data/chroma/,
config/, .env, memory/

Retention: 14 daily, 8 weekly, 6 monthly (restic forget --prune).
"""
import os
import subprocess
import tempfile
from datetime import datetime

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate

WATSON_DIR = "/home/billyomes/watson"
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
    f"{WATSON_DIR}/data/chroma",
    f"{WATSON_DIR}/config",
    f"{WATSON_DIR}/.env",
    f"{WATSON_DIR}/memory",
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
    result = subprocess.run(
        ["sqlite3", src, f".backup {dst}"],
        capture_output=True, text=True,
    )
    return dst, result


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

        sources = db_paths + DIR_SOURCES

        log("Running restic backup...")
        result = subprocess.run(
            ["restic", "-r", RESTIC_REPO, "backup"] + sources,
            capture_output=True, text=True, env=_restic_env(),
        )
        if result.returncode != 0:
            log(f"ERROR on restic backup: {result.stderr}")
            errors.append("restic-backup")
        else:
            log("OK: restic backup")

    if "restic-backup" not in errors:
        log("Running restic forget --prune...")
        result = subprocess.run(
            [
                "restic", "-r", RESTIC_REPO, "forget", "--prune",
                "--keep-daily", "14",
                "--keep-weekly", "8",
                "--keep-monthly", "6",
            ],
            capture_output=True, text=True, env=_restic_env(),
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
