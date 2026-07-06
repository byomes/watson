"""jobs/skillbuilder/build.py — three-tier skill builder: Ollama → Claude Sonnet → Claude Code."""
import json
import logging
import os
import sqlite3
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
DB_PATH = Path(os.getenv("WATSON_DB", str(REPO / "data" / "watson.db")))
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5-coder:7b"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Telegram ──────────────────────────────────────────────────────────────────

def _telegram(text: str) -> None:
    # Mix of build-progress and build-failure messages for the skill-acquisition
    # pipeline itself (not a core Watson job) — tagged "normal" per default.
    if vacation_gate("normal", "jobs.skillbuilder.build", text):
        return
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        log.warning("Telegram credentials not set — skipping notification")
        return
    # Telegram message limit is 4096 chars
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


# ── Context loaders ───────────────────────────────────────────────────────────

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


# ── Model callers ─────────────────────────────────────────────────────────────

def _call_ollama(prompt: str) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _call_anthropic(system: str, user: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()


# ── Code utilities ────────────────────────────────────────────────────────────

def _strip_fences(code: str) -> str:
    if "```" in code:
        lines = code.splitlines()
        return "\n".join(line for line in lines if not line.startswith("```")).strip()
    return code


def _ensure_run_function(code: str, description: str) -> str:
    """Append a minimal run() if the generated code is missing one."""
    import ast as _ast
    try:
        tree = _ast.parse(code)
        func_names = {n.name for n in _ast.walk(tree) if isinstance(n, _ast.FunctionDef)}
        if "run" not in func_names:
            log.info("run() missing — appending default stub")
            stub = (
                "\n\ndef run(message: str = None) -> str:\n"
                f'    return "{description[:80]}"\n'
            )
            code = code.rstrip() + stub
    except Exception:
        pass
    return code


def _auto_format(code: str) -> str:
    """Run black on the generated code. Returns original on failure."""
    try:
        import black
        return black.format_str(code, mode=black.Mode())
    except Exception as exc:
        log.debug("black format skipped: %s", exc)
        return code


def _enhance_code(code: str, description: str) -> tuple[str, list]:
    """Format, ensure run() exists, collect lint issues. Returns (enhanced_code, lint_issues)."""
    code = _ensure_run_function(code, description)
    code = _auto_format(code)

    lint_issues = []
    try:
        from jobs.dev.code_quality import lint_code
        lint_issues = lint_code(code)
    except Exception as exc:
        log.debug("lint_code skipped: %s", exc)

    return code, lint_issues


def _syntax_check(code: str) -> tuple[bool, str]:
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


# ── Git ───────────────────────────────────────────────────────────────────────

def _run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, **kwargs)


def safe_git_push() -> tuple[bool, str]:
    """Fetch, sync if behind, then push. Returns (success, error_message)."""
    r = _run(["git", "fetch", "origin"])
    if r.returncode != 0:
        return False, f"git fetch failed: {r.stderr.strip()}"

    r = _run(["git", "rev-list", "HEAD..origin/main", "--count"])
    if r.returncode != 0:
        return False, f"git rev-list failed: {r.stderr.strip()}"

    behind = int(r.stdout.strip() or "0")

    if behind > 0:
        _run(["git", "stash"])
        r = _run(["git", "pull", "origin", "main"])
        if r.returncode != 0:
            _run(["git", "stash", "pop"])
            return False, f"git pull failed: {r.stderr.strip()}"
        r = _run(["git", "stash", "pop"])
        if r.returncode != 0:
            msg = "Git conflict during build push — manual resolution needed"
            _telegram(msg)
            log.error(msg)
            return False, msg

    r = _run(["git", "push", "origin", "main"])
    if r.returncode != 0:
        return False, f"git push failed: {r.stderr.strip()}"

    return True, ""


def _commit_and_push(dest: Path, job_path: str, built_by: str) -> tuple[bool, str]:
    r = _run(["git", "add", str(dest)])
    if r.returncode != 0:
        return False, f"git add failed: {r.stderr.strip()}"
    r = _run(["git", "commit", "-m", f"skill: {job_path} — generated by {built_by}"])
    if r.returncode != 0:
        return False, f"git commit failed: {r.stderr.strip()}"
    return safe_git_push()


# ── DB tracking ───────────────────────────────────────────────────────────────

def _db_init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS build_attempts (
            id           INTEGER PRIMARY KEY,
            description  TEXT,
            job_path     TEXT,
            tier_reached INTEGER,
            success      INTEGER,
            error_1      TEXT,
            error_2      TEXT,
            error_3      TEXT,
            built_by     TEXT,
            created_at   TEXT
        )
    """)
    conn.commit()
    conn.close()


def _log_build(
    description: str,
    job_path: str,
    tier_reached: int,
    success: bool,
    error_1: str = "",
    error_2: str = "",
    error_3: str = "",
    built_by: str = "",
) -> None:
    try:
        _db_init()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO build_attempts "
            "(description, job_path, tier_reached, success, error_1, error_2, error_3, built_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                description, job_path, tier_reached, int(success),
                error_1, error_2, error_3, built_by,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.warning("DB log failed (non-fatal): %s", exc)


# ── Memory updates ────────────────────────────────────────────────────────────

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


def _update_python_memory(job_path: str, description: str, built_by: str) -> None:
    python_md = MEMORY / "coding" / "python.md"
    if not python_md.exists():
        return
    content = python_md.read_text(encoding="utf-8")
    today = date.today().isoformat()
    entry = f"- {job_path}: {description[:80]} (built by {built_by} on {today})"
    if "## Recently Built" in content:
        python_md.write_text(content.rstrip() + f"\n{entry}\n", encoding="utf-8")
    else:
        python_md.write_text(
            content.rstrip() + f"\n\n## Recently Built\n{entry}\n",
            encoding="utf-8",
        )


def _validate_after_build(job_path: str) -> None:
    """Run full validation after a successful build; notify and auto-fix if needed."""
    slug = Path(job_path).stem
    try:
        from jobs.dev.skill_validator import validate_skill
        result = validate_skill(slug)
        score = result["score"]

        # Log validation result to the most recent build_attempts row
        try:
            conn = sqlite3.connect(str(DB_PATH))
            try:
                conn.execute("ALTER TABLE build_attempts ADD COLUMN validation_result TEXT")
                conn.commit()
            except Exception:
                pass  # column already exists
            row = conn.execute(
                "SELECT id FROM build_attempts WHERE job_path=? ORDER BY id DESC LIMIT 1",
                (job_path,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE build_attempts SET validation_result=? WHERE id=?",
                    (f"{score} {'pass' if result['passed'] else 'fail'}", row[0]),
                )
                conn.commit()
            conn.close()
        except Exception as db_exc:
            log.debug("Validation DB log failed: %s", db_exc)

        if result["passed"]:
            _telegram(f"✓ Built and validated: {slug} — ready to approve ({score} checks passed)")
        else:
            failed = [k for k, v in result["checks"].items() if not v["passed"]]
            _telegram(f"⚠ {slug} built but {score} checks passed. Failing: {', '.join(failed)}")
            try:
                from jobs.dev.auto_fixer import auto_fix_skill
                fix_result = auto_fix_skill(slug)
                _telegram(f"Auto-fix: {fix_result[:200]}")
            except Exception as fx_exc:
                log.warning("Auto-fix after build failed: %s", fx_exc)
    except Exception as exc:
        log.warning("Post-build validation failed (non-fatal): %s", exc)


def _post_success(job_path: str, description: str, built_by: str, code: str = "") -> None:
    Path(tempfile.gettempdir()).joinpath("watson_skill_draft.py").unlink(missing_ok=True)
    try:
        _update_python_memory(job_path, description, built_by)
        _update_skills_json(job_path, description)
        if code:
            _save_learning(description, job_path, built_by, code)
        from jobs.memory.sync import main as sync_main
        sync_main()
    except Exception as exc:
        log.warning("Memory update failed (non-fatal): %s", exc)
    _validate_after_build(job_path)


# ── Build skill ───────────────────────────────────────────────────────────────

def _save_learning(description: str, job_path: str, built_by: str, code: str) -> None:
    """Summarize the newly built code and append the pattern to coding memory."""
    if not code:
        return
    first_50 = "\n".join(code.splitlines()[:50])
    first_20 = "\n".join(code.splitlines()[:20])
    today = date.today().isoformat()

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": "qwen2.5:14b",
                "prompt": (
                    "In one paragraph, what is the key coding pattern demonstrated in this code? "
                    "Focus on the approach, not the specifics.\n\n" + first_50
                ),
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        summary = resp.json().get("response", "").strip()
    except Exception as exc:
        log.warning("_save_learning: Ollama call failed: %s", exc)
        return

    desc_lower = description.lower()
    if any(k in desc_lower for k in ("telegram", "message", "notify", "bot")):
        target = MEMORY / "coding" / "telegram.md"
    elif any(k in desc_lower for k in ("sqlite", "db", "database", "sql")):
        target = MEMORY / "coding" / "sqlite.md"
    elif any(k in desc_lower for k in ("ollama", "llm", "llama", "model", "chat")):
        target = MEMORY / "coding" / "ollama.md"
    else:
        target = MEMORY / "coding" / "python.md"

    if not target.exists():
        return

    entry = (
        f"\n\n## Pattern: {description[:60]} ({today})\n"
        f"{summary}\n"
        f"```python\n{first_20}\n```"
    )
    try:
        with target.open("a", encoding="utf-8") as f:
            f.write(entry)
        log.info("_save_learning: appended to %s", target.name)
    except Exception as exc:
        log.warning("_save_learning: write failed: %s", exc)


def build_skill(description: str, job_path: str) -> bool:
    log.info("Building skill: %s → %s", description[:60], job_path)

    # Research phase: find relevant patterns before writing code
    research_context = ""
    try:
        from jobs.skillbuilder.research import research as _do_research
        research_context = _do_research(description)
        if research_context:
            log.info("Research complete: %d chars", len(research_context))
    except Exception as exc:
        log.warning("Research failed (non-fatal): %s", exc)

    context = _load_context()
    if research_context:
        context = context + "\n\n" + research_context
    examples = _load_examples()

    system = (
        "You are Watson's internal code writer. You write Python jobs for the Watson "
        "assistant system running on a Beelink EQi12 (Linux Mint, Python 3, systemd). "
        "You follow Watson's established conventions exactly. You output only raw Python "
        "code — no markdown, no backticks, no explanation. The code must be complete and "
        "ready to save directly to a file."
    )
    base_user = (
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

    dest = REPO / job_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    code_1 = error_1 = code_2 = error_2 = error_3 = ""

    # ── TIER 1: qwen2.5-coder:7b, first attempt ───────────────────────────
    log.info("Tier 1: calling %s…", OLLAMA_MODEL)
    try:
        raw = _call_ollama(f"SYSTEM:\n{system}\n\nUSER:\n{base_user}")
        code_1 = _strip_fences(raw)
    except Exception as exc:
        error_1 = str(exc)
        log.error("Tier 1 Ollama error: %s", exc)
        _telegram(f"Tier 1 Ollama error — skipping to Claude Sonnet.\n{exc}")

    lint_issues_1 = []
    if code_1:
        code_1, lint_issues_1 = _enhance_code(code_1, description)
        ok, err = _syntax_check(code_1)
        if ok:
            dest.write_text(code_1, encoding="utf-8")
            push_ok, push_err = _commit_and_push(dest, job_path, OLLAMA_MODEL)
            if push_ok:
                _telegram(
                    f"✓ Skill built: {job_path}\n\n{description}\n\n"
                    "Review before activating. Add a cron entry or start as a service to enable."
                )
                _log_build(description, job_path, 1, True, built_by=OLLAMA_MODEL)
                _post_success(job_path, description, OLLAMA_MODEL, code_1)
                return True
            _telegram(f"✗ Git push failed: {push_err}")
            _log_build(description, job_path, 1, False, error_1=push_err, built_by=OLLAMA_MODEL)
            return False
        else:
            error_1 = err
            log.warning("Tier 1 syntax error: %s", err)

    # ── TIER 2: qwen2.5-coder:7b, retry with error context ────────────────
    if code_1:
        log.info("Tier 2: retrying with error context…")
        _telegram("First attempt failed. Trying again with error context.")

        lint_hint = ""
        if lint_issues_1:
            lint_hint = f"\n\nAdditional lint issues to fix:\n" + "\n".join(lint_issues_1[:5])

        tier2_user = (
            base_user
            + f"\n\nYour previous attempt failed with this syntax error:\n{error_1}"
            + lint_hint
            + f"\n\nHere was your attempt:\n{code_1}"
            + "\n\nFix the error and rewrite the complete file."
        )
        try:
            raw = _call_ollama(f"SYSTEM:\n{system}\n\nUSER:\n{tier2_user}")
            code_2 = _strip_fences(raw)
        except Exception as exc:
            error_2 = str(exc)
            log.error("Tier 2 Ollama error: %s", exc)

        if code_2:
            code_2, _ = _enhance_code(code_2, description)
            ok, err = _syntax_check(code_2)
            if ok:
                dest.write_text(code_2, encoding="utf-8")
                push_ok, push_err = _commit_and_push(dest, job_path, OLLAMA_MODEL)
                if push_ok:
                    _telegram(
                        f"✓ Skill built on second attempt: {job_path}\n\n{description}\n\n"
                        "Review before activating."
                    )
                    _log_build(
                        description, job_path, 2, True,
                        error_1=error_1, built_by=OLLAMA_MODEL,
                    )
                    _post_success(job_path, description, OLLAMA_MODEL, code_2)
                    return True
                _telegram(f"✗ Git push failed: {push_err}")
                _log_build(
                    description, job_path, 2, False,
                    error_1=error_1, error_2=push_err, built_by=OLLAMA_MODEL,
                )
                return False
            else:
                error_2 = err
                log.warning("Tier 2 syntax error: %s", err)

    # ── TIER 3: Claude Sonnet via Anthropic API ────────────────────────────
    log.info("Tier 3: escalating to Claude Sonnet…")
    _telegram("Two attempts failed. Escalating to Claude.")

    if not os.getenv("ANTHROPIC_API_KEY"):
        _telegram("Anthropic API key not configured — skipping Claude escalation.")
    else:
        tier3_user = base_user + "\n\nTwo previous attempts by a smaller model failed."
        if code_1:
            tier3_user += f"\n\nAttempt 1 error:\n{error_1}\nAttempt 1 code:\n{code_1}"
        if code_2:
            tier3_user += f"\n\nAttempt 2 error:\n{error_2}\nAttempt 2 code:\n{code_2}"
        tier3_user += "\n\nWrite a correct, complete implementation."

        code_3 = ""
        try:
            code_3 = _strip_fences(_call_anthropic(system, tier3_user))
        except Exception as exc:
            error_3 = str(exc)
            log.error("Tier 3 Anthropic error: %s", exc)

        if code_3:
            code_3, _ = _enhance_code(code_3, description)
            ok, err = _syntax_check(code_3)
            if ok:
                dest.write_text(code_3, encoding="utf-8")
                push_ok, push_err = _commit_and_push(dest, job_path, "claude-sonnet")
                if push_ok:
                    _telegram(
                        f"✓ Skill built by Claude Sonnet after escalation: {job_path}\n\n"
                        f"{description}\n\nReview before activating."
                    )
                    _log_build(
                        description, job_path, 3, True,
                        error_1=error_1, error_2=error_2, built_by="claude-sonnet",
                    )
                    _post_success(job_path, description, "claude-sonnet", code_3)
                    return True
                _telegram(f"✗ Git push failed: {push_err}")
                _log_build(
                    description, job_path, 3, False,
                    error_1=error_1, error_2=error_2, error_3=push_err, built_by="claude-sonnet",
                )
                return False
            else:
                error_3 = err
                log.error("Tier 3 syntax error: %s", err)

    # ── TIER 4: Claude Code prompt via Telegram ────────────────────────────
    log.info("Tier 4: sending Claude Code prompt to Bill…")
    _log_build(
        description, job_path, 4, False,
        error_1=error_1, error_2=error_2, error_3=error_3, built_by="manual",
    )

    failed_parts = []
    if code_1:
        failed_parts.append(f"Attempt 1 error:\n{error_1}\n\nAttempt 1 code (first 400 chars):\n{code_1[:400]}")
    if code_2:
        failed_parts.append(f"Attempt 2 error:\n{error_2}\n\nAttempt 2 code (first 400 chars):\n{code_2[:400]}")
    if error_3:
        failed_parts.append(f"Claude Sonnet error:\n{error_3}")

    claude_prompt = (
        f"Watson coding conventions (excerpt):\n{context[:600]}...\n\n"
        f"Write a complete Python job for: {description}\n\n"
        f"Save to: {job_path}\n\n"
        + ("\n\n".join(failed_parts) + "\n\n" if failed_parts else "")
        + "Output only the Python code. Nothing else."
    )

    _telegram(
        "All automated attempts failed. Here is a Claude Code prompt ready to run manually:\n\n"
        f"---\n{claude_prompt}\n---\n\n"
        "Run: cd ~/watson && claude --dangerously-skip-permissions\n"
        "Then paste the prompt above."
    )
    return False


# ── Entry points ──────────────────────────────────────────────────────────────

def run() -> str:
    """Prompt Bill to describe the skill he wants built."""
    return "Skill builder ready. Describe what you need and I'll write the code."


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python3 -m jobs.skillbuilder.build '<description>' '<job_path>'")
        sys.exit(1)
    success = build_skill(sys.argv[1], sys.argv[2])
    sys.exit(0 if success else 1)
