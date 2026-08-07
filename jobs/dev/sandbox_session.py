"""jobs/dev/sandbox_session.py — Dev Sandbox: interactive, sandboxed Claude
Code sessions launchable from the Watson dashboard.

This is NOT Dev Loop (jobs/dev_loop/) and does not touch it. Dev Loop is
unattended/Ollama-driven, triggered via Telegram `devloop:`. Dev Sandbox is
a live, human-attended terminal: Bill types into it and answers Claude
Code's own prompts himself, same as sitting at Claude Code on the host
today — just isolated in a throwaway per-session container and a fresh
clone, instead of running directly against ~/watson or ~/wcky.

AUTH — why this differs from the OpenHands sandbox test earlier tonight
(fully unwired and reverted — see git log, "revert OpenHands Claude Code
credential mount"): that container ran as a fixed, image-baked UID (10001)
that didn't match the host user, so a direct mount of ~/.claude (owner-only
permissions) would have been unreadable inside it. The only way to get any
process in that container to read it was to extract a scoped COPY of the
OAuth token into a separate, world-readable file — exactly the posture that
got flagged as a ToS/security risk and fully reverted, nothing kept "just
in case." This image (deploy/dev-sandbox/) has no such excuse: we control
it end to end and run it explicitly with `--user 1000:1000`, matching the
Beelink host user, so ~/.claude bind-mounts read-write with its real,
unmodified host permissions intact. No extraction, no copy, no separate
credential file, ever. It's also not headless: Bill is present at a real
ttyd terminal, answering Claude Code's own prompts directly — no
--dangerously-skip-permissions, no unattended-agent auth-reuse question to
begin with. If a future session is tempted to "simplify" auth here by
copying credentials again, that's the OpenHands mistake repeating, not a
valid shortcut.

Session lifecycle is explicit-stop-only for v1 — no auto-timeout/cleanup
job. That gap is tracked in project_backlog, not silently left undocumented
(see the `backlog:` entry added alongside this build).
"""
import logging
import os
import secrets
import socket
import sqlite3
import subprocess
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

log = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/watson/data/watson.db")
SANDBOX_ROOT = Path(os.path.expanduser("~/dev-sandbox"))
IMAGE_NAME = "watson-dev-sandbox:latest"
TAILSCALE_IP = "100.117.237.96"  # never bind sandbox ports to 0.0.0.0 —
# this box's Tailscale Funnel proxies only :5200 to the public internet,
# but a port explicitly bound to the Tailscale interface is unreachable
# from the LAN (192.168.1.x) and the wider internet regardless, not just
# "not currently proxied" — belt and suspenders.
PORT_RANGE = range(7700, 7750)
CONTAINER_WORKDIR = "/workspace"
CONTAINER_HOME = "/home/node"
HOST_CLAUDE_DIR = os.path.expanduser("~/.claude")
# Auth state actually lives in TWO places on the host, both required for
# Claude Code to recognize an existing login rather than showing its
# first-run setup wizard: ~/.claude/ (settings, .credentials.json — the
# OAuth token itself) AND ~/.claude.json at the home-dir root (onboarding/
# account state — without this, the CLI ignores a perfectly valid
# .credentials.json and still prompts for a fresh login). Confirmed by a
# live end-to-end test during this build (2026-08-06) — mounting only the
# directory left the container asking to log in from scratch. Both are
# real host files, mounted directly, same as the directory above — no
# extraction or copying either.
HOST_CLAUDE_JSON = os.path.expanduser("~/.claude.json")

# Repos this feature will clone. Deliberately not the full Repos & Paths
# table in WATSON_ARCHITECTURE.md — `fms` is excluded because its GitHub
# repo doesn't exist yet ("not yet created", per the FMS Site section);
# add it here once that changes.
KNOWN_REPOS = {
    "watson": "byomes/watson",
    "wcky": "byomes/wcky",
    "watson-admin": "byomes/watson-admin",
    "watson-ui": "byomes/watson-ui",
    "bodyrec": "byomes/bodyrec",
}

dev_sandbox_bp = Blueprint("dev_sandbox", __name__)


def _require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        from jobs.dashboard.app import _admin_required
        redir = _admin_required()
        if redir:
            return jsonify({"error": "not authenticated"}), 401
        return f(*args, **kwargs)
    return wrapper


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def ensure_table():
    conn = _conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dev_sandbox_sessions (
                id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                container_id TEXT,
                container_name TEXT NOT NULL,
                port INTEGER,
                status TEXT NOT NULL DEFAULT 'running',
                created_at TEXT NOT NULL,
                stopped_at TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _port_in_use_by_db(conn, port: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM dev_sandbox_sessions WHERE port=? AND status='running'",
        (port,),
    ).fetchone()
    return row is not None


def _port_bindable(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((TAILSCALE_IP, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _find_free_port(conn) -> int:
    for port in PORT_RANGE:
        if not _port_in_use_by_db(conn, port) and _port_bindable(port):
            return port
    raise RuntimeError("No free dev-sandbox port available in range")


def start_session(repo: str) -> dict:
    if repo not in KNOWN_REPOS:
        raise ValueError(f"Unknown repo {repo!r} — must be one of {sorted(KNOWN_REPOS)}")

    ensure_table()
    conn = _conn()
    try:
        session_id = secrets.token_hex(6)
        session_dir = SANDBOX_ROOT / session_id
        session_dir.mkdir(parents=True, exist_ok=False)

        github_token = os.getenv("GITHUB_TOKEN", "")
        if not github_token:
            raise RuntimeError("GITHUB_TOKEN not set in .env — cannot clone")

        clone_url = f"https://x-access-token:{github_token}@github.com/{KNOWN_REPOS[repo]}.git"
        clone = subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(session_dir)],
            capture_output=True, text=True, timeout=120,
        )
        if clone.returncode != 0:
            # scrub the token out of any error text before it can end up
            # in a log line or an API response
            safe_err = clone.stderr.replace(github_token, "***")
            raise RuntimeError(f"git clone failed: {safe_err}")

        # Fresh clone via https+token leaves an origin remote with the
        # token embedded — replace it with a plain URL so `git remote -v`
        # inside the interactive session never displays the token, and
        # any push attempt cleanly prompts/fails instead of silently
        # using an embedded credential.
        subprocess.run(
            ["git", "remote", "set-url", "origin", f"https://github.com/{KNOWN_REPOS[repo]}.git"],
            cwd=str(session_dir), capture_output=True, text=True, timeout=10,
        )

        port = _find_free_port(conn)
        container_name = f"dev-sandbox-{session_id}"

        run = subprocess.run(
            [
                "docker", "run", "-d", "--rm",
                "--name", container_name,
                "--user", "1000:1000",
                "-v", f"{session_dir}:{CONTAINER_WORKDIR}:rw",
                "-v", f"{HOST_CLAUDE_DIR}:{CONTAINER_HOME}/.claude:rw",
                "-v", f"{HOST_CLAUDE_JSON}:{CONTAINER_HOME}/.claude.json:rw",
                "-p", f"{TAILSCALE_IP}:{port}:7681",
                IMAGE_NAME,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if run.returncode != 0:
            raise RuntimeError(f"docker run failed: {run.stderr}")
        container_id = run.stdout.strip()

        conn.execute(
            """INSERT INTO dev_sandbox_sessions
               (id, repo, container_id, container_name, port, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'running', ?)""",
            (session_id, repo, container_id, container_name, port, _now()),
        )
        conn.commit()

        return {
            "id": session_id,
            "repo": repo,
            "port": port,
            "url": f"http://{TAILSCALE_IP}:{port}/",
        }
    finally:
        conn.close()


def stop_session(session_id: str) -> dict:
    ensure_table()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM dev_sandbox_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"No session {session_id!r}")
        if row["status"] != "running":
            return {"id": session_id, "status": row["status"]}

        subprocess.run(
            ["docker", "stop", row["container_name"]],
            capture_output=True, text=True, timeout=30,
        )
        # --rm on the container means `docker stop` also removes it —
        # no separate `docker rm` needed, and nothing lingers in `docker
        # ps -a` afterward.

        conn.execute(
            "UPDATE dev_sandbox_sessions SET status='stopped', stopped_at=? WHERE id=?",
            (_now(), session_id),
        )
        conn.commit()
        return {"id": session_id, "status": "stopped"}
    finally:
        conn.close()


def list_running() -> list[dict]:
    ensure_table()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, repo, port, created_at FROM dev_sandbox_sessions WHERE status='running' ORDER BY created_at DESC"
        ).fetchall()
        return [
            {**dict(r), "url": f"http://{TAILSCALE_IP}:{r['port']}/"}
            for r in rows
        ]
    finally:
        conn.close()


@dev_sandbox_bp.route("/api/dev-sandbox/repos", methods=["GET"])
@_require_admin
def api_repos():
    return jsonify(sorted(KNOWN_REPOS.keys()))


@dev_sandbox_bp.route("/api/dev-sandbox/status", methods=["GET"])
@_require_admin
def api_status():
    return jsonify(list_running())


@dev_sandbox_bp.route("/api/dev-sandbox/start", methods=["POST"])
@_require_admin
def api_start():
    data = request.get_json(force=True) or {}
    repo = (data.get("repo") or "").strip()
    try:
        result = start_session(repo)
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.error("dev-sandbox start failed: %s", e)
        return jsonify({"error": "Failed to start sandbox session"}), 500


@dev_sandbox_bp.route("/api/dev-sandbox/stop", methods=["POST"])
@_require_admin
def api_stop():
    data = request.get_json(force=True) or {}
    session_id = (data.get("id") or "").strip()
    if not session_id:
        return jsonify({"error": "id required"}), 400
    try:
        result = stop_session(session_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log.error("dev-sandbox stop failed: %s", e)
        return jsonify({"error": "Failed to stop sandbox session"}), 500
