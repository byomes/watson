import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import requests

from config.settings import DB_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobs.email_job.gmail import send_as_watson

log = logging.getLogger(__name__)

SPEC_EMAIL = "bill.yomes@gmail.com"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5-coder:7b"
FALLBACK_MODEL = "claude-opus-4-6"
MAX_ATTEMPTS = 3

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _bootstrap():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS code_agent_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            directive TEXT,
            spec TEXT,
            status TEXT DEFAULT 'awaiting_confirm',
            created_at TEXT,
            confirmed_at TEXT,
            result TEXT
        )
    """)
    conn.commit()
    conn.close()


_bootstrap()


def _telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not set — skipping message: %s", text[:80])
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
    except Exception as exc:
        log.error("Telegram message failed: %s", exc)


def _load_prompt():
    return (_PROMPTS_DIR / "build.md").read_text()


def _call_ollama(directive, attempt):
    prompt = _load_prompt() + "\n\nBUILD REQUEST:\n" + directive
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json().get("response", "").strip()
    if not text:
        raise ValueError("Empty response from Ollama")
    return text


def _call_claude_api(directive):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")
    prompt = _load_prompt() + "\n\nBUILD REQUEST:\n" + directive
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()


def _send_spec(subject, spec_text):
    body = spec_text + "\n\nReply with CONFIRM to proceed."
    send_as_watson(
        to=SPEC_EMAIL,
        subject=f"Watson Spec: {subject}",
        body_plain=body,
    )


def _store_job(directive, spec):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO code_agent_jobs (directive, spec, status, created_at)
           VALUES (?, ?, 'awaiting_confirm', ?)""",
        (directive, spec, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def handle(subject, body):
    _telegram("📬 Directive received — " + subject)
    _telegram("🧠 Thinking — drafting spec")

    directive = subject + "\n\n" + body
    spec = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if attempt < 3:
                spec = _call_ollama(directive, attempt)
            else:
                _telegram("🆘 Escalating to Claude API — Ollama failed twice")
                spec = _call_claude_api(directive)
            break
        except Exception as exc:
            _telegram(f"❌ Failed attempt {attempt} of 3 — {exc}")
            log.error("Code Agent attempt %d failed: %s", attempt, exc)

    if spec is None:
        _telegram("❌ Code Agent failed — manual intervention needed")
        return

    _send_spec(subject, spec)
    _telegram("🧠 Spec ready — check your email")
    _store_job(directive, spec)
    log.info("Code Agent spec generated and emailed for: %s", subject)


def refine_job(job_id: int, feedback: str) -> None:
    """Re-generate spec for an existing job incorporating user refinement feedback."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT directive, spec FROM code_agent_jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        conn.close()
        _telegram(f"❌ Code Agent: job {job_id} not found for refinement")
        return
    directive, original_spec = row[0], row[1] or ""
    conn.execute("UPDATE code_agent_jobs SET status='pending' WHERE id=?", (job_id,))
    conn.commit()
    conn.close()

    refined_directive = (
        directive
        + "\n\nPrevious spec:\n" + original_spec
        + "\n\nRefinement feedback:\n" + feedback
    )

    spec = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if attempt < 3:
                spec = _call_ollama(refined_directive, attempt)
            else:
                _telegram("🆘 Escalating to Claude API — Ollama failed twice")
                spec = _call_claude_api(refined_directive)
            break
        except Exception as exc:
            _telegram(f"❌ Failed attempt {attempt} of 3 — {exc}")
            log.error("Code Agent refine attempt %d failed: %s", attempt, exc)

    if spec is None:
        _telegram("❌ Code Agent refinement failed — manual intervention needed")
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE code_agent_jobs SET status='awaiting_confirm' WHERE id=?", (job_id,))
        conn.commit()
        conn.close()
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE code_agent_jobs SET directive=?, spec=?, status='awaiting_confirm' WHERE id=?",
        (refined_directive, spec, job_id),
    )
    conn.commit()
    conn.close()

    _send_spec("Refined Spec", spec)
    _telegram("🧠 Refined spec ready — check your email")
    log.info("Code Agent refined spec for job %d", job_id)
