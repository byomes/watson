# Cron: 0 2 * * * (runs at 2am daily — same slot vacated by retiring archive_transcripts.py)
"""
sync_and_index.py — Same-day sermon transcript sync and KB indexing.

Closes the gap between a transcript landing in kb/transcripts/ (scp'd from
FMSPC via jobs/generate.py) and it becoming searchable / getting a live
GitHub raw URL:

  1. git pull the Watson repo (fast-forward only) to receive anything
     pushed since the last run.
  2. Move every file currently in kb/transcripts/ into kb/documents/
     (no age threshold — same day, not 30 days).
  3. Incrementally index the new files into the "sermons" ChromaDB
     collection via jobs.build_kb.ingest_dir. This runs BEFORE the push: it is
     local-only, so a GitHub outage can never leave new transcripts unsearchable.
  4. Commit that move and push it.
  5. Send a Telegram summary.

Retry safety: each step that can fail is retried by the next run instead of
being skipped. data/.kb_needs_index is written before indexing and removed
only after it succeeds; a "kb: sync" commit that failed to push is pushed on
the next run (unrelated unpushed commits are never pushed, only reported).
Earlier versions pushed first and returned on failure, which left files moved
but never indexed, and the next run's "no new transcripts" hid them forever
(found 2026-10-05 after a transient "Invalid username or token" push failure).

Pull safety: fetch + `pull --ff-only` only. Never merges, rebases, or
resets. Any failure (diverged history, conflicting local changes, network)
aborts the run with a Telegram alert and leaves the working tree untouched.

The actual logic lives in run_sync(), called from two places:
  - main() — the nightly 2am cron, unconditional backstop.
  - jobs/kb/api.py's POST /api/kb/sync-now — triggered by generate.py
    immediately after a successful scp, so the raw URL (fed into claude.ai
    weekly for blog drafting) goes live within seconds instead of waiting
    for the next 2am run. Same code path either way, not a reimplementation.

Both callers can run at any time relative to each other (e.g. a transcript
lands right at 2am), and both do real git commits/pushes against the same
working tree — run_sync() is serialized via a file lock (_with_lock) so
they can never race each other. That race is exactly the bug class this
whole file exists to eliminate (bug #51 / backlog #24, #29).

Supersedes jobs/kb/archive_transcripts.py's reason for existing — see the
retirement note at the top of that file.

Usage:
  python jobs/kb/sync_and_index.py
"""

import fcntl
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
LOCK_PATH = REPO_ROOT / "data" / ".kb_sync.lock"
# Present while files have been moved into kb/documents/ but not yet indexed. Written
# BEFORE indexing starts and removed only after it succeeds, so a failed or interrupted
# index run is retried by the next run instead of being skipped (the files have already
# moved out of kb/transcripts/, so "no new transcripts" would otherwise hide them forever).
NEEDS_INDEX_PATH = REPO_ROOT / "data" / ".kb_needs_index"


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
    # Self-heal: a missing TRANSCRIPTS_DIR must not silently look like "nothing
    # to sync" (that's exactly how the directory going missing on 2026-08-03
    # went unnoticed for weeks) — recreate it, same pattern as DOCUMENTS_DIR.
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
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


def commit_moved(moved_count: int) -> tuple:
    # Scoped `git add` (not -A) — never sweep up unrelated in-progress
    # changes elsewhere in the working tree.
    add = _git(["add", "kb/documents", "kb/transcripts"])
    if add.returncode != 0:
        return False, f"git add failed:\n{add.stderr.strip()}"

    commit = _git(["commit", "-m", f"kb: sync {moved_count} transcript(s) to kb/documents (same-day)"])
    if commit.returncode != 0:
        return False, f"git commit failed:\n{commit.stderr.strip()}"
    return True, ""


def _unpushed_subjects() -> list:
    """Subjects of local commits not yet on origin/main (oldest first)."""
    res = _git(["log", "origin/main..HEAD", "--reverse", "--format=%s"])
    if res.returncode != 0:
        return []
    return [line for line in res.stdout.splitlines() if line.strip()]


def push_pending() -> tuple:
    """Push this job's own unpushed "kb: sync" commits, retrying anything a
    previous run committed but failed to push.

    Returns (pushed_anything, error_message). Refuses to push if there are
    unpushed commits that are NOT this job's own: `git push origin main` would
    publish someone's unrelated work-in-progress commits along with ours, so
    that case is reported for a human to resolve instead.
    """
    subjects = _unpushed_subjects()
    if not subjects:
        return False, ""
    foreign = [s for s in subjects if not s.startswith("kb: sync")]
    if foreign:
        return False, (
            "unpushed commits that are not KB syncs are on main, so not pushing "
            f"(would publish them too): {foreign[:3]}"
        )
    push = _git(["push", "origin", "main"])
    if push.returncode != 0:
        return False, f"git push failed (committed locally, will retry next run):\n{push.stderr.strip()}"
    return True, ""


def _with_lock(fn):
    """Serialize sync runs across processes (cron subprocess vs. Flask
    request thread) via an OS file lock — fcntl.flock blocks until any
    other holder releases it, so a cron run and an immediate-trigger run
    landing close together simply queue instead of racing on git's
    index.lock.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def run_sync(source: str = "cron") -> dict:
    """Core sync logic, callable from the nightly cron (main()) or the
    immediate post-transfer trigger (jobs/kb/api.py's /api/kb/sync-now).

    Routine success-path Telegram summaries ("nothing new" / "N synced and
    indexed") only fire for source="cron" — an immediate trigger's caller
    (generate.py) already tells Bill the outcome from the FMSPC side, so a
    second routine notice here would just be noise. Failure alerts fire
    unconditionally regardless of source, since those matter regardless of
    who triggered the run.

    Returns {"ok": bool, "moved": int, "indexed": int, "error": str|None}.
    """
    def _run():
        pull_ok, pull_msg = pull_repo()
        if not pull_ok:
            log.error(pull_msg)
            _send_telegram(
                f"🔴 KB sync: git pull failed, needs manual resolution.\n\n{pull_msg[:500]}",
                priority="system_failure",
            )
            return {"ok": False, "moved": 0, "indexed": 0, "error": pull_msg}
        log.info("Pull ok: %s", pull_msg or "already up to date")

        moved = move_new_transcripts()
        retry_index = NEEDS_INDEX_PATH.exists()
        retry_push = bool(_unpushed_subjects())
        if not moved and not retry_index and not retry_push:
            log.info("No new transcripts to sync")
            if source == "cron":
                _send_telegram("📂 KB sync: nothing new.")
            return {"ok": True, "moved": 0, "indexed": 0, "error": None}

        if moved:
            log.info("Moved %d file(s) to kb/documents/", len(moved))
            NEEDS_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            NEEDS_INDEX_PATH.touch()
            retry_index = True
        elif retry_index or retry_push:
            log.info("Recovering an earlier incomplete run (index pending=%s, unpushed commits=%s)",
                     retry_index, retry_push)

        errors = []

        # Index FIRST: it is local-only, so a GitHub outage cannot leave the new
        # transcripts unsearchable. Pushing afterward is the step that can fail
        # on the network, and it is now retried on every later run.
        added_chunks = 0
        if retry_index:
            try:
                added_chunks = ingest_dir(DOCUMENTS_DIR, COLLECTION_NAME, source_type="transcript")
                NEEDS_INDEX_PATH.unlink(missing_ok=True)
                log.info("Indexed %d new chunk(s)", added_chunks)
            except Exception as exc:
                log.exception("KB indexing failed")
                errors.append(f"indexing failed (will retry next run): {exc}")

        if moved:
            commit_ok, commit_msg = commit_moved(len(moved))
            if not commit_ok:
                log.error(commit_msg)
                errors.append(commit_msg)

        pushed, push_err = push_pending()
        if push_err:
            log.error(push_err)
            errors.append(push_err)

        if errors:
            detail = "\n".join(errors)
            _send_telegram(
                f"🔴 KB sync: {len(moved)} transcript(s) moved, but not everything finished. "
                f"Whatever failed is retried automatically on the next run.\n\n{detail[:500]}",
                priority="system_failure",
            )
            return {"ok": False, "moved": len(moved), "indexed": added_chunks, "error": detail}

        if source == "cron":
            _send_telegram(
                f"📂 KB sync: {len(moved)} new transcript(s) synced and indexed "
                f"({added_chunks} new chunks in '{COLLECTION_NAME}')."
            )
        return {"ok": True, "moved": len(moved), "indexed": added_chunks, "error": None}

    return _with_lock(_run)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    result = run_sync(source="cron")
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
