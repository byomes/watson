#!/usr/bin/env python3
"""
Nightly Telegram confirmation that both Watson backup legs actually produced
fresh, verifiable output overnight — not just that the job scripts exited 0.

Runs at 3:30am, after both legs have had time to finish (local/restic 2:30am,
OneDrive 3am; typical runs complete by ~2:31am and ~3:03am respectively).

Checks real backup artifacts rather than trusting each job's own log:
  - Local/restic leg: does `restic snapshots --latest 1` show a snapshot
    taken today?
  - OneDrive leg: does `.env` on the remote have today's mtime?
On a problem, falls back to pulling ERROR lines from tonight's run out of
each leg's own log, so the Telegram message says what actually broke.
"""
import json
import os
import subprocess
from datetime import datetime

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate

WATSON_DIR = "/home/billyomes/watson"
HOME_DIR = os.path.expanduser("~")
RESTIC_REPO = "/mnt/family-storage/watson/restic-repo"
REMOTE = "Watson-Backup:Watson-Backup"

CHECK_TIMEOUT = 60


def _send_telegram(text, priority):
    if vacation_gate(priority, "jobs.backup_status_report", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
        timeout=10,
    )


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _check_local_leg():
    """Returns (ok, detail) for the restic/local leg."""
    password = os.getenv("RESTIC_PASSWORD")
    if not password:
        return False, "RESTIC_PASSWORD not set in .env"

    env = os.environ.copy()
    env["RESTIC_PASSWORD"] = password
    try:
        result = subprocess.run(
            ["restic", "-r", RESTIC_REPO, "snapshots", "--latest", "1", "--json"],
            capture_output=True, text=True, timeout=CHECK_TIMEOUT, env=env,
        )
    except Exception as e:
        return False, f"restic check failed to run: {e}"

    if result.returncode != 0:
        return False, f"restic snapshots failed: {result.stderr.strip()[:200]}"

    try:
        snaps = json.loads(result.stdout)
    except Exception:
        return False, "restic returned unparseable snapshot list"

    if not snaps:
        return False, "restic repo has no snapshots at all"

    latest = snaps[-1]
    snap_date = latest["time"][:10]
    if snap_date != _today():
        return False, f"latest snapshot is from {snap_date}, not today"

    return True, f"snapshot {latest['short_id']} at {latest['time'][11:16]}"


def _check_onedrive_leg():
    """Returns (ok, detail) for the OneDrive leg."""
    try:
        result = subprocess.run(
            ["rclone", "lsl", f"{REMOTE}/.env"],
            capture_output=True, text=True, timeout=CHECK_TIMEOUT,
        )
    except Exception as e:
        return False, f"rclone check failed to run: {e}"

    if result.returncode != 0 or not result.stdout.strip():
        return False, f"rclone lsl .env failed: {result.stderr.strip()[:200]}"

    # rclone lsl format: "<size> <date> <time> <path>"
    parts = result.stdout.strip().split()
    mod_date = parts[1] if len(parts) > 1 else ""
    if mod_date != _today():
        return False, f".env on OneDrive last updated {mod_date}, not today"

    return True, f".env updated today ({parts[2][:5] if len(parts) > 2 else ''})"


def _tonight_errors(log_path, start_marker):
    """Best-effort: pull ERROR lines from the most recent run block in a log."""
    try:
        with open(log_path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return None

    last_start = None
    for i, line in enumerate(lines):
        if start_marker in line:
            last_start = i
    if last_start is None:
        return None

    errors = [line.strip() for line in lines[last_start:] if "ERROR" in line]
    return errors or None


def run():
    local_ok, local_detail = _check_local_leg()
    onedrive_ok, onedrive_detail = _check_onedrive_leg()

    lines = []
    if local_ok and onedrive_ok:
        lines.append("✅ Watson backups OK overnight")
        lines.append(f"• Local (restic): {local_detail}")
        lines.append(f"• OneDrive: {onedrive_detail}")
        priority = "normal"
    else:
        lines.append("⚠️ Watson backup problem overnight")

        status = "OK" if local_ok else "FAILED"
        lines.append(f"• Local (restic): {status} — {local_detail}")
        if not local_ok:
            errs = _tonight_errors(
                f"{WATSON_DIR}/logs/backup_local.log", "Watson local backup started"
            )
            if errs:
                lines.append(f"  log: {'; '.join(errs[:3])}")

        status = "OK" if onedrive_ok else "FAILED"
        lines.append(f"• OneDrive: {status} — {onedrive_detail}")
        if not onedrive_ok:
            errs = _tonight_errors(
                f"{WATSON_DIR}/logs/backup.log", "Watson backup started"
            )
            if errs:
                lines.append(f"  log: {'; '.join(errs[:3])}")

        priority = "system_failure"

    lines.append("- Watson")
    _send_telegram("\n".join(lines), priority)


if __name__ == "__main__":
    run()
