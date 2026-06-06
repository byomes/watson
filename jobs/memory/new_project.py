"""jobs/memory/new_project.py — create a new project memory file."""
import os
import subprocess
from datetime import date
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"


def _send_telegram(text: str) -> None:
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def create_project(slug: str, name: str) -> None:
    today = date.today().isoformat()
    project_file = MEMORY / "projects" / f"{slug}.md"
    index_file = MEMORY / "projects" / "_index.md"

    project_file.write_text(
        f"# {name}\n"
        f"**Status:** Active\n"
        f"**Goal:** [TBD]\n"
        f"\n"
        f"## Key Files\n"
        f"[TBD]\n"
        f"\n"
        f"## Current State\n"
        f"[TBD]\n"
        f"\n"
        f"## Next Steps\n"
        f"[TBD]\n"
        f"\n"
        f"## Notes\n"
        f"[TBD]\n",
        encoding="utf-8",
    )

    index_text = index_file.read_text(encoding="utf-8")
    new_row = f"| {slug} | {name} | Active | {today} |"
    if new_row not in index_text:
        index_file.write_text(index_text.rstrip() + f"\n{new_row}\n", encoding="utf-8")

    subprocess.run(
        ["git", "add", str(project_file), str(index_file)],
        cwd=str(REPO),
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", f"memory: new project {slug}"],
        cwd=str(REPO),
        check=True,
    )

    _send_telegram(
        f"Project '{name}' created. memory/projects/{slug}.md is ready."
    )

    from jobs.memory.sync import main as sync_main
    sync_main()


def run() -> str:
    """Prompt Bill to provide project details conversationally."""
    return "New project skill ready. Tell me the project name and I'll set it up."


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: new_project.py <slug> '<name>'")
        sys.exit(1)
    create_project(sys.argv[1], sys.argv[2])
