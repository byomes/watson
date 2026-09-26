"""core/ollama_json.py — robust JSON-mode Ollama calls.

Background (2026-09-26): small local models (llama3.2:3b) occasionally emit a
stop token before finishing the JSON object they were asked for — e.g. this
exact response for a Subsplash "Church Picnic" registration email:

    {
      "is_event_signup": true,
      ...
      "num_tickets": 2

(no closing `}`). `done_reason` from Ollama is "stop", not a token-limit
truncation, so it's not fixable by raising num_predict -- it's sampling
variance. json.loads() on that raises "Expecting ',' delimiter", which two
independent call sites (jobs/events/signup_detect.py's own signup classifier
and jobs/email_intake.py's generic non-whitelist triage) both surfaced as a
hard failure. For an email that's actually an already-tracked event
registration, that means BOTH classifiers can fail on the same email in the
same poll cycle, and email_intake.py's generic-triage fallback then pages
Dr. Bill on Telegram asking him to manually review a routine registration —
even though jobs/events/signup_detect.py's own per-minute retry (the email
stays unread until something marks it read) usually recovers and silently
completes the intake a poll or two later, leaving a stale "please review"
prompt behind for something Watson already finished.

`generate_json()` is a drop-in replacement for the copy-pasted
`requests.post(OLLAMA_URL, ...) -> json.loads(raw)` pattern used across
Watson's ~40 Ollama call sites, but only for callers that ask the model for
a single JSON object back (`stream: False`). It repairs the truncated-object
shape above locally (no extra model call) and retries the request itself
once for anything the repair can't fix. Callers keep their own
model/timeout/prompt choices and their own except-block fallback behavior —
this only replaces the request-and-parse step.
"""
import json
import logging

import requests

log = logging.getLogger(__name__)


def repair_truncated_json(raw: str) -> str:
    """Best-effort repair of a JSON string cut off mid-object/array: closes
    any string left open, then any objects/arrays left open, in the correct
    order. A no-op (returns `raw` unchanged) if nothing looks open — callers
    should still wrap json.loads in their own try/except, since this cannot
    fix every malformed shape (e.g. a dangling trailing comma)."""
    stack = []
    in_string = False
    escape = False
    for ch in raw:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    if not in_string and not stack:
        return raw

    repaired = raw
    if in_string:
        repaired += '"'
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired


def parse_json_response(raw: str) -> dict:
    """Strip markdown fences and parse `raw`, repairing truncation once
    before giving up. Raises json.JSONDecodeError if still unparseable."""
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return json.loads(repair_truncated_json(cleaned))


def generate_json(
    url: str,
    *,
    model: str,
    prompt: str,
    timeout: int = 60,
    retries: int = 1,
) -> dict:
    """POST a non-streaming generate request and parse its `response` field
    as JSON, repairing common truncation. On failure (network error, HTTP
    error, or unparseable JSON even after repair), retries the whole request
    up to `retries` more times -- a fresh sample from the model often just
    doesn't reproduce the same truncation. Raises the last exception if
    every attempt fails; callers keep their own except-block fallback."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                url,
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=timeout,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            return parse_json_response(raw)
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                log.warning(
                    "generate_json attempt %d/%d failed, retrying: %s",
                    attempt + 1, retries + 1, exc,
                )
    raise last_exc
