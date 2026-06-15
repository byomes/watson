#!/usr/bin/env python3
"""jobs/dev/command_executor.py — terminal agent using Claude Haiku with tiered safety."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import anthropic
except ImportError:
    anthropic = None
from dotenv import load_dotenv

# ─── Bootstrap ────────────────────────────────────────────────────────────────

load_dotenv(Path.home() / "watson" / ".env")

REPO = Path(__file__).resolve().parents[2]
LOG_PATH = REPO / "logs" / "terminal_agent.log"
MODEL = "claude-haiku-4-5-20251001"
MAX_COMMANDS = 5
OUTPUT_TRUNCATE = 4000
TIMEOUT = 30

_session_history: list[str] = []  # last 3 executed commands for Haiku context

# ─── Haiku system prompt ──────────────────────────────────────────────────────

_SYSTEM = (
    "You are Watson, Dr. Bill Yomes's AI assistant. You generate safe shell commands "
    "for a Linux Mint server. You never generate commands that could damage the system "
    "or expose credentials.\n\n"
    "When given a natural language instruction, respond ONLY with valid JSON "
    "(no markdown fences, no preamble):\n"
    '{"commands": ["cmd1", "cmd2"], "tier": "1", "reasoning": "...", "expected_output": "..."}\n\n'
    "Tier rules:\n"
    "  '1' = read-only: cat/tail/head/grep/ls/find, systemctl status, "
    "git status/log/diff, crontab -l, df/free/ps, top -bn1, python3 check_*/status_*\n"
    "  '2' = state-changing: systemctl restart, git pull/push, file writes/edits, "
    "pip/apt install, other python3 scripts, any rm/mv/chmod/chown\n\n"
    f"Max {MAX_COMMANDS} commands. No interactive commands (vim, nano, ssh, less, more)."
)

# ─── Safety classification ────────────────────────────────────────────────────

_PROTECTED_FILES = frozenset({".env", "credentials.json", "token.json"})
_TIER1_BASE = frozenset({"cat", "tail", "head", "grep", "ls", "find", "df", "free", "ps"})
_TIER2_WORDS = frozenset({"rm", "mv", "chmod", "chown"})

_T1_RE = [
    re.compile(r"^systemctl\s+status\b"),
    re.compile(r"^git\s+(status|log|diff)\b"),
    re.compile(r"^crontab\s+-l\b"),
    re.compile(r"^top\s+.*-b"),
    re.compile(r"^python3?\s+\S*(check_|status_)\S*"),
]
_T2_RE = [
    re.compile(r"^systemctl\s+restart\b"),
    re.compile(r"^git\s+(pull|push)\b"),
    re.compile(r"\bpip3?\s+(install|uninstall)\b"),
    re.compile(r"\bapt(-get)?\s+(install|remove|purge|upgrade)\b"),
    re.compile(r"^python3?\b"),
]

_ALLOWED_SUDO = re.compile(r"^sudo\s+systemctl\s+restart\s+watson-\S+")
_PIPE_SHELL = re.compile(r"\|\s*(bash|sh)\b")
_REDIR_SHELL = re.compile(r">\s*(bash|sh)\b")


def _classify(cmd: str) -> tuple[str, str | None]:
    """Return ('1'|'2'|'blocked', reason_or_None)."""
    c = cmd.strip()

    # rm -rf: blocked unless the target path is under /tmp
    if re.search(r"\brm\b", c) and re.search(
        r"-[a-zA-Z]*r[a-zA-Z]*f\b|-[a-zA-Z]*f[a-zA-Z]*r\b", c
    ):
        if "/tmp" not in c:
            return "blocked", "rm -rf is blocked outside /tmp"

    # Protected file references
    if any(f in c for f in _PROTECTED_FILES):
        return "blocked", "command references a protected file (.env, credentials.json, token.json)"

    # git push --force (allow --force-with-lease)
    if "git push" in c and "--force" in c and "--force-with-lease" not in c:
        return "blocked", "git push --force is blocked"

    # sudo: only systemctl restart watson-* services
    if "sudo" in c and not _ALLOWED_SUDO.match(c):
        return "blocked", "sudo is only allowed for: sudo systemctl restart watson-* services"

    # Pipe or redirect to bash/sh
    if _PIPE_SHELL.search(c) or _REDIR_SHELL.search(c):
        return "blocked", "piping or redirecting to bash or sh is blocked"

    # Tier 1
    first = c.split()[0] if c.split() else ""
    if first in _TIER1_BASE:
        return "1", None
    for pat in _T1_RE:
        if pat.match(c):
            return "1", None

    # Tier 2 — keyword check first, then patterns
    words = set(re.findall(r"\b\w+\b", c))
    if words & _TIER2_WORDS:
        return "2", None
    for pat in _T2_RE:
        if pat.search(c):
            return "2", None

    # Unknown command: conservative default
    return "2", None


# ─── Audit log ────────────────────────────────────────────────────────────────


def _log(tier: str, cmd: str, outcome: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(LOG_PATH, "a") as f:
        f.write(f"{ts} | {tier.upper()} | {cmd[:200]} | {outcome}\n")


# ─── Haiku interaction ────────────────────────────────────────────────────────

_client = None


def _api():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _plan(instruction: str, error_ctx: str = "") -> dict:
    """Ask Haiku for commands. Raises ValueError on bad response."""
    parts = [f"Instruction: {instruction}"]
    if _session_history:
        parts.append("Recent commands:\n" + "\n".join(f"  {c}" for c in _session_history[-3:]))
    if error_ctx:
        parts.append(
            f"Previous command failed with:\n{error_ctx[:500]}\n"
            "Please revise to fix the issue."
        )

    resp = _api().messages.create(
        model=MODEL,
        max_tokens=512,
        system=_SYSTEM,
        messages=[{"role": "user", "content": "\n\n".join(parts)}],
    )

    raw = resp.content[0].text.strip()
    # Strip markdown fences if Haiku wraps the JSON
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Haiku returned invalid JSON: {exc}\nRaw: {raw[:300]}") from exc

    if not isinstance(data.get("commands"), list):
        raise ValueError(f"Haiku response missing 'commands' list: {raw[:300]}")

    return data


def _summarize(instruction: str, combined_output: str) -> str:
    """Return a plain-English summary of command results from Haiku."""
    resp = _api().messages.create(
        model=MODEL,
        max_tokens=256,
        system=_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Task: {instruction}\n\n"
                f"Output:\n{combined_output[:OUTPUT_TRUNCATE]}\n\n"
                "Summarize what was found or done in 1-3 concise sentences."
            ),
        }],
    )
    return resp.content[0].text.strip()


# ─── Execution helpers ────────────────────────────────────────────────────────


def _run(cmd: str) -> tuple[bool, str]:
    """Run a shell command. Returns (success, combined_stdout_stderr)."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=str(REPO),
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {TIMEOUT}s"
    except Exception as exc:
        return False, str(exc)


def _prompt_confirm(cmd: str, reasoning: str) -> bool:
    """Print tier-2 confirmation prompt. Returns True if user confirms."""
    print(f"\n[tier 2 — confirmation required]")
    print(f"  Command   : {cmd}")
    if reasoning:
        print(f"  Reasoning : {reasoning}")
    try:
        answer = input("  Type 'confirm' to run (anything else cancels): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer == "confirm"


def _execute_one(cmd: str, tier: str, reasoning: str, instruction: str) -> tuple[bool, str]:
    """
    Execute a single classified command with retry-on-failure.
    Returns (success, output).  Output is '' on cancel.
    """
    if tier == "2" and not _prompt_confirm(cmd, reasoning):
        _log("2", cmd, "CANCELLED")
        return False, ""

    label = "auto" if tier == "1" else "executing"
    print(f"[{label}] {cmd}")
    success, output = _run(cmd)

    _session_history.append(cmd)
    if len(_session_history) > 3:
        _session_history.pop(0)

    if success:
        _log(tier, cmd, "SUCCESS")
        return True, output

    # First failure: ask Haiku for a revised command
    _log(tier, cmd, f"FAILED: {output[:100]}")
    print(f"[failed] {output[:300]}")
    print("[watson] Retrying with error context …")

    try:
        retry_data = _plan(instruction, error_ctx=output)
    except ValueError as exc:
        print(f"[error] Haiku retry error: {exc}")
        return False, output

    retry_cmds = retry_data.get("commands", [])[:MAX_COMMANDS]
    if not retry_cmds:
        print("[error] Haiku produced no retry commands.")
        return False, output

    r_cmd = retry_cmds[0]
    r_tier, r_block = _classify(r_cmd)

    if r_block:
        print(f"[blocked on retry] {r_cmd}\n  Reason: {r_block}")
        _log("blocked", r_cmd, f"BLOCKED-RETRY: {r_block}")
        return False, output

    if r_tier == "2" and not _prompt_confirm(r_cmd, retry_data.get("reasoning", "")):
        _log("2", r_cmd, "CANCELLED-RETRY")
        return False, ""

    print(f"[retry] {r_cmd}")
    ok2, out2 = _run(r_cmd)

    _session_history.append(r_cmd)
    if len(_session_history) > 3:
        _session_history.pop(0)

    if ok2:
        _log(r_tier, r_cmd, "SUCCESS-RETRY")
        return True, out2

    _log(r_tier, r_cmd, f"FAILED-RETRY: {out2[:100]}")
    print("[error] Retry also failed. Full output:")
    print(out2[:2000])
    return False, out2


# ─── Main flow ────────────────────────────────────────────────────────────────


def execute(instruction: str) -> None:
    """Process a natural language instruction end-to-end."""
    print(f"\n[watson] {instruction}")

    try:
        plan = _plan(instruction)
    except ValueError as exc:
        print(f"[error] {exc}")
        return

    commands: list[str] = plan.get("commands", [])[:MAX_COMMANDS]
    haiku_tier: str = str(plan.get("tier", "2"))
    reasoning: str = plan.get("reasoning", "")

    if not commands:
        print("[watson] No commands generated.")
        return

    all_outputs: list[str] = []

    for cmd in commands:
        our_tier, block_reason = _classify(cmd)

        if our_tier == "blocked":
            print(f"[blocked] {cmd}")
            print(f"  Reason: {block_reason}")
            _log("blocked", cmd, f"BLOCKED: {block_reason}")
            return

        # Use the more restrictive of Haiku's tier and our classification
        eff_tier = "2" if haiku_tier == "2" or our_tier == "2" else "1"

        success, output = _execute_one(cmd, eff_tier, reasoning, instruction)

        if output:
            all_outputs.append(f"$ {cmd}\n{output}")

        if not success:
            return

    # Natural language summary from Haiku
    if all_outputs:
        combined = "\n\n".join(all_outputs)
        summary = _summarize(instruction, combined)
        print(f"\n[watson] {summary}")


# ─── Skill entry point ───────────────────────────────────────────────────────


def run(message: str = None) -> str:
    if anthropic is None:
        return "Run: pip install anthropic --break-system-packages"
    if not message:
        return "Please provide an instruction for the command executor."
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        execute(message)
    return buf.getvalue().strip() or "Done."


# ─── Entry point ─────────────────────────────────────────────────────────────


def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("[error] ANTHROPIC_API_KEY not found — check ~/watson/.env")
        sys.exit(1)

    if len(sys.argv) > 1:
        instruction = " ".join(sys.argv[1:])
    else:
        try:
            instruction = input("instruction> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)

    if not instruction:
        print("No instruction provided.")
        sys.exit(1)

    execute(instruction)


if __name__ == "__main__":
    main()
