import json
import logging
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

WATSON_ROOT = Path.home() / "watson"
DB_PATH = WATSON_ROOT / "data" / "watson.db"

GEMINI_MODEL = "gemini-2.5-flash-preview-05-20"
GEMINI_MAX_TOKENS = 8192

_SYSTEM_PROMPT = """
You are a Python code assistant for Watson, a personal AI assistant system \
running on a Linux server at ~/watson. The codebase uses Flask, SQLite, \
python-telegram-bot 20.7, and standard Python 3.12.

When given a build request, respond ONLY with a JSON object in this format:
{
  "summary": "one sentence description of what this does",
  "files": [
    {
      "path": "relative/path/from/watson/root.py",
      "content": "full file content here",
      "action": "create" or "update"
    }
  ],
  "commit_message": "short git commit message"
}

Rules:
- Always use PYTHONPATH-safe imports (from jobs.x.y import ...)
- DB path: ~/watson/data/watson.db
- Read credentials from .env via os.environ.get()
- Never hardcode secrets
- Follow existing Watson patterns exactly
- Return ONLY the JSON object, no markdown, no explanation
"""


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _create_table():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gemini_builds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                request_text    TEXT,
                gemini_response TEXT,
                summary         TEXT,
                status          TEXT DEFAULT 'pending',
                created_at      TEXT
            )
        """)


_create_table()


def _send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)


def _call_gemini(description: str) -> dict:
    import google.generativeai as genai

    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=_SYSTEM_PROMPT,
        generation_config=genai.types.GenerationConfig(max_output_tokens=GEMINI_MAX_TOKENS),
    )
    response = model.generate_content(description)
    text = response.text.strip()
    # Strip markdown code fences if Gemini wraps the JSON anyway
    if text.startswith("```"):
        lines = text.splitlines()
        end = -1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end])
    return json.loads(text)


def request_build(description: str) -> int:
    """Call Gemini with the build description, store result, notify via Telegram. Returns build id."""
    parsed = _call_gemini(description)
    summary = parsed.get("summary", "")
    response_json = json.dumps(parsed)
    file_paths = [f["path"] for f in parsed.get("files", [])]

    with _get_db() as conn:
        cur = conn.execute(
            """INSERT INTO gemini_builds (request_text, gemini_response, summary, status, created_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (description, response_json, summary, datetime.utcnow().isoformat()),
        )
        build_id = cur.lastrowid

    files_list = "\n".join(f"  • {p}" for p in file_paths)
    _send_telegram(
        f"Build ready: {summary}\nFiles:\n{files_list}\n"
        f"Reply 'apply {build_id}' to write files or 'cancel {build_id}' to discard."
    )
    log.info("gemini_builds row %d created (%d file(s))", build_id, len(file_paths))
    return build_id


def apply_build(build_id: int) -> None:
    """Write files to disk, commit, push, update status to applied."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT gemini_response, status FROM gemini_builds WHERE id = ?", (build_id,)
        ).fetchone()

    if not row:
        _send_telegram(f"Build {build_id} not found.")
        return
    if row["status"] != "pending":
        _send_telegram(f"Build {build_id} is already {row['status']}.")
        return

    parsed = json.loads(row["gemini_response"])
    files = parsed.get("files", [])
    commit_message = parsed.get("commit_message", f"build: apply gemini build {build_id}")

    for f in files:
        dest = WATSON_ROOT / f["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f["content"], encoding="utf-8")

    subprocess.run(["git", "-C", str(WATSON_ROOT), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(WATSON_ROOT), "commit", "-m", commit_message], check=True
    )
    subprocess.run(
        ["git", "-C", str(WATSON_ROOT), "push", "origin", "main"], check=True
    )

    with _get_db() as conn:
        conn.execute(
            "UPDATE gemini_builds SET status = 'applied' WHERE id = ?", (build_id,)
        )

    _send_telegram(
        f"Applied and pushed. {len(files)} file(s) written. Pull on Beelink to deploy."
    )
    log.info("build %d applied (%d file(s))", build_id, len(files))


def cancel_build(build_id: int) -> None:
    """Mark build as cancelled and notify via Telegram."""
    with _get_db() as conn:
        cur = conn.execute(
            "UPDATE gemini_builds SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (build_id,),
        )
        rowcount = cur.rowcount

    if rowcount == 0:
        _send_telegram(f"Build {build_id} not found or not pending.")
        return

    _send_telegram("Build cancelled.")
    log.info("build %d cancelled", build_id)
