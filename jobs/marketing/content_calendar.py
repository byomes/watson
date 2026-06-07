"""jobs/marketing/content_calendar.py — Content calendar from DB drafts + AI suggestions."""
import logging
import os
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("WATSON_DB", str(REPO / "data" / "watson.db")))
OLLAMA_URL = "http://localhost:11434/api/generate"

log = logging.getLogger(__name__)


def get_upcoming_content() -> list:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            """SELECT title, slug, scheduled_date, status
               FROM blog_drafts
               WHERE scheduled_date BETWEEN date('now') AND date('now', '+30 days')
               ORDER BY scheduled_date ASC"""
        ).fetchall()
        conn.close()
        return [{"title": r[0], "slug": r[1], "date": r[2], "status": r[3]} for r in rows]
    except Exception as exc:
        log.warning("get_upcoming_content: %s (table may not exist yet)", exc)
        return []


def suggest_content_ideas(topic: str, count: int = 5) -> list:
    prompt = (
        f"Suggest {count} blog post or social media content ideas for a pastor/speaker "
        f"named Dr. Bill Yomes. Topic: {topic}. "
        "Return only a numbered list, one idea per line."
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": "llama3.2:3b", "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        ideas = [line.strip().lstrip("0123456789.)- ") for line in raw.splitlines() if line.strip()]
        return [i for i in ideas if i][:count]
    except Exception as exc:
        log.error("suggest_content_ideas failed: %s", exc)
        return []


def format_calendar() -> str:
    items = get_upcoming_content()
    if not items:
        return "No content scheduled in the next 30 days."
    lines = ["Content Calendar — Next 30 Days", "─" * 34]
    for item in items:
        status_icon = "✓" if item["status"] == "published" else "◌"
        lines.append(f"{item['date']}  {status_icon}  {item['title']}")
    return "\n".join(lines)


def run(message: str = None) -> str:
    if message and any(kw in message.lower() for kw in ("ideas", "suggest", "topic")):
        topic = message.replace("content ideas", "").replace("suggest", "").strip() or "ministry and faith"
        ideas = suggest_content_ideas(topic)
        if not ideas:
            return "Could not generate ideas right now."
        return "Content ideas:\n" + "\n".join(f"  {i+1}. {idea}" for i, idea in enumerate(ideas))
    return format_calendar()
