"""jobs/devdispatch/poller.py — Scheduled poller for devdispatch jobs.

Closes the gap documented in jobs/devdispatch/api.py's module docstring:
check_claude_code_job only cross-references `claude agents --json --all`
and finalizes (commit/push/PR/Telegram) when someone actually calls it —
a job that's never checked sits at 'queued'/'running' indefinitely with
its background session idling. This script exercises that exact same
check on a schedule instead, so Bill gets the "✅ devdispatch job {id}
done — {pr_url}" Telegram message automatically.

Reuses jobs.devdispatch.api._check_claude_code_job directly — it already
owns the claude-agents cross-reference, _finalize_completed_job (commit/
push/PR/Telegram), and failure handling. Nothing here duplicates that
logic; this is purely "which job ids need checking, and don't overlap
with a previous run."

Additive only — dispatch_claude_code_job and check_claude_code_job (the
manual, on-demand path) are untouched and keep working exactly as before.

Cron (every 2 minutes):
  */2 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/devdispatch/poller.py \
    >> /home/billyomes/watson/logs/devdispatch_poller.log 2>&1
"""
import fcntl
import json
import logging
import os
import subprocess
from pathlib import Path

from core.database import get_connection
from jobs.devdispatch.api import (
    _check_claude_code_job, _get_job_row, _merge_claude_code_job, _repo_path,
    _PR_URL_RE, _telegram, _worktree_path,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [devdispatch.poller] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "data" / ".devdispatch_poller.lock"


def _pending_job_ids() -> list[int]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM claude_code_jobs WHERE status IN ('queued', 'running') ORDER BY id"
        ).fetchall()
        return [row["id"] for row in rows]
    finally:
        conn.close()


def _check_progress(job_id: int) -> None:
    """Read .devdispatch/progress.json out of the job's worktree (see
    api.py's _PROGRESS_PROTOCOL) and, if the reported step has advanced past
    last_progress_step, send a Telegram update and record the new step.
    Silently does nothing if the worktree/file don't exist yet or the file
    is mid-write (partial/malformed JSON) — this is a best-effort progress
    signal, not the terminal-state finalize path."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT repo, branch, last_progress_step FROM claude_code_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return

    worktree = _worktree_path(row["repo"], row["branch"])
    progress_path = worktree / ".devdispatch" / "progress.json"
    if not worktree.is_dir() or not progress_path.is_file():
        return

    try:
        data = json.loads(progress_path.read_text())
    except (OSError, ValueError):
        return

    step = data.get("step")
    if not isinstance(step, int) or step <= row["last_progress_step"]:
        return

    label = data.get("label", "")
    detail = data.get("detail", "")
    _telegram(f"🔧 devdispatch job {job_id}: {step}/5 {label}: {detail}")

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE claude_code_jobs SET last_progress_step = ? WHERE id = ?",
            (step, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _record_suggestion_outcome(suggestion_id, status, detail) -> None:
    if not suggestion_id:
        return
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE fast_path_suggestions SET status=?, applied_detail=?, resolved_at=datetime('now') WHERE id=?",
            (status, detail, suggestion_id),
        )
        conn.commit()
    finally:
        conn.close()


def _log_auto_fix(job_id: int, repo: str, pr_url: str | None, suggestion_id, deployed: bool) -> None:
    """Best-effort entry in jobs.dev.fix_log for a job that just merged
    (and, for watson, deployed) with no human review -- see that module's
    docstring for why Bill wanted this durable record."""
    try:
        from jobs.dev.fix_log import log_fix
        example_question = None
        if suggestion_id:
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT example_question FROM fast_path_suggestions WHERE id = ?", (suggestion_id,)
                ).fetchone()
                example_question = row["example_question"] if row else None
            finally:
                conn.close()
        title = f'Fast-path auto-fix: "{example_question}"' if example_question else f"Fast-path auto-fix (job {job_id})"
        description = "Merged and deployed automatically, no review." if deployed else "Merged automatically; deploy to this repo still manual."
        log_fix(title=title, description=description, repo=repo, source="fast_path_dispatch", pr_url=pr_url)
    except Exception:
        pass  # best-effort log -- never blocks the actual fix from landing


# Per Bill's 2026-09-16 direction, after PR #62 (job 52) auto-merged a
# routing-logic change to bot.py's compute_team_chat_reply with zero
# review: auto-merge stays limited to the exact file the already-safe
# _auto_apply path edits directly (jobs/skills/cdb_query.py -- see
# fast_path_patcher.py's own file-scope docstring for why that one file is
# considered safe: pure lookup trigger-phrase additions, validated with
# ast.parse() before write). A dispatched fix that touches anything else --
# bot.py's message routing, a write path like family_edit.py, or a new
# file -- is a judgment call, not a mechanical one, so it stops short of
# auto-merge and waits for Bill same as every other (non-fast-path)
# devdispatch job already does.
_LOOKUP_ONLY_ALLOWED_FILES = {"jobs/skills/cdb_query.py"}
_LOOKUP_ONLY_IGNORED_FILES = {".devdispatch/progress.json"}


def _is_lookup_only_pr(pr_url: str) -> bool:
    """True only if every file this PR touches (other than the job's own
    .devdispatch/progress.json bookkeeping) is jobs/skills/cdb_query.py.
    Returns False -- never auto-merge -- if the file list can't even be
    fetched, so a GitHub API hiccup fails closed toward review, not toward
    silently shipping an unreviewed routing change."""
    match = _PR_URL_RE.match(pr_url or "")
    if not match:
        return False
    owner, repo_name, pr_number = match.group(1), match.group(2), int(match.group(3))
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return False
    try:
        from github import Github
        gh_repo = Github(token).get_repo(f"{owner}/{repo_name}")
        pr = gh_repo.get_pull(pr_number)
        changed = {f.filename for f in pr.get_files()} - _LOOKUP_ONLY_IGNORED_FILES
    except Exception as exc:
        log.error("could not fetch changed files for %s: %s", pr_url, exc)
        return False
    return bool(changed) and changed.issubset(_LOOKUP_ONLY_ALLOWED_FILES)


def _auto_merge_and_deploy(job_id: int) -> None:
    """Called right after a job transitions to 'done' (PR opened) for a job
    dispatched with auto_merge=1 -- merges immediately with no approval
    step, then pulls + restarts the live watson services so the fix is
    actually live, not just merged into main. See _merge_claude_code_job's
    docstring for the Bill-authorized (2026-09-15) exception this is,
    scoped to jobs.analytics.fast_path_suggestions dispatches only -- and
    _is_lookup_only_pr's docstring above for the 2026-09-16 narrowing of
    that exception to lookup-only changes."""
    row = _get_job_row(job_id)
    if row is None or not row["auto_merge"]:
        return
    source_suggestion_id = row["source_suggestion_id"]
    repo = row["repo"]

    if not _is_lookup_only_pr(row["pr_url"]):
        with get_connection() as conn:
            conn.execute("UPDATE claude_code_jobs SET auto_merge=0 WHERE id=?", (job_id,))
            conn.commit()
        _telegram(
            f"🔍 devdispatch job {job_id} built a fix that goes beyond a simple lookup addition "
            f"(touches routing/behavior, not just jobs/skills/cdb_query.py) -- holding for your "
            f"review instead of auto-merging.\n\n{row['pr_url']}\n\n- Watson"
        )
        _record_suggestion_outcome(
            source_suggestion_id, "needs_review",
            f"PR opened but not lookup-only -- held for manual review: {row['pr_url']}",
        )
        return

    result = _merge_claude_code_job(job_id)
    status = result.get("status")
    if status not in ("merged", "already_merged"):
        err = result.get("error", "unknown error")
        _telegram(
            f"⚠️ devdispatch job {job_id} auto-fix built a PR but couldn't "
            f"auto-merge: {err}\nStopping short of deploy, needs a manual look."
        )
        _record_suggestion_outcome(source_suggestion_id, "failed", f"PR opened but auto-merge failed: {err}")
        return

    if repo != "watson":
        # Only watson's live services are ours to restart here -- an
        # auto-merged fix to another repo still needs its own manual deploy
        # step. This trigger only ever targets watson in practice (fast
        # path suggestions are all about jobs/skills/cdb_query.py, bot.py,
        # jobs/location/*), but this guard keeps that from silently
        # expanding if that ever changes.
        _record_suggestion_outcome(
            source_suggestion_id, "applied", f"Merged (devdispatch job {job_id}) — deploy to {repo} still manual."
        )
        _log_auto_fix(job_id, repo, result.get("pr_url"), source_suggestion_id, deployed=False)
        return

    try:
        pull = subprocess.run(
            ["git", "pull"], cwd=str(_repo_path("watson")), capture_output=True, text=True, timeout=60,
        )
        if pull.returncode != 0:
            raise RuntimeError((pull.stderr or pull.stdout or "git pull failed").strip()[:300])
        subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", "restart", "watson-dashboard.service"], timeout=15, check=True,
        )
        # Fire-and-forget, same reasoning as fast_path_patcher.apply_and_
        # deploy(): `systemctl restart` on watson-bot.service sends SIGTERM
        # to whatever's calling it when the caller IS that service, and
        # blocks until it exits -- but the poller itself isn't watson-bot,
        # so this is just consistency with the established safe pattern.
        subprocess.Popen(["sudo", "-n", "/usr/bin/systemctl", "restart", "watson-bot.service"])
    except Exception as exc:
        _telegram(
            f"⚠️ devdispatch job {job_id} merged but deploy failed: {exc}\n"
            f"Code is on main but NOT live yet: needs `git pull` + a service restart by hand."
        )
        _record_suggestion_outcome(source_suggestion_id, "failed", f"Merged (devdispatch job {job_id}) but deploy failed: {exc}")
        return

    _telegram(
        f"✅ Auto-fixed and deployed (devdispatch job {job_id}, no review needed): "
        f"the Team Chat gap that triggered this is closed.\n- Watson"
    )
    _record_suggestion_outcome(
        source_suggestion_id, "applied", f"Auto-dispatched, merged, and deployed via devdispatch job {job_id}.",
    )
    _log_auto_fix(job_id, repo, result.get("pr_url"), source_suggestion_id, deployed=True)


def poll() -> None:
    job_ids = _pending_job_ids()
    if not job_ids:
        return

    log.info("checking %d job(s): %s", len(job_ids), job_ids)
    for job_id in job_ids:
        try:
            result = _check_claude_code_job(job_id)
        except Exception as exc:
            log.error("job %d: check failed: %s", job_id, exc)
        else:
            status = result.get("status")
            if status in ("done", "failed"):
                log.info("job %d: transitioned to %s (%s)", job_id, status, result.get("summary"))
                if status == "done":
                    try:
                        _auto_merge_and_deploy(job_id)
                    except Exception as exc:
                        log.error("job %d: auto-merge/deploy raised: %s", job_id, exc)
                        _telegram(f"⚠️ devdispatch job {job_id} auto-merge/deploy step crashed: {exc}")
            else:
                log.info("job %d: still %s", job_id, status)

        try:
            _check_progress(job_id)
        except Exception as exc:
            log.error("job %d: progress check failed: %s", job_id, exc)


def main() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("previous poller run still in progress — skipping this tick")
            return
        try:
            poll()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


if __name__ == "__main__":
    main()
