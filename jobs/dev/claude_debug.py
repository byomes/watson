"""jobs/dev/claude_debug.py — Watson debug loop: diagnose, fix, review, notify."""
import json
import logging
import os
import re
import shlex
import subprocess
from pathlib import Path

from dotenv import load_dotenv

WATSON_ROOT = Path.home() / "watson"
load_dotenv(WATSON_ROOT / ".env")

log = logging.getLogger(__name__)

MAX_ITERATIONS = 6

_DIAGNOSE_SYSTEM = (
    "You are diagnosing a problem in Watson, an AI assistant system running on a Beelink Linux server. "
    "Watson's codebase is at ~/watson. Analyze the problem and context provided. "
    "Return a JSON object with three fields: "
    "diagnosis (string, plain English explanation of root cause), "
    "claude_code_prompt (string, exact prompt to hand to Claude Code to fix it), "
    "confidence (high/medium/low). "
    "The claude_code_prompt must not touch auth, credentials, or .env files."
)

_REVIEW_SYSTEM = (
    "You are reviewing the output of Claude Code that attempted to fix a problem in Watson, "
    "an AI assistant system. Assess whether the fix succeeded. "
    "Return JSON with exactly three fields: "
    "success (bool), summary (string, plain English), follow_up_needed (bool)."
)


def _gather_context(problem: str) -> str:
    parts = [f"PROBLEM: {problem}\n"]

    for unit, label in [
        ("watson-dashboard.service", "Dashboard log"),
        ("watson-bot.service", "Bot log"),
    ]:
        try:
            result = subprocess.run(
                ["journalctl", "-u", unit, "-n", "50", "--no-pager"],
                capture_output=True, text=True, timeout=10,
            )
            parts.append(f"=== {label} (last 50 lines) ===\n{result.stdout or '(empty)'}\n")
        except Exception as exc:
            parts.append(f"=== {label} ===\nCould not retrieve: {exc}\n")

    # If a Watson file path is mentioned in the problem, include a snippet
    file_match = re.search(r'jobs/[\w/]+\.py', problem)
    if file_match:
        candidate = WATSON_ROOT / file_match.group()
        if candidate.exists():
            try:
                lines = candidate.read_text(encoding="utf-8").splitlines()[:100]
                parts.append(
                    f"=== File snippet: {file_match.group()} ===\n{chr(10).join(lines)}\n"
                )
            except Exception:
                pass

    return "\n".join(parts)


def _call_claude(system: str, user_message: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    return json.loads(raw)


def _run_claude_code(prompt: str) -> str:
    venv_python = WATSON_ROOT / "venv" / "bin" / "python"
    activate = WATSON_ROOT / "venv" / "bin" / "activate"
    safe_prompt = shlex.quote(prompt)
    cmd = f"cd {WATSON_ROOT} && source {activate} && claude --dangerously-skip-permissions {safe_prompt}"
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": str(WATSON_ROOT)},
        )
        output = (result.stdout + result.stderr).strip()
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "Claude Code timed out after 120 seconds."
    except Exception as exc:
        return f"Error running Claude Code: {exc}"


def _ask_bill_continue(problem: str, iteration: int, summary: str) -> bool:
    """Send Bill a check-in and wait up to 30 min for 'continue' or 'stop'."""
    import time
    import requests

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Telegram credentials not set — halting loop")
        return False

    check_msg = (
        f"🔧 Debug loop on {problem} — attempt {iteration}. "
        f"Claude says: {summary}. Continue? Reply 'continue' or 'stop'."
    )
    _send_telegram(check_msg)

    # Grab offset so we only see replies sent after the check-in
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"limit": 1, "timeout": 0},
            timeout=10,
        )
        updates = resp.json().get("result", [])
        offset = (updates[-1]["update_id"] + 1) if updates else 0
    except Exception:
        offset = 0

    deadline = time.time() + 30 * 60
    while time.time() < deadline:
        poll_timeout = min(60, int(deadline - time.time()))
        if poll_timeout <= 0:
            break
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "timeout": poll_timeout, "allowed_updates": ["message"]},
                timeout=poll_timeout + 10,
            )
            updates = resp.json().get("result", [])
        except Exception as exc:
            log.warning("getUpdates error: %s", exc)
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            if str(msg.get("chat", {}).get("id", "")) == str(chat_id):
                text = msg.get("text", "").strip().lower()
                if text == "continue":
                    log.info("Bill approved continuation at iteration %d", iteration)
                    return True
                if text == "stop":
                    log.info("Bill halted loop at iteration %d", iteration)
                    return False

    log.info("No reply from Bill within 30 minutes — halting loop")
    _send_telegram(f"🔧 Debug loop halted: no reply within 30 minutes.\nProblem: {problem}")
    return False


def _send_telegram(message: str) -> None:
    import asyncio
    from telegram import Bot
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Telegram credentials not set — skipping notification")
        return

    async def _send() -> None:
        async with Bot(token=token) as bot:
            await bot.send_message(chat_id=chat_id, text=message)

    asyncio.run(_send())


def debug(problem: str) -> str:
    log.info("Debug loop starting: %s", problem)

    try:
        context = _gather_context(problem)
    except Exception as exc:
        log.error("Context gathering failed: %s", exc)
        context = f"PROBLEM: {problem}\n(Could not gather logs: {exc})"

    diagnosis = "unknown"
    summary = "no review"
    follow_up = False
    iteration = 0

    while True:
        iteration += 1
        log.info("Debug iteration %d", iteration)

        # Step 1: Claude API diagnosis
        try:
            diag = _call_claude(_DIAGNOSE_SYSTEM, context)
            diagnosis = diag.get("diagnosis", "No diagnosis returned")
            prompt = diag.get("claude_code_prompt", "")
            confidence = diag.get("confidence", "unknown")
            log.info("Diagnosis (confidence=%s): %s", confidence, diagnosis)
        except Exception as exc:
            log.error("Claude API diagnosis failed: %s", exc)
            _send_telegram(
                f"Watson debug failed during diagnosis.\n\n"
                f"Problem: {problem}\nError: {exc}\n\n"
                f"Raw logs (truncated):\n{context[:2000]}"
            )
            return f"Debug failed: Claude API unavailable ({exc})"

        if not prompt:
            log.warning("No claude_code_prompt returned — stopping loop")
            break

        # Step 2: Run Claude Code
        code_output = _run_claude_code(prompt)
        log.info("Claude Code output (%d chars): %.200s", len(code_output), code_output)

        # Step 3: Claude reviews the output
        try:
            review_msg = (
                f"Claude Code ran this prompt:\n{prompt}\n\n"
                f"Output:\n{code_output}\n\n"
                "Return JSON: success (bool), summary (string), follow_up_needed (bool)."
            )
            review = _call_claude(_REVIEW_SYSTEM, review_msg)
            success = review.get("success", False)
            summary = review.get("summary", "No summary")
            follow_up = review.get("follow_up_needed", False)
            log.info("Review: success=%s follow_up=%s — %s", success, follow_up, summary)
        except Exception as exc:
            log.error("Claude API review failed: %s", exc)
            summary = f"Review unavailable: {exc}"
            success = False
            follow_up = False

        if success and not follow_up:
            break

        if not follow_up:
            break

        # follow_up is True — check in with Bill at and beyond iteration 6
        if iteration >= MAX_ITERATIONS:
            if not _ask_bill_continue(problem, iteration, summary):
                break

        context += (
            f"\n\n=== Iteration {iteration} result ===\n"
            f"Diagnosis: {diagnosis}\n"
            f"Code output: {code_output}\n"
            f"Review: {summary}\n"
        )

    telegram_msg = (
        f"🔧 Debug complete: {problem}\n"
        f"Diagnosis: {diagnosis}\n"
        f"Result: {summary}\n"
        f"Follow-up needed: {'yes' if follow_up else 'no'}"
    )
    _send_telegram(telegram_msg)
    return telegram_msg


def run(message: str = None) -> str:
    if not message:
        return "Debug job ready. Provide a problem description."
    for prefix in ("debug:", "diagnose this:", "watson debug:", "fix this:"):
        if message.lower().startswith(prefix):
            message = message[len(prefix):].strip()
            break
    return debug(message)


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    problem = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "watson-bot.service not responding"
    print(run(problem))
