"""jobs/memory/reflect.py — automatic post-session reflection."""
import logging
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
DB_PATH = REPO / "data" / "watson.db"
OLLAMA_MODEL = "qwen2.5:14b"

log = logging.getLogger(__name__)

_RELATIONAL_HEADER = (
    "# Watson Relational Memory\n"
    "*Auto-updated after every session*\n"
    "*This is Watson's long-term memory of Dr. Bill's work, thinking, and patterns*\n"
)


def _ensure_relational() -> Path:
    path = MEMORY / "relational.md"
    if not path.exists():
        path.write_text(_RELATIONAL_HEADER + "\n", encoding="utf-8")
    return path


def _load_messages(session_id, limit: int = 20) -> list[dict]:
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
            "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (sid, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]
    except Exception as exc:
        log.warning("reflect: DB load failed: %s", exc)
        return []


def _format_transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = "Bill" if m["role"] == "user" else "Watson"
        lines.append(f"{role}: {m['content']}")
    return "\n".join(lines)


def _call_ollama_chat(system: str, user: str) -> str:
    resp = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


def _git_commit(files: list[Path], message: str) -> None:
    try:
        for f in files:
            subprocess.run(["git", "add", str(f)], cwd=str(REPO), capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=str(REPO), capture_output=True)
    except Exception as exc:
        log.warning("reflect: git commit failed: %s", exc)


def reflect(session_id, project_slug: str = None) -> str | None:
    messages = _load_messages(session_id, limit=20)
    if len(messages) < 3:
        log.info("reflect: skipping session %s — only %d messages", session_id, len(messages))
        return None

    transcript = _format_transcript(messages)
    today = date.today().isoformat()
    sid_short = str(session_id)[:8]

    system = (
        "You are Watson's memory system. Summarize the conversation below concisely. Extract:\n"
        "1. What was discussed or worked on\n"
        "2. Any decisions made\n"
        "3. Any next steps identified\n"
        "4. Anything worth remembering long-term\n\n"
        "Be brief. 3-5 sentences maximum. Write in past tense. "
        "Do not invent dates, context, or content not present in the transcript."
    )

    try:
        summary = _call_ollama_chat(system, transcript)
    except Exception as exc:
        log.error("reflect: Ollama failed: %s", exc)
        return None

    entry = f"\n---\n{today} | Session {sid_short}\n{summary}\n"
    changed_files = []

    relational = _ensure_relational()
    with relational.open("a", encoding="utf-8") as f:
        f.write(entry)
    changed_files.append(relational)

    if project_slug:
        project_memory = MEMORY / "projects" / project_slug / "memory.md"
        if project_memory.exists():
            with project_memory.open("a", encoding="utf-8") as f:
                f.write(entry)
            changed_files.append(project_memory)

    _git_commit(changed_files, f"memory: reflect session {sid_short}")

    try:
        from jobs.memory.sync import main as sync_main
        sync_main()
    except Exception as exc:
        log.warning("reflect: sync failed (non-fatal): %s", exc)

    return summary


def run() -> str:
    return "Reflect runs automatically every 10 messages. No manual invocation needed."


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 -m jobs.memory.reflect <session_id> [project_slug]")
        sys.exit(1)
    result = reflect(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(result or "(skipped — too few messages)")
