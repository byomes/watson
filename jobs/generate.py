"""
generate.py — Archive clean transcript to two destinations:
  1. Transfer to Beelink's kb/transcripts/ via scp over Tailscale SSH.
     Beelink's own jobs/kb/sync_and_index.py (nightly 2am) moves it into
     kb/documents/, commits + pushes to GitHub, and indexes it into Chroma.
     FMSPC does no git operations for this file at all anymore — the old
     git add/commit/push from FMSPC raced Beelink's own frequent commits
     and routinely lost, which is how transcripts ended up silently
     unindexed (bug #51). See backlog #24 / #29.
  2. Local knowledge base inbox (KB_LOCAL_DIR from .env, e.g. F:\\Knowledge_Database\\_inbox)
     for the local ingest pipeline. No Git involvement, unchanged.

Then notify via Telegram with the raw GitHub link — live once Beelink's
nightly 2am KB sync has moved + pushed the file (see _telegram_notify).

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

from core.vacation import vacation_gate

load_dotenv()

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Local staging copy on FMSPC before transfer to Beelink — no longer
# git-tracked/pushed from this side; Beelink's jobs/kb/sync_and_index.py is
# now the sole git writer for transcripts (bug #51 / backlog #24, #29).
KB_STAGING_DIR = REPO_ROOT / "kb" / "transcripts"

# Local knowledge base inbox on F: drive (or wherever .env points)
# Set KB_LOCAL_DIR in .env, e.g. KB_LOCAL_DIR=F:\Knowledge_Database\_inbox
# Falls back to same as staging dir if not set.
KB_LOCAL_DIR = Path(os.getenv("KB_LOCAL_DIR", str(KB_STAGING_DIR)))

# GitHub raw URL base — live once Beelink's nightly kb sync (2am) has moved
# this file into kb/documents/ and pushed; update if repo name changes
GITHUB_RAW_BASE = os.getenv(
    "GITHUB_RAW_BASE",
    "https://raw.githubusercontent.com/byomes/watson/main/kb/transcripts"
)

# Beelink transfer target — direct scp over Tailscale SSH, replacing the old
# git add/commit/push (backlog #29 confirmed key-based, non-interactive SSH
# working: `ssh -i <FMSPC_SSH_KEY> billyomes@watson.tail0243ff.ts.net`).
# Explicit -i (not an ~/.ssh/config alias) so this doesn't depend on FMSPC-side
# SSH config existing/staying correct — the key path is the only thing that
# has to be right. BatchMode=yes means any auth problem fails fast instead of
# hanging on an interactive prompt, which an unattended job can't answer.
BEELINK_SSH_HOST = os.getenv("BEELINK_SSH_HOST", "watson.tail0243ff.ts.net")
BEELINK_SSH_USER = os.getenv("BEELINK_SSH_USER", "billyomes")
BEELINK_KB_TRANSCRIPTS_DIR = os.getenv(
    "BEELINK_KB_TRANSCRIPTS_DIR", "/home/billyomes/watson/kb/transcripts"
)
FMSPC_SSH_KEY = os.getenv("FMSPC_SSH_KEY", r"C:\Users\billy\.ssh\fmspc_beelink")

# Watson Telegram bot
WATSON_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
WATSON_CHAT_ID   = os.getenv("WATSON_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID")

# Matches any leading date: YYYY-MM-DD or MM-DD-YYYY
_DATE_PREFIX_RE = re.compile(r"^\d{2,4}-\d{2}-\d{2,4}-?")


def _strip_date_prefix(slug: str) -> str:
    """Remove any leading date pattern from a slug."""
    return _DATE_PREFIX_RE.sub("", slug).strip("-")


# --- Transfer to Beelink -----------------------------------------------

def _scp_to_beelink(local_path: Path) -> None:
    remote = f"{BEELINK_SSH_USER}@{BEELINK_SSH_HOST}:{BEELINK_KB_TRANSCRIPTS_DIR}/{local_path.name}"
    result = subprocess.run(
        [
            "scp",
            "-i", FMSPC_SSH_KEY,
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15",
            str(local_path),
            remote,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"scp to Beelink failed:\n{result.stderr.strip()}")
    log.info("Transferred to Beelink: %s", remote)


# --- Telegram ---------------------------------------------------------

def _telegram_notify(raw_url: str, title: str, transfer_succeeded: bool = True) -> None:
    # transfer_succeeded=False means the scp to Beelink failed (network/SSH/
    # permission problem), so that branch is tagged system_failure; the
    # routine "archived" notice is normal.
    priority = "normal" if transfer_succeeded else "system_failure"
    if vacation_gate(priority, "jobs.generate._telegram_notify", title):
        return
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.warning("Telegram not configured — skipping notification")
        return

    if transfer_succeeded:
        text = (
            f"📄 <b>New transcript archived</b>\n\n"
            f"<b>{title}</b>\n\n"
            f"Raw URL (copy and paste into claude.ai) — live after tonight's "
            f"2am KB sync:\n"
            f"<code>{raw_url}</code>\n\n"
            f"Paste into claude.ai with:\n"
            f"<i>\"Draft a blog article from this transcript.\"</i>"
        )
        payload = {
            "chat_id":    WATSON_CHAT_ID,
            "text":       text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "📂 Open Transcript", "url": raw_url}
                ]]
            },
        }
    else:
        text = (
            f"⚠️ <b>Transcript saved locally — transfer to Beelink failed</b>\n\n"
            f"<b>{title}</b>\n\n"
            f"The transcript was written locally on FMSPC but the scp transfer "
            f"to Beelink failed (network, SSH, or permission error). It has NOT "
            f"reached the KB yet.\n\n"
            f"Check FMSPC and retry the transfer manually."
        )
        payload = {
            "chat_id":    WATSON_CHAT_ID,
            "text":       text,
            "parse_mode": "HTML",
        }

    url = f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json=payload, timeout=10)
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

    # --- Destination 1: local staging copy, then scp to Beelink ---
    KB_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    staging_path = KB_STAGING_DIR / filename
    staging_path.write_text(md_content, encoding="utf-8")
    log.info("Transcript staged locally: %s", staging_path)

    transfer_succeeded = True
    try:
        _scp_to_beelink(staging_path)
    except RuntimeError as e:
        log.error("Transfer to Beelink failed: %s", e)
        transfer_succeeded = False

    # --- Destination 2: Local KB inbox (F: drive or wherever KB_LOCAL_DIR points) ---
    if KB_LOCAL_DIR != KB_STAGING_DIR:
        try:
            KB_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
            local_kb_path = KB_LOCAL_DIR / filename
            local_kb_path.write_text(md_content, encoding="utf-8")
            log.info("Transcript written to local KB inbox: %s", local_kb_path)
        except Exception as e:
            log.error("Local KB write failed: %s", e)
    else:
        log.info("KB_LOCAL_DIR same as staging dir — skipping duplicate write")

    # Build raw GitHub URL and notify
    raw_url = f"{GITHUB_RAW_BASE}/{filename}"
    _telegram_notify(raw_url, title, transfer_succeeded=transfer_succeeded)

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
