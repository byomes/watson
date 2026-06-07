"""jobs/dev/skill_validator.py — validate skills before promoting from Dev to Ready."""
import json
import logging
import os
import py_compile
import re
import sqlite3
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]
SKILLS_FILE = REPO / "memory" / "skills.json"
DB_PATH = Path(os.getenv("WATSON_DB", str(REPO / "data" / "watson.db")))


def _load_skills() -> list:
    try:
        return json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_skills(skills: list) -> None:
    SKILLS_FILE.write_text(json.dumps(skills, indent=2), encoding="utf-8")


def _telegram(text: str) -> None:
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000]},
            timeout=15,
        )
    except Exception as exc:
        log.warning("Telegram failed: %s", exc)


def validate_skill(slug: str) -> dict:
    skills = _load_skills()
    skill = next((s for s in skills if s["slug"] == slug), None)

    if not skill:
        return {
            "slug": slug, "passed": False, "score": "0/8",
            "checks": {"skill_found": {"passed": False, "detail": "Not in skills.json"}},
        }

    full_path = REPO / (skill["job_module"].replace(".", "/") + ".py")
    checks = {}

    # 1. file_exists
    checks["file_exists"] = {"passed": full_path.exists(), "detail": str(full_path.relative_to(REPO))}
    if not full_path.exists():
        return {"slug": slug, "passed": False, "checks": checks, "score": "1/8"}

    content = full_path.read_text(encoding="utf-8", errors="ignore")

    # 2. has_run_function
    has_run = bool(re.search(r'^\s*def run\(', content, re.MULTILINE))
    checks["has_run_function"] = {"passed": has_run, "detail": "found" if has_run else "missing def run("}

    # 3. no_syntax_errors
    try:
        py_compile.compile(str(full_path), doraise=True)
        checks["no_syntax_errors"] = {"passed": True, "detail": "OK"}
    except py_compile.PyCompileError as exc:
        checks["no_syntax_errors"] = {"passed": False, "detail": str(exc)[:120]}

    # 4. no_tilde_paths
    tilde_count = len(re.findall(r'"~/', content))
    checks["no_tilde_paths"] = {
        "passed": tilde_count == 0,
        "detail": f"{tilde_count} unexpanded path(s)" if tilde_count else "OK",
    }

    # 5. imports_resolve
    script = (
        "import sys\n"
        f"sys.path.insert(0, {repr(str(REPO))})\n"
        "try:\n"
        "    import importlib\n"
        f"    importlib.import_module({repr(skill['job_module'])})\n"
        "    print('OK')\n"
        "except Exception as e:\n"
        "    print('FAIL:' + str(e))\n"
    )
    try:
        r = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=20, cwd=str(REPO),
        )
        out = (r.stdout + r.stderr).strip()
        checks["imports_resolve"] = {"passed": out == "OK", "detail": out if out != "OK" else "OK"}
    except subprocess.TimeoutExpired:
        checks["imports_resolve"] = {"passed": False, "detail": "Import timed out"}

    # 6–8: run() behaviour via isolated subprocess
    from jobs.dev.skill_tester import test_skill
    result = test_skill(slug)

    checks["no_crashes"] = {
        "passed": result["success"],
        "detail": result["error"][:120] if result["error"] else "OK",
    }
    checks["execution_under_10s"] = {
        "passed": result["execution_time_ms"] < 10000,
        "detail": f"{result['execution_time_ms']}ms",
    }
    checks["run_returns_string"] = {
        "passed": result["success"] and isinstance(result.get("output"), str),
        "detail": "returns str" if result["success"] else result["error"][:80],
    }

    passed_count = sum(1 for c in checks.values() if c["passed"])
    total = len(checks)
    return {
        "slug": slug,
        "passed": passed_count == total,
        "checks": checks,
        "score": f"{passed_count}/{total}",
    }


def validate_all_dev_skills() -> str:
    skills = _load_skills()
    dev_skills = [s for s in skills if s.get("status") == "dev"]
    if not dev_skills:
        return "No dev skills found."

    lines = [f"Validating {len(dev_skills)} dev skill(s)...\n"]
    ready_to_promote, needs_fixes = [], []

    for skill in dev_skills:
        result = validate_skill(skill["slug"])
        if result["passed"]:
            lines.append(f"✓ {skill['slug']} ({result['score']}) — ready to promote")
            ready_to_promote.append(skill["slug"])
        else:
            failed_checks = [k for k, v in result["checks"].items() if not v["passed"]]
            lines.append(f"✗ {skill['slug']} ({result['score']}) — failing: {', '.join(failed_checks)}")
            needs_fixes.append(skill["slug"])

    lines.append(f"\nReady to promote: {', '.join(ready_to_promote) or 'none'}")
    lines.append(f"Needs fixes: {', '.join(needs_fixes) or 'none'}")
    return "\n".join(lines)


def auto_promote(slug: str) -> bool:
    result = validate_skill(slug)
    if not result["passed"]:
        failed = [k for k, v in result["checks"].items() if not v["passed"]]
        _telegram(f"✗ {slug} failed validation ({result['score']}): {', '.join(failed)}")
        return False

    skills = _load_skills()
    updated = False
    for s in skills:
        if s["slug"] == slug and s.get("status") != "ready":
            s["status"] = "ready"
            updated = True
            break

    if not updated:
        return False

    _save_skills(skills)
    subprocess.run(["git", "add", str(SKILLS_FILE)], cwd=str(REPO), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"skill: auto-promoted {slug} to ready"],
        cwd=str(REPO), capture_output=True,
    )
    try:
        from jobs.memory.sync import main as sync_main
        sync_main()
    except Exception:
        pass
    _telegram(f"✓ Skill auto-promoted to Ready: {slug} ({result['score']} checks passed)")
    return True


def run(message: str = None) -> str:
    if not message or message.lower().strip() in ("all", "validate all", "validate all skills"):
        return validate_all_dev_skills()

    match = re.search(r'[\w]+', message.replace("-", "_"))
    slug = match.group() if match else ""
    if not slug:
        return validate_all_dev_skills()

    result = validate_skill(slug)
    lines = [f"Validation: {slug} — {result['score']}"]
    for check, info in result["checks"].items():
        icon = "✓" if info["passed"] else "✗"
        lines.append(f"  {icon} {check}: {info['detail']}")
    if result["passed"]:
        lines.append(f"\nAll checks passed. Run auto_promote('{slug}') to promote.")
    else:
        failed = [k for k, v in result["checks"].items() if not v["passed"]]
        lines.append(f"\nFailing: {', '.join(failed)}")
    return "\n".join(lines)
