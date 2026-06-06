"""jobs/skillbuilder/audit.py — weekly capability gap audit."""
import json
import logging
import os
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
DB_PATH = Path(os.getenv("WATSON_DB", str(REPO / "data" / "watson.db")))
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

log = logging.getLogger(__name__)


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
            timeout=30,
        )
    except Exception as exc:
        log.error("Telegram failed: %s", exc)


def _ensure_gaps_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capability_gaps (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            gap_name    TEXT NOT NULL,
            reason      TEXT,
            job_path    TEXT,
            description TEXT,
            status      TEXT NOT NULL DEFAULT 'proposed',
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _call_ollama(prompt: str) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def run_audit() -> str:
    skills_file = MEMORY / "skills.json"
    skills_json = skills_file.read_text(encoding="utf-8") if skills_file.exists() else "[]"

    index_path = MEMORY / "projects" / "_index.md"
    projects = index_path.read_text(encoding="utf-8")[:2000] if index_path.exists() else "(no projects)"

    relational = MEMORY / "relational.md"
    recent_sessions = relational.read_text(encoding="utf-8")[-2000:] if relational.exists() else "(no session history)"

    research_log = REPO / "logs" / "research.log"
    if research_log.exists():
        lines = research_log.read_text(encoding="utf-8").splitlines()
        research_excerpt = "\n".join(lines[-100:])
    else:
        research_excerpt = "(no research log)"

    system = (
        "You are Watson's capability auditor. Analyze Watson's current skills, active projects, "
        "and recent activity. Identify the top 3 capability gaps — things Bill has needed that "
        "Watson cannot do, or things that would significantly improve Watson's usefulness. "
        "For each gap, provide: gap name, why it matters, suggested job path, brief description "
        "of what to build.\n\n"
        "Format response as a JSON array:\n"
        '[{"gap": "name", "reason": "why it matters", "job_path": "jobs/category/skill_name.py", '
        '"description": "what to build"}]\n\n'
        "job_path must be a short two-part path: jobs/<category>/<skill_name>.py\n"
        "Examples: jobs/monitoring/disk_watch.py, jobs/email/weekly_digest.py, "
        "jobs/ministry/sermon_outline.py\n"
        "Do NOT use long descriptive paths or dot-separated names.\n\n"
        "Output ONLY the JSON array. No preamble, no explanation."
    )
    user = (
        f"Current skills:\n{skills_json}\n\n"
        f"Active projects:\n{projects}\n\n"
        f"Recent sessions:\n{recent_sessions}\n\n"
        f"Recent research queries:\n{research_excerpt}"
    )

    try:
        raw = _call_ollama(f"SYSTEM:\n{system}\n\nUSER:\n{user}")
    except Exception as exc:
        log.error("Ollama audit call failed: %s", exc)
        _telegram(f"⚠️ Weekly audit failed: {exc}")
        return f"Audit failed: {exc}"

    gaps = []
    try:
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            gaps = json.loads(raw[start:end])
    except Exception as exc:
        log.error("Audit JSON parse failed: %s — raw: %s", exc, raw[:200])
        _telegram(f"⚠️ Audit parse error: {exc}\n\nRaw: {raw[:300]}")
        return f"Audit parse failed: {exc}"

    if not gaps:
        _telegram("📊 Weekly audit complete — no capability gaps identified.")
        return "No gaps identified."

    _PASTORAL_TERMS = {
        "pastoral", "counseling", "counsel", "prayer", "pray",
        "spiritual authority", "pastor",
    }

    def _is_pastoral(gap: dict) -> bool:
        text = " ".join([
            gap.get("gap", ""),
            gap.get("description", ""),
        ]).lower()
        return any(term in text for term in _PASTORAL_TERMS)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    _ensure_gaps_table(conn)

    summary_lines = ["📊 Weekly Capability Audit\n"]
    for n, gap in enumerate(gaps[:3], 1):
        gap_name = gap.get("gap", "Unknown gap")
        reason = gap.get("reason", "")
        job_path = gap.get("job_path", "jobs/misc/new_skill.py")
        description = gap.get("description", "")

        if _is_pastoral(gap):
            log.info("Audit rejected pastoral gap: %s", gap_name)
            summary_lines.append(f"#{n} {gap_name} — SKIPPED (pastoral content)")
            continue

        conn.execute(
            "INSERT INTO capability_gaps (gap_name, reason, job_path, description, status) "
            "VALUES (?, ?, ?, ?, 'proposed')",
            (gap_name, reason, job_path, description),
        )
        conn.commit()

        _telegram(
            f"📊 Weekly Audit — Capability Gap #{n}\n\n"
            f"{gap_name}\n\n"
            f"Why: {reason}\n\n"
            f"Shall I build this? Reply YES to build {job_path}"
        )
        summary_lines.append(f"#{n} {gap_name} — {job_path}")

    conn.close()
    summary = "\n".join(summary_lines)
    log.info("Audit complete: %d gaps proposed", len(gaps[:3]))
    return summary


def run() -> str:
    return run_audit()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    print(run_audit())
