"""jobs/skillbuilder/router.py — route messages to skills or fall through to chat."""
import importlib
import io
import json
import logging
from contextlib import redirect_stdout
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
SKILLS_FILE = REPO / "memory" / "skills.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

log = logging.getLogger(__name__)


def _load_skills(interface: str) -> list:
    if not SKILLS_FILE.exists():
        return []
    try:
        skills = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [s for s in skills if interface in s.get("interfaces", [])]


def _ask_router(message: str, skills: list) -> str:
    skills_json = json.dumps(skills, indent=2)
    prompt = (
        "SYSTEM: You are Watson's skill router. Given a user message and a list of "
        "available skills, determine the best action. Reply with exactly one of: "
        "SKILL:<slug>, PROPOSE, or CHAT. Nothing else.\n\n"
        "SKILL:<slug> — the message clearly maps to a known skill. Use the slug exactly as listed.\n"
        "PROPOSE — the message describes a task Watson should be able to do but currently cannot.\n"
        "CHAT — this is general conversation, a question, or something Watson should just respond to normally.\n\n"
        f"Available skills:\n{skills_json}\n\n"
        f"User message: {message}"
    )
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _run_skill(skill: dict) -> str:
    mod = importlib.import_module(skill["job_module"])
    fn = getattr(mod, skill["function"])
    buf = io.StringIO()
    result = None
    with redirect_stdout(buf):
        result = fn()
    output = buf.getvalue().strip()
    if result is not None:
        return str(result)
    return output or "(no output)"


def route(message: str, interface: str) -> dict:
    """Route a message to a skill, propose a new skill, or fall through to chat.

    Returns one of:
      {"action": "skill", "slug": str, "result": str}
      {"action": "propose", "message": str}
      {"action": "chat"}
    """
    skills = _load_skills(interface)
    if not skills:
        return {"action": "chat"}

    try:
        decision = _ask_router(message, skills)
    except Exception as exc:
        log.warning("Skill router LLM call failed: %s", exc)
        return {"action": "chat"}

    if decision.startswith("SKILL:"):
        slug = decision[len("SKILL:"):].strip()
        skill = next((s for s in skills if s["slug"] == slug), None)
        if not skill:
            return {"action": "chat"}
        try:
            result = _run_skill(skill)
        except Exception as exc:
            result = f"Skill failed to execute: {exc}"
        return {"action": "skill", "slug": slug, "result": result}

    if decision == "PROPOSE":
        return {
            "action": "propose",
            "message": "I don't have a skill for that yet. Want me to build one?",
        }

    return {"action": "chat"}
