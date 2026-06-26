"""jobs/memory/wrap_up.py — manual session wrap-up and memory save."""
import logging
import os
import sqlite3
import subprocess
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
DB_PATH = REPO / "data" / "watson.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:14b"
SESSION_DIVIDER = "---SESSION SUMMARIES---"

log = logging.getLogger(__name__)


def _load_all_messages(session_id) -> list[dict]:
    try:
        sid = int(session_id)
    except (ValueError, TypeError):
        return []
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT role, content FROM chat_messages "
            "WHERE session_id = ? ORDER BY created_at ASC",
            (sid,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("wrap_up: DB load failed: %s", exc)
        return []


def _format_transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = "Bill" if m["role"] == "user" else "Watson"
        lines.append(f"{role}: {m['content']}")
    return "\n".join(lines)


def _call_ollama(prompt: str) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _telegram(text: str) -> None:
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        return
    if len(text) > 4000:
        text = text[:3950] + "\n…[truncated]"
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
    except Exception as exc:
        log.error("wrap_up: Telegram failed: %s", exc)


def _write_project_memory(project_slug: str, summary: str) -> Path | None:
    project_memory = MEMORY / "projects" / project_slug / "memory.md"
    if not project_memory.exists():
        return None
    content = project_memory.read_text(encoding="utf-8")
    today = date.today().isoformat()
    session_block = f"\n---\n{today} — Session wrap-up\n{summary}\n"

    if SESSION_DIVIDER in content:
        before, _ = content.split(SESSION_DIVIDER, 1)
        new_content = before + SESSION_DIVIDER + session_block
    else:
        new_content = content.rstrip() + f"\n\n{SESSION_DIVIDER}{session_block}"

    project_memory.write_text(new_content, encoding="utf-8")
    return project_memory


def _append_relational(summary: str, session_id) -> Path:
    relational = MEMORY / "relational.md"
    if not relational.exists():
        relational.write_text(
            "# Watson Relational Memory\n"
            "*Auto-updated after every session*\n"
            "*This is Watson's long-term memory of Dr. Bill's work, thinking, and patterns*\n\n",
            encoding="utf-8",
        )
    today = date.today().isoformat()
    sid_short = str(session_id)[:8] if session_id is not None else "unknown"
    entry = f"\n---\n{today} | Wrap-up {sid_short}\n{summary}\n"
    with relational.open("a", encoding="utf-8") as f:
        f.write(entry)
    return relational


def _git_commit(files: list[Path], message: str) -> None:
    try:
        for f in files:
            subprocess.run(["git", "add", str(f)], cwd=str(REPO), capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=str(REPO), capture_output=True)
    except Exception as exc:
        log.warning("wrap_up: git commit failed: %s", exc)


def wrap_up(session_id=None, project_slug: str = None) -> str:
    messages = _load_all_messages(session_id) if session_id is not None else []
    transcript = _format_transcript(messages) if messages else "(no messages recorded in this session)"

    today = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    prompt = (
        "SYSTEM: You are Watson's memory system. Bill is explicitly closing a working session. "
        f"Today's date and time: {today}\n\n"
        "Create a detailed session summary for project memory. Include:\n"
        "1. What was accomplished in this session\n"
        "2. Decisions made and why\n"
        "3. Specific next steps with enough detail to pick up immediately next time\n"
        "4. Any open questions or blockers\n"
        "5. Key context Watson should always remember for this project\n\n"
        "Write in a structured format with clear sections. "
        "This will be read at the start of every future session on this project.\n\n"
        f"USER: {transcript}"
    )

    try:
        summary = _call_ollama(prompt)
    except Exception as exc:
        log.error("wrap_up: Ollama failed: %s", exc)
        summary = "(summary generation failed)"

    changed_files = []
    project_name = project_slug.replace("_", " ").title() if project_slug else "General"

    if project_slug:
        project_file = _write_project_memory(project_slug, summary)
        if project_file:
            changed_files.append(project_file)

    relational = _append_relational(summary, session_id)
    changed_files.append(relational)

    sid_short = str(session_id)[:8] if session_id is not None else "unknown"
    _git_commit(changed_files, f"memory: wrap up session {sid_short}")

    try:
        from jobs.memory.sync import main as sync_main
        sync_main()
    except Exception as exc:
        log.warning("wrap_up: sync failed (non-fatal): %s", exc)

    _telegram(f"Session saved to [{project_name}] memory.\n\nSummary:\n{summary[:500]}")

    return summary


def run() -> str:
    return "Say 'wrap this up' or 'save this session' to save the current session to memory."


if __name__ == "__main__":
    import sys
    session_id = sys.argv[1] if len(sys.argv) > 1 else None
    project_slug = sys.argv[2] if len(sys.argv) > 2 else None
    print(wrap_up(session_id, project_slug))
