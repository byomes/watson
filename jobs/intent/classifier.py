import json
import logging
import re

import requests

from core.ollama_lock import BUSY_MESSAGE, ollama_busy

log = logging.getLogger(__name__)

_OLLAMA_URL = "http://localhost:11434/api/generate"
_MODEL = "gemma3:4b"
_KEEP_ALIVE = "30m"

_SYSTEM_PROMPT = """
You are a strict intent classifier. Your only job is to read a command and return a JSON object.
Do not respond conversationally. Do not explain. Return ONLY valid JSON, nothing else.

Focus on the COMMAND STRUCTURE, not the content words.

Rules:
- "book X hours/minutes for [anything]" or "block time for [anything]" = block_time
- "what's my day" or "what's my schedule" or "what do I have" = calendar_query
- "mark rest of day busy" or "I'm headed to [anywhere]" or "going to [anywhere]" = calendar_busy
- "what's available" or "when am I free" or "availability" = calendar_availability
- reflective, opinion-seeking, or advice questions ("what do you think about...", "any thoughts on...",
  "how should I handle...", "what's your take on...") are ALWAYS general, even when they mention a
  meeting, a day, a schedule, or something happening tomorrow/yesterday — mentioning a day or a meeting
  is not the same as asking to check, book, or block the calendar. Only classify as a calendar_* intent
  when the message is actually asking to check/change the calendar, not asking for an opinion about
  something that happens to have a day attached to it.
- "remind me" or "remind me later" or "set a reminder" = reminder_create
- "add task" or "don't forget" = task_create
- "what are my tasks" or "what's due" = task_list
- "book an appointment for [person]" or "schedule [person]" = book_appointment
- "lookup [name]" or "who is [name]" or "phone/email/contact info for [name]" or "how do I reach [name]" or "find [name]'s number/email/contact" or "get me [name]'s email/phone/number" or "what is [name]'s email/phone/number" = contact_lookup
- requests to find, search for, or show an image, photo, or picture = image_search
- a bare greeting or social opener with no specific request (e.g. "hi", "hey", "good morning", "hi there") = general — do NOT infer calendar_query just because a greeting is otherwise content-free
- everything else = general

Intents and their params:
- block_time: {duration_minutes: int, day: str, title: str}
- calendar_query: {day: str or null}
- calendar_busy: {}
- calendar_availability: {}
- book_appointment: {name: str or null, email: str or null, duration_minutes: int, day: str or null, type: "virtual|inperson"}
- reminder_create: {title: str, due_datetime: str or null}
- task_create: {title: str}
- task_list: {}
- contact_lookup: {name: str}
- image_search: {query: str}
- general: {}

Also include a confidence field based on how unambiguous the intent is:
- "HIGH" — intent is clear and unambiguous
- "MEDIUM" — intent is likely but the phrasing is indirect or the extracted value is uncertain
- "LOW" — intent is genuinely uncertain or the message could mean multiple things

Examples:
"book 2 hours next wednesday for Bible Study Planning" → {"intent": "block_time", "params": {"duration_minutes": 120, "day": "next wednesday", "title": "Bible Study Planning"}, "confidence": "HIGH"}
"what's my day on monday" → {"intent": "calendar_query", "params": {"day": "monday"}, "confidence": "HIGH"}
"I'm headed to the hospital" → {"intent": "calendar_busy", "params": {}, "confidence": "HIGH"}
"remind me at 3pm to call John" → {"intent": "reminder_create", "params": {"title": "call John", "due_datetime": "3pm"}, "confidence": "HIGH"}
"remind me later to call John" → {"intent": "reminder_create", "params": {"title": "call John", "due_datetime": null}, "confidence": "HIGH"}
"add task buy groceries" → {"intent": "task_create", "params": {"title": "buy groceries"}, "confidence": "HIGH"}
"lookup Sarah Mitchell" → {"intent": "contact_lookup", "params": {"name": "Sarah Mitchell"}, "confidence": "HIGH"}
"what's Sarah's phone number" → {"intent": "contact_lookup", "params": {"name": "Sarah"}, "confidence": "MEDIUM"}
"that person from last Sunday" → {"intent": "contact_lookup", "params": {"name": ""}, "confidence": "LOW"}
"send an email to John" → {"intent": "general", "params": {}, "confidence": "HIGH"}
"good morning Watson" → {"intent": "general", "params": {}, "confidence": "HIGH"}
"hey, what's up" → {"intent": "general", "params": {}, "confidence": "HIGH"}
"any thoughts on how to handle the deacon meeting tomorrow" → {"intent": "general", "params": {}, "confidence": "HIGH"}
"what do you think about the sermon outline I sent yesterday" → {"intent": "general", "params": {}, "confidence": "HIGH"}
"can you summarize what a good small group leader does" → {"intent": "general", "params": {}, "confidence": "HIGH"}

Return ONLY the JSON object. No markdown. No explanation. No other text.
"""


def classify(message_text: str, system_prompt: str = "") -> dict:
    # bug_tracker #118/#121: don't race a contended, single-request-at-a-time
    # Ollama queue and silently guess "general" on timeout — that's a
    # hallucination wearing a latency costume. If a long job (audit.py,
    # build.py, etc.) is holding the busy lock, say so honestly instead.
    busy = ollama_busy()
    if busy:
        log.info("classify() skipped — Ollama busy with %s", busy.get("job"))
        return {"intent": "busy", "params": {}, "confidence": "HIGH", "message": BUSY_MESSAGE}

    prompt = f"{_SYSTEM_PROMPT}\n\nMessage: {message_text}"
    payload: dict = {
        "model": _MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": _KEEP_ALIVE,
        # Bounds worst-case generation time — classify only ever needs a small
        # JSON object back, but with no cap a stuck/looping generation can run
        # for minutes under CPU contention instead of failing fast (bug #28).
        "options": {"num_predict": 200},
    }
    if system_prompt:
        payload["system"] = system_prompt
    try:
        resp = requests.post(
            _OLLAMA_URL,
            json=payload,
            # This CPU-only host's prompt-prefix cache for the ~1150-1280
            # token classifier prompt is inconsistent — measured wall times
            # of 2.5s-38.4s across repeated real calls (bug #29). 55s gives
            # real margin over the worst observed case while still leaving
            # room, under bot.py's 80s outer handle_text timeout, for the
            # general-chat fallback to run if this does time out.
            timeout=55,
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
