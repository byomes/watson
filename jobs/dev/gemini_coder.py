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


_ARCH_FILE = WATSON_ROOT / "memory" / "architecture.md"

_ARCH_FALLBACK = """
STACK:
- Python 3.12, Flask 3.0.3, SQLite, python-telegram-bot 20.7
- httpx MUST stay pinned at 0.25.2
- Ollama local LLM at http://localhost:11434
- All imports must be PYTHONPATH-safe: from jobs.x.y import ...

KEY PATHS:
- Watson root: /home/billyomes/watson
- Main DB: /home/billyomes/watson/data/watson.db
- Congregation DB: /home/billyomes/watson/data/congregation.db
- Dashboard: jobs/dashboard/app.py (Flask, port 5200)
- Telegram bot: bot/bot.py
- Skills: jobs/skillbuilder/router.py, memory/skills.json
- Config: config/settings.py
""".strip()

_SYSTEM_PROMPT_RULES = """
WATSON CODING RULES — always include these constraints verbatim in your output prompt:
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
- NEVER modify jobs/dashboard/static/app.js — this file is off-limits
- NEVER modify jobs/dashboard/templates/index.html — off-limits
- NEVER modify bot/bot.py directly — off-limits

YOUR OUTPUT RULES — non-negotiable:
- Output ONLY the Claude Code prompt string — nothing else
- No markdown, no explanation, no code fences, no preamble, no trailing notes
- Do not write, create, or apply any files yourself
- Do not touch app.js, bot.py, or index.html under any circumstance
- The text you output is sent verbatim to Claude Code as its entire input
"""


def _load_arch_context() -> str:
    try:
        return _ARCH_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        log.warning("architecture.md not found, using fallback context")
        return _ARCH_FALLBACK


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _create_table():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS builds (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                description      TEXT,
                generated_prompt TEXT,
                status           TEXT DEFAULT 'pending',
                created_at       TEXT
            )
        """)


_create_table()


def _send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)


def _call_gemini(description: str) -> str:
    from google import genai

    arch = _load_arch_context()
    system_prompt = (
        "You are a build-planning assistant for Watson, a personal AI assistant system\n"
        "running on a Linux server at /home/billyomes/watson.\n\n"
        "CRITICAL: Your response must be plain prose only. No JSON. No code blocks. No bullet points. No file actions. No structured data of any kind.\n\n"
        "Your only job is to produce a single plain-English instruction paragraph that will be passed verbatim to Claude Code.\n"
        "Claude Code will write all files. You describe what to build in plain English only.\n\n"
        "ARCHITECTURE CONTEXT:\n"
        f"{arch}\n\n"
        f"{_SYSTEM_PROMPT_RULES}"
    )

    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_AI_STUDIO_API_KEY")
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        config={"system_instruction": system_prompt},
        contents=description,
    )
    return response.text.strip()


def request_build(description: str) -> int:
    """Call Gemini with the build description, store result, notify via Telegram. Returns build id."""
    prompt = _call_gemini(description)

    with _get_db() as conn:
        cur = conn.execute(
            """INSERT INTO builds (description, generated_prompt, status, created_at)
               VALUES (?, ?, 'pending', ?)""",
            (description, prompt, datetime.utcnow().isoformat()),
        )
        build_id = cur.lastrowid

    _send_telegram(
        f"Claude Code prompt ready:\n\n{prompt}\n\n"
        f"Reply apply {build_id} to run or cancel {build_id} to discard."
    )
    log.info("builds row %d created", build_id)
    return build_id


def apply_build(build_id: int) -> None:
    """Run the generated prompt via Claude Code, commit, push, update status to applied."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT description, generated_prompt, status FROM builds WHERE id = ?", (build_id,)
        ).fetchone()

    if not row:
        _send_telegram(f"Build {build_id} not found.")
        return
    if row["status"] != "pending":
        _send_telegram(f"Build {build_id} is already {row['status']}.")
        return

    prompt = row["generated_prompt"]
    raw_desc = row["description"]
    short_desc = (raw_desc[:60] + "...") if len(raw_desc) > 60 else raw_desc

    subprocess.run(
        ["/home/billyomes/.nvm/versions/node/v24.16.0/bin/claude", "--dangerously-skip-permissions", prompt],
        cwd=str(WATSON_ROOT),
        check=True,
        timeout=300,
        text=True,
    )
    subprocess.run(["git", "-C", str(WATSON_ROOT), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(WATSON_ROOT), "commit", "-m", f"feat: {short_desc}"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(WATSON_ROOT), "push", "origin", "main"], check=True
    )
    subprocess.run(
        ["git", "-C", str(WATSON_ROOT), "pull"],
        capture_output=True,
        text=True,
    )

    with _get_db() as conn:
        conn.execute("UPDATE builds SET status = 'applied' WHERE id = ?", (build_id,))

    _send_telegram(f"Build {build_id} applied and deployed.")
    log.info("build %d applied", build_id)


def cancel_build(build_id: int) -> None:
    """Mark build as cancelled and notify via Telegram."""
    with _get_db() as conn:
        cur = conn.execute(
            "UPDATE builds SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (build_id,),
        )
        rowcount = cur.rowcount

    if rowcount == 0:
        _send_telegram(f"Build {build_id} not found or not pending.")
        return

    _send_telegram("Build cancelled.")
    log.info("build %d cancelled", build_id)
