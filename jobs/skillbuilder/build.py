"""jobs/skillbuilder/build.py — generate a new Watson job via qwen2.5-coder:7b."""
import json
import logging
import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5-coder:7b"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _telegram(text: str) -> None:
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        log.warning("Telegram credentials not set — skipping notification")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=300,
        )
    except Exception as exc:
        log.error("Telegram failed: %s", exc)


def _load_context() -> str:
    files = [
        MEMORY / "coding" / "python.md",
        MEMORY / "coding" / "sqlite.md",
        MEMORY / "coding" / "telegram.md",
        MEMORY / "coding" / "ollama.md",
    ]
    parts = []
    for path in files:
        if path.exists():
            parts.append(f"### {path.name}\n{path.read_text(encoding='utf-8')}")
        else:
            log.warning("Memory file not found: %s", path)
    return "\n\n".join(parts)


def _load_examples() -> str:
    paths = [
        REPO / "jobs" / "memory" / "sync.py",
        REPO / "jobs" / "people" / "server.py",
    ]
    parts = []
    for path in paths:
        if path.exists():
            parts.append(f"### {path.relative_to(REPO)}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _call_ollama(prompt: str) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _syntax_check(code: str) -> tuple[bool, str]:
    """Write code to a temp file, run py_compile, return (ok, error_msg)."""
    tmp = Path(tempfile.gettempdir()) / "watson_skill_draft.py"
    tmp.write_text(code, encoding="utf-8")
    result = subprocess.run(
        ["python3", "-m", "py_compile", str(tmp)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        return False, result.stderr.strip()
    return True, ""


def _update_skills_json(job_path: str, description: str) -> None:
    skills_file = REPO / "memory" / "skills.json"
    slug = Path(job_path).stem
    module = job_path.replace("/", ".").removesuffix(".py")
    try:
        skills = json.loads(skills_file.read_text(encoding="utf-8")) if skills_file.exists() else []
    except Exception:
        skills = []
    if not any(s.get("slug") == slug for s in skills):
        skills.append({
            "slug": slug,
            "description": description,
            "triggers": [],
            "job_module": module,
            "function": "run",
            "interfaces": ["dashboard", "telegram"],
        })
        skills_file.write_text(json.dumps(skills, indent=2), encoding="utf-8")


def _update_python_memory(job_path: str, description: str) -> None:
    python_md = MEMORY / "coding" / "python.md"
    if not python_md.exists():
        return
    content = python_md.read_text(encoding="utf-8")
    today = date.today().isoformat()
    entry = f"- {job_path}: {description[:100]} (built {today})"
    if "## Recently Built" in content:
        python_md.write_text(content.rstrip() + f"\n{entry}\n", encoding="utf-8")
    else:
        python_md.write_text(
            content.rstrip() + f"\n\n## Recently Built\n{entry}\n",
            encoding="utf-8",
        )


def build_skill(description: str, job_path: str) -> bool:
    log.info("Building skill: %s → %s", description[:60], job_path)

    # 1. Load memory context
    context = _load_context()

    # 2. Load example jobs
    examples = _load_examples()

    # 3. Build prompt
    system = (
        "You are Watson's internal code writer. You write Python jobs for the Watson "
        "assistant system running on a Beelink EQi12 (Linux Mint, Python 3, systemd). "
        "You follow Watson's established conventions exactly. You output only raw Python "
        "code — no markdown, no backticks, no explanation. The code must be complete and "
        "ready to save directly to a file."
    )
    user = (
        f"Watson coding conventions and patterns:\n{context}\n\n"
        f"Example jobs for reference:\n{examples}\n\n"
        f"Write a complete Python job that does the following:\n{description}\n\n"
        "The job should:\n"
        "- Import only standard library and packages already in requirements.txt "
        "(python-telegram-bot, requests, sqlite3, ollama, python-dotenv)\n"
        "- Load credentials from environment variables via dotenv\n"
        "- Use ~/watson/data/watson.db for any DB operations\n"
        "- Log errors to ~/watson/logs/\n"
        "- Follow the naming and structure conventions in the coding memory above\n"
        "- Be complete and runnable as-is\n\n"
        "Output only the Python code. Nothing else."
    )
    full_prompt = f"SYSTEM:\n{system}\n\nUSER:\n{user}"

    # 4. Call Ollama
    log.info("Calling %s via Ollama…", OLLAMA_MODEL)
    try:
        code = _call_ollama(full_prompt)
    except Exception as exc:
        msg = f"✗ Skill build failed ({job_path}): Ollama error — {exc}"
        log.error(msg)
        _telegram(msg)
        return False

    if not code:
        msg = f"✗ Skill build failed ({job_path}): empty response from model"
        log.error(msg)
        _telegram(msg)
        return False

    # Strip any accidental markdown fences the model may have emitted
    if code.startswith("```"):
        lines = code.splitlines()
        code = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()

    # 5. Syntax check
    ok, err = _syntax_check(code)
    if not ok:
        msg = f"✗ Skill build failed ({job_path}): syntax error\n\n{err}"
        log.error(msg)
        _telegram(msg)
        return False

    # 6. Save file
    dest = REPO / job_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(code, encoding="utf-8")
    log.info("Saved: %s", dest)

    tmp = Path(tempfile.gettempdir()) / "watson_skill_draft.py"
    tmp.unlink(missing_ok=True)

    # 7. Commit and push
    try:
        subprocess.run(["git", "add", str(dest)], cwd=str(REPO), check=True)
        subprocess.run(
            ["git", "commit", "-m", f"skill: {job_path} — generated by {OLLAMA_MODEL}"],
            cwd=str(REPO),
            check=True,
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(REPO),
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        msg = f"✗ Skill build ({job_path}): git error — {exc}"
        log.error(msg)
        _telegram(msg)
        return False

    # 8. Notify Bill
    _telegram(
        f"✓ Skill built: {job_path}\n\n"
        "Review before running. To activate add a cron entry or start as a service.\n\n"
        "Pull on Beelink to confirm: git log --oneline -1"
    )

    # 9. Update memory, skills registry, and sync DB
    try:
        _update_python_memory(job_path, description)
        _update_skills_json(job_path, description)
        from jobs.memory.sync import main as sync_main
        sync_main()
    except Exception as exc:
        log.warning("Memory update failed (non-fatal): %s", exc)

    log.info("Skill build complete: %s", job_path)
    return True


def run() -> str:
    """Prompt Bill to describe the skill he wants built."""
    return "Skill builder ready. Describe what you need and I'll write the code."


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python3 -m jobs.skillbuilder.build '<description>' '<job_path>'")
        sys.exit(1)
    description = sys.argv[1]
    job_path = sys.argv[2]
    success = build_skill(description, job_path)
    sys.exit(0 if success else 1)
