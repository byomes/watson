"""jobs/devdispatch/api.py — MCP dispatcher for headless Claude Code jobs.

Mount on the Watson dashboard app:
    from jobs.devdispatch.api import devdispatch_bp
    app.register_blueprint(devdispatch_bp)

Implements the minimal MCP (Model Context Protocol) JSON-RPC surface needed
to register /mcp/devdispatch as a Claude.ai custom connector — initialize,
tools/list, tools/call — over a single POST endpoint (no streaming/SSE
needed since all tools return synchronously). See
MCP-Claude-Code-Dispatcher-Spec.md for the full spec.

Auth: two independent paths, either satisfies it —
  1. X-Watson-Key header against MCP_DISPATCH_API_KEY (.env) — same
     shared-secret pattern as Writing Room / bodyrec.
  2. Authorization: Bearer <token> — a token issued by the OAuth 2.1
     authorization-code shim below.

OAuth shim (added 2026-08-04): Claude.ai's custom-connector UI only offers
an interactive OAuth flow (authorization_code + mandatory S256 PKCE) —
confirmed it does NOT support client_credentials, so a machine-to-machine
token endpoint alone is unreachable from that UI. This is a minimal,
single-user shim (Bill only) around that requirement, not real multi-user
OAuth: /oauth/authorize auto-approves (no login/consent screen — there's
only one user) but still strictly validates client_id and redirect_uri as
exact matches before issuing anything, and requires PKCE. Endpoints:
  GET  /mcp/devdispatch/.well-known/oauth-protected-resource  (RFC 9728)
  GET  /mcp/devdispatch/.well-known/oauth-authorization-server (RFC 8414)
  GET  /mcp/devdispatch/oauth/authorize   — issues a short-lived code
  POST /mcp/devdispatch/oauth/token       — authorization_code grant only
No dynamic client registration (RFC 7591) — Bill pastes a fixed
MCP_OAUTH_CLIENT_ID/SECRET into the connector's Advanced settings instead,
which the MCP spec explicitly allows as the alternative to DCR. Access
tokens are opaque (checked against devdispatch_oauth_tokens), not signed
JWTs, and live 90 days — no refresh-token grant is implemented, so this is
a deliberate simplicity tradeoff against the OAuth 2.1 "SHOULD issue
short-lived tokens" guidance, justified by this being single-user with no
one else to leak a token to. NOT independently verified against Claude.ai's
actual client end-to-end (would require Bill's browser session) — the
individual pieces (PKCE math, redirect/client_id strict-match rejection,
code single-use, bearer-token acceptance) were tested directly.

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
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from flask import Blueprint, jsonify, redirect, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from jobs.devdispatch.schema import ALLOWED_REPOS, create_tables
from jobs.session_archives import storage as _archives
from jobs.session_archives.schema import create_tables as _create_archive_tables
from jobs.skillbuilder import router as _skill_router

import requests

log = logging.getLogger(__name__)

devdispatch_bp = Blueprint("devdispatch", __name__)

create_tables()
_create_archive_tables()

_API_KEY = lambda: os.getenv("MCP_DISPATCH_API_KEY", "")
_PROTOCOL_VERSION = "2025-06-18"

# Public Tailscale Funnel URL (see WATSON_ARCHITECTURE.md) — hardcoded
# rather than derived from request.host_url, since RFC 8707 requires a
# stable canonical resource URI and only the Funnel URL is reachable from
# Claude.ai's servers in the first place.
_BASE_URL = "https://watson.tail0243ff.ts.net"
_RESOURCE_URL = f"{_BASE_URL}/mcp/devdispatch"

# The only redirect_uri this shim will ever issue a code against — Claude.ai's
# actual OAuth callback, confirmed 2026-08-04 (not guessed).
_REGISTERED_REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"

_AUTH_CODE_TTL_S = 120
_ACCESS_TOKEN_TTL_S = 60 * 60 * 24 * 90  # 90 days — see module docstring

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
    {
        "name": "merge_claude_code_job",
        "description": (
            "Merge a previously dispatched Claude Code job's PR into main, after "
            "verifying it is still open, has no merge conflicts, and has no "
            "failing status checks. Only call this on an explicit per-job "
            "approval from Bill in that conversation turn — never call it "
            "proactively, automatically on job completion, or as a batch. This "
            "replaces manually clicking Merge on GitHub; it does not replace "
            "Bill's review."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer", "description": "The job_id to merge (from dispatch_claude_code_job or check_claude_code_job)."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "run_watson_skill",
        "description": (
            "Run one of Watson's existing skills (bible lookup, web search, "
            "contacts lookup, KB search, etc.) synchronously and return its "
            "result. Only a curated, read-only/low-risk subset of Watson's "
            "skill registry is exposed here — see list_watson_skills for the "
            "current set. Does not cover shell execution, credentials, email "
            "sending, or pastoral/congregant lookups — those stay "
            "dashboard/Telegram-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The natural-language request to route to a Watson skill, exactly as Bill would type it in the dashboard or Telegram.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "list_watson_skills",
        "description": "List the Watson skills exposed to this connector, with descriptions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "archive_session",
        "description": (
            "Archive an entire Claude.ai session — full verbatim transcript plus "
            "any files created during it — to Watson for durable, backed-up "
            "storage. Call this every time Bill says 'send to watson' (or a close "
            "variant like 'save this session to watson' / 'archive this to "
            "watson'): send the complete transcript, never a summary in its "
            "place. Every archive is filed under a project — if you know which "
            "one (this session is clearly part of a specific book/course/app "
            "project), name it; if not, pass 'general' and Watson will try to "
            "classify it itself from the title/summary against known projects, "
            "falling back to a general catch-all only if nothing matches "
            "confidently — in that case the response may include a "
            "'possible_duplicate' field if another project already has an "
            "archive opening with nearly the same text (same conversation "
            "re-archived under a different project slug); check it before "
            "assuming this is a genuinely new archive. Files can be given "
            "inline as base64, or by file_ref from stage_archive_file for a "
            "large file that shouldn't be base64-encoded and sent more than "
            "once; anything over roughly 8MB per file is rejected individually "
            "and reported back in skipped_files, never silently dropped. The "
            "response includes a 'warnings' field if the transcript or any "
            "file looks suspiciously small — treat that as a signal to double "
            "check before assuming the archive is complete. Never pass a real "
            "project slug for throwaway/test archives — use '_test' (any slug "
            "starting with '_' is reserved for internal testing and is never "
            "an auto-classify target) so test data never lands in real project "
            "history. Retrieve archives later with the list_archives, "
            "search_archives, get_archive, list_projects, and get_project_summary "
            "tools — those work even from a future session with no memory of "
            "this one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "transcript": {
                    "type": "string",
                    "description": "The full, verbatim conversation transcript for this session — not a summary.",
                },
                "files": {
                    "type": "array",
                    "description": "Files created during the session (drafts, docs, code). Pass an empty array if none. Each item needs either content_base64 (inline) or file_ref (from a prior stage_archive_file call) — not both.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "content_base64": {"type": "string", "description": "Inline base64 file content. Omit if using file_ref instead."},
                            "file_ref": {"type": "string", "description": "A token from stage_archive_file, referencing an already-uploaded file instead of inline content_base64."},
                        },
                        "required": ["filename"],
                    },
                },
                "project": {
                    "type": "string",
                    "description": "Project slug this session belongs to (e.g. 'curator', 'comms-desk'), if known. Required field — pass 'general' explicitly when unsure, which triggers Watson's own classifier (title/summary similarity against known projects) rather than leaving it in a generic bucket. For throwaway/test archives, use '_test' instead of a real project or 'general'.",
                },
                "title": {
                    "type": "string",
                    "description": "A short, scannable one-line title for this session, written by Claude.ai.",
                },
                "summary": {
                    "type": "string",
                    "description": "A few-sentence recap of what happened this session and what's next, written by Claude.ai — appended to the project's rolling catch-up file.",
                },
            },
            "required": ["transcript", "files", "project", "title", "summary"],
        },
    },
    {
        "name": "list_archives",
        "description": (
            "List archived Claude.ai sessions, newest first, optionally filtered "
            "to one project. Superseded archives (see mark_archive_superseded) "
            "are hidden by default. Use get_archive with an id from here to "
            "read a specific archive's full transcript."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project slug to filter to, e.g. 'curator'. Omit to list across all projects."},
                "include_superseded": {"type": "boolean", "description": "Include archives marked superseded. Defaults to false."},
            },
        },
    },
    {
        "name": "search_archives",
        "description": (
            "Full-text search over archived session titles and transcripts. "
            "Superseded archives are excluded by default."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms, e.g. 'retreat budget decision'."},
                "include_superseded": {"type": "boolean", "description": "Include archives marked superseded. Defaults to false."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_archive",
        "description": (
            "Fetch one archived session by id — its full verbatim transcript "
            "and the list of attached files, or (if filename is given) one "
            "attached file's content, base64-encoded. Get ids from "
            "list_archives or search_archives."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "The archive id, from list_archives/search_archives."},
                "filename": {"type": "string", "description": "If given, return this one attached file's base64 content instead of the transcript."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "list_projects",
        "description": "List every project with archived sessions, with archive counts and the most recent archive date. Superseded archives are excluded from counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_project_summary",
        "description": "Get a project's rolling catch-up summary (newest entry first) — a quick recap without pulling every individual archive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project slug, e.g. 'curator'."},
            },
            "required": ["project"],
        },
    },
    {
        "name": "mark_archive_superseded",
        "description": (
            "Mark a bad archive (truncated, wrong content, botched upload) as "
            "superseded by a corrected one. The bad archive is kept — nothing "
            "is deleted — but it's hidden from list_archives/search_archives/ "
            "list_projects by default so it stops cluttering retrieval. Pass "
            "superseded_by omitted or null to un-mark an archive."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "The bad archive's id to hide."},
                "superseded_by": {"type": "integer", "description": "The id of the corrected archive that replaces it."},
            },
            "required": ["id", "superseded_by"],
        },
    },
    {
        "name": "reclassify_archive",
        "description": (
            "Move an existing archive into a different project — fixes a "
            "misclassification (e.g. the auto-classifier filed it under the "
            "wrong project, or a possible_duplicate turned out to be a genuine "
            "misfile rather than a real duplicate). Relocates the archive's "
            "files on disk and updates its project. Nothing is deleted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "The archive id to move."},
                "project": {"type": "string", "description": "The correct project slug to file it under."},
            },
            "required": ["id", "project"],
        },
    },
    {
        "name": "stage_archive_file",
        "description": (
            "Upload one file's base64 content to Watson ahead of an "
            "archive_session call, getting back a file_ref token — use this "
            "instead of putting content_base64 directly in archive_session's "
            "files list when a large file might need to be staged, checked, "
            "or retried before the archive actually commits, so the bytes "
            "don't have to be re-sent. The staged file is single-use: "
            "consumed and deleted the moment archive_session's files list "
            "references its file_ref, or purged automatically after 24 hours "
            "if never used."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "The file's name."},
                "content_base64": {"type": "string", "description": "The file's full content, base64-encoded."},
            },
            "required": ["filename", "content_base64"],
        },
    },
]


def _cleanup_oauth_state() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM devdispatch_oauth_codes WHERE expires_at <= datetime('now')")
        conn.execute("DELETE FROM devdispatch_oauth_tokens WHERE expires_at <= datetime('now')")
        conn.commit()
    finally:
        conn.close()


def _valid_bearer_token(token: str) -> bool:
    if not token:
        return False
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM devdispatch_oauth_tokens WHERE access_token = ? AND expires_at > datetime('now')",
            (token,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _is_authorized() -> bool:
    """True if either auth path succeeds: the original X-Watson-Key shared
    secret, or a bearer token issued by the OAuth shim."""
    key = _API_KEY()
    if key and request.headers.get("X-Watson-Key") == key:
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return _valid_bearer_token(auth_header[len("Bearer "):].strip())
    return False


def _rpc_result(req_id, result):
    return jsonify({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id, code, message, error_code=None):
    # error_code (in `data`, alongside the numeric JSON-RPC `code`) lets a
    # caller tell apart a malformed request ("bad_request"), a tool that
    # genuinely doesn't exist ("not_found"), and an unhandled exception on
    # Watson's side ("internal_error") — all three used to surface as an
    # equally opaque string, indistinguishable from a transient connector/
    # network failure that never reaches this function at all.
    error = {"code": code, "message": message}
    if error_code:
        error["data"] = {"error_code": error_code}
    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": error})


def _tool_content(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


# ── Constants / small helpers ────────────────────────────────────────────

_PROGRESS_PROTOCOL = '''

PROGRESS REPORTING (required): as you work, write your current phase to .devdispatch/progress.json in the repo root (create the .devdispatch/ directory if it doesn't exist), overwriting the file each time, in this exact JSON shape:
  {"step": N, "total": 5, "label": "<phase>", "detail": "<one short sentence>"}
Write it after completing each of these five phases in order — not all tasks weight each phase equally (a pure diagnostic may barely touch Coding), but pass through all five in order so progress is visible from outside this session:
  1. Reading   — pulled relevant docs/architecture, read the existing code this task touches
  2. Speccing  — confirmed scope, decided what will actually change
  3. Coding    — implemented the change
  4. Testing   — ran py_compile / build / tests as applicable
  5. Reporting — committing, pushing, opening a PR (or writing the diagnostic deliverable)
'''

_MAX_BUDGET_USD = "5"
_LAUNCH_TIMEOUT_S = 20  # bound on `claude --bg` itself registering + returning; NOT the build
_BACKGROUNDED_RE = re.compile(r"backgrounded\s*[·\-]\s*([0-9a-fA-F]+)")
_TOKEN_URL_RE = re.compile(r"https://[^@\s]+@")
_PR_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)$")
_FAILING_STATUS_STATES = ("failure", "error")
_FAILING_CHECK_CONCLUSIONS = ("failure", "timed_out", "cancelled", "action_required")

# watson-dashboard.service runs under systemd's default PATH, which does not
# include nvm's shim/version directory — `shutil.which("claude")` (and a bare
# "claude" argv entry) resolves fine in an interactive shell but fails with
# FileNotFoundError under the service. Resolve once at import time to an
# absolute path instead of relying on PATH resolution at call time: an
# explicit CLAUDE_BIN env override wins if set, then shutil.which() (covers
# the case where PATH is ever fixed at the service level instead), then the
# nvm path confirmed via `which claude` in an interactive shell 2026-08-04.
_CLAUDE_BIN = (
    os.getenv("CLAUDE_BIN")
    or shutil.which("claude")
    or "/home/billyomes/.nvm/versions/node/v24.16.0/bin/claude"
)


def _repo_path(repo: str) -> Path:
    return Path.home() / repo


# `-w <branch_name>` cannot create a nested directory for a worktree when
# branch_name contains "/" (e.g. the default auto-generated
# "devdispatch/<timestamp>" format) — confirmed empirically 2026-08-04 via
# `claude agents --json --all` and `ls -la ~/watson/.claude/worktrees/` on
# the live Beelink: the CLI silently substitutes "+" for "/" in the on-disk
# worktree directory name (e.g. "devdispatch/20260804-115219" becomes
# ".claude/worktrees/devdispatch+20260804-115219"). This does NOT affect the
# git branch name itself (see _git_branch_for) — only the worktree directory
# path. Caused 2 of 3 real dispatched jobs to be misreported as "failed"
# (worktree missing at completion) despite completing successfully.
def _worktree_dirname(branch_name: str) -> str:
    return branch_name.replace("/", "+")


def _worktree_path(repo: str, branch_name: str) -> Path:
    return _repo_path(repo) / ".claude" / "worktrees" / _worktree_dirname(branch_name)


def _git_branch_for(branch_name: str) -> str:
    return f"worktree-{_worktree_dirname(branch_name)}"


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
        from github import Github, GithubException
        gh_repo = Github(token).get_repo(f"byomes/{repo}")
        try:
            pr = gh_repo.create_pull(
                title=title, body=body, head=git_branch, base=gh_repo.default_branch
            )
            return pr.html_url, None
        except GithubException as exc:
            # The dispatched Claude Code session itself may already have
            # opened a PR for this branch before finishing (its own
            # end-of-task convention) — GitHub then replies 422 "A pull
            # request already exists" to our create_pull call rather than
            # handing back the existing PR. That's not a real failure, just
            # a create-vs-lookup mismatch, so treat it as success and look
            # the existing PR up instead of surfacing an error with no
            # usable link. Any other GithubException (permissions, bad
            # base/head, etc.) still falls through to the generic handler
            # below and is reported as a real failure.
            msg = str(exc)
            if exc.status == 422 and "A pull request already exists" in msg:
                existing = gh_repo.get_pulls(state="all", head=f"byomes:{git_branch}")
                match = next(iter(existing), None)
                if match is not None:
                    return match.html_url, None
            return None, msg
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
        _CLAUDE_BIN, "--bg", "-w", branch_name,
        "--permission-mode", "bypassPermissions",
        "--max-budget-usd", _MAX_BUDGET_USD,
        spec + _PROGRESS_PROTOCOL,
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
        [_CLAUDE_BIN, "agents", "--json", "--all"], capture_output=True, text=True, timeout=30
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

    # Worktrees of the same repo share refs, so the local `main` pointer in
    # THIS worktree does not move on its own when another job — running
    # concurrently or moments earlier in a different worktree of the same
    # repo — merges a PR into main via GitHub. Comparing against a stale
    # local `main` let the ahead-count check below report commits as
    # "ahead" when they were actually already present on origin/main,
    # which then made _open_pr() fail with a 422 "No commits between main
    # and <branch>" once GitHub compared against the real, current
    # origin/main (bug_tracker #95, jobs 36 & 38, 2026-08-16). Fetch first
    # and compare against origin/main, not local main, everywhere below.
    fetch_proc = _run_git(["fetch", "origin", "main"], worktree)
    if fetch_proc.returncode != 0:
        err = _redact((fetch_proc.stderr or fetch_proc.stdout).strip())[:500]
        _update_job(job_id, status="failed", summary=f"git fetch origin main failed: {err}")
        _telegram(f"❌ devdispatch job {job_id} — git fetch origin main failed: {err}")
        return _row_to_dict(_get_job_row(job_id))

    # A clean working tree does NOT mean "nothing happened" — confirmed
    # 2026-08-04: with bypassPermissions and no instruction against it, a
    # dispatched session sometimes commits its own work unprompted. Check
    # whether HEAD is actually ahead of origin/main rather than trusting
    # `git status` alone.
    ahead_proc = _run_git(["rev-list", "--count", "origin/main..HEAD"], worktree)
    if ahead_proc.returncode != 0:
        err = _redact(ahead_proc.stderr.strip())[:500]
        _update_job(job_id, status="failed", summary=f"git rev-list failed: {err}")
        _telegram(f"❌ devdispatch job {job_id} — git rev-list failed: {err}")
        return _row_to_dict(_get_job_row(job_id))

    if int((ahead_proc.stdout or "0").strip() or "0") == 0:
        summary = "Completed — no commits produced (branch is even with origin/main)."
        _update_job(job_id, status="done", summary=summary)
        try:
            subprocess.run([_CLAUDE_BIN, "rm", cli_id], capture_output=True, text=True, timeout=30)
        except Exception as exc:
            log.warning("devdispatch: claude rm %s failed (no-op job): %s", cli_id, exc)
        _telegram(f"ℹ️ devdispatch job {job_id} done — no changes produced.")
        return _row_to_dict(_get_job_row(job_id))

    # PR title reflects what actually landed in the last commit — accurate
    # whether we just committed above or the session committed on its own.
    subject_proc = _run_git(["log", "-1", "--format=%s"], worktree)
    pr_title = (subject_proc.stdout or "").strip() or f"devdispatch job #{job_id}"

    # Push whatever HEAD actually contains under the expected remote branch
    # name via an explicit refspec, rather than assuming the worktree's
    # checked-out local branch is literally named git_branch. A dispatched
    # session sometimes runs its own `git checkout -b <custom-name>` inside
    # the worktree instead of committing on the branch Claude Code created
    # for -w — `git push -u origin git_branch` (bare name, no local ref)
    # then fails with "src refspec ... does not match any" since no local
    # branch by that exact name exists (bug_tracker #95, job 37,
    # 2026-08-16). `HEAD:<git_branch>` pushes the current commit content
    # under the desired remote ref name regardless of what the local branch
    # is actually called.
    push_proc = _run_git(["push", "-u", "origin", f"HEAD:{git_branch}"], worktree)
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


def _merge_claude_code_job(job_id) -> dict:
    """Merge a dispatched job's PR into main. Only ever invoked as an
    explicit, separate call on Bill's per-job approval — never from
    _dispatch_claude_code_job's or _finalize_completed_job's own completion
    path, which keep stopping at "PR opened, Telegram sent" exactly as
    before."""
    if job_id is None:
        return {"error": "job_id is required"}

    row = _get_job_row(job_id)
    if not row:
        return {"error": f"job {job_id} not found"}

    pr_url = row["pr_url"]
    if not pr_url:
        return {"error": "no PR associated with this job"}

    if row["merged_at"]:
        return {"status": "already_merged", "pr_url": pr_url, "merged_at": row["merged_at"]}

    match = _PR_URL_RE.match(pr_url)
    if not match:
        return {"error": f"could not parse owner/repo/PR number from pr_url: {pr_url}"}
    owner, repo_name, pr_number = match.group(1), match.group(2), int(match.group(3))

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return {"error": "GITHUB_TOKEN not set"}

    try:
        from github import Github
        gh_repo = Github(token).get_repo(f"{owner}/{repo_name}")
        pr = gh_repo.get_pull(pr_number)
    except Exception as exc:
        return {"error": f"could not fetch PR #{pr_number}: {exc}"}

    # (a) PR must still be open — unless GitHub already shows it merged,
    # in which case reconcile our local state instead of erroring.
    if pr.state != "open":
        if pr.merged:
            merged_at = (pr.merged_at.isoformat() if pr.merged_at else datetime.utcnow().isoformat())
            _update_job(job_id, merged_at=merged_at)
            return {"status": "already_merged", "pr_url": pr_url, "merged_at": merged_at}
        return {"error": f"PR #{pr_number} is not open (state={pr.state}) and was not merged"}

    # (b) No merge conflicts. `mergeable` is None while GitHub is still
    # computing it — that's not a conflict, just not ready yet.
    if pr.mergeable is None:
        return {"error": f"GitHub is still computing mergeability for PR #{pr_number} — retry in a few seconds"}
    if pr.mergeable is False:
        return {"error": f"PR #{pr_number} has a merge conflict with {gh_repo.default_branch}"}

    # (c) No failing checks. Combines the legacy commit-status API and the
    # newer checks API (GitHub Actions uses checks, not statuses) since
    # either can carry a failing result. Pending/queued checks are fine —
    # only a completed failing/erroring result blocks the merge.
    try:
        commit = gh_repo.get_commit(pr.head.sha)
        failing = [s.context for s in commit.get_combined_status().statuses if s.state in _FAILING_STATUS_STATES]
        failing += [
            c.name for c in commit.get_check_runs()
            if c.status == "completed" and c.conclusion in _FAILING_CHECK_CONCLUSIONS
        ]
    except Exception as exc:
        return {"error": f"could not fetch status checks for PR #{pr_number}: {exc}"}

    if failing:
        return {"error": f"PR #{pr_number} has failing check(s): {', '.join(failing)}"}

    try:
        merge_result = pr.merge(merge_method="squash")
    except Exception as exc:
        return {"error": f"merge failed: {exc}"}
    if not merge_result.merged:
        return {"error": f"merge did not succeed: {merge_result.message}"}

    merged_at = datetime.utcnow().isoformat()
    _update_job(job_id, merged_at=merged_at)
    _telegram(f"✅ devdispatch job {job_id} merged into main (PR #{pr_number}).")
    return {"status": "merged", "pr_url": pr_url, "merged_at": merged_at}


# Skills deliberately withheld from this connector: shell execution
# (command_executor), anything touching credentials (secrets_audit,
# logins), real email send with a dashboard-session confirm-flow UI that
# doesn't exist here (email_send), pastoral/congregant PII (pastoral_search),
# and claude_debug (redundant with dispatch_claude_code_job, recursive-cost
# risk). Telegram-only skills (pastoral_notes, kb_export, logins) are
# already excluded by routing with interface="dashboard" below.
#
# Included with awareness of their side effects (Bill's call, 2026-08-23):
# qr_generate/image_gen only return a confirmation string here — the actual
# QR/image still goes out over Telegram, not inline in the MCP response.
# book_appointment creates a real Google Calendar event from a hallucinated
# match, same as it would from Telegram. The nine file-output skills
# (pdf/word/excel/powerpoint/document_converter/svg_generator/screenshot/
# chart_generator/data_analyzer) write files on the Beelink and mostly
# return a file path string, not the file itself.
#
# kb_export_link (added 2026-08-24) is a deliberate exception to the
# "file-output skills return a bare path" note above: it returns a scoped,
# expiring (15 min), single-use https://watson.tail0243ff.ts.net/kb/download/
# link instead of a local path, so it's safe to hand back over this
# stateless connector the same way qr_generate/image_gen are — no raw
# filesystem access, no credentials, and the link stops working on its own.
# kb_export itself (Telegram raw-zip-attach) stays excluded, same as before.
#
# Bill: add/remove slugs here to change what's reachable from Claude.ai.
_MCP_SKILL_ALLOWLIST = frozenset({
    "bible_lookup", "add_task", "contacts_lookup", "time_check",
    "web_search", "kb", "kb_search", "news_search", "academic_search",
    "isbn_lookup", "summarizer", "image_search", "grammar_checker",
    "readability", "style_checker", "citation_manager", "date_helper",
    "dad_joke", "riddle", "system_monitor", "skill_audit",
    "calendar_query", "polish", "gutenberg", "classics",
    "qr_generate", "image_gen", "book_appointment",
    "pdf", "word", "excel", "powerpoint", "document_converter",
    "svg_generator", "screenshot", "chart_generator", "data_analyzer",
    "kb_export_link",
    "list_archives", "search_archives", "get_archive",
    "list_projects", "get_project_summary",
})


def _list_watson_skills() -> dict:
    skills = _skill_router._load_skills("dashboard")
    exposed = [s for s in skills if s["slug"] in _MCP_SKILL_ALLOWLIST]
    return {
        "skills": [
            {"slug": s["slug"], "description": s.get("description", "")}
            for s in exposed
        ]
    }


def _run_watson_skill(message) -> dict:
    if not message:
        return {"error": "message is required", "error_code": "bad_request"}

    try:
        route_result = _skill_router.route(message, "dashboard")
    except Exception as exc:
        return {"error": f"router failed: {exc}", "error_code": "internal_error"}

    if route_result.get("action") != "skill":
        # build/propose/chat/wrap_up/conversational all assume a live
        # dashboard or Telegram session (background threads, Ollama chat
        # fallback, session memory) that doesn't exist over this stateless
        # connector — surface that no skill matched instead of guessing. This
        # is a genuine "your phrasing didn't match anything" result, distinct
        # from a network/transport failure, which never reaches this line —
        # those raise and get caught at the tools/call level (see
        # mcp_endpoint), returning a JSON-RPC error instead of this dict.
        return {
            "matched": False,
            "action": route_result.get("action", "chat"),
            "note": "No Watson skill matched this message.",
            "error_code": "no_match",
        }

    slug = route_result.get("slug", "unknown")
    if slug not in _MCP_SKILL_ALLOWLIST:
        return {"error": f"skill '{slug}' matched but is not exposed to this connector", "error_code": "bad_request"}

    if "result" not in route_result:
        skills = _skill_router._load_skills("dashboard")
        skill = next((s for s in skills if s["slug"] == slug), None)
        if not skill:
            return {"error": f"skill '{slug}' not found in registry", "error_code": "not_found"}
        try:
            route_result["result"] = _skill_router._run_skill(
                skill, message=route_result.get("message")
            )
        except Exception as exc:
            return {"error": f"skill '{slug}' failed: {exc}", "error_code": "internal_error"}

    result = route_result["result"]
    if isinstance(result, dict) and result.get("confirm"):
        return {
            "error": f"skill '{slug}' requires interactive confirmation and isn't supported over this connector",
            "error_code": "unsupported",
        }
    return {
        "matched": True,
        "slug": slug,
        "result": result if isinstance(result, dict) else str(result),
    }


def _archive_session_tool(args: dict) -> dict:
    return _archives.archive_session(
        args.get("transcript"),
        args.get("files"),
        args.get("project"),
        args.get("title"),
        args.get("summary"),
    )


def _reclassify_archive_tool(args: dict) -> dict:
    return _archives.reclassify_archive(
        args.get("id"), args.get("project"), args.get("source_conversation_uuid")
    )


_TOOL_IMPLS = {
    "dispatch_claude_code_job": lambda args: _dispatch_claude_code_job(
        args.get("spec"), args.get("repo"), args.get("branch_name")
    ),
    "check_claude_code_job": lambda args: _check_claude_code_job(args.get("job_id")),
    "merge_claude_code_job": lambda args: _merge_claude_code_job(args.get("job_id")),
    "run_watson_skill": lambda args: _run_watson_skill(args.get("message")),
    "list_watson_skills": lambda args: _list_watson_skills(),
    "archive_session": _archive_session_tool,
    "list_archives": lambda args: _archives.list_archives_by_project(
        args.get("project"), bool(args.get("include_superseded", False))
    ),
    "search_archives": lambda args: _archives.search_archives_by_query(
        args.get("query"), bool(args.get("include_superseded", False))
    ),
    "get_archive": lambda args: _archives.get_archive_by_id(args.get("id"), args.get("filename")),
    "list_projects": lambda args: _archives.list_projects(),
    "get_project_summary": lambda args: _archives.get_project_summary_for(args.get("project")),
    "mark_archive_superseded": lambda args: _archives.mark_superseded(
        args.get("id"), args.get("superseded_by")
    ),
    "reclassify_archive": _reclassify_archive_tool,
    "stage_archive_file": lambda args: _archives.stage_file(
        args.get("filename"), args.get("content_base64")
    ),
}


# ── MCP JSON-RPC endpoint ──────────────────────────────────────────────────

@devdispatch_bp.route("/mcp/devdispatch/.well-known/oauth-protected-resource", methods=["GET"])
@devdispatch_bp.route("/.well-known/oauth-protected-resource", methods=["GET"])
def oauth_protected_resource_metadata():
    # Mirrored at the domain root too (added 2026-08-04) — Claude.ai's
    # connector requested /authorize at bare root instead of the real
    # /mcp/devdispatch/oauth/authorize path, consistent with its client
    # treating the plain origin as the issuer and looking for discovery
    # metadata there first; the /mcp/devdispatch/.well-known/... path alone
    # 404s for that lookup. Content is identical either way — the endpoint
    # values below are unchanged, still the full /mcp/devdispatch/oauth/...
    # absolute URLs.
    return jsonify({
        "resource": _RESOURCE_URL,
        "authorization_servers": [_RESOURCE_URL],
    })


@devdispatch_bp.route("/mcp/devdispatch/.well-known/oauth-authorization-server", methods=["GET"])
@devdispatch_bp.route("/.well-known/oauth-authorization-server", methods=["GET"])
def oauth_authorization_server_metadata():
    # Mirrored at the domain root too — see oauth_protected_resource_metadata.
    return jsonify({
        "issuer": _RESOURCE_URL,
        "authorization_endpoint": f"{_RESOURCE_URL}/oauth/authorize",
        "token_endpoint": f"{_RESOURCE_URL}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
    })


def _authorize_redirect(redirect_uri: str, params: dict):
    sep = "&" if "?" in redirect_uri else "?"
    return redirect(f"{redirect_uri}{sep}{urlencode(params)}")


@devdispatch_bp.route("/mcp/devdispatch/oauth/authorize", methods=["GET"])
def oauth_authorize():
    client_id = request.args.get("client_id", "")
    redirect_uri = request.args.get("redirect_uri", "")
    response_type = request.args.get("response_type", "")
    code_challenge = request.args.get("code_challenge", "")
    code_challenge_method = request.args.get("code_challenge_method", "")
    state = request.args.get("state", "")
    resource = request.args.get("resource", "")

    # client_id and redirect_uri must be exact matches against the one
    # registered client — reject outright (no redirect) on mismatch, since
    # redirecting to an unvalidated redirect_uri is an open-redirect risk.
    expected_client_id = os.getenv("MCP_OAUTH_CLIENT_ID", "")
    if not expected_client_id or client_id != expected_client_id:
        return jsonify({"error": "unauthorized_client", "error_description": "unknown client_id"}), 400
    if redirect_uri != _REGISTERED_REDIRECT_URI:
        return jsonify({"error": "invalid_request", "error_description": "redirect_uri does not match the registered value"}), 400

    # Past this point redirect_uri is trusted, so errors go back to the
    # client via redirect, per standard OAuth error handling.
    if response_type != "code":
        return _authorize_redirect(redirect_uri, {"error": "unsupported_response_type", "state": state})
    if not code_challenge or code_challenge_method != "S256":
        return _authorize_redirect(redirect_uri, {
            "error": "invalid_request",
            "error_description": "PKCE code_challenge with code_challenge_method=S256 is required",
            "state": state,
        })

    _cleanup_oauth_state()
    code = secrets.token_urlsafe(32)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO devdispatch_oauth_codes "
            "(code, client_id, redirect_uri, code_challenge, resource, expires_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now', ?))",
            (code, client_id, redirect_uri, code_challenge, resource or None, f"+{_AUTH_CODE_TTL_S} seconds"),
        )
        conn.commit()
    finally:
        conn.close()

    # Single-user shim — auto-approves immediately, no login/consent screen.
    return _authorize_redirect(redirect_uri, {"code": code, **({"state": state} if state else {})})


def _extract_client_credentials(data: dict):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header[len("Basic "):]).decode()
            client_id, _, client_secret = decoded.partition(":")
            return client_id, client_secret
        except Exception:
            return "", ""
    return data.get("client_id", ""), data.get("client_secret", "")


@devdispatch_bp.route("/mcp/devdispatch/oauth/token", methods=["POST"])
def oauth_token():
    data = request.form.to_dict() or (request.get_json(silent=True) or {})

    if data.get("grant_type") != "authorization_code":
        return jsonify({"error": "unsupported_grant_type"}), 400

    code = data.get("code", "")
    redirect_uri = data.get("redirect_uri", "")
    code_verifier = data.get("code_verifier", "")
    client_id, client_secret = _extract_client_credentials(data)

    expected_id = os.getenv("MCP_OAUTH_CLIENT_ID", "")
    expected_secret = os.getenv("MCP_OAUTH_CLIENT_SECRET", "")
    if not expected_id or not expected_secret or client_id != expected_id or client_secret != expected_secret:
        return jsonify({"error": "invalid_client"}), 401

    _cleanup_oauth_state()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM devdispatch_oauth_codes WHERE code = ? AND used = 0 AND expires_at > datetime('now')",
            (code,),
        ).fetchone()
        if not row:
            return jsonify({"error": "invalid_grant", "error_description": "unknown, expired, or already-used code"}), 400
        if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
            return jsonify({"error": "invalid_grant", "error_description": "client_id/redirect_uri mismatch"}), 400

        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        if not code_verifier or expected_challenge != row["code_challenge"]:
            return jsonify({"error": "invalid_grant", "error_description": "PKCE verification failed"}), 400

        conn.execute("UPDATE devdispatch_oauth_codes SET used = 1 WHERE code = ?", (code,))

        access_token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO devdispatch_oauth_tokens (access_token, client_id, expires_at) "
            "VALUES (?, ?, datetime('now', ?))",
            (access_token, client_id, f"+{_ACCESS_TOKEN_TTL_S} seconds"),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": _ACCESS_TOKEN_TTL_S,
    })


# ── Root-level /authorize and /token proxies ─────────────────────────────
#
# Workaround for a confirmed Claude.ai connector bug (anthropics/claude-ai-mcp
# #82, #283, #644): it ignores authorization_endpoint/token_endpoint in the
# AS metadata entirely and hardcodes {origin}/authorize and {origin}/token
# at the bare domain root, regardless of what the issuer's path actually is.
# Confirmed nothing else in app.py claims "/", "/authorize", or "/token"
# before adding these. Not a bug in our metadata — it's already correct.

@devdispatch_bp.route("/authorize", methods=["GET"])
def root_authorize_proxy():
    qs = request.query_string.decode()
    target = "/mcp/devdispatch/oauth/authorize"
    if qs:
        target = f"{target}?{qs}"
    return redirect(target)


@devdispatch_bp.route("/token", methods=["POST"])
def root_token_proxy():
    # Same request/response cycle, not a real network hop — oauth_token()
    # reads from the same `request` object already bound to this call.
    return oauth_token()


@devdispatch_bp.route("/mcp/devdispatch", methods=["GET", "HEAD", "POST"])
def mcp_endpoint():
    # The auth gate applies to every method, not just POST — added
    # 2026-08-04: Claude.ai's connector probes the bare URL with a GET
    # before attempting OAuth, and a plain 405 (from Flask's default
    # method-not-allowed handling) carries no WWW-Authenticate hint, so it
    # read as a dead endpoint. GET/HEAD now get the same 401 + discovery
    # header as POST when unauthenticated. OPTIONS is deliberately not
    # listed here — it's still handled by Flask's automatic CORS-preflight
    # response, unauthenticated, exactly as before.
    if not _is_authorized():
        resp = jsonify({"error": "unauthorized"})
        resp.headers["WWW-Authenticate"] = (
            f'Bearer resource_metadata="{_RESOURCE_URL}/.well-known/oauth-protected-resource"'
        )
        return resp, 401

    if request.method in ("GET", "HEAD"):
        # Reachability probe only — real MCP JSON-RPC traffic is POST-only.
        # Werkzeug strips the body for HEAD automatically.
        return jsonify({"ok": True, "service": "watson-devdispatch"})

    body = request.get_json(force=True, silent=True) or {}
    req_id = body.get("id")
    method = body.get("method")

    if method == "initialize":
        # capabilities.tools.listChanged is deliberately omitted, not an
        # oversight: this endpoint is stateless HTTP POST/response only (see
        # module docstring — no SSE/streaming, no persistent per-client
        # session), so there is no channel to push an unsolicited
        # notifications/tools/list_changed over. Declaring listChanged: true
        # here would advertise a capability the server cannot fulfill. If
        # this endpoint ever grows a persistent connection (SSE transport),
        # add the notification send *and* this flag together — never one
        # without the other. See WATSON_ARCHITECTURE.md, MCP Claude Code
        # Dispatcher > Known gaps, for the resulting manual-reconnect
        # requirement.
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
            return _rpc_error(req_id, -32602, f"unknown tool: {name}", error_code="bad_request")
        try:
            result = impl(args)
        except Exception as exc:
            log.error("devdispatch tool %s failed: %s", name, exc)
            return _rpc_error(req_id, -32000, str(exc), error_code="internal_error")
        return _rpc_result(req_id, _tool_content(result))

    return _rpc_error(req_id, -32601, f"unknown method: {method}", error_code="bad_request")
