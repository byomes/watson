# Cron: 0 2 * * * (runs at 2am daily — same slot vacated by retiring archive_transcripts.py)
"""
sync_and_index.py — Same-day sermon transcript sync and KB indexing.

Closes the 30-day gap between a transcript landing in kb/transcripts/
(pushed from FMSPC via jobs/generate.py) and it becoming searchable:

  1. git pull the Watson repo (fast-forward only) to receive anything
     pushed since the last run.
  2. Move every file currently in kb/transcripts/ into kb/documents/
     (no age threshold — same day, not 30 days).
  3. Commit + push that move.
  4. Incrementally index the new files into the "sermons" ChromaDB
     collection via jobs.build_kb.ingest_dir.
  5. Send a Telegram summary.

Pull safety: fetch + `pull --ff-only` only. Never merges, rebases, or
resets. Any failure (diverged history, conflicting local changes, network)
aborts the run with a Telegram alert and leaves the working tree untouched.

Supersedes jobs/kb/archive_transcripts.py's reason for existing — see the
retirement note at the top of that file.

Usage:
  python jobs/kb/sync_and_index.py
"""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate
from jobs.build_kb import ingest_dir

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TRANSCRIPTS_DIR = REPO_ROOT / "kb" / "transcripts"
DOCUMENTS_DIR = REPO_ROOT / "kb" / "documents"
COLLECTION_NAME = "sermons"


def _send_telegram(text: str, priority: str = "normal") -> None:
    if vacation_gate(priority, "jobs.kb.sync_and_index", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)


def _git(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=REPO_ROOT, capture_output=True, text=True)


def pull_repo() -> tuple:
    """Fast-forward-only pull. Never merges, rebases, or resets.

    Returns (ok, message). On failure the working tree is left exactly
    as it was — no destructive recovery is attempted here; it needs a
    human look.
    """
    fetch = _git(["fetch", "origin"])
    if fetch.returncode != 0:
        return False, f"git fetch failed:\n{fetch.stderr.strip()}"

    pull = _git(["pull", "--ff-only", "origin", "main"])
    if pull.returncode != 0:
        return False, f"git pull --ff-only failed (needs manual resolution on the Beelink):\n{pull.stderr.strip()}"

    return True, pull.stdout.strip()


def move_new_transcripts() -> list:
    if not TRANSCRIPTS_DIR.exists():
        return []
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

    moved = []
    for path in sorted(TRANSCRIPTS_DIR.iterdir()):
        if not path.is_file():
            continue
        dest = DOCUMENTS_DIR / path.name
        shutil.move(str(path), dest)
        log.info("Synced: %s -> kb/documents/", path.name)
        moved.append(path.name)
    return moved


def commit_and_push(moved_count: int) -> tuple:
    # Scoped `git add` (not -A) — never sweep up unrelated in-progress
    # changes elsewhere in the working tree.
    add = _git(["add", "kb/documents", "kb/transcripts"])
    if add.returncode != 0:
        return False, f"git add failed:\n{add.stderr.strip()}"

    commit = _git(["commit", "-m", f"kb: sync {moved_count} transcript(s) to kb/documents (same-day)"])
    if commit.returncode != 0:
        return False, f"git commit failed:\n{commit.stderr.strip()}"

    push = _git(["push", "origin", "main"])
    if push.returncode != 0:
        return False, f"git push failed (committed locally, not pushed):\n{push.stderr.strip()}"

    return True, ""


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    pull_ok, pull_msg = pull_repo()
    if not pull_ok:
        log.error(pull_msg)
        _send_telegram(
            f"🔴 KB sync: git pull failed, needs manual resolution.\n\n{pull_msg[:500]}",
            priority="system_failure",
        )
        sys.exit(1)
    log.info("Pull ok: %s", pull_msg or "already up to date")

    moved = move_new_transcripts()
    if not moved:
        log.info("No new transcripts to sync")
        _send_telegram("📂 KB sync: nothing new.")
        sys.exit(0)

    log.info("Moved %d file(s) to kb/documents/", len(moved))

    push_ok, push_msg = commit_and_push(len(moved))
    if not push_ok:
        log.error(push_msg)
        _send_telegram(
            f"🔴 KB sync: moved {len(moved)} transcript(s) locally but git commit/push failed.\n\n{push_msg[:500]}",
            priority="system_failure",
        )
        sys.exit(1)

    try:
        added_chunks = ingest_dir(DOCUMENTS_DIR, COLLECTION_NAME)
    except Exception as exc:
        log.exception("KB indexing failed")
        _send_telegram(
            f"⚠️ KB sync: {len(moved)} transcript(s) moved and pushed, but indexing failed: {exc}",
            priority="system_failure",
        )
        sys.exit(1)

    log.info("Indexed %d new chunk(s)", added_chunks)
    _send_telegram(
        f"📂 KB sync: {len(moved)} new transcript(s) synced and indexed "
        f"({added_chunks} new chunks in '{COLLECTION_NAME}')."
    )


if __name__ == "__main__":
    main()
