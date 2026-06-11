import logging

import requests

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


def draft_reply(email: dict) -> str:
    """Call Ollama qwen2.5:7b and return a draft reply for the given email dict."""
    prompt = (
        f"From: {email['sender_name']} <{email['sender_email']}>\n"
        f"Subject: {email['subject']}\n\n"
        f"{email['body']}"
    )
    try:
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
