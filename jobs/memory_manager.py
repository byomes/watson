"""
jobs/memory_manager.py — Watson Memory Manager

Handles reading and writing Watson's memory files.

Memory layers:
  - working.md: rolling last-10 exchanges, always loaded
  - memory/<topic>.md: project-scoped notes, loaded on demand
"""

import os
import re
from datetime import datetime

REPO_ROOT = os.environ.get("WATSON_ROOT", os.path.expanduser("~/watson"))
MEMORY_DIR = os.path.join(REPO_ROOT, "memory")
WORKING_MEMORY_FILE = os.path.join(MEMORY_DIR, "working.md")
MAX_WORKING_ENTRIES = 10

TOPIC_KEYWORDS = {
    "joshua": ["joshua", "courage", "israel"],
    "twj": ["twj", "wrong jesus", "the wrong jesus"],
    "fms": ["fms", "faith makes sense"],
}


def ensure_memory_dir():
    os.makedirs(MEMORY_DIR, exist_ok=True)


def detect_topic(text: str) -> str | None:
    """Return topic key if a known topic is mentioned, else None."""
    lower = text.lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return topic
    return None


def load_working_memory() -> str:
    """Return contents of working.md or empty string."""
    if not os.path.exists(WORKING_MEMORY_FILE):
        return ""
    with open(WORKING_MEMORY_FILE, "r") as f:
        return f.read().strip()


def load_project_memory(topic: str) -> str:
    """Return contents of memory/<topic>.md or empty string."""
    path = os.path.join(MEMORY_DIR, f"{topic}.md")
    if not os.path.exists(path):
        return ""
    with open(path, "r") as f:
        return f.read().strip()


def append_working_memory(user_msg: str, watson_reply: str):
    """Append exchange to working.md and trim to MAX_WORKING_ENTRIES."""
    ensure_memory_dir()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"[{timestamp}]\nUser: {user_msg}\nWatson: {watson_reply}\n\n"

    existing = ""
    if os.path.exists(WORKING_MEMORY_FILE):
        with open(WORKING_MEMORY_FILE, "r") as f:
            existing = f.read()

    # Split into entries by timestamp pattern
    entries = re.split(r'(?=\[\d{4}-\d{2}-\d{2})', existing)
    entries = [e for e in entries if e.strip()]
    entries.append(entry)

    # Keep only last MAX_WORKING_ENTRIES
    entries = entries[-MAX_WORKING_ENTRIES:]

    with open(WORKING_MEMORY_FILE, "w") as f:
        f.write("".join(entries))


def append_project_memory(topic: str, summary: str):
    """Append a one-line summary to memory/<topic>.md."""
    ensure_memory_dir()
    path = os.path.join(MEMORY_DIR, f"{topic}.md")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = topic.replace("-", " ").title()

    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(f"# {title}\n\n")

    with open(path, "a") as f:
        f.write(f"**{timestamp}** — {summary}\n\n")


def build_context(user_msg: str) -> str:
    """
    Assemble memory context to prepend to Ollama prompt.
    Returns a string to inject before the user message.
    """
    sections = []

    working = load_working_memory()
    if working:
        sections.append(f"## Recent Conversation\n{working}")

    topic = detect_topic(user_msg)
    if topic:
        project = load_project_memory(topic)
        if project:
            sections.append(f"## Project Notes: {topic.upper()}\n{project}")

    return "\n\n".join(sections)
