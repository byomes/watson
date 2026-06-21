"""jobs/dev/file_map.py — Regenerate ~/watson/memory/FILE_MAP.md from live file tree.

Cron: 0 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python
      /home/billyomes/watson/jobs/dev/file_map.py >> /home/billyomes/watson/logs/file_map.log 2>&1
"""
import subprocess
from datetime import date
from pathlib import Path

REPOS = [
    ("~/watson",       "~/watson/"),
    ("~/wcky",         "~/wcky/"),
    ("~/watson-admin", "~/watson-admin/"),
    ("~/watson-ui",    "~/watson-ui/"),
]

EXCLUDE_PATTERNS = [
    "*/node_modules/*",
    "*/.git/*",
    "*/__pycache__/*",
    "*/.next/*",
    "*/venv/*",
    "*/chroma/*",
    "*/kb/documents/*",
    "*/kb/transcripts/*",
    "*/.claude/*",
    "*/logs/*",
    "*/outputs/*",
]

OUTPUT = Path.home() / "watson" / "memory" / "FILE_MAP.md"
WATSON_DIR = Path.home() / "watson"


def find_files(repo_path: str) -> list[str]:
    expanded = Path(repo_path.replace("~", str(Path.home())))
    if not expanded.exists():
        return []

    # Use shell=False to prevent the shell from glob-expanding the -path patterns.
    # -type f: only files (directories appear via their children in the tree).
    cmd = ["find", str(expanded), "-type", "f"]
    for pat in EXCLUDE_PATTERNS:
        cmd += ["-not", "-path", pat]

    result = subprocess.run(cmd, capture_output=True, text=True)
    # Sort in Python rather than piping through shell sort
    lines = sorted(ln.strip() for ln in result.stdout.splitlines() if ln.strip())

    # Strip the repo root prefix and return relative paths
    prefix = str(expanded)
    relative = []
    for ln in lines:
        if ln == prefix:
            continue
        rel = ln[len(prefix):].lstrip("/")
        if rel:
            relative.append(rel)
    return relative


def build_tree(paths: list[str]) -> str:
    """Build an indented tree string from a sorted list of relative paths."""
    lines = []
    seen_dirs: set[str] = set()

    for path in paths:
        parts = path.split("/")
        for depth, part in enumerate(parts):
            dir_key = "/".join(parts[: depth + 1])
            if depth < len(parts) - 1:
                # directory
                if dir_key not in seen_dirs:
                    seen_dirs.add(dir_key)
                    indent = "  " * depth
                    lines.append(f"{indent}{part}/")
            else:
                # file
                indent = "  " * depth
                lines.append(f"{indent}{part}")

    return "\n".join(lines)


def git_commit_push() -> None:
    today = date.today().isoformat()
    commands = [
        ["git", "-C", str(WATSON_DIR), "add", "memory/FILE_MAP.md"],
        [
            "git", "-C", str(WATSON_DIR),
            "commit", "-m", f"docs: file map {today}",
        ],
        ["git", "-C", str(WATSON_DIR), "push", "origin", "main"],
    ]
    for cmd in commands:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            print(f"  git warning: {result.stderr.strip() or result.stdout.strip()}")


def main() -> None:
    today = date.today().isoformat()
    sections = []

    sections.append(f"# Watson File Map")
    sections.append(
        f"*Generated: {today}*\n"
        f"*Excludes: logs/, data/chroma/, kb/documents/, kb/transcripts/, "
        f".git/, node_modules/, venv/, __pycache__/, .next/, outputs/, .claude/*"
    )
    sections.append("")

    for repo_label, repo_path in REPOS:
        expanded = Path(repo_path.strip().replace("~", str(Path.home())))
        if not expanded.exists():
            sections.append(f"## {repo_label}/\n\n*(not found on this machine)*\n")
            continue

        paths = find_files(repo_path)
        tree = build_tree(paths)
        sections.append(f"## {repo_label}/\n\n```\n{repo_label}/\n{tree}\n```\n")

    content = "\n".join(sections)
    OUTPUT.write_text(content)
    print(f"FILE_MAP.md written ({len(content)} chars, {today})")

    git_commit_push()
    print("Committed and pushed FILE_MAP.md")


if __name__ == "__main__":
    main()
