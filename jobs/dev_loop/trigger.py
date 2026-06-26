"""
trigger.py — Launch a dev loop on FMSPC via Tailscale SSH.

Usage:
    from jobs.dev_loop.trigger import trigger_dev_loop
    trigger_dev_loop(slug, title, input_type, input_text)
"""
import base64
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

FMSPC_HOST = os.getenv("FMSPC_TAILSCALE_HOST", "fmspc")
FMSPC_USER = os.getenv("FMSPC_SSH_USER", "billyomes")
WATSON_API_URL = os.getenv("WATSON_API_URL", "https://watson.tail0243ff.ts.net")
LOOP_SCRIPT = os.getenv("FMSPC_LOOP_SCRIPT", "~/watson-dev/loop.py")

DB = os.path.expanduser("~/watson/data/watson.db")


def _db():
    import sqlite3
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def trigger_dev_loop(
    slug: str,
    title: str,
    input_type: str,
    input_text: str,
    max_iterations: int = 3,
    start_iteration: int = 1,
    extend_by: int = 0,
    feedback: str = "",
) -> dict:
    """Insert/update project record and SSH to FMSPC to start loop.py."""
    conn = _db()

    existing = conn.execute("SELECT id FROM dev_projects WHERE slug = ?", (slug,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE dev_projects SET title=?, input_type=?, input_text=?, status='running', "
            "updated_at=datetime('now') WHERE slug=?",
            (title, input_type, input_text, slug),
        )
    else:
        conn.execute(
            "INSERT INTO dev_projects (slug, title, input_type, input_text, status, max_iterations) "
            "VALUES (?, ?, ?, ?, 'running', ?)",
            (slug, title, input_type, input_text, max_iterations),
        )
    conn.commit()

    input_b64 = base64.b64encode(input_text.encode("utf-8")).decode("ascii")
    feedback_b64 = base64.b64encode(feedback.encode("utf-8")).decode("ascii") if feedback else ""

    args = (
        f"--slug {slug} "
        f"--input-type {input_type} "
        f"--input-b64 {input_b64} "
        f"--watson-url {WATSON_API_URL} "
        f"--start-iteration {start_iteration} "
        f"--extend-by {extend_by}"
    )
    if feedback_b64:
        args += f" --feedback-b64 {feedback_b64}"

    remote_cmd = (
        f"mkdir -p ~/watson-dev/logs && "
        f"nohup python {LOOP_SCRIPT} {args} "
        f">> ~/watson-dev/logs/{slug}.log 2>&1 &"
    )

    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=15",
        "-o", "BatchMode=yes",
        f"{FMSPC_USER}@{FMSPC_HOST}",
        remote_cmd,
    ]

    log.info("DevLoop trigger: SSH to %s@%s for slug=%s (start_iter=%d)", FMSPC_USER, FMSPC_HOST, slug, start_iteration)
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        conn.execute("UPDATE dev_projects SET status='failed', updated_at=datetime('now') WHERE slug=?", (slug,))
        conn.commit()
        conn.close()
        return {"ok": False, "error": "SSH timeout connecting to FMSPC"}

    if result.returncode != 0:
        err = (result.stderr or "").strip() or "SSH failed"
        log.error("DevLoop SSH failed for %s: %s", slug, err)
        conn.execute("UPDATE dev_projects SET status='failed', updated_at=datetime('now') WHERE slug=?", (slug,))
        conn.commit()
        conn.close()
        return {"ok": False, "error": err}

    conn.close()
    log.info("DevLoop started on FMSPC: slug=%s", slug)
    return {"ok": True, "slug": slug}
