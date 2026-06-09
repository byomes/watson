import json
import logging
import os
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

WATSON_ROOT = Path.home() / "watson"
DB_PATH = WATSON_ROOT / "data" / "watson.db"


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
    from google import genai

    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_AI_STUDIO_API_KEY")
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=_SYSTEM_PROMPT + "\n\nBuild request: " + description,
    )
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # Remove control characters that break JSON parsing
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    try:
        # Strip control characters that break JSON parsing
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try extracting just the JSON object
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', match.group())
                try:
                    return json.loads(clean)
                except json.JSONDecodeError:
                    # Replace literal newlines inside strings
                    clean = re.sub(r'(?<=: ")(.*?)(?=")', lambda m: m.group().replace('\n', '\\n').replace('\r', ''), clean, flags=re.DOTALL)
                    return json.loads(clean)
            raise
    except json.JSONDecodeError:
        # Try extracting just the JSON object
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', match.group())
            return json.loads(clean)
        raise


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
    subprocess.run(
        ["git", "-C", "/home/billyomes/watson", "pull"],
        capture_output=True, text=True
    )

    with _get_db() as conn:
        conn.execute(
            "UPDATE gemini_builds SET status = 'applied' WHERE id = ?", (build_id,)
        )

    _send_telegram(
        f"Applied and deployed. {len(files)} file(s) written."
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
