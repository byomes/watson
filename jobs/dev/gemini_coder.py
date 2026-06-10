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
You are a Python code assistant for Watson, a personal AI assistant system
running on a Linux server at /home/billyomes/watson.

STACK:
- Python 3.12, Flask 3.0.3, SQLite, python-telegram-bot 20.7
- httpx MUST stay pinned at 0.25.2 — do not change this
- Ollama local LLM at http://localhost:11434
- All imports must be PYTHONPATH-safe: from jobs.x.y import ...

KEY PATHS:
- Watson root: /home/billyomes/watson
- Main DB: /home/billyomes/watson/data/watson.db (watson.db)
- Congregation DB: /home/billyomes/watson/data/congregation.db
- Dashboard app: jobs/dashboard/app.py (Flask, port 5200)
- Dashboard frontend: jobs/dashboard/static/app.js and templates/index.html
- Telegram bot: bot/bot.py
- Skills: jobs/skillbuilder/router.py, memory/skills.json
- Config/settings: config/settings.py
- Credentials: loaded from .env via os.environ.get()

DATABASE TABLES (watson.db):
- blog_drafts, facebook_queue, connect_cards, people, tasks, reminders
- reading_list, chat_sessions, chat_messages, pastoral_notes, notes_pending
- email_queue, gemini_builds, capability_gaps, voice_notes

CONGREGATION DB (congregation.db):
- members, connect_cards, attendance, follow_ups, prayer_requests
- next_steps, duplicate_flags

CRITICAL RULES:
- Never change httpx version — it breaks python-telegram-bot 20.7
- Never use localStorage or sessionStorage in frontend JS
- Always use PYTHONPATH-safe imports
- Read credentials from .env via os.environ.get(), never hardcode
- DB connections use get_connection() from core.database for watson.db
- Dashboard uses SSE streaming for chat responses (_sse_response, _stream_simple)
- Telegram bot uses python-telegram-bot 20.7 async patterns
- All new jobs go under jobs/ directory
- Skills go in jobs/ and register in memory/skills.json
- Frontend JS: Watson uses vanilla JS, no frameworks, no npm
- Always read the target file before modifying it
- Never add a print() or file path return where a rendered result is expected
- NEVER modify jobs/dashboard/static/app.js — this file is off-limits
- NEVER modify jobs/dashboard/templates/index.html — off-limits
- NEVER modify bot/bot.py directly — off-limits
- If a fix requires frontend changes, describe what needs to change in the summary but do not include app.js or index.html in the files array
- If a fix requires bot.py changes, describe what needs to change in the summary but do not include bot.py in the files array

WHEN FIXING DASHBOARD SKILLS:
- Skill run() functions must return a string result
- If result is an image, return "data:image/png;base64,..."
- The dashboard renders data:image/ strings as <img> tags
- Never return a file path — the browser cannot access server paths

WHEN FIXING BOT.PY:
- Message handlers are async def
- Use await update.message.reply_text() for responses
- Use run_in_executor for blocking calls

Respond ONLY with a JSON object:
{
  "summary": "one sentence description",
  "files": [
    {
      "path": "relative/path/from/watson/root.py",
      "content": "full file content",
      "action": "create" or "update"
    }
  ],
  "commit_message": "short git commit message"
}

Return ONLY the JSON. No markdown. No explanation. No code fences.
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
        model="gemini-3.5-flash",
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
