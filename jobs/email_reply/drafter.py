import logging

import requests

from core.claude_tier import call_claude
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"

SYSTEM_PROMPT = (
    "You are Watson, AI-powered digital assistant to Dr. Bill Yomes "
    "(pastor, author, apologist). Draft a professional, warm, concise email "
    "reply on his behalf. Do not add placeholders like [Your Name]. Sign off as: "
    "Watson / AI-powered digital assistant / Office of Dr. Bill Yomes. "
    "Keep replies under 150 words unless the email clearly requires more."
)


def draft_reply(email: dict, extra_instruction: str | None = None) -> str:
    """Call Ollama qwen2.5:7b and return a draft reply for the given email dict.

    extra_instruction: optional guidance from Bill on what the reply should
    say (e.g. "let them know I'll call this afternoon") — appended to the
    prompt so the draft follows his direction instead of guessing generically.
    Used when Bill replies with free text to a triage prompt instead of
    tapping a button; see jobs/email_intake.py's handle_instruction_reply()."""
    prompt = (
        f"From: {email['sender_name']} <{email['sender_email']}>\n"
        f"Subject: {email['subject']}\n\n"
        f"{email['body']}"
    )
    if extra_instruction:
        prompt += f"\n\n---\nDr. Bill's instructions for this reply: {extra_instruction}"
    try:
        claude_result = call_claude(
            system=SYSTEM_PROMPT, user=prompt, job_name="email_reply.drafter",
            message=f"Reply to \"{email['subject']}\" from {email['sender_name']}",
        )
        if claude_result:
            return claude_result

        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "system": SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as exc:
        log.error("Ollama draft failed for message %s: %s", email.get("message_id"), exc)
        return ""
