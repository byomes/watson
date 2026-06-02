"""
jobs/note.py — Watson Note Job

Handles note creation and appending for sermon series or any topic.

Telegram usage:
  Watson note joshua This is my thought
  Watson note [topic] [text]

Behavior:
  - Creates notes/<topic>.md if it doesn't exist
  - Appends a timestamped entry if it does
  - Commits and pushes to GitHub so notes are synced

"""

import os
import sys
import subprocess
from datetime import datetime

REPO_ROOT = os.environ.get("WATSON_ROOT", os.path.expanduser("~/watson"))
NOTES_DIR = os.path.join(REPO_ROOT, "notes")


def parse_command(text: str):
    parts = text.strip().split(None, 3)
    if len(parts) < 3:
        return None, None
    topic = parts[2].lower().replace(" ", "-")
    content = parts[3].strip() if len(parts) >= 4 else None
    return topic, content


def ensure_notes_dir():
    os.makedirs(NOTES_DIR, exist_ok=True)


def note_file_path(topic: str) -> str:
    return os.path.join(NOTES_DIR, f"{topic}.md")


def create_note_file(topic: str) -> str:
    path = note_file_path(topic)
    if os.path.exists(path):
        return f"Notes file for *{topic}* already exists."
    title = topic.replace("-", " ").title()
    with open(path, "w") as f:
        f.write(f"# {title}\n\n")
    git_commit(path, f"Create notes file: {topic}")
    return f"Created notes file for *{topic}*."


def append_note(topic: str, content: str) -> str:
    path = note_file_path(topic)
    existed = os.path.exists(path)
    if not existed:
        title = topic.replace("-", " ").title()
        with open(path, "w") as f:
            f.write(f"# {title}\n\n")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"**{timestamp}** — {content}\n\n"
    with open(path, "a") as f:
        f.write(entry)
    git_commit(path, f"Add note to {topic}")
    action = "Created and added note to" if not existed else "Added note to"
    return f"{action} *{topic}*."


def git_commit(filepath: str, message: str):
    try:
        subprocess.run(["git", "-C", REPO_ROOT, "add", filepath], check=True)
        subprocess.run(["git", "-C", REPO_ROOT, "commit", "-m", message], check=True)
        subprocess.run(["git", "-C", REPO_ROOT, "push"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e}", file=sys.stderr)


def run(message_text: str) -> str:
    ensure_notes_dir()
    topic, content = parse_command(message_text)
    if topic is None:
        return "Usage: `Watson note <topic> <your note>` or `Watson note <topic>` to create a file."
    if content is None:
        return create_note_file(topic)
    return append_note(topic, content)


if __name__ == "__main__":
    test_msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Watson note joshua Test note entry"
    print(run(test_msg))
