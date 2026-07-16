#!/usr/bin/env python3
"""
Watson nightly backup to OneDrive via rclone.
Backs up: data/, .env, config/, kb/chroma/, kb/documents/
"""
import subprocess
import os
from datetime import datetime

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate

WATSON_DIR = "/home/billyomes/watson"
REMOTE = "Watson-Backup:Watson-Backup"
LOG = f"{WATSON_DIR}/logs/backup.log"

TARGETS = [
    ("data", "data"),
    ("config", "config"),
    ("kb/chroma", "kb/chroma"),
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

def run_backup():
    log("=== Watson backup started ===")
    errors = []

    for local, remote in TARGETS:
        src = f"{WATSON_DIR}/{local}"
        dst = f"{REMOTE}/{remote}"
        log(f"Backing up {local}...")
        result = subprocess.run(
            ["rclone", "copy", src, dst, "--stats-one-line"],
            capture_output=True, text=True
        )
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
