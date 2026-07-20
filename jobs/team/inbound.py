"""jobs/team/inbound.py — Process forwarded emails Bill sends to watson.wcky@gmail.com."""
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

WATSON_DB = BASE_DIR / "data" / "watson.db"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("WATSON_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")   or os.getenv("WATSON_CHAT_ID", "")

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "llama3.2:1b"

log = logging.getLogger(__name__)

_TONE_EMOJI = {
    "urgent":        "🚨",
    "informational": "📬",
    "request":       "📋",
    "update":        "✅",
    "concern":       "⚠️",
}

_FWD_MARKERS = [
    "---------- Forwarded message",
    "Begin forwarded message",
    "-----Original Message-----",
    "-------- Original Message --------",
]


# ── Public helper ──────────────────────────────────────────────────────────────

def is_forwarded_email(subject: str, body: str) -> bool:
    subj = (subject or "").strip().lower()
    if subj.startswith("fwd:") or subj.startswith("fw:"):
        return True
    for marker in _FWD_MARKERS:
        if marker.lower() in (body or "").lower():
            return True
    return False


# ── Internal helpers ───────────────────────────────────────────────────────────

def _extract_forwarded_sender(body: str) -> tuple[str | None, str | None]:
    """Return (email, name) extracted from the forwarded block's From: line."""
    email = None
    name  = None

    # Look for "From: Name <email>" or "From: email" inside forwarded block
    from_match = re.search(
        r'From:\s*(.*?)\n',
        body,
        re.IGNORECASE,
    )
    if from_match:
        from_line = from_match.group(1).strip()
        email_match = re.search(r'<([^>]+)>', from_line)
        if email_match:
            email = email_match.group(1).strip().lower()
            name  = re.sub(r'\s*<[^>]+>', '', from_line).strip().strip('"')
        elif '@' in from_line:
            email = from_line.strip().lower()
        else:
            name = from_line.strip()

    return email, name


def _strip_forwarding_wrapper(body: str) -> str:
    """Keep only the original forwarded message content."""
    for marker in _FWD_MARKERS:
        idx = body.lower().find(marker.lower())
        if idx != -1:
            # Skip past the header block (From/Date/Subject/To lines)
            section = body[idx:]
            lines   = section.split('\n')
            # Skip lines until we hit a blank line (end of fwd header)
            content_start = 0
            in_header = True
            for i, line in enumerate(lines):
                if in_header and line.strip() == '':
                    content_start = i + 1
                    in_header = False
                    break
            return '\n'.join(lines[content_start:]).strip()
    return body.strip()


def _find_member(email: str | None, name: str | None) -> dict | None:
    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row
    member = None

    if email:
        member = conn.execute(
            "SELECT * FROM team_members WHERE LOWER(email)=LOWER(?) AND active=1 LIMIT 1",
            (email,),
        ).fetchone()

    if not member and name:
        member = conn.execute(
            "SELECT * FROM team_members WHERE LOWER(name)=LOWER(?) AND active=1 LIMIT 1",
            (name,),
        ).fetchone()

    conn.close()
    return dict(member) if member else None


def _ollama_digest(member_name: str, body: str) -> dict:
    prompt = (
        f"You are Watson. A ministry leader named {member_name} sent this email to their "
        f"pastor Dr. Bill Yomes. Extract:\n"
        f"1. A 2-3 sentence summary of what the leader is communicating\n"
        f"2. Any action items or requests directed at Dr. Bill (list, or empty list if none)\n"
        f"3. Any tasks the leader mentioned they are working on (list, or empty list if none)\n"
        f"4. Tone: one word (urgent / informational / request / update / concern)\n\n"
        f"Return only valid JSON:\n"
        f'{{"summary": "string", "action_items": ["string"], "leader_tasks": ["string"], "tone": "string"}}\n\n'
        f"Email body:\n{body[:3000]}"
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": prompt, "stream": False},
            timeout=90,
        )
        resp.raise_for_status()
        raw = (resp.json().get("response") or "").strip()
        # Strip markdown fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return json.loads(raw)
    except Exception as exc:
        log.warning("Ollama digest failed: %s", exc)
        return {
            "summary": body[:300],
            "action_items": [],
            "leader_tasks": [],
            "tone": "informational",
        }


def _send_telegram(text: str) -> None:
    # NOTE: this includes emails Ollama tone-classifies as "urgent" (jobs/team/inbound.py
    # _TONE_EMOJI) — that's a business-urgency label for forwarded staff email, not a
    # pastoral crisis or system failure, so it's tagged "normal" per the two-tier scheme.
    if vacation_gate("normal", "jobs.team.inbound", text):
        return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    ).raise_for_status()


def _ensure_tone_column() -> None:
    conn = sqlite3.connect(WATSON_DB)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(team_messages)").fetchall()}
    if "tone" not in cols:
        conn.execute("ALTER TABLE team_messages ADD COLUMN tone TEXT")
        conn.commit()
    conn.close()


def _log_message(member_id: int, subject: str, body: str, received_at: str, tone: str | None = None) -> None:
    _ensure_tone_column()
    conn = sqlite3.connect(WATSON_DB)
    conn.execute(
        "INSERT INTO team_messages (member_id, direction, subject, body, sent_at, tone) "
        "VALUES (?, 'in', ?, ?, ?, ?)",
        (member_id, subject, body, received_at, tone),
    )
    conn.commit()
    conn.close()


def _create_tasks(member_id: int, leader_tasks: list[str]) -> int:
    if not leader_tasks:
        return 0
    conn = sqlite3.connect(WATSON_DB)
    for task_title in leader_tasks:
        title = (task_title or "").strip()
        if title:
            conn.execute(
                "INSERT INTO team_tasks (member_id, title, status, source) VALUES (?, ?, 'open', 'email')",
                (member_id, title),
            )
    conn.commit()
    conn.close()
    return len([t for t in leader_tasks if (t or "").strip()])


# ── Main entry point ───────────────────────────────────────────────────────────

def process_inbound(subject: str, body: str, received_at: str) -> dict:
    try:
        fwd_email, fwd_name = _extract_forwarded_sender(body)
        member = _find_member(fwd_email, fwd_name)

        if not member:
            log.info(
                "Forwarded email not matched to team member (from_email=%r, from_name=%r)",
                fwd_email, fwd_name,
            )
            return {"matched": False}

        clean_body = _strip_forwarding_wrapper(body)
        digest     = _ollama_digest(member["name"], clean_body)

        tone = (digest.get("tone") or "informational").lower()
        _log_message(member["id"], subject, clean_body, received_at, tone=tone)

        tasks_created = _create_tasks(member["id"], digest.get("leader_tasks", []))

        emoji = _TONE_EMOJI.get(tone, "📬")

        lines = [
            f"{emoji} Email from <b>{member['name']}</b> ({member.get('ministry') or 'Team'}):",
            digest.get("summary", ""),
        ]

        action_items = [i for i in (digest.get("action_items") or []) if i and i.strip() and i.strip().lower() not in ("none", "none mentioned", "n/a", "")]
        if action_items:
            lines.append("\n📌 <b>Needs your attention:</b>")
            for item in action_items:
                lines.append(f"  - {item}")

        leader_tasks = [i for i in (digest.get("leader_tasks") or []) if i and i.strip() and i.strip().lower() not in ("none", "none mentioned", "n/a", "")]
        if leader_tasks:
            lines.append("\n📝 <b>They're working on:</b>")
            for task in leader_tasks:
                lines.append(f"  - {task}")

        lines.append("\nLogged to Team &gt; Comms.")

        try:
            _send_telegram("\n".join(lines))
        except Exception as exc:
            log.error("Telegram alert failed: %s", exc)

        return {
            "matched":       True,
            "member_id":     member["id"],
            "member_name":   member["name"],
            "tasks_created": tasks_created,
        }

    except Exception as exc:
        log.error("process_inbound failed: %s", exc)
        return {"matched": False, "error": str(exc)}
