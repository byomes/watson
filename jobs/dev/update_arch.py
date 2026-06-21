"""jobs/dev/update_arch.py — Append last-24h git commits from all repos to WATSON_ARCHITECTURE.md.

Cron: 0 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python
      /home/billyomes/watson/jobs/dev/update_arch.py >> /home/billyomes/watson/logs/update_arch.log 2>&1
"""
import subprocess
from datetime import date
from pathlib import Path

REPOS = [
    ("~/watson",       "watson"),
    ("~/wcky",         "wcky"),
    ("~/watson-admin", "watson-admin"),
    ("~/watson-ui",    "watson-ui"),
]

ARCH_FILE = Path.home() / "watson" / "memory" / "WATSON_ARCHITECTURE.md"
WATSON_DIR = Path.home() / "watson"


def get_recent_commits(repo_path: str) -> list[str]:
    expanded = str(Path(repo_path.replace("~", str(Path.home()))))
    result = subprocess.run(
        ["git", "-C", expanded, "log", "--since=24 hours ago", "--oneline"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    return lines


def build_section(today: str) -> str | None:
    repo_blocks = []

    for repo_path, label in REPOS:
        commits = get_recent_commits(repo_path)
        if commits:
            lines = "\n".join(f"- {c}" for c in commits)
            repo_blocks.append(f"### ~/{label}\n{lines}")

    if not repo_blocks:
        return None

    body = "\n\n".join(repo_blocks)
    return f"\n---\n\n## Recent Changes — {today}\n\n{body}\n"


def git_commit_push(today: str) -> None:
    commands = [
        ["git", "-C", str(WATSON_DIR), "add", "memory/WATSON_ARCHITECTURE.md"],
        [
            "git", "-C", str(WATSON_DIR),
            "commit", "-m", f"docs: architecture update {today}",
        ],
        ["git", "-C", str(WATSON_DIR), "push", "origin", "main"],
    ]
    for cmd in commands:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            print(f"  git warning: {result.stderr.strip() or result.stdout.strip()}")


def main() -> None:
    today = date.today().isoformat()
    section = build_section(today)

    if section is None:
        print(f"No commits in last 24h across any repo — skipping ({today})")
        return

    current = ARCH_FILE.read_text()
    ARCH_FILE.write_text(current + section)
    print(f"Appended Recent Changes section to WATSON_ARCHITECTURE.md ({today})")

    git_commit_push(today)
    print("Committed and pushed WATSON_ARCHITECTURE.md")


if __name__ == "__main__":
    main()
