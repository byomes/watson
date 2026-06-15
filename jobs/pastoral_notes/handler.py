"""
handle_notes_reply(reply_text) — called by the Telegram bot when the user
replies to a pastoral notes prompt.

Resolves which notes_pending row is active, fuzzy-matches the appointment
title against the people table, stores the note, and sends a Telegram
confirmation.

Supports both single-item replies ("skip" / free text) and consolidated
numbered replies ("1: skip\n2: Met with Dave, notes here").
"""

import difflib
import logging
import re
from pathlib import Path

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.pastoral_notes.db import get_db

log = logging.getLogger(__name__)

_FUZZY_THRESHOLD = 0.6


def _append_skip_keyword(title: str) -> None:
    path = Path(__file__).parent.parent.parent / "memory" / "skip_keywords.txt"
    keyword = title.strip().lower()
    with open(path, "a") as f:
        f.write(f"\n{keyword}")
_NUMBERED_LINE_RE = re.compile(r'^(\d+):\s*(.+)$')

# Tracks ambiguous matches waiting for yes/no confirmation.
# Keyed by event_id → {"candidates": [...], "note_text": str}
_pending_confirmations: dict[str, dict] = {}


def _send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)


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
            """INSERT INTO pastoral_notes
               (person_id, event_id, appointment_title, appointment_time, note_text, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (person_id, event_id, appointment_title, appointment_time, note_text),
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


def _process_note_text(row: dict, note_text: str) -> None:
    """Fuzzy-match and store (or queue confirmation for) a single note."""
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
        _send_telegram(f"Note stored and linked to {person['name']}.")

    elif len(matches) > 1:
        top = matches[0]
        _pending_confirmations[event_id] = {
            "candidates": matches,
            "note_text": note_text,
            "pending_id": pending_id,
            "appointment_title": appointment_title,
            "appointment_time": appointment_time,
        }
        _send_telegram(f"Is this about {top['name']}? Reply yes or no.")

    else:
        _store_note(event_id, appointment_title, appointment_time, note_text, None)
        _mark_complete(pending_id)
        _send_telegram("Note stored.")


def _handle_numbered_reply(parsed: list[tuple[int, str]]) -> None:
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
            _process_note_text(row, text)


def handle_confirmation_reply(reply_text: str, event_id: str) -> bool:
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
        _send_telegram(f"Note stored and linked to {person['name']}.")
    else:
        _store_note(event_id, appointment_title, appointment_time, note_text, None)
        _mark_complete(pending_id)
        _send_telegram("Note stored.")

    return True


def handle_notes_reply(reply_text: str) -> None:
    """Entry point called by the bot for any incoming text while a notes_pending row is active."""
    # Check if this is a yes/no response to any pending ambiguous match
    lower = reply_text.strip().lower()
    if lower in ("yes", "no") and _pending_confirmations:
        event_id = next(iter(_pending_confirmations))
        if handle_confirmation_reply(reply_text, event_id):
            return

    # Check for consolidated numbered reply (e.g. "1: skip\n2: notes text")
    parsed = _parse_numbered_reply(reply_text)
    if parsed:
        _handle_numbered_reply(parsed)
        return

    # Single-row fallback
    pending = _get_active_pending()
    if not pending:
        return

    pending_id = pending["id"]
    event_id = pending["event_id"]
    appointment_title = pending["appointment_title"]
    appointment_time = pending["appointment_time"]

    if handle_confirmation_reply(reply_text, event_id):
        return

    if lower == "skip":
        _mark_dismissed(pending_id)
        return

    if lower == "skip all":
        _append_skip_keyword(appointment_title)
        _mark_dismissed(pending_id)
        _send_telegram(f'Got it — I\'ll never ask for notes on "{appointment_title}" again.')
        return

    _process_note_text(dict(pending), reply_text)
