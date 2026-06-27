"""
trigger.py — Launch the dev loop locally on the Beelink.

Usage:
    from jobs.dev_loop.trigger import trigger_dev_loop
    trigger_dev_loop(slug, title, input_type, input_text)
"""
import base64
import logging
import os
import subprocess

log = logging.getLogger(__name__)

LOOP_SCRIPT = os.path.expanduser("~/watson/jobs/dev_loop/loop.py")
WATSON_API_URL = os.getenv("WATSON_API_URL", "https://watson.tail0243ff.ts.net")

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
    """Insert/update project record and launch loop.py locally via Popen."""
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
    conn.close()

    input_b64 = base64.b64encode(input_text.encode("utf-8")).decode("ascii")
    feedback_b64 = base64.b64encode(feedback.encode("utf-8")).decode("ascii") if feedback else ""

    cmd = [
        "python3", LOOP_SCRIPT,
        "--slug", slug,
        "--input-type", input_type,
        "--input-b64", input_b64,
        "--watson-url", WATSON_API_URL,
        "--start-iteration", str(start_iteration),
        "--extend-by", str(extend_by),
    ]
    if feedback_b64:
        cmd += ["--feedback-b64", feedback_b64]

    log_path = os.path.expanduser(f"~/watson/logs/devloop-{slug}.log")
    log.info("DevLoop trigger: launching locally for slug=%s (start_iter=%d)", slug, start_iteration)

    try:
        with open(log_path, "w") as lf:
            subprocess.Popen(cmd, stdout=lf, stderr=lf)
    except Exception as e:
        import sqlite3
        conn2 = sqlite3.connect(DB)
        conn2.execute(
            "UPDATE dev_projects SET status='failed', updated_at=datetime('now') WHERE slug=?",
            (slug,),
        )
        conn2.commit()
        conn2.close()
        log.error("DevLoop Popen failed for %s: %s", slug, e)
        return {"ok": False, "error": str(e)}

    log.info("DevLoop started locally: slug=%s", slug)
    return {"ok": True, "slug": slug}
