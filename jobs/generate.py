"""
generate.py — Archive clean transcript to two destinations:
  1. Transfer to Beelink's kb/transcripts/ via scp over Tailscale SSH, then
     trigger Beelink's jobs.kb.sync_and_index.run_sync() immediately (via
     jobs/kb/api.py's POST /api/kb/sync-now) so the file is moved into
     kb/documents/, committed, pushed to GitHub, and indexed into Chroma
     within seconds — not just on the nightly 2am cron. The raw GitHub URL
     is fed into claude.ai every week to draft a blog post from that
     sermon, so it has to be live same-day; the nightly cron remains as an
     unconditional backstop for anything this trigger misses.
     FMSPC does no git operations for this file at all anymore — the old
     git add/commit/push from FMSPC raced Beelink's own frequent commits
     and routinely lost, which is how transcripts ended up silently
     unindexed (bug #51). See backlog #24 / #29.
  2. Local knowledge base inbox (KB_LOCAL_DIR from .env, e.g. F:\\Knowledge_Database\\_inbox)
     for the local ingest pipeline. No Git involvement, unchanged.

Then notify via Telegram with the raw GitHub link (see _telegram_notify for
the three possible outcomes: transfer failed, transfer+sync both succeeded
so the link is live now, or transfer succeeded but the immediate sync
trigger didn't — link goes live on the next 2am backstop instead).

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

# GitHub raw URL base — live immediately if the sync-now trigger succeeds,
# otherwise live after the next 2am backstop; update if repo name changes.
# Must point at kb/documents, not kb/transcripts: sync_and_index.py moves
# every synced file into kb/documents/, so that's where it's actually
# committed and where the raw URL resolves. Was wrongly pointed at
# kb/transcripts (a 404 after every sync) until caught during the 2026-08-03
# sync-now auth incident verification.
GITHUB_RAW_BASE = os.getenv(
    "GITHUB_RAW_BASE",
    "https://raw.githubusercontent.com/byomes/watson/main/kb/documents"
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

# Immediate KB-sync trigger — hit Beelink's dashboard directly over Tailscale
# (not the public Funnel: this stays inside the private tailnet, same network
# path as the scp above, and doesn't depend on the public Funnel being up).
# Shares WRITING_ROOM_API_KEY as the X-Watson-Key secret — same reused
# shared-secret convention as jobs/bodyrec/api.py, not a new credential.
BEELINK_API_BASE = os.getenv("BEELINK_API_BASE", "http://watson.tail0243ff.ts.net:5200")
WATSON_API_KEY = os.getenv("WRITING_ROOM_API_KEY", "")

# Watson Telegram bot
WATSON_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
WATSON_CHAT_ID   = os.getenv("WATSON_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID")

# Matches any leading date: YYYY-MM-DD or MM-DD-YYYY
_DATE_PREFIX_RE = re.compile(r"^\d{2,4}-\d{2}-\d{2,4}-?")


def _strip_date_prefix(slug: str) -> str:
    """Remove any leading date pattern from a slug."""
    return _DATE_PREFIX_RE.sub("", slug).strip("-")


# --- Transfer to Beelink -----------------------------------------------

def _ensure_remote_dir() -> None:
    """mkdir -p the remote kb/transcripts/ dir before every scp. Cheap
    defense in depth against the 2026-08-03 incident (the directory went
    missing on Beelink with nothing to recreate it, so scp failed silently
    from FMSPC's point of view — connect, no destination, disconnect).
    Beelink-side jobs/kb/sync_and_index.py now self-heals this too, but scp
    itself still can't create a missing destination, so this has to happen
    before the transfer, not after.
    """
    result = subprocess.run(
        [
            "ssh",
            "-i", FMSPC_SSH_KEY,
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15",
            f"{BEELINK_SSH_USER}@{BEELINK_SSH_HOST}",
            f"mkdir -p {BEELINK_KB_TRANSCRIPTS_DIR}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"remote mkdir -p failed:\n{result.stderr.strip()}")


def _scp_to_beelink(local_path: Path) -> None:
    _ensure_remote_dir()
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


def _trigger_immediate_sync() -> dict:
    """POST to Beelink's /api/kb/sync-now right after a successful transfer
    so the file is moved, committed, pushed, and indexed within seconds
    instead of waiting for the 2am cron. Failure here is non-fatal — the
    nightly jobs/kb/sync_and_index.py run is the unconditional backstop for
    anything this misses (including WATSON_API_KEY not being set here, which
    would just get a 401 back and fall through to the backstop).
    """
    url = f"{BEELINK_API_BASE}/api/kb/sync-now"
    resp = requests.post(url, headers={"X-Watson-Key": WATSON_API_KEY}, timeout=120)
    resp.raise_for_status()
    return resp.json()


# --- Telegram ---------------------------------------------------------

def _telegram_notify(raw_url: str, title: str, transfer_succeeded: bool = True,
                      sync_ok: bool = False, sync_error: str = None) -> None:
    if not transfer_succeeded:
        # scp itself failed — infra problem, nothing reached Beelink at all.
        priority = "system_failure"
        text = (
            f"⚠️ <b>Transcript saved locally — transfer to Beelink failed</b>\n\n"
            f"<b>{title}</b>\n\n"
            f"The transcript was written locally on FMSPC but the scp transfer "
            f"to Beelink failed (network, SSH, or permission error). It has NOT "
            f"reached the KB yet.\n\n"
            f"Check FMSPC and retry the transfer manually."
        )
        payload = {"chat_id": WATSON_CHAT_ID, "text": text, "parse_mode": "HTML"}
    elif sync_ok:
        # Transfer + immediate sync both succeeded — link is live right now.
        priority = "normal"
        text = (
            f"📄 <b>New transcript archived</b>\n\n"
            f"<b>{title}</b>\n\n"
            f"Raw URL (copy and paste into claude.ai):\n"
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
        # Transfer succeeded but the immediate sync trigger didn't — file is
        # safe on Beelink, but not yet moved/committed/indexed. The 2am
        # backstop will catch it.
        priority = "system_failure"
        detail = f" ({sync_error})" if sync_error else ""
        text = (
            f"⚠️ <b>Transcript transferred, but immediate KB sync didn't complete</b>\n\n"
            f"<b>{title}</b>\n\n"
            f"The file reached Beelink's kb/transcripts/ safely, but the immediate "
            f"sync/index/push trigger failed{detail}. The raw URL is not live yet — "
            f"tonight's 2am KB sync will catch it as a backstop.\n\n"
            f"<code>{raw_url}</code>"
        )
        payload = {"chat_id": WATSON_CHAT_ID, "text": text, "parse_mode": "HTML"}

    if vacation_gate(priority, "jobs.generate._telegram_notify", title):
        return
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.warning("Telegram not configured — skipping notification")
        return

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

    # --- Destination 1: local staging copy, then scp to Beelink, then
    # trigger immediate sync/index/push ---
    KB_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    staging_path = KB_STAGING_DIR / filename
    staging_path.write_text(md_content, encoding="utf-8")
    log.info("Transcript staged locally: %s", staging_path)

    transfer_succeeded = True
    sync_ok = False
    sync_error = None
    try:
        _scp_to_beelink(staging_path)
    except Exception as e:
        # Broad catch, not just RuntimeError: an unexpected failure here
        # (missing scp/ssh binary, etc.) must still reach the Telegram
        # alert below, not crash the script before notify runs.
        log.error("Transfer to Beelink failed: %s", e)
        transfer_succeeded = False

    if transfer_succeeded:
        try:
            result = _trigger_immediate_sync()
            sync_ok = bool(result.get("ok"))
            if not sync_ok:
                sync_error = result.get("error") or "unknown error"
        except Exception as e:
            log.error("Immediate sync trigger failed: %s", e)
            sync_error = str(e)

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
    _telegram_notify(raw_url, title, transfer_succeeded=transfer_succeeded,
                      sync_ok=sync_ok, sync_error=sync_error)

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
