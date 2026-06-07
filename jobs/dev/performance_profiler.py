"""jobs/dev/performance_profiler.py — profile skill execution time and memory."""
import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]
SKILLS_FILE = REPO / "memory" / "skills.json"


def _load_skills() -> list:
    try:
        return json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def profile_skill(slug: str) -> dict:
    skills = _load_skills()
    skill = next((s for s in skills if s["slug"] == slug), None)
    if not skill:
        return {"slug": slug, "execution_time_ms": 0, "peak_memory_mb": 0.0, "rating": "unknown", "error": "not found"}

    script = (
        "import sys, json, time, tracemalloc\n"
        f"sys.path.insert(0, {repr(str(REPO))})\n"
        "try:\n"
        "    import importlib\n"
        "    tracemalloc.start()\n"
        f"    mod = importlib.import_module({repr(skill['job_module'])})\n"
        "    _start = time.time()\n"
        "    mod.run(None)\n"
        "    _ms = int((time.time() - _start) * 1000)\n"
        "    _, _peak = tracemalloc.get_traced_memory()\n"
        "    tracemalloc.stop()\n"
        "    print(json.dumps({'ms': _ms, 'peak_kb': _peak // 1024}))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    try:
        r = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=60, cwd=str(REPO),
        )
        stdout = r.stdout.strip()
        if not stdout:
            return {"slug": slug, "execution_time_ms": 0, "peak_memory_mb": 0.0, "rating": "error", "error": r.stderr.strip()[:200]}
        data = json.loads(stdout)
        if "error" in data:
            return {"slug": slug, "execution_time_ms": 0, "peak_memory_mb": 0.0, "rating": "error", "error": data["error"]}
        ms = data.get("ms", 0)
        mb = round(data.get("peak_kb", 0) / 1024, 2)
        rating = "fast" if ms < 2000 else ("slow" if ms < 5000 else "very_slow")
        return {"slug": slug, "execution_time_ms": ms, "peak_memory_mb": mb, "rating": rating}
    except subprocess.TimeoutExpired:
        return {"slug": slug, "execution_time_ms": 60000, "peak_memory_mb": 0.0, "rating": "very_slow", "error": "timeout"}
    except Exception as exc:
        return {"slug": slug, "execution_time_ms": 0, "peak_memory_mb": 0.0, "rating": "error", "error": str(exc)}


def profile_all_skills() -> str:
    skills = _load_skills()
    ready = [s for s in skills if s.get("status") == "ready"]
    if not ready:
        return "No ready skills to profile."

    results = [profile_skill(s["slug"]) for s in ready]
    results.sort(key=lambda r: r["execution_time_ms"], reverse=True)

    lines = [f"Performance report ({len(results)} skills):\n"]
    for r in results:
        flag = " ⚠" if r.get("error") else (" 🔴" if r["execution_time_ms"] > 5000 else ("" if r["peak_memory_mb"] <= 100 else " 💾"))
        lines.append(f"  {r['rating']:9} {r['slug']}: {r['execution_time_ms']}ms, {r['peak_memory_mb']}MB{flag}")
    return "\n".join(lines)


def run(message: str = None) -> str:
    if not message:
        return profile_all_skills()
    match = re.search(r'[\w]+', message.replace("-", "_"))
    if not match:
        return profile_all_skills()
    slug = match.group()
    r = profile_skill(slug)
    if r.get("error"):
        return f"Profile error for {slug}: {r['error']}"
    return f"{slug}: {r['execution_time_ms']}ms, {r['peak_memory_mb']}MB — {r['rating']}"
