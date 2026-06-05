import json
import logging
import re

import requests

log = logging.getLogger(__name__)

_OLLAMA_URL = "http://localhost:11434/api/generate"
_MODEL = "llama3.2:3b"

_SYSTEM_PROMPT = """\
You are Watson's intent classifier. Read the user message and return ONLY a JSON object with no other text.

Intents:
- calendar_query: asking about schedule/events. Params: {day: "today|monday|tuesday|..."|null}
- calendar_busy: telling Watson to mark time as busy (hospital, emergency, going out). Params: {}
- calendar_availability: asking what time slots are open. Params: {}
- block_time: asking Watson to block time for a specific purpose. Params: {duration_minutes: int, day: "next wednesday|this thursday|...", title: str}
- book_appointment: booking someone else into calendar. Params: {name: str|null, email: str|null, duration_minutes: int, day: str|null, type: "virtual|inperson"}
- task_create: creating a task or reminder. Params: {title: str, due_datetime: str|null}
- task_list: asking to see tasks or reminders. Params: {}
- task_done: marking a task complete. Params: {title: str|null}
- general: everything else. Params: {}

Return ONLY valid JSON. Example:
{"intent": "block_time", "params": {"duration_minutes": 120, "day": "next wednesday", "title": "Bible Study Planning"}}"""


def classify(message_text: str) -> dict:
    prompt = f"{_SYSTEM_PROMPT}\n\nMessage: {message_text}"
    try:
        resp = requests.post(
            _OLLAMA_URL,
            json={"model": _MODEL, "prompt": prompt, "stream": False},
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        raw = re.sub(r"```json|```", "", raw).strip()
        return json.loads(raw)
    except requests.Timeout:
        log.warning("Ollama classify timed out")
    except Exception as e:
        log.warning("Ollama classify failed: %s", e)
    return {"intent": "general", "params": {}}
