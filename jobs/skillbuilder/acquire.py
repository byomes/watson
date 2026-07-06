"""jobs/skillbuilder/acquire.py — autonomous skill acquisition pipeline."""
import json
import logging
import os
import re
import sqlite3
import subprocess
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("WATSON_DB", str(REPO / "data" / "watson.db")))
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:14b"
PYPI_URL = "https://pypi.org/pypi/{}/json"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"

_BLOCKED_LIBRARIES = frozenset({"send", "sms", "message", "text", "messages", "notify", "notification"})

log = logging.getLogger(__name__)


def _telegram(text: str, reply_markup: dict = None) -> dict:
    if vacation_gate("normal", "jobs.skillbuilder.acquire", text):
        return {}
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        log.warning("Telegram credentials not set — skipping notification")
        return {}
    if len(text) > 4000:
        text = text[:3950] + "\n…[truncated]"
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json=payload,
            timeout=30,
        )
        return resp.json()
    except Exception as exc:
        log.error("Telegram failed: %s", exc)
        return {}


def _db_connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table() -> None:
    conn = _db_connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_acquisitions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            capability        TEXT NOT NULL,
            library           TEXT,
            install_name      TEXT,
            reason            TEXT,
            skill_description TEXT,
            status            TEXT NOT NULL DEFAULT 'pending',
            created_at        TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def search_for_skill(capability_description: str) -> dict:
    """Search PyPI and GitHub for the best library to fill a capability gap."""
    words = re.findall(r'\b[a-zA-Z]{3,}\b', capability_description.lower())
    stopwords = {
        "the", "and", "for", "with", "that", "this", "able", "can", "need",
        "want", "will", "make", "build", "create", "add", "get", "use",
        "from", "into", "onto", "over", "when", "what", "which", "you",
    }
    keywords = [w for w in words if w not in stopwords][:3]
    if not keywords:
        keywords = ["python", "utility"]

    pypi_results = []
    for kw in keywords:
        try:
            resp = requests.get(PYPI_URL.format(kw), timeout=10)
            if resp.status_code == 200:
                info = resp.json().get("info", {})
                pypi_results.append({
                    "name": info.get("name", kw),
                    "summary": info.get("summary", ""),
                    "latest_version": info.get("version", ""),
                    "project_url": info.get("project_url") or f"https://pypi.org/project/{kw}/",
                })
        except Exception as exc:
            log.debug("PyPI search for %s failed: %s", kw, exc)

    github_results = []
    try:
        resp = requests.get(
            GITHUB_SEARCH_URL,
            params={"q": " ".join(keywords) + " language:python", "sort": "stars", "per_page": 3},
            headers={"Accept": "application/vnd.github+json"},
            timeout=15,
        )
        if resp.status_code == 200:
            for item in resp.json().get("items", []):
                github_results.append({
                    "name": item.get("full_name", ""),
                    "description": item.get("description", ""),
                    "stars": item.get("stargazers_count", 0),
                    "html_url": item.get("html_url", ""),
                })
    except Exception as exc:
        log.debug("GitHub search failed: %s", exc)

    if not pypi_results and not github_results:
        return {}

    system = (
        "You are Watson's skill researcher. Given a capability need and search results, "
        "recommend the single best Python library to use. "
        "Only recommend libraries with active PyPI pages and real documentation. "
        "Do not recommend libraries named after generic words like 'send', 'message', or 'text'. "
        'Return JSON only: {"library": "name", "install_name": "pip install name", '
        '"reason": "one sentence why", "skill_description": "what Watson will be able to do"}'
    )
    base_user = (
        f"Capability needed: {capability_description}\n\n"
        f"PyPI results: {json.dumps(pypi_results)}\n\n"
        f"GitHub results: {json.dumps(github_results)}"
    )

    recommendation = None
    for attempt in range(2):
        call_user = base_user if attempt == 0 else (
            base_user + "\n\nIMPORTANT: Your previous suggestion was a generic or hallucinated "
            "library name. Recommend a well-known, established Python library with active PyPI "
            "downloads and real documentation. Examples of good answers: requests, httpx, "
            "beautifulsoup4, pillow, pandas, pydantic."
        )
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": f"SYSTEM:\n{system}\n\nUSER:\n{call_user}",
                    "stream": False,
                },
                timeout=120,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
            if not match:
                log.error("Ollama returned no JSON (attempt %d): %s", attempt + 1, raw[:200])
                continue
            candidate = json.loads(match.group())
            lib = candidate.get("library", "").lower().strip()
            if lib in _BLOCKED_LIBRARIES:
                log.warning("Blocked library '%s' from LLM (attempt %d) — retrying", lib, attempt + 1)
                continue
            recommendation = candidate
            break
        except Exception as exc:
            log.error("Ollama recommendation failed (attempt %d): %s", attempt + 1, exc)

    if not recommendation:
        log.error("No valid library recommendation after retries for: %s", capability_description)
        return {}

    return {
        "library": recommendation.get("library", ""),
        "install_name": recommendation.get("install_name", ""),
        "reason": recommendation.get("reason", ""),
        "skill_description": recommendation.get("skill_description", ""),
        "pypi_url": pypi_results[0]["project_url"] if pypi_results else "",
        "github_url": github_results[0]["html_url"] if github_results else "",
    }


def propose_skill(capability_description: str) -> bool:
    """Search for a library and send Bill an approval proposal via Telegram."""
    result = search_for_skill(capability_description)
    if not result or not result.get("library"):
        _telegram(f"Could not find a suitable library for: {capability_description}")
        return False

    _ensure_table()
    conn = _db_connect()
    cursor = conn.execute(
        """INSERT INTO skill_acquisitions
           (capability, library, install_name, reason, skill_description, status)
           VALUES (?, ?, ?, ?, ?, 'pending')""",
        (
            capability_description,
            result["library"],
            result["install_name"],
            result["reason"],
            result["skill_description"],
        ),
    )
    acquisition_id = cursor.lastrowid
    conn.commit()
    conn.close()

    message = (
        f"🔍 Skill Acquisition Proposal\n\n"
        f"Capability: {capability_description}\n\n"
        f"Library: {result['library']}\n"
        f"Reason: {result['reason']}\n\n"
        f"This will let me: {result['skill_description']}\n\n"
        f"Approve to install and build?"
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"acquire_approve_{acquisition_id}"},
            {"text": "❌ Reject", "callback_data": f"acquire_reject_{acquisition_id}"},
        ]]
    }
    _telegram(message, reply_markup)
    return True


def execute_acquisition(acquisition_id: int) -> bool:
    """Install the approved library and build the skill wrapper."""
    log.info("execute_acquisition called: id=%d", acquisition_id)
    _ensure_table()
    conn = _db_connect()
    row = conn.execute(
        "SELECT * FROM skill_acquisitions WHERE id=?", (acquisition_id,)
    ).fetchone()
    conn.close()

    if not row:
        log.error("Acquisition %d not found", acquisition_id)
        return False

    row = dict(row)
    library = row["library"]
    install_name = row["install_name"]
    skill_description = row["skill_description"]

    install_name = install_name.replace("pip install ", "").replace("pip ", "").strip().split()[0]

    result = subprocess.run(
        ["pip", "install", install_name, "--break-system-packages"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err = result.stderr.strip()[:500]
        log.error("pip install failed for %s: %s", install_name, err)
        _telegram(f"❌ pip install failed for {install_name}:\n{err}")
        conn = _db_connect()
        conn.execute(
            "UPDATE skill_acquisitions SET status='failed' WHERE id=?", (acquisition_id,)
        )
        conn.commit()
        conn.close()
        return False

    req_path = REPO / "requirements.txt"
    if req_path.exists():
        existing = req_path.read_text(encoding="utf-8")
        if library.lower() not in existing.lower() and install_name.lower() not in existing.lower():
            with req_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{install_name}")

    library_slug = re.sub(r"[^a-z0-9]+", "_", library.lower()).strip("_")
    job_path = f"jobs/acquired/{library_slug}.py"
    (REPO / "jobs" / "acquired").mkdir(parents=True, exist_ok=True)

    try:
        from jobs.skillbuilder.build import build_skill
        success = build_skill(skill_description, job_path)
    except Exception as exc:
        log.error("build_skill failed for %s: %s", library, exc)
        _telegram(f"❌ Skill build failed for {library}:\n{exc}")
        conn = _db_connect()
        conn.execute(
            "UPDATE skill_acquisitions SET status='failed' WHERE id=?", (acquisition_id,)
        )
        conn.commit()
        conn.close()
        return False

    conn = _db_connect()
    if success:
        conn.execute(
            "UPDATE skill_acquisitions SET status='built' WHERE id=?", (acquisition_id,)
        )
        conn.commit()
        conn.close()
        _telegram(f"✅ Skill acquired: {library}\nInstalled and built at {job_path}")
        return True
    else:
        conn.execute(
            "UPDATE skill_acquisitions SET status='failed' WHERE id=?", (acquisition_id,)
        )
        conn.commit()
        conn.close()
        _telegram(f"❌ Skill build failed for {library}. Check logs.")
        return False


def run(message: str = None) -> str:
    if message:
        propose_skill(message.strip())
        return "Searching for a skill to handle that. I'll send you a proposal via Telegram."
    return "Skill acquisition ready. Tell me what you need me to be able to do."


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        propose_skill(" ".join(sys.argv[1:]))
    else:
        print(run())
