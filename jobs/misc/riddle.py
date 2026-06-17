"""Riddle skill — fetch a random riddle from a public API, avoid repeats, and
keep the answer back until the user asks for it.

Entry point: ``run(message)`` (registered in memory/skills.json as slug "riddle").
The bot dispatches a riddle request to ``run`` and shows the returned text. A
follow-up "what's the answer" is handled in bot.py via ``reveal_answer``.

History (already-shown riddle questions) is persisted to data/riddle_history.json
so the same riddle is not shown twice across restarts. The currently-pending
riddle (question + answer) is held in a module-level "session" variable and
mirrored to disk so the answer survives a bot restart.
"""

import json
import logging
import random
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# riddle.py lives at jobs/misc/riddle.py → repo root is two parents up.
_BASE_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = _BASE_DIR / "data"
HISTORY_FILE = _DATA_DIR / "riddle_history.json"
CURRENT_FILE = _DATA_DIR / "riddle_current.json"

# Public riddle source. The Official Joke API riddle endpoint returns no riddles
# (empty list), so we use this endpoint, which returns {"riddle", "answer"}.
_API_URL = "https://riddles-api.vercel.app/random"
_API_TIMEOUT = 12

# How many times to fetch a fresh riddle while trying to avoid a repeat.
_MAX_ATTEMPTS = 10

# Offline fallback set, used when the API is unreachable. Only the question is
# shown to the user; the answer is revealed on request.
_FALLBACK_RIDDLES = [
    {"question": "What has keys but can't open locks?", "answer": "A piano."},
    {"question": "What has hands but cannot clap?", "answer": "A clock."},
    {"question": "The more you take, the more you leave behind. What am I?",
     "answer": "Footsteps."},
    {"question": "What gets wetter the more it dries?", "answer": "A towel."},
    {"question": "I have cities, but no houses; forests, but no trees; and water, "
                 "but no fish. What am I?", "answer": "A map."},
    {"question": "What begins with T, ends with T, and has T in it?",
     "answer": "A teapot."},
    {"question": "What can travel around the world while staying in a corner?",
     "answer": "A postage stamp."},
    {"question": "What has to be broken before you can use it?", "answer": "An egg."},
]

# Module-level "session" variable holding the riddle currently awaiting an answer
# reveal. Persisted to CURRENT_FILE so it survives a bot restart.
_current_riddle = None


def get_random_riddle():
    """Fetch a random riddle from the external API.

    Returns a ``{"question": str, "answer": str}`` dict. Falls back to a random
    entry from the offline set if the API call fails or returns malformed data.
    """
    try:
        resp = requests.get(_API_URL, timeout=_API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        question = (data.get("riddle") or "").strip()
        answer = (data.get("answer") or "").strip()
        if question and answer:
            return {"question": question, "answer": answer}
        log.warning("Riddle API returned malformed data: %s", data)
    except Exception as exc:  # network error, bad JSON, etc.
        log.warning("Riddle API fetch failed (%s); using fallback set.", exc)
    return dict(random.choice(_FALLBACK_RIDDLES))


def load_shown_riddles():
    """Read the list of already-shown riddle questions from disk.

    Returns a list of question strings. Returns an empty list if the history
    file is missing or unreadable.
    """
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        shown = data.get("shown_riddles", [])
        return shown if isinstance(shown, list) else []
    except FileNotFoundError:
        return []
    except Exception as exc:
        log.warning("Could not read riddle history (%s); starting fresh.", exc)
        return []


def save_shown_riddles(shown_riddles):
    """Persist the list of shown riddle questions to disk."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
        json.dump({"shown_riddles": shown_riddles}, fh, indent=2, ensure_ascii=False)


def _set_current_riddle(riddle):
    """Store the pending riddle in the module session variable and mirror to disk."""
    global _current_riddle
    _current_riddle = riddle
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CURRENT_FILE, "w", encoding="utf-8") as fh:
            json.dump(riddle, fh, indent=2, ensure_ascii=False)
    except Exception as exc:
        log.warning("Could not persist current riddle (%s).", exc)


def _load_current_riddle():
    """Return the pending riddle from the session variable, or disk as a fallback."""
    if _current_riddle is not None:
        return _current_riddle
    try:
        with open(CURRENT_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def reveal_answer():
    """Return the answer to the currently pending riddle, or None if none is set."""
    riddle = _load_current_riddle()
    if not riddle or not riddle.get("answer"):
        return None
    return f"💡 The answer is: {riddle['answer']}"


def handle_riddle_request():
    """Orchestrate fetching a new (unseen) riddle and return it without the answer.

    Loops up to ``_MAX_ATTEMPTS`` times to find a riddle whose question is not
    already in the history. Records the chosen question, stores the full riddle
    as the pending one for an answer reveal, and returns the user-facing text.
    """
    shown = load_shown_riddles()

    riddle = None
    candidate = None
    for attempt in range(_MAX_ATTEMPTS):
        candidate = get_random_riddle()
        if candidate["question"] not in shown:
            riddle = candidate
            break
        log.info("Riddle already shown (attempt %d/%d), fetching another.",
                 attempt + 1, _MAX_ATTEMPTS)

    # If every attempt returned a previously-shown riddle (small API pool or all
    # exhausted), fall back to the last candidate rather than failing.
    if riddle is None:
        riddle = candidate

    shown.append(riddle["question"])
    save_shown_riddles(shown)
    _set_current_riddle(riddle)

    return (
        f"🧩 {riddle['question']}\n\n"
        "_(Stumped? Ask \"what's the answer\" to reveal it.)_"
    )


def run(message: str = None) -> str:
    """Router entry point — returns a fresh riddle, answer withheld."""
    return handle_riddle_request()
