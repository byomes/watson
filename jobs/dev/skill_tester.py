"""jobs/dev/skill_tester.py — safely run any Watson skill in isolation."""
import json
import logging
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]
SKILLS_FILE = REPO / "memory" / "skills.json"


def _load_skills() -> list:
    if not SKILLS_FILE.exists():
        return []
    try:
        data = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        return data.get('skills', data) if isinstance(data, dict) else data
    except Exception:
        return []


def test_skill(slug: str, message: str = None) -> dict:
    skills = _load_skills()
    skill = next((s for s in skills if s["slug"] == slug), None)
    if not skill:
        return {
            "slug": slug, "success": False, "output": "",
            "error": f"Skill '{slug}' not found in skills.json",
            "execution_time_ms": 0, "traceback": "",
        }

    script = (
        "import sys, json, time, traceback as _tb\n"
        f"sys.path.insert(0, {repr(str(REPO))})\n"
        "try:\n"
        "    import importlib\n"
        f"    mod = importlib.import_module({repr(skill.get('module', skill.get('job_module', '')))})\n"
        "    _start = time.time()\n"
        f"    result = mod.run({repr(message)})\n"
        "    _ms = int((time.time() - _start) * 1000)\n"
        "    print(json.dumps({'output': str(result), 'ms': _ms}))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e), 'traceback': _tb.format_exc()}))\n"
    )

    wall_start = time.time()
    try:
        proc = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=30, cwd=str(REPO),
        )
        elapsed_ms = int((time.time() - wall_start) * 1000)
        stdout = proc.stdout.strip()
        if not stdout:
            err = proc.stderr.strip()[:500] or "No output"
            return {
                "slug": slug, "success": False, "output": "",
                "error": err, "execution_time_ms": elapsed_ms, "traceback": err,
            }
        data = json.loads(stdout)
        if "error" in data:
            return {
                "slug": slug, "success": False, "output": "",
                "error": data["error"],
                "execution_time_ms": elapsed_ms,
                "traceback": data.get("traceback", ""),
            }
        return {
            "slug": slug, "success": True,
            "output": data.get("output", ""),
            "error": "", "traceback": "",
            "execution_time_ms": data.get("ms", elapsed_ms),
        }
    except subprocess.TimeoutExpired:
        return {
            "slug": slug, "success": False, "output": "",
            "error": "Timed out after 30 seconds",
            "execution_time_ms": 30000, "traceback": "",
        }
    except Exception as exc:
        return {
            "slug": slug, "success": False, "output": "",
            "error": str(exc), "execution_time_ms": 0, "traceback": "",
        }


def run_all_skill_tests() -> dict:
    skills = _load_skills()
    ready = [s for s in skills if s.get("status") == "ready"]
    passed, failed, errors = [], [], []
    for skill in ready:
        result = test_skill(skill["slug"])
        if result["success"]:
            passed.append(result)
        elif result["error"]:
            failed.append(result)
        else:
            errors.append(result)
    return {"passed": passed, "failed": failed, "errors": errors}


def run(message: str = None) -> str:
    if message:
        match = re.search(r'[\w]+', message.replace("-", "_"))
        slug = match.group() if match else message.strip()
        result = test_skill(slug, message)
        if result["success"]:
            return f"✓ {slug} ({result['execution_time_ms']}ms)\n{result['output'][:500]}"
        return f"✗ {slug}: {result['error']}"

    results = run_all_skill_tests()
    passing = len(results["passed"])
    failing = len(results["failed"]) + len(results["errors"])
    lines = [
        "SKILL AUDIT REPORT",
        f"✅ {passing} passing",
        f"❌ {failing} failing",
    ]
    all_failed = results["failed"] + results["errors"]
    if all_failed:
        lines.append("\nFAILING:")
        for r in all_failed:
            err = r["error"][:60]
            lines.append(f"• {r['slug']} — {err}")
    lines.append(
        '\nReply "Watson fix skill [slug]" to fix one at a time, '
        'or "Watson fix all failing skills" to queue them all.'
    )
    return "\n".join(lines)
