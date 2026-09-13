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

import json
import logging
import os
import re
import subprocess
import sys
from datetime import date, timedelta
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


def _extract_original_date(slug: str) -> str | None:
    """Parse a leading YYYY-MM-DD or MM-DD-YYYY date out of an audio filename
    stem and normalize it to YYYY-MM-DD. Returns None if the filename has no
    date prefix.

    Historical backfill audio (e.g. "2011-11-27-Bill-Nativity1") carries the
    actual date preached; a live weekly drop with no date in its name has no
    such signal and the caller should fall back to date.today(). Before this
    existed, generate() always stamped every file with today's ingestion
    date, silently overwriting years-old preached dates with the date the
    file happened to be transcribed (caught 2026-09-13 processing the
    Sermon Audio Master backlog into the KB).
    """
    m = re.match(r"^(\d{2,4})-(\d{2})-(\d{2,4})-?", slug)
    if not m:
        return None
    a, b, c = m.groups()
    if len(a) == 4:
        year, month, day = a, b, c
    elif len(c) == 4:
        month, day, year = a, b, c
    else:
        return None
    try:
        date(int(year), int(month), int(day))
    except ValueError:
        return None
    return f"{year}-{month}-{day}"


def _most_recent_sunday(from_date: date) -> date:
    """Bill preaches on Sundays; a weekly transcript often gets processed a
    few days after the fact. When the filename carries no date, the most
    recent Sunday on/before the processing date is a better guess at the
    actual preached date than "today"."""
    return from_date - timedelta(days=(from_date.weekday() - 6) % 7)


# Bill's annual preaching-plan spreadsheet, snapshotted locally so an
# undated sermon slug can be cross-referenced against the actual Sunday it
# was scheduled for instead of just guessing "most recent Sunday" (added
# 2026-09-13). Source: docs.google.com/spreadsheets/d/1aDrJ_jlNmJcMIQiA9F8p9QNqGoaXls4WiN232ZEuCcY
# — see data/sermon_calendar.json's _meta for a data-quality note (one tab's
# dates are mislabeled by a year in the sheet itself, corrected here).
SERMON_CALENDAR_PATH = REPO_ROOT / "data" / "sermon_calendar.json"
_STOPWORDS = {"the", "a", "an", "of", "to", "in", "on", "and", "for", "is", "are",
              "was", "were", "with", "from", "by", "at", "this", "that"}
_sermon_calendar_cache = None


def _tokenize(text: str) -> set[str]:
    words = re.split(r"[^A-Za-z0-9]+", text.lower())
    return {w for w in words if w and w not in _STOPWORDS and len(w) > 1}


def _numbers_in(text: str) -> set[str]:
    """Normalized (no leading zeros) digit runs, for calendar-side passage/
    message text (e.g. "Joshua 2" or "2:12-18")."""
    return {str(int(n)) for n in re.findall(r"\d+", text)}


_CHAPTER_RE = re.compile(r"ch(?:apter)?\.?\s*(\d+)", re.IGNORECASE)


def _slug_chapter_numbers(slug: str) -> set[str]:
    """Chapter numbers explicitly marked as such in a slug (e.g. "Ch2",
    "Chapter 3"). Deliberately narrower than _numbers_in() -- a slug's bare
    ordinal like "Bulletproof-Joy---04---..." (the 4th sermon in the
    series) is NOT a scripture reference and must not be treated as one, or
    it can coincidentally collide with an unrelated week's passage number
    (caught testing: "04" falsely matched a row whose passage was "4:4-23")."""
    return {str(int(n)) for n in _CHAPTER_RE.findall(slug)}


def _load_sermon_calendar() -> list[dict]:
    global _sermon_calendar_cache
    if _sermon_calendar_cache is None:
        try:
            _sermon_calendar_cache = json.loads(SERMON_CALENDAR_PATH.read_text(encoding="utf-8"))["rows"]
        except Exception as e:
            log.warning("Could not load sermon calendar (%s) -- falling back to Sunday guess only: %s",
                        SERMON_CALENDAR_PATH, e)
            _sermon_calendar_cache = []
    return _sermon_calendar_cache


def _calendar_lookup_date(slug: str, near: date, window_days: int = 21) -> str | None:
    """Cross-reference an undated sermon slug against the preaching-plan
    calendar for a more precise date than the Sunday-before-transcription
    guess. Deliberately conservative -- a wrong date is worse than the
    Sunday guess, so this requires the sermon's own series name (a
    non-trivial token, not a stray short word) to actually appear in the
    slug before it will return anything; a bare word-overlap coincidence
    isn't enough.
    """
    calendar = _load_sermon_calendar()
    slug_tokens = _tokenize(slug)
    if not slug_tokens:
        return None
    best = None
    for row in calendar:
        row_date = date.fromisoformat(row["date"])
        if abs((row_date - near).days) > window_days:
            continue
        series_tokens_all = _tokenize(row.get("series", ""))
        matched_series = {t for t in series_tokens_all if len(t) >= 4} & slug_tokens
        if not matched_series:
            continue
        detail_tokens = _tokenize(row.get("message", "")) | _tokenize(row.get("passage", ""))
        # Exclude ANY series word (not just the >=4-letter ones used for the
        # match gate above) -- a message title that happens to restate part
        # of the series name (e.g. "Joy in the Lord" under a series called
        # "Bulletproof Joy") must not get double-counted and tip the score
        # toward the wrong week just because "joy" is short.
        extra_overlap = (detail_tokens & slug_tokens) - series_tokens_all
        score = len(matched_series) * 2 + len(extra_overlap)
        row_numbers = _numbers_in(row.get("message", "")) | _numbers_in(row.get("passage", ""))
        if row_numbers and (row_numbers & _slug_chapter_numbers(slug)):
            score += 4
        if score < 3:
            continue
        candidate = (score, -abs((row_date - near).days), row["date"])
        if best is None or candidate > best:
            best = candidate
    return best[2] if best else None


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


def push_transcript_to_beelink(local_path: Path) -> dict:
    """scp local_path to Beelink's kb/transcripts/ and trigger an immediate
    sync/index/push. Shared by generate() (weekly) and watcher.py's
    handle_archive() (archive mode, added after bug #164 — archive
    transcripts used to dead-end in F:\\Knowledge_Database\\_inbox with no
    path to the KB at all).

    Returns {"transfer_succeeded": bool, "sync_ok": bool, "sync_error": str|None}.
    """
    transfer_succeeded = True
    sync_ok = False
    sync_error = None
    try:
        _scp_to_beelink(local_path)
    except Exception as e:
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

    return {"transfer_succeeded": transfer_succeeded, "sync_ok": sync_ok, "sync_error": sync_error}


def notify_archive_transfer(title: str, result: dict) -> None:
    """Lightweight Telegram notification for archive-mode transfers.

    Silent on full success — these are historical backfill sermons with no
    blog-draft step, and a per-file ping would spam Bill across the
    hundreds of files in the Sermon Audio Master backlog. Still alerts on
    failure so a stranded transcript doesn't go unnoticed again (bug #164).
    """
    if result["transfer_succeeded"] and result["sync_ok"]:
        return

    if not result["transfer_succeeded"]:
        text = (
            f"⚠️ <b>Archive transcript transfer failed</b>\n\n<b>{title}</b>\n\n"
            f"Saved locally on FMSPC but the scp transfer to Beelink failed. "
            f"It has NOT reached the KB yet — check FMSPC and retry manually."
        )
    else:
        detail = f" ({result['sync_error']})" if result["sync_error"] else ""
        text = (
            f"⚠️ <b>Archive transcript transferred, sync didn't complete</b>\n\n<b>{title}</b>\n\n"
            f"Reached Beelink's kb/transcripts/ safely{detail}, but the immediate "
            f"sync/index/push trigger failed. Tonight's 2am KB sync will catch it."
        )

    if vacation_gate("system_failure", "jobs.generate.notify_archive_transfer", title):
        return
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.warning("Telegram not configured — skipping notification")
        return

    resp = requests.post(
        f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
        json={"chat_id": WATSON_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()
    log.info("Archive transfer alert sent")


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

    # Resolve the preached date in three tiers: (1) the filename's own
    # embedded date, the historical-backfill case; (2) a confident match
    # against the preaching-plan calendar for the exact scheduled Sunday;
    # (3) the most recent Sunday before processing, as a last resort.
    preached_date = (
        _extract_original_date(sermon_slug)
        or _calendar_lookup_date(sermon_slug, date.today())
        or _most_recent_sunday(date.today()).strftime("%Y-%m-%d")
    )

    # Strip any existing date prefix from slug, then apply the resolved date
    clean_slug = _strip_date_prefix(sermon_slug).replace(" ", "-")
    dated_slug = f"{preached_date}-{clean_slug}"
    filename   = f"{dated_slug}.md"

    # Human-readable title from clean slug
    title = clean_slug.replace("-", " ").title()

    # Wrap transcript in minimal markdown for readability in claude.ai
    md_content = (
        f"# Transcript: {title}\n"
        f"Date: {preached_date}\n\n"
        f"---\n\n"
        f"{clean_text.strip()}\n"
    )

    # --- Destination 1: local staging copy, then scp to Beelink, then
    # trigger immediate sync/index/push ---
    KB_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    staging_path = KB_STAGING_DIR / filename
    staging_path.write_text(md_content, encoding="utf-8")
    log.info("Transcript staged locally: %s", staging_path)

    transfer_result = push_transcript_to_beelink(staging_path)
    transfer_succeeded = transfer_result["transfer_succeeded"]
    sync_ok = transfer_result["sync_ok"]
    sync_error = transfer_result["sync_error"]

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
