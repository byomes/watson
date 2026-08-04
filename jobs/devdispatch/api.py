"""jobs/devdispatch/api.py — MCP dispatcher for headless Claude Code jobs.

Mount on the Watson dashboard app:
    from jobs.devdispatch.api import devdispatch_bp
    app.register_blueprint(devdispatch_bp)

Implements the minimal MCP (Model Context Protocol) JSON-RPC surface needed
to register /mcp/devdispatch as a Claude.ai custom connector — initialize,
tools/list, tools/call — over a single POST endpoint (no streaming/SSE
needed since both tools return synchronously). See
MCP-Claude-Code-Dispatcher-Spec.md for the full spec.

Auth: X-Watson-Key header against MCP_DISPATCH_API_KEY (.env) — same
shared-secret pattern as Writing Room / bodyrec.

Invocation notes (confirmed empirically against CLI 2.1.221 — `--bg` rejects
`-p`/`--print` and `--output-format`, so there is no JSON-mode return value;
the session id has to be scraped from the "backgrounded · <id>" stdout line):
  claude --bg -w <branch_name> --permission-mode bypassPermissions
    --max-budget-usd 5 "<spec_text>"
`-w <name>` creates the worktree at a predictable path —
`~/<repo>/.claude/worktrees/<name>` — but the git branch Claude Code actually
creates is `worktree-<name>` (prefixed), not `<name>` verbatim. Claude Code
does not auto-commit; completion (commit/push/PR) is handled here, in
_finalize_completed_job, once `claude agents --json --all` reports the
session `state` as `done`. Sessions stay idle after finishing and must be
torn down explicitly with `claude rm <id>` — which itself refuses to run
until the worktree is clean, so finalize always commits/pushes (or confirms
there's nothing to commit) before attempting it.

`claude logs <id>` returns a raw ANSI/TUI transcript, not parseable plain
text — not used here. The `failed` job state has not been directly observed
(only `working`/`done`); any other state value is recorded verbatim in
`summary` rather than assumed.
"""
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from jobs.devdispatch.schema import ALLOWED_REPOS, create_tables

import requests

log = logging.getLogger(__name__)

devdispatch_bp = Blueprint("devdispatch", __name__)

create_tables()

_API_KEY = lambda: os.getenv("MCP_DISPATCH_API_KEY", "")
_PROTOCOL_VERSION = "2025-06-18"

_TOOLS = [
    {
        "name": "dispatch_claude_code_job",
        "description": (
            "Dispatch a headless Claude Code build job against a Watson-ecosystem "
            "repo. Runs on a feature branch only (never main); opens a PR when "
            "done. Never restarts services or deploys — Bill reviews, merges, "
            "pulls, and restarts manually."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "string",
                    "description": "The build spec / instructions for Claude Code.",
                },
                "repo": {
                    "type": "string",
                    "enum": list(ALLOWED_REPOS),
                    "description": "Which Watson-ecosystem repo to build against.",
                },
                "branch_name": {
                    "type": "string",
                    "description": "Optional feature branch name. Auto-generated if omitted. Must not be main/master.",
                },
            },
            "required": ["spec", "repo"],
        },
    },
    {
        "name": "check_claude_code_job",
        "description": "Check the status of a previously dispatched Claude Code job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The job_id returned by dispatch_claude_code_job.",
                },
            },
            "required": ["job_id"],
        },
    },
]


def _require_key() -> bool:
    key = _API_KEY()
    return bool(key) and request.headers.get("X-Watson-Key") == key


def _rpc_result(req_id, result):
    return jsonify({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id, code, message):
    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _tool_content(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


# ── Constants / small helpers ────────────────────────────────────────────

_MAX_BUDGET_USD = "5"
_LAUNCH_TIMEOUT_S = 20  # bound on `claude --bg` itself registering + returning; NOT the build
_BACKGROUNDED_RE = re.compile(r"backgrounded\s*[·\-]\s*([0-9a-fA-F]+)")
_TOKEN_URL_RE = re.compile(r"https://[^@\s]+@")


def _repo_path(repo: str) -> Path:
    return Path.home() / repo


def _worktree_path(repo: str, branch_name: str) -> Path:
    return _repo_path(repo) / ".claude" / "worktrees" / branch_name


def _git_branch_for(branch_name: str) -> str:
    return f"worktree-{branch_name}"


def _redact(text: str) -> str:
    return _TOKEN_URL_RE.sub("https://<redacted>@", text or "")


def _run_git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60
    )


def _telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("devdispatch: Telegram not configured — skipping: %s", text[:80])
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
    except Exception as exc:
        log.error("devdispatch: Telegram send failed: %s", exc)


def _update_job(job_id, **fields) -> None:
    fields["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE claude_code_jobs SET {set_clause} WHERE id = ?",
            (*fields.values(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _get_job_row(job_id):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM claude_code_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()


def _row_to_dict(row) -> dict:
    return {
        "job_id": row["id"],
        "repo": row["repo"],
        "branch": row["branch"],
        "status": row["status"],
        "pr_url": row["pr_url"],
        "summary": row["summary"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _open_pr(repo: str, git_branch: str, title: str, body: str):
    """Returns (pr_url, error) — error is None on success."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return None, "GITHUB_TOKEN not set"
    try:
        from github import Github
        gh_repo = Github(token).get_repo(f"byomes/{repo}")
        pr = gh_repo.create_pull(
            title=title, body=body, head=git_branch, base=gh_repo.default_branch
        )
        return pr.html_url, None
    except Exception as exc:
        return None, str(exc)


# ── Tool implementations ──────────────────────────────────────────────────

def _dispatch_claude_code_job(spec, repo, branch_name=None) -> dict:
    if not spec or not str(spec).strip():
        return {"error": "spec is required"}
    if repo not in ALLOWED_REPOS:
        return {"error": f"repo must be one of: {', '.join(ALLOWED_REPOS)}"}

    repo_path = _repo_path(repo)
    if not repo_path.is_dir():
        return {"error": f"{repo} is not cloned on this machine ({repo_path} does not exist)"}

    branch_name = (branch_name or "").strip()
    if not branch_name:
        branch_name = f"devdispatch/{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    if branch_name.lower() in ("main", "master"):
        return {"error": "branch_name may not be main or master — dispatched jobs are feature-branch only"}

    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO claude_code_jobs (spec_text, repo, branch, status) "
            "VALUES (?, ?, ?, 'queued')",
            (spec, repo, branch_name),
        )
        conn.commit()
        job_id = cursor.lastrowid
    finally:
        conn.close()

    # `--bg` rejects -p/--print and --output-format (confirmed against CLI
    # 2.1.221), so the spec is passed positionally and there is no JSON
    # return value — the session id is scraped from "backgrounded · <id>".
    # `claude --bg` itself registers and returns almost immediately; the
    # dispatched build continues detached regardless of what happens here,
    # so this short, bounded communicate() is not the same as waiting on
    # the build — it only waits on the launcher acknowledging the job.
    cmd = [
        "claude", "--bg", "-w", branch_name,
        "--permission-mode", "bypassPermissions",
        "--max-budget-usd", _MAX_BUDGET_USD,
        spec,
    ]
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(repo_path),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        stdout, stderr = proc.communicate(timeout=_LAUNCH_TIMEOUT_S)
    except Exception as exc:
        _update_job(job_id, status="failed", summary=f"launch failed: {exc}")
        _telegram(f"❌ devdispatch job {job_id} failed to launch — {exc}")
        return {"job_id": job_id, "status": "failed", "error": str(exc)}

    if proc.returncode != 0:
        err = _redact((stderr or stdout or "").strip())[:500]
        _update_job(job_id, status="failed", summary=f"launch failed (exit {proc.returncode}): {err}")
        _telegram(f"❌ devdispatch job {job_id} failed to launch — {err}")
        return {"job_id": job_id, "status": "failed", "error": err}

    match = _BACKGROUNDED_RE.search(stdout or "")
    if not match:
        err = _redact((stdout or stderr or "").strip())[:500]
        _update_job(job_id, status="failed", summary=f"could not parse session id from launch output: {err}")
        _telegram(f"❌ devdispatch job {job_id} launched but session id unparseable — {err}")
        return {"job_id": job_id, "status": "failed", "error": "could not parse session id from launch output"}

    cli_session_id = match.group(1)
    _update_job(job_id, status="running", cli_session_id=cli_session_id)
    log.info("devdispatch: job %d running (repo=%s, branch=%s, cli_session_id=%s)",
              job_id, repo, branch_name, cli_session_id)

    return {"job_id": job_id, "status": "running", "repo": repo, "branch": branch_name}


def _list_agents():
    result = subprocess.run(
        ["claude", "agents", "--json", "--all"], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(_redact((result.stderr or "").strip())[:300])
    return json.loads(result.stdout or "[]")


def _finalize_completed_job(row) -> dict:
    """Commit/push whatever the session produced, open a PR, notify, and
    tear down the background session. Runs synchronously inside a
    check_claude_code_job call once `claude agents` reports state=done —
    there is no separate poller; a job that's never checked just stays
    'running' with its background session idling until someone asks."""
    job_id = row["id"]
    repo = row["repo"]
    branch_name = row["branch"]
    git_branch = _git_branch_for(branch_name)
    worktree = _worktree_path(repo, branch_name)
    cli_id = row["cli_session_id"]

    if not worktree.is_dir():
        _update_job(job_id, status="failed", summary="worktree missing at completion")
        _telegram(f"❌ devdispatch job {job_id} — worktree missing at completion")
        return _row_to_dict(_get_job_row(job_id))

    status_proc = _run_git(["status", "--porcelain"], worktree)
    if status_proc.returncode != 0:
        err = _redact(status_proc.stderr.strip())[:500]
        _update_job(job_id, status="failed", summary=f"git status failed: {err}")
        _telegram(f"❌ devdispatch job {job_id} — git status failed: {err}")
        return _row_to_dict(_get_job_row(job_id))

    if status_proc.stdout.strip():
        # Uncommitted changes sitting in the worktree — Claude Code didn't
        # commit them itself, so do it here.
        first_line = row["spec_text"].strip().splitlines()[0][:72]
        commit_msg = f"devdispatch: {first_line}"

        add_proc = _run_git(["add", "-A"], worktree)
        if add_proc.returncode != 0:
            err = _redact(add_proc.stderr.strip())[:500]
            _update_job(job_id, status="failed", summary=f"git add failed: {err}")
            _telegram(f"❌ devdispatch job {job_id} — git add failed: {err}")
            return _row_to_dict(_get_job_row(job_id))

        commit_proc = _run_git(["commit", "-m", commit_msg], worktree)
        if commit_proc.returncode != 0:
            err = _redact((commit_proc.stderr or commit_proc.stdout).strip())[:500]
            _update_job(job_id, status="failed", summary=f"git commit failed: {err}")
            _telegram(f"❌ devdispatch job {job_id} — git commit failed: {err}")
            return _row_to_dict(_get_job_row(job_id))

    # A clean working tree does NOT mean "nothing happened" — confirmed
    # 2026-08-04: with bypassPermissions and no instruction against it, a
    # dispatched session sometimes commits its own work unprompted. Check
    # whether HEAD is actually ahead of main rather than trusting `git
    # status` alone.
    ahead_proc = _run_git(["rev-list", "--count", "main..HEAD"], worktree)
    if ahead_proc.returncode != 0:
        err = _redact(ahead_proc.stderr.strip())[:500]
        _update_job(job_id, status="failed", summary=f"git rev-list failed: {err}")
        _telegram(f"❌ devdispatch job {job_id} — git rev-list failed: {err}")
        return _row_to_dict(_get_job_row(job_id))

    if int((ahead_proc.stdout or "0").strip() or "0") == 0:
        summary = "Completed — no commits produced (branch is even with main)."
        _update_job(job_id, status="done", summary=summary)
        try:
            subprocess.run(["claude", "rm", cli_id], capture_output=True, text=True, timeout=30)
        except Exception as exc:
            log.warning("devdispatch: claude rm %s failed (no-op job): %s", cli_id, exc)
        _telegram(f"ℹ️ devdispatch job {job_id} done — no changes produced.")
        return _row_to_dict(_get_job_row(job_id))

    # PR title reflects what actually landed in the last commit — accurate
    # whether we just committed above or the session committed on its own.
    subject_proc = _run_git(["log", "-1", "--format=%s"], worktree)
    pr_title = (subject_proc.stdout or "").strip() or f"devdispatch job #{job_id}"

    push_proc = _run_git(["push", "-u", "origin", git_branch], worktree)
    if push_proc.returncode != 0:
        err = _redact((push_proc.stderr or push_proc.stdout).strip())[:500]
        _update_job(job_id, status="failed", summary=f"git push failed (committed locally): {err}")
        _telegram(f"❌ devdispatch job {job_id} — git push failed: {err}")
        return _row_to_dict(_get_job_row(job_id))

    pr_body = f"Dispatched via MCP devdispatch job #{job_id}.\n\nSpec:\n{row['spec_text']}"
    pr_url, pr_err = _open_pr(repo, git_branch, pr_title, pr_body)
    if pr_err:
        summary = f"Pushed {git_branch} but PR creation failed: {pr_err}"
        _update_job(job_id, status="failed", summary=summary)
        _telegram(f"⚠️ devdispatch job {job_id} — pushed but PR failed: {pr_err}\nBranch: {git_branch}")
        return _row_to_dict(_get_job_row(job_id))

    summary = f"PR opened: {pr_url}"
    _update_job(job_id, status="done", pr_url=pr_url, summary=summary)
    try:
        subprocess.run(["claude", "rm", cli_id], capture_output=True, text=True, timeout=30)
    except Exception as exc:
        log.warning("devdispatch: claude rm %s failed after PR: %s", cli_id, exc)
    _telegram(f"✅ devdispatch job {job_id} done — {pr_url}")
    return _row_to_dict(_get_job_row(job_id))


def _check_claude_code_job(job_id) -> dict:
    if job_id is None:
        return {"error": "job_id is required"}

    row = _get_job_row(job_id)
    if not row:
        return {"error": f"no job with id {job_id}"}

    if row["status"] in ("done", "failed", "expired"):
        return _row_to_dict(row)

    # status is 'queued' or 'running' — cross-reference the live session.
    try:
        agents = _list_agents()
    except Exception as exc:
        log.error("devdispatch: claude agents --json failed: %s", exc)
        result = _row_to_dict(row)
        result["note"] = f"could not query claude agents: {exc}"
        return result

    match = next(
        (a for a in agents if a.get("id") == row["cli_session_id"]
         or a.get("sessionId", "").startswith(row["cli_session_id"] or "\0")),
        None,
    )

    if match is None:
        result = _row_to_dict(row)
        result["note"] = "session not found in `claude agents` — may have exited unexpectedly"
        return result

    state = match.get("state")
    if state == "working":
        result = _row_to_dict(row)
        result["note"] = "running — log tail unavailable (`claude logs` returns a raw terminal transcript, not parseable text)"
        return result

    if state == "done":
        return _finalize_completed_job(row)

    # A state other than working/done has not been observed empirically —
    # record it verbatim rather than assume what it means.
    summary = f"unrecognized claude agents state: {state!r}"
    _update_job(job_id, status="failed", summary=summary)
    _telegram(f"❌ devdispatch job {job_id} — {summary}")
    return _row_to_dict(_get_job_row(job_id))


_TOOL_IMPLS = {
    "dispatch_claude_code_job": lambda args: _dispatch_claude_code_job(
        args.get("spec"), args.get("repo"), args.get("branch_name")
    ),
    "check_claude_code_job": lambda args: _check_claude_code_job(args.get("job_id")),
}


# ── MCP JSON-RPC endpoint ──────────────────────────────────────────────────

@devdispatch_bp.route("/mcp/devdispatch", methods=["POST"])
def mcp_endpoint():
    if not _require_key():
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True) or {}
    req_id = body.get("id")
    method = body.get("method")

    if method == "initialize":
        return _rpc_result(req_id, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "watson-devdispatch", "version": "0.1.0"},
        })

    if method == "notifications/initialized":
        return "", 204

    if method == "tools/list":
        return _rpc_result(req_id, {"tools": _TOOLS})

    if method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        impl = _TOOL_IMPLS.get(name)
        if impl is None:
            return _rpc_error(req_id, -32602, f"unknown tool: {name}")
        try:
            result = impl(args)
        except Exception as exc:
            log.error("devdispatch tool %s failed: %s", name, exc)
            return _rpc_error(req_id, -32000, str(exc))
        return _rpc_result(req_id, _tool_content(result))

    return _rpc_error(req_id, -32601, f"unknown method: {method}")
