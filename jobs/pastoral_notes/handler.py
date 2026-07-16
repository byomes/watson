"""
handle_notes_reply(reply_text) — called by the Telegram bot when the user
replies to a pastoral notes prompt.

Resolves which notes_pending row is active, fuzzy-matches the appointment
title against the people table, stores the note, and sends a Telegram
confirmation.

Supports both single-item replies ("skip" / free text) and consolidated
numbered replies ("1: skip\n2: Met with Dave, notes here").
"""

import asyncio
import difflib
import json
import logging
import re
from pathlib import Path

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate
from jobs.pastoral_notes.db import get_db

log = logging.getLogger(__name__)

_FUZZY_THRESHOLD = 0.6
_OLLAMA_URL = "http://localhost:11434/api/generate"
_OLLAMA_MODEL = "qwen2.5:7b"
_TASK_PROMPT = (
    "Extract any action items or tasks from the following meeting notes. "
    "Return only a JSON array of strings, one per task. "
    "If no tasks found, return an empty array."
)
_NUMBERED_LINE_RE = re.compile(r'^(\d+):\s*(.+)$')

# Tracks ambiguous matches waiting for yes/no confirmation.
# Keyed by event_id → {"candidates": [...], "note_text": str}
_pending_confirmations: dict[str, dict] = {}


def _append_skip_keyword(title: str) -> None:
    path = Path(__file__).parent.parent.parent / "memory" / "skip_keywords.txt"
    keyword = title.strip().lower()
    with open(path, "a") as f:
        f.write(f"\n{keyword}")


async def _send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.pastoral_notes.handler", text):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    await asyncio.to_thread(
        requests.post, url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10
    )


def _get_active_pending() -> dict | None:
    with get_db() as conn:
        return conn.execute(
            """SELECT id, event_id, appointment_title, appointment_time
               FROM notes_pending
               WHERE status = 'pending'
               ORDER BY prompted_at DESC
               LIMIT 1"""
        ).fetchone()


def _get_all_pending() -> list[dict]:
    """Returns all pending rows in the same order used by the consolidated reminder."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, event_id, appointment_title, appointment_time
               FROM notes_pending
               WHERE status = 'pending'
               ORDER BY prompted_at ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def _mark_dismissed(pending_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE notes_pending SET status = 'dismissed' WHERE id = ?",
            (pending_id,),
        )


def _mark_complete(pending_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE notes_pending SET status = 'complete' WHERE id = ?",
            (pending_id,),
        )


def _store_note(event_id: str, appointment_title: str, appointment_time: str,
                note_text: str, person_id: int | None) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO pastoral_notes (person_name, note, status)
               VALUES (?, ?, 'active')""",
            (appointment_title, note_text),
        )


def _get_all_people() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT id, name FROM people").fetchall()
    return [dict(r) for r in rows]


def _fuzzy_match(title: str, people: list[dict]) -> list[dict]:
    names = [p["name"] for p in people]
    matches = difflib.get_close_matches(title, names, n=3, cutoff=_FUZZY_THRESHOLD)
    return [p for p in people if p["name"] in matches]


def _parse_numbered_reply(reply_text: str) -> list[tuple[int, str]] | None:
    """Returns list of (1-based index, text) if any lines match 'N: text', else None."""
    lines = [l.strip() for l in reply_text.strip().splitlines() if l.strip()]
    parsed = []
    for line in lines:
        m = _NUMBERED_LINE_RE.match(line)
        if m:
            parsed.append((int(m.group(1)), m.group(2).strip()))
    return parsed if parsed else None


def _ollama_generate(note_text: str) -> str:
    prompt = f"{_TASK_PROMPT}\n\nNotes: {note_text}"
    resp = requests.post(
        _OLLAMA_URL,
        json={"model": _OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _parse_tasks(raw: str) -> list[str]:
    try:
        tasks = json.loads(raw)
        if isinstance(tasks, list):
            return [str(t).strip() for t in tasks if str(t).strip()]
    except (json.JSONDecodeError, ValueError):
        pass
    return []


def _save_tasks(tasks: list[str], appointment_title: str) -> None:
    with get_db() as conn:
        conn.executemany(
            """INSERT INTO tasks (title, priority, status, source, person)
               VALUES (?, 'medium', 'pending', 'pastoral_notes', ?)""",
            [(t, appointment_title) for t in tasks],
        )


async def _maybe_extract_tasks(note_text: str, appointment_title: str) -> None:
    try:
        raw = await asyncio.to_thread(_ollama_generate, note_text)
    except Exception as exc:
        log.warning("Ollama task extraction failed: %s", exc)
        return

    tasks = _parse_tasks(raw)
    if not tasks:
        return

    try:
        _save_tasks(tasks, appointment_title)
    except Exception as exc:
        log.warning("Failed to save extracted tasks: %s", exc)
        return

    task_list = "\n".join(f"• {t}" for t in tasks)
    await _send_telegram(f"📋 Tasks saved:\n{task_list}")
    log.info("Saved %d task(s) from notes for %s.", len(tasks), appointment_title)


async def _process_note_text(row: dict, note_text: str) -> None:
    """Fuzzy-match, store, and extract tasks for a single note."""
    pending_id = row["id"]
    event_id = row["event_id"]
    appointment_title = row["appointment_title"]
    appointment_time = row["appointment_time"]

    people = _get_all_people()
    matches = _fuzzy_match(appointment_title, people)

    if len(matches) == 1:
        person = matches[0]
        _store_note(event_id, appointment_title, appointment_time, note_text, person["id"])
        _mark_complete(pending_id)
        await _send_telegram(f"Note stored and linked to {person['name']}.")

    elif len(matches) > 1:
        top = matches[0]
        _pending_confirmations[event_id] = {
            "candidates": matches,
            "note_text": note_text,
            "pending_id": pending_id,
            "appointment_title": appointment_title,
            "appointment_time": appointment_time,
        }
        await _send_telegram(f"Is this about {top['name']}? Reply yes or no.")
        return  # Don't extract tasks yet — wait for confirmation

    else:
        _store_note(event_id, appointment_title, appointment_time, note_text, None)
        _mark_complete(pending_id)
        await _send_telegram("Note stored.")

    await _maybe_extract_tasks(note_text, appointment_title)


async def _handle_numbered_reply(parsed: list[tuple[int, str]]) -> None:
    """Route each numbered line to the corresponding pending row by position."""
    pending_rows = _get_all_pending()
    if not pending_rows:
        return

    for num, text in parsed:
        idx = num - 1
        if idx < 0 or idx >= len(pending_rows):
            log.warning("Numbered reply %d out of range (have %d pending)", num, len(pending_rows))
            continue

        row = pending_rows[idx]
        if text.lower() == "skip":
            _mark_dismissed(row["id"])
        else:
            await _process_note_text(row, text)


async def handle_confirmation_reply(reply_text: str, event_id: str) -> bool:
    """
    Handle a yes/no reply for an ambiguous name match.
    Returns True if this was a confirmation reply (consumed), False otherwise.
    """
    if event_id not in _pending_confirmations:
        return False

    lower = reply_text.strip().lower()
    if lower not in ("yes", "no"):
        return False

    ctx = _pending_confirmations.pop(event_id)
    candidates = ctx["candidates"]
    note_text = ctx["note_text"]
    pending_id = ctx["pending_id"]
    appointment_title = ctx["appointment_title"]
    appointment_time = ctx["appointment_time"]

    if lower == "yes" and candidates:
        person = candidates[0]
        _store_note(event_id, appointment_title, appointment_time, note_text, person["id"])
        _mark_complete(pending_id)
        await _send_telegram(f"Note stored and linked to {person['name']}.")
    else:
        _store_note(event_id, appointment_title, appointment_time, note_text, None)
        _mark_complete(pending_id)
        await _send_telegram("Note stored.")

    await _maybe_extract_tasks(note_text, appointment_title)
    return True


async def handle_notes_reply(reply_text: str) -> None:
    """Entry point called by the bot for any incoming text while a notes_pending row is active."""
    lower = reply_text.strip().lower()

    # Check if this is a yes/no response to any pending ambiguous match
    if lower in ("yes", "no") and _pending_confirmations:
        event_id = next(iter(_pending_confirmations))
        if await handle_confirmation_reply(reply_text, event_id):
            return

    # Check for consolidated numbered reply (e.g. "1: skip\n2: notes text")
    parsed = _parse_numbered_reply(reply_text)
    if parsed:
        await _handle_numbered_reply(parsed)
        return

    # Single-row fallback
    pending = _get_active_pending()
    if not pending:
        return

    pending_id = pending["id"]
    event_id = pending["event_id"]
    appointment_title = pending["appointment_title"]

    if await handle_confirmation_reply(reply_text, event_id):
        return

    if lower == "skip":
        _mark_dismissed(pending_id)
        return

    if lower == "skip all":
        _append_skip_keyword(appointment_title)
        _mark_dismissed(pending_id)
        await _send_telegram(f'Got it — I\'ll never ask for notes on "{appointment_title}" again.')
        return

    await _process_note_text(dict(pending), reply_text)
