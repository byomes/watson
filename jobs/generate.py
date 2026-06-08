"""
generate.py — Archive clean transcript to two destinations:
  1. Git-tracked KB in Watson repo (kb/transcripts/) → pushed to GitHub
     so the raw URL works on mobile for claude.ai blog drafting.
  2. Local knowledge base inbox (KB_LOCAL_DIR from .env, e.g. F:\\Knowledge_Database\\_inbox)
     for the local ingest pipeline. No Git involvement.

Then notify via Telegram with the raw GitHub link.

No API key required. Claude drafting is a manual human-in-the-loop step.

Usage:
  python jobs/generate.py <clean_transcript_path> <sermon_slug>

  sermon_slug: used for the KB filename, e.g. "2026-05-11-kingdom-citizenship"
              or "05-10-2026-kingdom-citizenship" — date prefix is normalized.
"""

import logging
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Destination 1: Git-tracked KB inside Watson repo — always pushed to GitHub
KB_GIT_DIR = REPO_ROOT / "kb" / "transcripts"

# Destination 2: Local knowledge base inbox on F: drive (or wherever .env points)
# Set KB_LOCAL_DIR in .env, e.g. KB_LOCAL_DIR=F:\Knowledge_Database\_inbox
# Falls back to same as Git KB if not set.
KB_LOCAL_DIR = Path(os.getenv("KB_LOCAL_DIR", str(KB_GIT_DIR)))

# GitHub raw URL base — update if repo name changes
GITHUB_RAW_BASE = os.getenv(
    "GITHUB_RAW_BASE",
    "https://raw.githubusercontent.com/byomes/watson/main/kb/transcripts"
)

# Watson Telegram bot
WATSON_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
WATSON_CHAT_ID   = os.getenv("WATSON_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID")

# Matches any leading date: YYYY-MM-DD or MM-DD-YYYY
_DATE_PREFIX_RE = re.compile(r"^\d{2,4}-\d{2}-\d{2,4}-?")


def _strip_date_prefix(slug: str) -> str:
    """Remove any leading date pattern from a slug."""
    return _DATE_PREFIX_RE.sub("", slug).strip("-")


# --- Git helpers ------------------------------------------------------

def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def _commit_and_push(file_path: Path, commit_message: str) -> None:
    _git(["add", str(file_path)], cwd=REPO_ROOT)
    _git(["commit", "-m", commit_message], cwd=REPO_ROOT)
    _git(["push"], cwd=REPO_ROOT)
    log.info("Committed and pushed: %s", file_path.name)


# --- Telegram ---------------------------------------------------------

def _telegram_notify(raw_url: str, title: str) -> None:
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.warning("Telegram not configured — skipping notification")
        return

    text = (
        f"📄 <b>New transcript archived</b>\n\n"
        f"<b>{title}</b>\n\n"
        f"Raw URL (copy and paste into claude.ai):\n"
        f"<code>{raw_url}</code>\n\n"
        f"Paste into claude.ai with:\n"
        f"<i>\"Draft a blog article from this transcript.\"</i>"
    )

    reply_markup = {
        "inline_keyboard": [[
            {"text": "📂 Open Transcript", "url": raw_url}
        ]]
    }

    url = f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id":      WATSON_CHAT_ID,
            "text":         text,
            "parse_mode":   "HTML",
            "reply_markup": reply_markup,
        },
        timeout=10,
    )
    resp.raise_for_status()
    log.info("Telegram notification sent")


# --- Main job ---------------------------------------------------------

def generate(clean_path: Path, sermon_slug: str) -> None:
    clean_text = clean_path.read_text(encoding="utf-8")
    today      = date.today().strftime("%Y-%m-%d")

    # Strip any existing date prefix from slug, then apply today's date
    clean_slug = _strip_date_prefix(sermon_slug).replace(" ", "-")
    dated_slug = f"{today}-{clean_slug}"
    filename   = f"{dated_slug}.md"

    # Human-readable title from clean slug
    title = clean_slug.replace("-", " ").title()

    # Wrap transcript in minimal markdown for readability in claude.ai
    md_content = (
        f"# Transcript: {title}\n"
        f"Date: {today}\n\n"
        f"---\n\n"
        f"{clean_text.strip()}\n"
    )

    # --- Destination 1: Git-tracked KB (always inside Watson repo) ---
    KB_GIT_DIR.mkdir(parents=True, exist_ok=True)
    git_kb_path = KB_GIT_DIR / filename
    git_kb_path.write_text(md_content, encoding="utf-8")
    log.info("Transcript written to Git KB: %s", git_kb_path)

    try:
        _commit_and_push(git_kb_path, f"transcript: add {dated_slug}")
    except RuntimeError as e:
        log.error("Git push failed: %s", e)
        log.warning("Telegram notification will still fire with expected URL")

    # --- Destination 2: Local KB inbox (F: drive or wherever KB_LOCAL_DIR points) ---
    if KB_LOCAL_DIR != KB_GIT_DIR:
        try:
            KB_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
            local_kb_path = KB_LOCAL_DIR / filename
            local_kb_path.write_text(md_content, encoding="utf-8")
            log.info("Transcript written to local KB inbox: %s", local_kb_path)
        except Exception as e:
            log.error("Local KB write failed: %s", e)
    else:
        log.info("KB_LOCAL_DIR same as Git KB — skipping duplicate write")

    # Build raw GitHub URL and notify
    raw_url = f"{GITHUB_RAW_BASE}/{filename}"
    _telegram_notify(raw_url, title)

    log.info("Generate job complete: %s", dated_slug)
    log.info("Raw URL: %s", raw_url)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) < 3:
        print("Usage: python jobs/generate.py <clean_transcript_path> <sermon_slug>")
        sys.exit(1)

    clean_path  = Path(sys.argv[1])
    sermon_slug = sys.argv[2]

    if not clean_path.exists():
        log.error("Clean transcript not found: %s", clean_path)
        sys.exit(1)

    generate(clean_path, sermon_slug)
    sys.exit(0)


if __name__ == "__main__":
    main()
