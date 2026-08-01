"""jobs/adelphos/client.py — thin wrapper around Moodle's REST web service API
for Adelphos Academy (www.adelphosonline.com). Centralizes auth, param
encoding, error detection, and retry/backoff so every adelphos job reuses one
HTTP path instead of rolling its own.
"""
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

log = logging.getLogger(__name__)

BASE_URL = (os.getenv("ADELPHOS_BASE_URL") or "").rstrip("/")
TOKEN = os.getenv("ADELPHOS_MOODLE_TOKEN", "")
_ENDPOINT = "/webservice/rest/server.php"
_TIMEOUT = 20
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 2


class MoodleAPIError(Exception):
    """Raised when Moodle's REST server responds 200 OK with an exception payload."""


def _flatten(params: dict) -> dict:
    """Flattens nested list/dict params into Moodle's bracketed form-field format,
    e.g. users=[{"id": 5, "suspended": 1}] -> {"users[0][id]": 5, "users[0][suspended]": 1}.
    """
    flat: dict = {}

    def _walk(prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                _walk(f"{prefix}[{k}]", v)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                _walk(f"{prefix}[{i}]", v)
        else:
            flat[prefix] = value

    for key, value in params.items():
        _walk(key, value)
    return flat


def call(wsfunction: str, **params):
    """Calls a Moodle web service function and returns the parsed JSON response.

    Moodle returns HTTP 200 even for API-level errors — the body is a dict
    with an "exception" key instead. That case is raised here as
    MoodleAPIError so callers never have to check for it themselves.
    """
    if not BASE_URL or not TOKEN:
        raise RuntimeError("ADELPHOS_BASE_URL and ADELPHOS_MOODLE_TOKEN must be set in .env")

    payload = {
        "wstoken": TOKEN,
        "wsfunction": wsfunction,
        "moodlewsrestformat": "json",
        **_flatten(params),
    }

    last_exc = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(f"{BASE_URL}{_ENDPOINT}", data=payload, timeout=_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            log.warning("Moodle call %s failed (attempt %d/%d): %s", wsfunction, attempt, _MAX_ATTEMPTS, exc)
            time.sleep(_BACKOFF_BASE * attempt)
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            last_exc = RuntimeError(f"HTTP {resp.status_code}")
            log.warning("Moodle call %s got HTTP %d (attempt %d/%d)", wsfunction, resp.status_code, attempt, _MAX_ATTEMPTS)
            time.sleep(_BACKOFF_BASE * attempt)
            continue

        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "exception" in data:
            raise MoodleAPIError(f"{wsfunction}: {data.get('errorcode')} — {data.get('message')}")
        return data

    raise RuntimeError(f"Moodle call {wsfunction} failed after {_MAX_ATTEMPTS} attempts: {last_exc}")
