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

Everything uploaded is gpg-symmetric-encrypted first (AES256, WATSON_BACKUP_
GPG_PASSPHRASE in .env) — security review 2026-09-16 found this leg was
uploading raw plaintext, including PII/financial DBs (congregation.db,
donors.db, servantcare.db, curator.db, trading.db) and a pile of stray
`*.bak-*`/`*.backup-*` snapshots of those DBs that had accumulated under
data/ over time and were never excluded from the generic data/ copy. Rather
than chase individual filenames, every file under every backed-up tree is
now encrypted recursively (uploaded as `<original name>.gpg`), so new stray
files are covered automatically. The local leg (jobs/backup_local.py, restic)
was already encrypted at rest by design — this only closes the OneDrive gap.

To decrypt a downloaded file: gpg --batch --yes --passphrase-file <(printf
'%s' "$WATSON_BACKUP_GPG_PASSPHRASE") --decrypt -o <output> <file>.gpg
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

GPG_PASSPHRASE = os.getenv("WATSON_BACKUP_GPG_PASSPHRASE")

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

def _gpg_encrypt(src, dst):
    """gpg-symmetric-encrypt src -> dst (dst's parent dir must already exist)."""
    return subprocess.run(
        ["gpg", "--batch", "--yes", "--pinentry-mode", "loopback",
         "--passphrase-fd", "0", "--symmetric", "--cipher-algo", "AES256",
         "-o", dst, src],
        input=GPG_PASSPHRASE, capture_output=True, text=True,
    )

def _encrypt_tree(src_dir, tmp_root, exclude_names=(), exclude_dirs=()):
    """Recursively gpg-encrypt every file under src_dir into a mirrored
    directory under tmp_root, each file suffixed .gpg. Returns the mirror
    root (to be rclone-copied in place of src_dir) and a list of files that
    failed to encrypt (non-fatal — caller decides whether that's an error).

    exclude_dirs prunes whole subdirectories (matched by basename, any
    depth) from the walk entirely."""
    enc_root = os.path.join(tmp_root, "enc-" + os.path.basename(src_dir.rstrip("/")))
    failed = []
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        rel = os.path.relpath(dirpath, src_dir)
        for fname in filenames:
            if fname in exclude_names:
                continue
            src_file = os.path.join(dirpath, fname)
            dst_dir = os.path.join(enc_root, rel) if rel != "." else enc_root
            os.makedirs(dst_dir, exist_ok=True)
            dst_file = os.path.join(dst_dir, fname + ".gpg")
            result = _gpg_encrypt(src_file, dst_file)
            if result.returncode != 0:
                log(f"ERROR gpg-encrypting {src_file}: {result.stderr.strip()}")
                failed.append(src_file)
    return enc_root, failed

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
        enc = _gpg_encrypt(dst, dst + ".gpg")
        if enc.returncode != 0:
            log(f"ERROR gpg-encrypting {db_name}: {enc.stderr.strip()}")
            errors.append(db_name)
            continue
        upload = run_with_retry(
            ["rclone", "copyto", dst + ".gpg", f"{REMOTE}/data/{db_name}.gpg"],
            budget_seconds=RETRY_BUDGET_SECONDS,
            description=f"rclone copyto {db_name}.gpg",
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

    enc = _gpg_encrypt(dst, dst + ".gpg")
    if enc.returncode != 0:
        log(f"ERROR gpg-encrypting crontab: {enc.stderr.strip()}")
        errors.append("crontab")
        return

    log("Backing up crontab...")
    upload = run_with_retry(
        ["rclone", "copyto", dst + ".gpg", f"{REMOTE}/crontab.txt.gpg"],
        budget_seconds=RETRY_BUDGET_SECONDS,
        description="rclone copyto crontab.txt.gpg",
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

    if not GPG_PASSPHRASE:
        log("ERROR: WATSON_BACKUP_GPG_PASSPHRASE not set in .env — aborting backup rather than uploading plaintext")
        _send_telegram("❌ OneDrive backup aborted: WATSON_BACKUP_GPG_PASSPHRASE not set")
        return

    with tempfile.TemporaryDirectory(prefix="watson-backup-") as tmp_dir:
        _backup_dbs(tmp_dir, errors)
        _backup_crontab(tmp_dir, errors)

        for src, remote in TARGETS:
            dst = f"{REMOTE}/{remote}"
            log(f"Encrypting + backing up {src}...")
            exclude = set(DB_NAMES) if src == f"{WATSON_DIR}/data" else set()
            # sermonshots_clips holds raw pre-launch Church Social clip video
            # (multi-GB .mp4s) — encrypting/uploading it nightly pushed this
            # step from ~2min to 40-60min, which raced backup_status_report.py's
            # 3:30am freshness check into false OneDrive-FAILED alerts
            # (2026-09-19). Not disaster-recovery data; excluded from the
            # OneDrive leg. Still covered by the local restic leg.
            exclude_dirs = {"sermonshots_clips"} if src == f"{WATSON_DIR}/data" else set()
            enc_root, failed = _encrypt_tree(src, tmp_dir, exclude_names=exclude, exclude_dirs=exclude_dirs)
            if failed:
                errors.append(src)
            args = ["rclone", "copy", enc_root, dst, "--stats-one-line"]
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
        env_enc = f"{tmp_dir}/.env.gpg"
        enc = _gpg_encrypt(f"{WATSON_DIR}/.env", env_enc)
        if enc.returncode != 0:
            log(f"ERROR gpg-encrypting .env: {enc.stderr.strip()}")
            errors.append(".env")
        else:
            result = run_with_retry(
                ["rclone", "copyto", env_enc, f"{REMOTE}/.env.gpg"],
                budget_seconds=RETRY_BUDGET_SECONDS,
                description="rclone copyto .env.gpg",
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
            f"❌ OneDrive backup failed: check rclone auth/logs\n\nFailed targets: {', '.join(errors)}"
        )
    else:
        log("=== Backup completed successfully ===")

if __name__ == "__main__":
    run_backup()
