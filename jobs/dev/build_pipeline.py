"""
build_pipeline.py — End-to-end Watson build pipeline.

Trigger via Telegram: "build <natural language request>"
Flow:
  Step 1  Ollama spec draft
  Step 2  Claude API spec review (approve/revise)
  Step 3  Claude Code execution
  Step 4  Local test
  Step 5  Claude API final review + Telegram approval gate (pipeline stops here)
  Step 6  handle_approval() — called by bot.py when Dr. Bill replies
"""

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

WATSON_ROOT = Path.home() / "watson"
load_dotenv(WATSON_ROOT / ".env")

log = logging.getLogger(__name__)

DB_PATH = WATSON_ROOT / "data" / "watson.db"
PIPELINE_LOG_DIR = WATSON_ROOT / "logs" / "build-pipeline"

_CLAUDE_CMD = shutil.which("claude") or "/home/billyomes/.nvm/versions/node/v24.16.0/bin/claude"
_GIT_CMD = shutil.which("git") or "/usr/bin/git"
_PYTHON_CMD = shutil.which("python3") or "/usr/bin/python3"

_BLOCKED_WORDS = {"auth", "password", "secret", "token", "credentials"}

_SPEC_SYSTEM_PROMPT = (
    "You are a spec writer for Watson, a Python-based AI assistant running on a Beelink mini PC "
    "(Linux Mint, user billyomes, path ~/watson). "
    "Write Claude Code build specs in plain text only — no YAML, no markdown code blocks, no invented filenames. "
    "A spec is a plain English numbered list of requirements. "
    "Always: use the exact file path provided, import at module level not inside functions, "
    "reuse existing Flask app instances never create new ones, "
    "end with: git add -A && git commit -m '[description]' && git push origin main. "
    "Never: create test files unless asked, pip install stdlib modules, force push, touch auth or credentials."
)

_SPEC_REVIEW_SYSTEM = (
    "You are a spec reviewer for Watson, a solo developer AI assistant project. "
    "Review specs for BLOCKING issues only. "
    "Approve unless you find: "
    "(1) wrong or ambiguous file path, "
    "(2) creates a new Flask app instance instead of reusing existing, "
    "(3) imports inside functions instead of module level, "
    "(4) touches auth/credentials/SECRETS.md, "
    "(5) force pushes or rewrites git history, "
    "(6) pip installs stdlib modules. "
    "Do NOT block on: git add scope, function naming style, error handling preferences, "
    "code style opinions, or minor wording. "
    "If in doubt, approve. "
    "Return JSON: recommendation (approve or revise), assessment (str), "
    "required_changes (null or list of BLOCKING issues only)."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tg_send(chat_id: int, text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("WATSON_BOT_TOKEN")
    if not token:
        log.error("No Telegram bot token for pipeline send")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)


def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_approvals_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS build_approvals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id       INTEGER NOT NULL,
            build_request TEXT    NOT NULL,
            refined_spec  TEXT    NOT NULL,
            code_diff     TEXT    NOT NULL,
            test_output   TEXT    NOT NULL,
            review_json   TEXT    NOT NULL,
            status        TEXT    NOT NULL DEFAULT 'pending',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _strip_json_fences(raw: str) -> str:
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


# ---------------------------------------------------------------------------
# Step 1 — Draft spec via Claude Haiku
# ---------------------------------------------------------------------------

def _draft_spec(build_request: str, chat_id: int | None = None) -> str:
    import anthropic
    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=_SPEC_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Build request: {build_request}\n\n"
                    "Write a Claude Code spec for this. Be specific about the exact file "
                    "to modify and the exact changes needed."
                ),
            }],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        log.warning("Claude Haiku spec draft failed (%s), falling back to Ollama", exc)
        if chat_id is not None:
            _tg_send(chat_id, f"⚠️ Claude spec draft unavailable ({exc}), falling back to Ollama...")
        resp = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": "llama3.2:3b",
                "messages": [
                    {"role": "system", "content": _SPEC_SYSTEM_PROMPT},
                    {"role": "user", "content": build_request},
                ],
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()


# ---------------------------------------------------------------------------
# Step 2 — Claude API spec review
# ---------------------------------------------------------------------------

def _review_spec(spec_draft: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_SPEC_REVIEW_SYSTEM,
        messages=[{"role": "user", "content": f"=== BUILD SPEC ===\n{spec_draft}"}],
    )
    return json.loads(_strip_json_fences(msg.content[0].text))


# ---------------------------------------------------------------------------
# Step 3 — Claude Code execution
# ---------------------------------------------------------------------------

def _run_claude_code(spec: str, timestamp: str) -> tuple[int, str]:
    PIPELINE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    spec_file = PIPELINE_LOG_DIR / f"{timestamp}-spec.md"
    spec_file.write_text(spec, encoding="utf-8")

    with spec_file.open("r", encoding="utf-8") as stdin_f:
        result = subprocess.run(
            [_CLAUDE_CMD, "--dangerously-skip-permissions"],
            stdin=stdin_f,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(WATSON_ROOT),
        )
    combined = result.stdout + ("\n" + result.stderr if result.stderr else "")
    return result.returncode, combined.strip()


# ---------------------------------------------------------------------------
# Step 4 — Local test
# ---------------------------------------------------------------------------

def _detect_modified_file() -> str | None:
    result = subprocess.run(
        [_GIT_CMD, "diff", "HEAD~1", "--name-only"],
        capture_output=True, text=True, cwd=str(WATSON_ROOT), timeout=10,
    )
    for line in result.stdout.splitlines():
        if line.strip().endswith(".py"):
            return str(WATSON_ROOT / line.strip())
    return None


def _run_local_test(file_path: str) -> tuple[bool, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "/home/billyomes/watson"
    result = subprocess.run(
        [_PYTHON_CMD, file_path],
        capture_output=True, text=True, timeout=60, env=env,
        cwd=str(WATSON_ROOT),
    )
    combined = result.stdout + ("\n" + result.stderr if result.stderr else "")
    return result.returncode == 0, combined.strip()


# ---------------------------------------------------------------------------
# Step 5 — Store pending approval in DB
# ---------------------------------------------------------------------------

def _get_code_diff() -> str:
    try:
        result = subprocess.run(
            [_GIT_CMD, "diff", "HEAD"],
            capture_output=True, text=True, cwd=str(WATSON_ROOT), timeout=15,
        )
        diff = result.stdout.strip()
        if diff:
            return diff
        # Changes already committed
        result = subprocess.run(
            [_GIT_CMD, "show", "HEAD"],
            capture_output=True, text=True, cwd=str(WATSON_ROOT), timeout=15,
        )
        return result.stdout.strip()
    except Exception as exc:
        return f"(diff unavailable: {exc})"


def _store_pending_approval(
    chat_id: int,
    build_request: str,
    refined_spec: str,
    code_diff: str,
    test_output: str,
    review_json: dict,
) -> int:
    with _db_conn() as conn:
        _init_approvals_table(conn)
        cursor = conn.execute(
            """INSERT INTO build_approvals
               (chat_id, build_request, refined_spec, code_diff, test_output, review_json, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (
                chat_id,
                build_request,
                refined_spec,
                code_diff,
                test_output,
                json.dumps(review_json),
            ),
        )
        return cursor.lastrowid


# ---------------------------------------------------------------------------
# Main pipeline — runs in background thread
# ---------------------------------------------------------------------------

def run(build_request: str, chat_id: int) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log.info("Build pipeline started: %r  chat_id=%d  ts=%s", build_request, chat_id, ts)

    # Hard guard: reject requests touching sensitive domains
    request_lower = build_request.lower()
    if any(w in request_lower for w in _BLOCKED_WORDS):
        _tg_send(chat_id, "Rejected: build requests cannot touch auth or credentials.")
        return
    if "build_pipeline" in request_lower:
        _tg_send(chat_id, "Rejected: build requests cannot modify the build pipeline itself.")
        return

    # ── Step 1: Draft spec ──────────────────────────────────────────────────
    try:
        spec_draft = _draft_spec(build_request, chat_id)
    except Exception as exc:
        _tg_send(chat_id, f"Build pipeline failed at spec draft: {exc}")
        return
    _tg_send(chat_id, "📋 Spec drafted. Sending to Claude for review...")

    # ── Step 2: Claude API spec review (up to 2 attempts with auto-revision) ─
    auto_revised = False
    for attempt in range(1, 3):
        try:
            spec_review = _review_spec(spec_draft)
        except Exception as exc:
            _tg_send(chat_id, f"Build pipeline failed at spec review: {exc}")
            return

        if spec_review.get("recommendation") == "approve":
            break

        changes = spec_review.get("required_changes") or []
        changes_numbered = (
            "\n".join(f"{i + 1}. {c}" for i, c in enumerate(changes))
            if changes
            else spec_review.get("assessment", "No details provided.")
        )

        if attempt == 2:
            changes_text = (
                "\n".join(f"• {c}" for c in changes)
                if changes
                else spec_review.get("assessment", "No details provided.")
            )
            _tg_send(
                chat_id,
                f"⚠️ Spec revision failed after 2 attempts. Required changes:\n{changes_text}\n\n"
                f"Re-trigger with: build [refined request]",
            )
            return

        # attempt == 1: auto-revise spec and loop back for second review
        revision_prompt = (
            f"You are Watson's spec writer. Revise the following spec based on required changes.\n\n"
            f"ORIGINAL REQUEST: {build_request}\n\n"
            f"PREVIOUS SPEC:\n{spec_draft}\n\n"
            f"REQUIRED CHANGES:\n{changes_numbered}\n\n"
            f"Produce a corrected spec incorporating all required changes."
        )
        try:
            spec_draft = _draft_spec(revision_prompt, chat_id)
        except Exception as exc:
            _tg_send(chat_id, f"Build pipeline failed at spec auto-revision: {exc}")
            return
        auto_revised = True

    refined_spec = spec_draft
    if auto_revised:
        _tg_send(chat_id, "🔄 Spec auto-revised and approved. Continuing build...")
    else:
        _tg_send(chat_id, "✅ Spec approved by Claude. Running Claude Code...")

    # ── Step 3: Claude Code execution ───────────────────────────────────────
    try:
        returncode, claude_code_output = _run_claude_code(refined_spec, ts)
    except subprocess.TimeoutExpired:
        _tg_send(chat_id, "Build pipeline failed: Claude Code timed out after 300s.")
        return
    except Exception as exc:
        _tg_send(chat_id, f"Build pipeline failed at Claude Code: {exc}")
        return

    if returncode != 0:
        summary = claude_code_output[-1000:] if len(claude_code_output) > 1000 else claude_code_output
        _tg_send(chat_id, f"Claude Code exited with error (code {returncode}):\n\n{summary}")
        return

    _tg_send(chat_id, "🔨 Claude Code finished. Running local test...")

    # ── Step 4: Local test ──────────────────────────────────────────────────
    file_path = _detect_modified_file()
    if not file_path:
        _tg_send(
            chat_id,
            "⚠️ Could not detect modified Python file. Check ~/watson/logs/build-pipeline/ and test manually.",
        )
        return

    try:
        test_passed, test_output = _run_local_test(file_path)
    except subprocess.TimeoutExpired:
        _tg_send(chat_id, f"Build pipeline failed: local test timed out after 60s ({file_path}).")
        return
    except Exception as exc:
        _tg_send(chat_id, f"Build pipeline failed at local test: {exc}")
        return

    if not test_passed:
        summary = test_output[-800:] if len(test_output) > 800 else test_output
        _tg_send(chat_id, f"Local test failed for {file_path}:\n\n{summary}")
        return

    _tg_send(chat_id, "✅ Local test passed. Sending to Claude for final review...")

    # ── Step 5: Claude API final review ─────────────────────────────────────
    from jobs.dev.claude_api_final_review import run as _final_review

    code_diff = _get_code_diff()
    try:
        review_json = _final_review(
            spec=refined_spec,
            code_diff=code_diff,
            test_output=test_output,
            systems_affected=["watson"],
            build_metadata={"request": build_request, "timestamp": ts},
        )
    except Exception as exc:
        log.error("Final review call failed: %s", exc)
        review_json = {"recommendation": "error", "assessment": str(exc)}

    risks = review_json.get("risks") or []
    risks_text = "\n".join(f"• {r}" for r in risks) if risks else "None identified"

    rec = (review_json.get("recommendation") or "unknown").upper()
    rec_icon = "✅" if rec == "DEPLOY" else "⚠️"

    approval_msg = (
        f"🔍 Claude's Review:\n\n"
        f"{review_json.get('assessment', '')}\n\n"
        f"Recommendation: {rec} {rec_icon}\n"
        f"Confidence: {review_json.get('confidence', 'N/A')}\n"
        f"Deployment Safety: {review_json.get('deployment_safety', 'N/A')}\n\n"
        f"Risks:\n{risks_text}\n\n"
        f"Reply 'approve' to deploy or 'refine: [feedback]' to revise."
    )
    _tg_send(chat_id, approval_msg)

    _store_pending_approval(
        chat_id=chat_id,
        build_request=build_request,
        refined_spec=refined_spec,
        code_diff=code_diff,
        test_output=test_output,
        review_json=review_json,
    )
    log.info("Build pipeline paused — awaiting Telegram approval  chat_id=%d", chat_id)


# ---------------------------------------------------------------------------
# Step 6 — Telegram approval handler (called by bot.py)
# ---------------------------------------------------------------------------

def has_pending_approval(chat_id: int) -> bool:
    """Quick check used by bot.py before calling handle_approval."""
    try:
        with _db_conn() as conn:
            _init_approvals_table(conn)
            row = conn.execute(
                "SELECT id FROM build_approvals WHERE chat_id=? AND status='pending' LIMIT 1",
                (chat_id,),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def handle_approval(chat_id: int, message: str) -> None:
    msg = message.lower().strip()

    with _db_conn() as conn:
        _init_approvals_table(conn)
        row = conn.execute(
            """SELECT * FROM build_approvals
               WHERE chat_id=? AND status='pending'
               ORDER BY created_at DESC LIMIT 1""",
            (chat_id,),
        ).fetchone()

    if not row:
        return

    row = dict(row)

    if msg == "approve":
        commit_msg = f"Watson build: {row['build_request'][:60]}"

        result = subprocess.run(
            [_GIT_CMD, "add", "-A"],
            capture_output=True, text=True, cwd=str(WATSON_ROOT), timeout=15,
        )
        if result.returncode != 0:
            _tg_send(chat_id, f"Deployment failed at git add:\n{result.stderr.strip()}")
            return

        result = subprocess.run(
            [_GIT_CMD, "commit", "--allow-empty", "-m", commit_msg],
            capture_output=True, text=True, cwd=str(WATSON_ROOT), timeout=15,
        )
        if result.returncode != 0:
            _tg_send(chat_id, f"Deployment failed at git commit:\n{result.stderr.strip()}")
            return

        result = subprocess.run(
            [_GIT_CMD, "push", "origin", "main"],
            capture_output=True, text=True, cwd=str(WATSON_ROOT), timeout=30,
        )
        if result.returncode != 0:
            _tg_send(chat_id, f"Deployment failed at git push:\n{result.stderr.strip()}")
            return

        with _db_conn() as conn:
            conn.execute(
                "UPDATE build_approvals SET status='approved' WHERE id=?", (row["id"],)
            )

        _tg_send(
            chat_id,
            "🚀 Deployed. Pull on Beelink: cd ~/watson && git pull && sudo systemctl restart watson-dashboard watson-bot",
        )

        # Archive build record
        try:
            from jobs.dev.build_memory_store import run as _store_build
            review_json = json.loads(row["review_json"])
            memory_result = _store_build(
                build_name=row["build_request"][:40].replace(" ", "-"),
                spec_text=row["refined_spec"],
                code_diff=row["code_diff"],
                test_output=row["test_output"],
                claude_review_json=review_json,
                human_approval="approve",
                services_restarted=["watson-dashboard", "watson-bot"],
                files_changed=[],
            )
            _tg_send(chat_id, f"📦 Build archived. Build ID: {memory_result['build_id']}")
        except Exception as exc:
            log.error("Build archive failed: %s", exc)
            _tg_send(chat_id, f"Build archived with error: {exc}")

    elif msg.startswith("refine:"):
        feedback = message[len("refine:"):].strip()
        with _db_conn() as conn:
            conn.execute(
                "UPDATE build_approvals SET status='revised' WHERE id=?", (row["id"],)
            )
        _tg_send(
            chat_id,
            f"Got it. Re-trigger with: build {row['build_request']} — {feedback}",
        )


# ---------------------------------------------------------------------------
# Local test mode — Steps 1 and 2 only
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    test_request = "add a health check endpoint to the dashboard"
    print(f"Testing Steps 1 and 2 only for: {test_request!r}\n")

    print("=== Step 1: Draft Spec (Ollama) ===")
    try:
        spec = _draft_spec(test_request)
        print(spec)
    except Exception as e:
        print(f"Draft failed: {e}")
        sys.exit(1)

    print("\n=== Step 2: Claude API Spec Review ===")
    try:
        review = _review_spec(spec)
        print(json.dumps(review, indent=2))
    except Exception as e:
        print(f"Spec review failed: {e}")
        sys.exit(1)

    print(f"\nResult: {review.get('recommendation', 'unknown')}")
    if review.get("required_changes"):
        print("Required changes:")
        for c in review["required_changes"]:
            print(f"  • {c}")
