# Cron: 0 2 * * * (runs at 2am daily)
"""
archive_transcripts.py — Move transcripts older than 30 days from kb/transcripts/ to kb/documents/.

After archiving, commits and pushes the changes to git and sends a Telegram confirmation.

Usage:
  python jobs/kb/archive_transcripts.py
"""

import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TRANSCRIPTS_DIR = REPO_ROOT / "kb" / "transcripts"
DOCUMENTS_DIR = REPO_ROOT / "kb" / "documents"
AGE_THRESHOLD_DAYS = 30

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.kb.archive_transcripts", text):
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


def archive_transcripts() -> int:
    if not TRANSCRIPTS_DIR.exists():
        log.info("kb/transcripts/ does not exist — nothing to archive")
        return 0

    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=AGE_THRESHOLD_DAYS)
    moved = 0

    for path in sorted(TRANSCRIPTS_DIR.iterdir()):
        if not path.is_file():
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            dest = DOCUMENTS_DIR / path.name
            shutil.move(str(path), dest)
            log.info("Archived: %s -> %s", path.name, dest)
            moved += 1

    return moved


def git_commit_and_push():
    subprocess.run(
        ["git", "add", "-A"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "archive: move aged transcripts to kb/documents"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=REPO_ROOT,
        check=True,
    )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    moved = archive_transcripts()

    if moved == 0:
        log.info("No files to archive")
        sys.exit(0)

    log.info("Moved %d file(s) to kb/documents/", moved)
    git_commit_and_push()
    log.info("Committed and pushed %d archived transcript(s)", moved)
    _send_telegram(f"📂 Transcript archive: {moved} file(s) moved to kb/documents/ and committed.")


if __name__ == "__main__":
    main()
