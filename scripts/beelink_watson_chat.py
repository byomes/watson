"""scripts/beelink_watson_chat.py -- local, tool-calling chat agent for the
Beelink itself, launched from the ShellDrop shortcut on Bill's phone.

Per Bill's explicit scope (2026-10-02): this agent gets exactly TWO tools,
nothing else. No shell access, no SSH, no arbitrary report execution --
unlike scripts/fmspc_watson_twin.py (which deliberately gets full Beelink
shell access because it only runs from the FMSPC console). Bill's own words
building this: "only give it access to run reports and send me telegram
messages. i dont want this causing problems for anyone else."

  1. ask_data(question) -- delegates to jobs.analytics.data_chat's existing,
     already-hardened Q&A layer (the same one Catalyst leaders use in
     Telegram team-chat). Read-only: SQL is generated then validated against
     a table/column whitelist and run on a `mode=ro` sqlite connection, so
     it cannot write, and it cannot reach sms_messages/notes/pastoral_notes
     (see data_chat.py's own guardrail comments). Covers attendance, people,
     birthdays, classes (classroom_attendance/kids_checkin), events.
     asker_name is hardcoded to "Dr. Bill" with allow_contact_info=True --
     this agent only ever runs for Bill himself from his own phone.

  2. send_telegram(message) -- sends a Telegram message to Bill's OWN chat
     ID only (WATSON_CHAT_ID from .env). There is no recipient parameter --
     the model cannot direct a message anywhere else, mirroring the existing
     send-to-self exception already granted to Watson's SMS path.

No other tool is defined. If the model asks for anything else, it gets told
no such tool exists -- there is nothing to execute.

Every tool call is appended to beelink_watson_chat_audit.log next to this
script (local only, not synced to the repo), same pattern as the FMSPC
twin's audit log.

Usage (manual only -- never wired to cron/bot.py):
  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    scripts/beelink_watson_chat.py
"""

import json
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")  # hide the urllib3/chardet version-mismatch noise

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID  # noqa: E402
import jobs.analytics.data_chat as _data_chat  # noqa: E402

# The whole point of this script is a chat agent that works entirely on
# Watson's own local Ollama -- no cloud, no cost, usable when Claude usage
# is exhausted (same reasoning as scripts/fmspc_watson_twin.py). But
# data_chat.py's _generate() tries a real, budget-tracked Claude API call
# FIRST (core/claude_tier.py, ~$0.011/call) and only falls back to local
# Ollama if that fails -- correct for its real caller (Telegram team-chat,
# where the $10/mo Claude-tier budget is an accepted, deliberate quality
# tradeoff), wrong for this one. Discovered 2026-10-02 when a real API call
# fired during testing. Forcing call_claude to always report "unavailable"
# here makes _generate() fall through to local Ollama every time, with zero
# changes to the shared data_chat.py file -- Telegram team-chat still gets
# its normal Claude-tier behavior untouched.
_data_chat.call_claude = lambda *a, **kw: None
answer_data_question = _data_chat.answer_data_question

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = os.getenv("BEELINK_CHAT_MODEL", "watson")
AUDIT_LOG = Path(__file__).resolve().parent / "beelink_watson_chat_audit.log"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ask_data",
            "description": (
                "Answer a question about church attendance, people/members, "
                "birthdays, kids' classes/classroom headcounts, or event "
                "signups, by querying the real Catalyst database read-only. "
                "Use this for ANY question needing real data -- never guess "
                "or make up numbers or names yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The natural-language question to answer, verbatim.",
                    }
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_telegram",
            "description": (
                "Send a Telegram message to Dr. Bill (yourself/the user you're "
                "talking to right now). There is no other recipient -- this "
                "can never message anyone else."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The message text to send."}
                },
                "required": ["message"],
            },
        },
    },
]


def _audit(tool: str, args: dict, result: str) -> None:
    with open(AUDIT_LOG, "a") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "tool": tool,
                    "args": args,
                    "result_preview": result[:300],
                }
            )
            + "\n"
        )


def ask_data(question: str) -> str:
    on_topic, reply = answer_data_question(question, asker_name="Dr. Bill", allow_contact_info=True)
    if not on_topic:
        result = "That's not an attendance/people/birthday/classes/events question I can query -- try rephrasing."
    else:
        result = reply or "No answer came back for that."
    _audit("ask_data", {"question": question}, result)
    return result


def send_telegram(message: str) -> str:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
            json={"chat_id": WATSON_CHAT_ID, "text": f"{message}\n\n- Watson"},
            timeout=10,
        )
        ok = resp.ok
    except Exception as exc:
        ok = False
        message = f"{message} (send failed: {exc})"
    result = "sent" if ok else "failed to send"
    _audit("send_telegram", {"message": message}, result)
    return result


DISPATCH = {"ask_data": ask_data, "send_telegram": send_telegram}


def run_tool(name: str, args: dict) -> str:
    fn = DISPATCH.get(name)
    if fn is None:
        return f"No such tool: {name}"
    return fn(**args)


def read_line(prompt: str) -> str:
    # Plain stdin read instead of the builtin input() -- input() hooks GNU
    # readline for line-editing, and readline's cursor-redraw escape codes
    # are what caused the repeating-character garbling in ShellDrop's narrow
    # terminal (a long typed line wraps, readline redraws, the mobile
    # terminal mis-renders the redraw). Reading straight from stdin leaves
    # all echo/backspace handling to the terminal itself, nothing fancier.
    print(prompt, end="", flush=True)
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.strip()


def chat():
    # Plain print, not Rich's Panel/Markdown/console.status -- those write
    # frequent cursor-movement ANSI codes (console.status's spinner alone
    # redraws ~12x/second while a request is in flight). Same root cause as
    # the readline fix above: over ShellDrop's laggy mobile SSH link, those
    # escape codes queue up and show as literal garbage if you scroll mid-
    # redraw, and the constant redraw traffic is also what made the whole
    # session feel laggy. A single flat write per turn has nothing to
    # misrender.
    print(
        "Watson -- scoped to: attendance / people / birthdays / classes "
        "lookups, and sending you a Telegram message. Nothing else.\n"
        "Type /bye to exit.\n"
    )
    messages = []
    while True:
        try:
            user_input = read_line("you> ")
        except EOFError:
            break
        if not user_input:
            continue
        if user_input in ("/bye", "exit", "quit"):
            break
        messages.append({"role": "user", "content": user_input})

        print("thinking...", flush=True)
        while True:
            resp = requests.post(
                OLLAMA_URL,
                json={"model": MODEL, "messages": messages, "tools": TOOLS, "stream": False},
                timeout=300,
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["message"]
            messages.append(msg)

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                break

            for call in tool_calls:
                fn_name = call["function"]["name"]
                fn_args = call["function"].get("arguments") or {}
                result = run_tool(fn_name, fn_args)
                messages.append(
                    {"role": "tool", "content": result, "name": fn_name}
                )

        print(f"\nwatson> {msg.get('content', '')}\n", flush=True)


if __name__ == "__main__":
    chat()
