"""
Watson Twin -- local Ollama-backed agent that runs on FMSPC and executes
Watson duties on the Beelink over SSH. Manual/interactive use only: this is
NOT wired into any cron job, Telegram bot, or dashboard trigger. It only acts
when you are sitting at FMSPC and talking to it directly.
"""

import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1:8b"
SSH_HOST = "watson"          # alias configured in C:\Users\billy\.ssh\config
SSH_TIMEOUT = 90             # seconds per remote command
KEEP_ALIVE = "10m"
MAX_TOOL_ROUNDS = 8          # per user turn, safety cap on tool-call chaining

SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT_LOG = SCRIPT_DIR / "watson_twin_audit.log"
OLLAMA_START_SCRIPT = r"C:\Users\billy\AppData\Local\Programs\Ollama\start-ollama-server.ps1"

SYSTEM_PROMPT = """You are Watson Twin, a local AI running on Bill's FMSPC workstation.

You are the offline fallback for Watson, Bill's personal AI assistant system that
normally runs on his Beelink server. You only run when Bill starts this script
and talks to you directly at the FMSPC console -- you are not wired into
Watson's cron jobs, Telegram bot, or dashboard, and never run unattended.

You have one tool, run_on_beelink, which executes a shell command on the
Beelink over SSH as the billyomes user and returns stdout, stderr, and exit
code. Use it to read or write files, run sqlite3 queries against Watson's
databases (e.g. watson.db, congregation.db), inspect or edit code under
~/watson, or run Watson jobs directly. When running a Watson job module, set
PYTHONPATH=/home/billyomes/watson first, e.g.:
  PYTHONPATH=/home/billyomes/watson python3 -m jobs.some_module

The Watson codebase lives at ~/watson on the Beelink, not the home directory
root -- e.g. CLAUDE.md is ~/watson/CLAUDE.md and the architecture reference is
~/watson/memory/WATSON_ARCHITECTURE.md. If a file isn't where you expect, run
ls or find to locate it before concluding it doesn't exist -- never conclude
something is missing after a single failed lookup.

You have full read/write access on the Beelink through this tool -- there is
no dry-run and no approval gate. Before running anything destructive (deleting
files, dropping/altering a table, overwriting data with no backup), say what
you're about to do in one line first, then do it. Chain as many tool calls as
you need to get a real answer -- don't guess about the state of the Beelink
when you can just check it.

Be direct. Skip preamble and disclaimers.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_on_beelink",
            "description": (
                "Execute a shell command on Bill's Beelink server (hostname "
                "'watson') over SSH as the billyomes user. Returns stdout, "
                "stderr, and exit code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run on the Beelink.",
                    }
                },
                "required": ["command"],
            },
        },
    }
]


def audit(line: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {line}\n")


def ollama_alive() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/version", timeout=2)
        return True
    except Exception:
        return False


def ensure_ollama_running() -> None:
    if ollama_alive():
        return
    print("Ollama isn't running -- starting it...")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", OLLAMA_START_SCRIPT],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
    for _ in range(30):
        if ollama_alive():
            print("Ollama is up.")
            return
        time.sleep(1)
    print("WARNING: Ollama still not responding after 30s. Continuing anyway.")


def check_ssh() -> bool:
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", SSH_HOST, "echo ok"],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0 and "ok" in result.stdout
    except Exception:
        return False


def run_on_beelink(command: str) -> dict:
    audit(f"COMMAND: {command}")
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", SSH_HOST, command],
            capture_output=True, text=True, timeout=SSH_TIMEOUT, stdin=subprocess.DEVNULL,
        )
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        stdout, stderr, code = "", f"TIMED OUT after {SSH_TIMEOUT}s", -1
    except Exception as e:
        stdout, stderr, code = "", f"local SSH invocation failed: {e}", -1

    audit(f"  exit={code} stdout={len(stdout)}b stderr={len(stderr)}b")
    if stdout:
        audit(f"  STDOUT: {stdout[:2000]}")
    if stderr:
        audit(f"  STDERR: {stderr[:2000]}")

    def clip(s: str, n: int = 6000) -> str:
        return s if len(s) <= n else s[:n] + f"\n...[truncated, {len(s)} bytes total]"

    return {"exit_code": code, "stdout": clip(stdout), "stderr": clip(stderr)}


def call_ollama(model: str, messages: list) -> dict:
    body = json.dumps({
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def handle_turn(model: str, messages: list) -> None:
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            resp = call_ollama(model, messages)
        except urllib.error.URLError as e:
            print(f"[error contacting Ollama: {e}]")
            return

        msg = resp.get("message", {})
        tool_calls = msg.get("tool_calls")
        messages.append(msg)

        if not tool_calls:
            print(f"\nwatson-twin> {msg.get('content', '').strip()}\n")
            return

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}

            if name == "run_on_beelink":
                command = args.get("command", "")
                print(f"  [running on beelink: {command}]")
                result = run_on_beelink(command)
            else:
                result = {"error": f"unknown tool {name}"}

            messages.append({
                "role": "tool",
                "content": json.dumps(result),
            })

    print("[stopped after max tool-call rounds -- ask a follow-up to continue]\n")


def main() -> None:
    model = DEFAULT_MODEL
    if len(sys.argv) > 1:
        model = sys.argv[1]

    print("=== Watson Twin ===")
    print("Manual/offline fallback -- talks to the Beelink over SSH, no automation involved.")
    print(f"Model: {model}   (switch anytime with /model <name>)")
    print("Commands: /model <name>  /reset  /exit\n")

    ensure_ollama_running()

    if not check_ssh():
        print("WARNING: could not reach the Beelink over SSH (host 'watson'). "
              "Tool calls will fail until this is fixed.\n")
    else:
        print("Beelink SSH link: OK\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break

        if not user_input:
            continue
        if user_input in ("/exit", "/quit"):
            print("bye.")
            break
        if user_input == "/reset":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("[conversation reset]\n")
            continue
        if user_input.startswith("/model "):
            model = user_input.split(" ", 1)[1].strip()
            print(f"[switched to {model}]\n")
            continue

        messages.append({"role": "user", "content": user_input})
        handle_turn(model, messages)


if __name__ == "__main__":
    main()
