import json
import logging
import re

import requests

log = logging.getLogger(__name__)

_OLLAMA_URL = "http://localhost:11434/api/generate"
_MODEL = "llama3.2:3b"

_SYSTEM_PROMPT = """
You are a strict intent classifier. Your only job is to read a command and return a JSON object.
Do not respond conversationally. Do not explain. Return ONLY valid JSON, nothing else.

Focus on the COMMAND STRUCTURE, not the content words.

Rules:
- "book X hours/minutes for [anything]" or "block time for [anything]" = block_time
- "what's my day" or "what's my schedule" or "what do I have" = calendar_query
- "mark rest of day busy" or "I'm headed to [anywhere]" or "going to [anywhere]" = calendar_busy
- "what's available" or "when am I free" or "availability" = calendar_availability
- "remind me" or "add task" or "don't forget" = task_create
- "what are my tasks" or "what's due" = task_list
- "book an appointment for [person]" or "schedule [person]" = book_appointment
- everything else = general

Intents and their params:
- block_time: {duration_minutes: int, day: str, title: str}
- calendar_query: {day: str or null}
- calendar_busy: {}
- calendar_availability: {}
- book_appointment: {name: str or null, email: str or null, duration_minutes: int, day: str or null, type: "virtual|inperson"}
- task_create: {title: str, due_datetime: str or null}
- task_list: {}
- general: {}

Examples:
"book 2 hours next wednesday for Bible Study Planning" → {"intent": "block_time", "params": {"duration_minutes": 120, "day": "next wednesday", "title": "Bible Study Planning"}}
"what's my day on monday" → {"intent": "calendar_query", "params": {"day": "monday"}}
"I'm headed to the hospital" → {"intent": "calendar_busy", "params": {}}
"remind me at 3pm to call John" → {"intent": "task_create", "params": {"title": "call John", "due_datetime": "3pm"}}

Return ONLY the JSON object. No markdown. No explanation. No other text.
"""


def classify(message_text: str) -> dict:
    prompt = f"{_SYSTEM_PROMPT}\n\nMessage: {message_text}"
    try:
        resp = requests.post(
            _OLLAMA_URL,
            json={"model": _MODEL, "prompt": prompt, "stream": False},
            timeout=45,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        raw = re.sub(r"```json|```", "", raw).strip()
        raw = raw.strip().lstrip('`').rstrip('`')
        if raw.startswith('json'):
            raw = raw[4:].strip()
        return json.loads(raw)
    except requests.Timeout:
        log.warning("Ollama classify timed out")
    except Exception as e:
        log.warning("Ollama classify failed: %s", e)
    return {"intent": "general", "params": {}}
