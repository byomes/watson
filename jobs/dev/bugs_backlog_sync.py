"""jobs/dev/bugs_backlog_sync.py — Regenerate BUGS.md / DEV_PROJECTS.md from
bug_tracker / project_backlog and publish them the same way
jobs/dev/update_arch.py and jobs/dev/file_map.py publish their files.

Unlike WATSON_ARCHITECTURE.md (manually edited, nightly-appended) and
FILE_MAP.md (regenerated from a filesystem scan), these two files are 100%
derived from the database — a full fresh regeneration every run, no append,
no manual-edit preservation. Source of truth is bug_tracker / project_backlog;
hand edits to BUGS.md / DEV_PROJECTS.md will be overwritten on the next run.

Cron: 0 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python
      /home/billyomes/watson/jobs/dev/bugs_backlog_sync.py >> /home/billyomes/watson/logs/bugs_backlog_sync.log 2>&1

On-demand: `python3 jobs/dev/bugs_backlog_sync.py` — same logic, runnable any time.
"""
import subprocess
from datetime import date, datetime
from pathlib import Path

from core.database import get_connection
from jobs.dev import docs_sync

BUGS_FILE = Path.home() / "watson" / "memory" / "BUGS.md"
BACKLOG_FILE = Path.home() / "watson" / "memory" / "DEV_PROJECTS.md"
WATSON_DIR = Path.home() / "watson"


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _md_cell(text: str | None) -> str:
    """Collapse to a single line and escape pipes so it survives a markdown table cell."""
    if not text:
        return ""
    return " ".join(text.replace("|", "\\|").split())


def generate_bugs_md() -> str:
    conn = get_connection()
    try:
        open_rows = conn.execute(
            "SELECT id, title, repo, discovered_at FROM bug_tracker "
            "WHERE status='open' ORDER BY discovered_at DESC, id DESC"
        ).fetchall()
        resolved_rows = conn.execute(
            "SELECT id, title, repo, resolved_at, commit_hash FROM bug_tracker "
            "WHERE status='resolved' AND resolved_at >= datetime('now', '-30 days') "
            "ORDER BY resolved_at DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()

    lines = [
        "# Watson Bug Tracker",
        "_Auto-generated nightly from bug_tracker. Source of truth is the database — do not hand-edit this file, changes will be overwritten._",
        f"Last generated: {_now_str()}",
        "",
        f"## Open ({len(open_rows)})",
        "| ID | Title | Repo | Discovered |",
        "|---|---|---|---|",
    ]
    for r in open_rows:
        lines.append(f"| {r['id']} | {_md_cell(r['title'])} | {r['repo']} | {r['discovered_at']} |")

    lines += [
        "",
        "## Recently Resolved (last 30 days)",
        "| ID | Title | Repo | Resolved | Commit |",
        "|---|---|---|---|---|",
    ]
    for r in resolved_rows:
        lines.append(f"| {r['id']} | {_md_cell(r['title'])} | {r['repo']} | {r['resolved_at']} | {r['commit_hash'] or ''} |")

    return "\n".join(lines) + "\n"


def generate_backlog_md() -> str:
    conn = get_connection()
    try:
        planned_rows = conn.execute(
            "SELECT id, title, summary, added_date FROM project_backlog "
            "WHERE status='planned' ORDER BY added_date DESC, id DESC"
        ).fetchall()
        done_rows = conn.execute(
            "SELECT id, title, summary, added_date FROM project_backlog "
            "WHERE status != 'planned' AND added_date >= date('now', '-30 days') "
            "ORDER BY added_date DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()

    lines = [
        "# Watson Project Backlog",
        "_Auto-generated nightly from project_backlog. Source of truth is the database — do not hand-edit this file, changes will be overwritten._",
        f"Last generated: {_now_str()}",
        "",
        f"## Planned ({len(planned_rows)})",
        "| ID | Title | Summary | Added |",
        "|---|---|---|---|",
    ]
    for r in planned_rows:
        lines.append(f"| {r['id']} | {_md_cell(r['title'])} | {_md_cell(r['summary'])} | {r['added_date']} |")

    lines += [
        "",
        "## Done (last 30 days)",
        "| ID | Title | Summary | Added |",
        "|---|---|---|---|",
    ]
    for r in done_rows:
        lines.append(f"| {r['id']} | {_md_cell(r['title'])} | {_md_cell(r['summary'])} | {r['added_date']} |")

    return "\n".join(lines) + "\n"


def git_commit_push() -> None:
    today = date.today().isoformat()
    commands = [
        ["git", "-C", str(WATSON_DIR), "add", "memory/BUGS.md", "memory/DEV_PROJECTS.md"],
        [
            "git", "-C", str(WATSON_DIR),
            "commit", "-m", f"docs: bugs/backlog export {today}",
        ],
        ["git", "-C", str(WATSON_DIR), "push", "origin", "main"],
    ]
    for cmd in commands:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            print(f"  git warning: {result.stderr.strip() or result.stdout.strip()}")


def main() -> None:
    bugs_content = generate_bugs_md()
    backlog_content = generate_backlog_md()

    BUGS_FILE.write_text(bugs_content)
    BACKLOG_FILE.write_text(backlog_content)
    print(f"BUGS.md written ({len(bugs_content)} chars)")
    print(f"DEV_PROJECTS.md written ({len(backlog_content)} chars)")

    docs_sync.push_file("BUGS.md", bugs_content)
    docs_sync.push_file("DEV_PROJECTS.md", backlog_content)

    git_commit_push()
    print("Committed and pushed BUGS.md and DEV_PROJECTS.md")


if __name__ == "__main__":
    main()
