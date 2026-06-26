"""jobs/dev/error_analyzer.py — analyze Python tracebacks via Ollama and suggest fixes."""
import json
import logging
import os
import re
from pathlib import Path

import requests

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:14b"


def analyze_traceback(traceback_str: str, code: str = None) -> dict:
    system = (
        "You are Watson's error analyzer. Given a Python traceback and optionally the source code, "
        "identify the root cause and provide a specific fix. "
        'Return JSON only: {"root_cause": "one sentence", "fix_description": "what to change", '
        '"fix_code": "the corrected code snippet if applicable", "confidence": "high/medium/low"}'
    )
    user = f"Traceback:\n{traceback_str[:2000]}\n\nSource code:\n{(code or 'not provided')[:1000]}"
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": f"SYSTEM:\n{system}\n\nUSER:\n{user}", "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as exc:
        log.error("analyze_traceback Ollama call failed: %s", exc)
    return {"root_cause": "Analysis unavailable", "fix_description": "", "fix_code": "", "confidence": "low"}


def analyze_skill_error(slug: str) -> str:
    from jobs.dev.skill_tester import test_skill
    result = test_skill(slug)
    if result["success"]:
        return f"No error — {slug} ran successfully."

    code = None
    skills_file = REPO / "memory" / "skills.json"
    try:
        skills = json.loads(skills_file.read_text(encoding="utf-8"))
        skill = next((s for s in skills if s["slug"] == slug), None)
        if skill:
            job_path = REPO / (skill["job_module"].replace(".", "/") + ".py")
            if job_path.exists():
                code = job_path.read_text(encoding="utf-8")
    except Exception:
        pass

    analysis = analyze_traceback(result["traceback"] or result["error"], code)
    lines = [
        f"Root cause: {analysis.get('root_cause', 'Unknown')}",
        f"\nFix: {analysis.get('fix_description', 'No fix suggested')}",
        f"\nConfidence: {analysis.get('confidence', 'low')}",
    ]
    if analysis.get("fix_code"):
        lines.append(f"\nCode:\n{analysis['fix_code'][:400]}")
    return "\n".join(lines)


def run(message: str = None) -> str:
    if not message:
        return "Error analyzer ready. Give me a skill slug or paste a traceback."
    match = re.search(r'[\w]+', message.replace("-", "_"))
    slug = match.group() if match else message.strip()
    return analyze_skill_error(slug)
