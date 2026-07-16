"""jobs/team/extractor.py — Extract structured data from meeting transcripts via Ollama."""
import json
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"


def _build_prompt(member_name: str, transcript: str, date_str: str) -> tuple[str, str]:
    first_name = member_name.split()[0]
    system = (
        f"You are Watson, an administrative assistant. Extract structured information from "
        f"this meeting transcript between Dr. Bill Yomes and {member_name} on {date_str}. "
        f"Return only valid JSON, no other text."
    )
    prompt = f"""Extract from this transcript:
1. A one-paragraph meeting summary (key topics, decisions, tone)
2. All tasks mentioned — items with clear action and owner. Include suggested due dates if mentioned or implied.
3. Any new goals that emerged (time-bound objectives)
4. A draft follow-up email from Watson to {member_name}

The email must:
- Open: "Hi {first_name} —"
- State Watson is following up on behalf of Dr. Bill
- Summarize the meeting in 2-3 sentences
- List all tasks with due dates clearly formatted
- Close warmly, invite reply if anything needs clarification
- Sign off: Watson | Administrative Assistant to Dr. Bill Yomes | Catalyst Community Church

Return JSON in this exact shape:
{{
  "summary": "string",
  "tasks": [{{"title": "string", "due_date": "YYYY-MM-DD or null"}}],
  "goals": [{{"title": "string", "target_date": "YYYY-MM-DD or null"}}],
  "email_subject": "string",
  "email_draft": "string"
}}

Transcript:
{transcript}"""
    return system, prompt


def _call_ollama(system: str, prompt: str, timeout: int = 120) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "system": system,
            "prompt": prompt,
            "stream": False,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return (resp.json().get("response") or "").strip()


def _parse_json(raw: str) -> dict | None:
    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def process_transcript(member_name: str, transcript: str, date_str: str) -> dict:
    system, prompt = _build_prompt(member_name, transcript, date_str)

    try:
        raw = _call_ollama(system, prompt)
        result = _parse_json(raw)
        if result:
            return result
    except Exception as exc:
        log.warning("First extraction attempt failed: %s", exc)

    # Retry with stricter prompt
    strict_system = system
    strict_prompt = (
        "Return ONLY a JSON object with exactly these keys: "
        "summary, tasks, goals, email_subject, email_draft. "
        "tasks is an array of {title, due_date}. "
        "goals is an array of {title, target_date}. "
        "No markdown. No explanation. Just the JSON.\n\n"
        f"Transcript:\n{transcript}"
    )
    try:
        raw = _call_ollama(strict_system, strict_prompt, timeout=90)
        result = _parse_json(raw)
        if result:
            return result
    except Exception as exc:
        log.error("Second extraction attempt failed: %s", exc)

    return {"error": "extraction failed"}
