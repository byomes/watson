# 25 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/writing_digest/nightly_digest.py >> /home/billyomes/watson/logs/writing_digest.log 2>&1
"""Nightly per-project writing digest.

Bill's problem: when he opens a book project in Claude.ai to keep writing,
that chat has no idea what got archived to Watson since its own context
was last refreshed -- it's working from stale memory while Watson already
holds the real, current record.

Fix, two layers:

1. A small MASTER file per project (outline, outstanding/open items, a
   condensed work log) that Claude.ai loads in full every session. This is
   the "base knowledge" -- it has to stay complete and current, so it's
   rebuilt from scratch each run rather than incrementally patched.
2. One ARCHIVE file per session, pushed once and never rewritten (archives
   are immutable, so there's nothing to re-sync). The master file's outline
   cites which archive(s) hold the source material for each chapter, as
   links. Claude.ai is instructed to fetch a cited archive only when Bill
   is actually working that chapter, not up front -- so "current" doesn't
   mean "everything loaded into context every time."

Both land in the watson-review repo at FIXED paths. The raw GitHub URL for
the master file never changes, only its content, so Bill adds that one URL
to a project's Claude.ai custom instructions once ("fetch and read this
before doing anything") and every future session there pulls the latest
state automatically, then follows citation links on demand. This is the
same fetch-a-pushed-link pattern already used ad hoc in Guardrails sessions
(see project_guardrails / project_watson_session_archives memory), just
automated and stable instead of one-off.

The merge itself runs as a headless Claude Code session (`claude -p`) --
the same mechanism jobs/devdispatch already uses to dispatch coding work --
NOT a metered Anthropic API key. Watson deliberately has no
ANTHROPIC_API_KEY set (a cost decision -- see feedback_anthropic_key_unset
in memory); nothing in this job should ever add one.
"""
import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  # noqa: E402
from core.database import get_connection  # noqa: E402
from core.vacation import vacation_gate  # noqa: E402

import requests  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

WATSON_DIR = Path(__file__).resolve().parents[2]
ARCHIVES_DIR = WATSON_DIR / "data" / "session_archives"
REVIEW_REPO = Path.home() / "watson-review"
REVIEW_CONTEXT_DIR = "context"  # matches watson-review's existing convention

# Add a project slug here (must match its session_archives project slug)
# once it has real, ongoing live activity worth digesting nightly. Cron
# only invokes this list -- pass --project explicitly to run one ad hoc.
ENABLED_PROJECTS = ["guardrails"]

_CLAUDE_BIN = (
    os.getenv("CLAUDE_BIN")
    or shutil.which("claude")
    or "/home/billyomes/.nvm/versions/node/v24.16.0/bin/claude"
)
_MAX_BUDGET_USD = "5"
_CLAUDE_TIMEOUT_S = 900  # bound a single nightly merge run


def send_telegram(text):
    if vacation_gate("normal", "jobs.writing_digest.nightly_digest", text):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)


def _get_state(conn, key):
    row = conn.execute("SELECT value FROM job_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(conn, key, value):
    conn.execute(
        "INSERT INTO job_state (key, value, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, str(value)),
    )
    conn.commit()


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=str(REVIEW_REPO), capture_output=True, text=True, timeout=30
    )


def _archive_rel_path(project, archive_id):
    return f"{REVIEW_CONTEXT_DIR}/{project}/archives/{archive_id}.md"


def _archive_url(project, archive_id):
    return (
        f"https://raw.githubusercontent.com/byomes/watson-review/main/"
        f"{_archive_rel_path(project, archive_id)}"
    )


def _push_archive_files(project, new_rows):
    """Copy each new archive's transcript into the repo at a fixed,
    permanent path -- archives are immutable, so a given id's file is
    written once and never touched again. This is deterministic (no LLM)
    on purpose: the master file only needs to link to these, not author
    them."""
    written = []
    for r in new_rows:
        src = WATSON_DIR / r["dir_path"] / "transcript.md"
        dest = REVIEW_REPO / _archive_rel_path(project, r["id"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(str(dest.relative_to(REVIEW_REPO)))
    return written


def _build_prompt(project, master_rel_path, master_exists, new_rows):
    archive_list = "\n".join(
        f"- id {r['id']}, archived {r['created_at']}, \"{r['title']}\"\n"
        f"  read: {WATSON_DIR / r['dir_path'] / 'transcript.md'}\n"
        f"  cite as: {_archive_url(project, r['id'])}"
        for r in new_rows
    )

    if master_exists:
        baseline = (
            f"A master file already exists at {master_rel_path} (relative to "
            f"this repo). Read it first -- it is your baseline. Preserve every "
            f"decision already locked in it, including existing citation "
            f"links in the outline for chapters tonight's new sessions don't "
            f"touch. Only change something already there if one of tonight's "
            f"new sessions explicitly supersedes it."
        )
    else:
        baseline = (
            f"No master file exists yet at {master_rel_path} -- this is the "
            f"first run. Build it from scratch, in order, from every archive "
            f"listed below."
        )

    return (
        f"You are updating Watson's nightly writing digest for the "
        f"\"{project}\" book project. This file is BASE KNOWLEDGE that a "
        f"Claude.ai session loads in full every time -- it must stay small "
        f"and scannable. Full session transcripts live elsewhere (cited "
        f"below); do not inline their content here beyond a short summary.\n\n"
        f"{baseline}\n\n"
        f"New archived sessions to incorporate tonight -- read the local "
        f"transcript.md for each (full content) to know what happened, but "
        f"when you cite it in the outline, use the \"cite as\" URL, never the "
        f"local path (Claude.ai can fetch a raw GitHub URL, it cannot reach "
        f"this filesystem):\n{archive_list}\n\n"
        f"Rewrite {master_rel_path} as ONE complete markdown file with "
        f"exactly these sections, in this order:\n"
        f"1. \"# {project} -- Working Status\" with a one-line \"last updated\" "
        f"date, followed immediately by this exact note in a blockquote: "
        f"\"> This file is your current base knowledge for this project -- "
        f"always read in full. The links under each chapter below point to "
        f"the archived session(s) that chapter's material came from. Fetch "
        f"one of those only when Bill is actively working that specific "
        f"chapter and you need the original verbatim detail -- don't fetch "
        f"them up front or by default.\"\n"
        f"2. \"## Current Outline\" -- the locked chapter/section structure as "
        f"it stands right now, not a history of how it changed. Under each "
        f"chapter, add a \"Sources:\" line listing every archive (old and "
        f"new) whose session material belongs to that chapter, as markdown "
        f"links using each archive's \"cite as\" URL and its date as the link "
        f"text, e.g. \"Sources: [2026-09-09](<url>), [2026-09-14](<url>)\". A "
        f"session touching multiple chapters gets cited under all of them.\n"
        f"3. \"## Outstanding / Open Items\" -- everything still undecided or "
        f"unwritten, phrased so a fresh reader with no other context knows "
        f"exactly what's left and why.\n"
        f"4. \"## Work Log\" -- a chronological, dated summary of what's been "
        f"decided or drafted session by session. Keep older entries brief; "
        f"condense/compact anything more than a few weeks old rather than "
        f"letting this section grow forever -- the Outline and Open Items "
        f"sections above are what a resuming session actually needs, this "
        f"log is supporting history, not the primary content.\n\n"
        f"Ground every claim in what the transcripts and the existing master "
        f"file actually say -- never invent a decision, story, or word count "
        f"that isn't in the source material. If something is ambiguous or "
        f"contradicts an earlier locked decision, list it under Outstanding "
        f"Items rather than silently picking one.\n\n"
        f"Only write to {master_rel_path}. Do not touch any other file, and "
        f"do not run git commit or git push -- exit once the file is written, "
        f"a separate step handles version control."
    )


def run_digest(project):
    conn = get_connection()
    state_key = f"writing_digest:{project}:last_archive_id"
    last_id = int(_get_state(conn, state_key) or 0)

    new_rows = conn.execute(
        "SELECT id, title, created_at, dir_path FROM session_archives "
        "WHERE project = ? AND superseded_by IS NULL AND id > ? ORDER BY id",
        (project, last_id),
    ).fetchall()

    if not new_rows:
        log.info("writing_digest: %s has no new archives since id %d", project, last_id)
        return

    master_rel_path = f"{REVIEW_CONTEXT_DIR}/{project}-master.md"
    master_abs_path = REVIEW_REPO / master_rel_path
    master_exists = master_abs_path.is_file()

    archive_rel_paths = _push_archive_files(project, new_rows)

    prompt = _build_prompt(project, master_rel_path, master_exists, new_rows)

    cmd = [
        _CLAUDE_BIN, "-p",
        "--permission-mode", "bypassPermissions",
        "--add-dir", str(ARCHIVES_DIR),
        "--max-budget-usd", _MAX_BUDGET_USD,
        prompt,
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(REVIEW_REPO), capture_output=True, text=True,
            timeout=_CLAUDE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        log.error("writing_digest: %s merge timed out after %ds", project, _CLAUDE_TIMEOUT_S)
        send_telegram(
            f"Writing digest for {project} timed out before finishing tonight's "
            f"merge. Nothing was pushed, nothing was marked processed, it'll "
            f"retry the same {len(new_rows)} archive(s) tomorrow night.\n- Watson"
        )
        return

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        log.error("writing_digest: %s merge failed: %s", project, err)
        send_telegram(
            f"Writing digest for {project} failed tonight: {err}\n"
            f"Nothing was pushed, it'll retry the same {len(new_rows)} "
            f"archive(s) tomorrow night.\n- Watson"
        )
        return

    if not master_abs_path.is_file():
        log.error("writing_digest: %s merge exited clean but wrote no file", project)
        send_telegram(
            f"Writing digest for {project} ran but never wrote {master_rel_path}. "
            f"Nothing pushed, will retry tomorrow.\n- Watson"
        )
        return

    for rel_path in archive_rel_paths:
        _git("add", rel_path)

    status = _git("status", "--porcelain", master_rel_path, *archive_rel_paths)
    if not status.stdout.strip():
        log.info("writing_digest: %s merge produced no changes", project)
        _set_state(conn, state_key, new_rows[-1]["id"])
        return

    _git("add", master_rel_path)
    commit = _git(
        "commit", "-m",
        f"writing_digest: {project} -- {len(new_rows)} new archive(s) incorporated",
    )
    if commit.returncode != 0:
        log.error("writing_digest: %s git commit failed: %s", project, commit.stderr.strip())
        send_telegram(f"Writing digest for {project}: git commit failed, will retry tomorrow.\n- Watson")
        return

    push = _git("push", "origin", "main")
    if push.returncode != 0:
        log.error("writing_digest: %s git push failed: %s", project, push.stderr.strip())
        send_telegram(
            f"Writing digest for {project}: committed locally but push failed "
            f"({push.stderr.strip()[:200]}), will retry the push tomorrow.\n- Watson"
        )
        return

    _set_state(conn, state_key, new_rows[-1]["id"])
    url = f"https://raw.githubusercontent.com/byomes/watson-review/main/{master_rel_path}"
    log.info("writing_digest: %s updated (%d new archives), pushed to %s", project, len(new_rows), url)
    send_telegram(
        f"Writing digest updated for {project} ({len(new_rows)} new session(s) "
        f"incorporated). Same link as always:\n{url}\n- Watson"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", help="Run just this project slug instead of ENABLED_PROJECTS")
    args = parser.parse_args()

    projects = [args.project] if args.project else ENABLED_PROJECTS
    for project in projects:
        run_digest(project)


if __name__ == "__main__":
    main()
