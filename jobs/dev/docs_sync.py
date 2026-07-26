"""jobs/dev/docs_sync.py — Mirror WATSON_ARCHITECTURE.md / FILE_MAP.md to the
public byomes/watson-docs repo so Claude.ai's web_fetch can read them as the
source of truth.

Supersedes gist_sync.py: gist.githubusercontent.com is blocked by robots.txt
for Claude's fetch tool, so a Gist can never actually be read even though the
API push worked fine. raw.githubusercontent.com carries no such block and is
confirmed reachable — a plain public repo, not a Gist, is the fetchable target.

Working copy lives at ~/watson-docs-sync, outside ~/watson (gitignored there,
never committed into the watson repo). Clones on first run, pulls on every
run after, then commits + pushes only the one file that changed.

Called by jobs/dev/update_arch.py and jobs/dev/file_map.py after each
regenerates its own file. Uses the same GITHUB_TOKEN as gist_sync.py did
(classic PAT, repo scope) — a normal git push needs no gist-specific scope.

The token is never placed in a URL or in any string this module prints —
auth is passed to git via a one-shot `-c http.extraheader=...` on the push
subprocess call only, never written to .git/config or logged.
"""
import base64
import os
import re
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.home() / "watson" / ".env")

REPO = "byomes/watson-docs"
REPO_URL = f"https://github.com/{REPO}.git"
WORKDIR = Path.home() / "watson-docs-sync"

RAW_URLS = {
    "WATSON_ARCHITECTURE.md": f"https://raw.githubusercontent.com/{REPO}/main/WATSON_ARCHITECTURE.md",
    "FILE_MAP.md": f"https://raw.githubusercontent.com/{REPO}/main/FILE_MAP.md",
}

_SECRET_RE = re.compile(
    r"ghp_[A-Za-z0-9]{20,}"
    r"|gh[ousr]_[A-Za-z0-9]{20,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9]{8,}",
    re.IGNORECASE,
)


def _run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _auth_header(token: str) -> str:
    b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"AUTHORIZATION: basic {b64}"


def _ensure_identity() -> None:
    if not _run(["git", "config", "user.name"], cwd=WORKDIR).stdout.strip():
        _run(["git", "config", "user.name", "Watson"], cwd=WORKDIR)
    if not _run(["git", "config", "user.email"], cwd=WORKDIR).stdout.strip():
        _run(["git", "config", "user.email", "watson@williamckyomes.com"], cwd=WORKDIR)


def _ensure_workdir() -> bool:
    if (WORKDIR / ".git").exists():
        result = _run(["git", "pull", "--ff-only", "origin", "main"], cwd=WORKDIR)
        if result.returncode != 0:
            print(f"  docs_sync: pull failed — {result.stderr.strip()[:300]}")
            return False
        return True

    WORKDIR.parent.mkdir(parents=True, exist_ok=True)
    result = _run(["git", "clone", REPO_URL, str(WORKDIR)])
    if result.returncode != 0:
        print(f"  docs_sync: clone failed — {result.stderr.strip()[:300]}")
        return False

    branch = _run(["git", "branch", "--show-current"], cwd=WORKDIR).stdout.strip()
    if branch != "main":
        _run(["git", "checkout", "-B", "main"], cwd=WORKDIR)

    _ensure_identity()
    return True


def push_file(filename: str, content: str) -> bool:
    """Write one file into the byomes/watson-docs working copy and push it."""
    match = _SECRET_RE.search(content)
    if match:
        print(f"  docs_sync: BLOCKED push of {filename} — content matches secret pattern at offset {match.start()}")
        return False

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("  docs_sync: GITHUB_TOKEN not set, skipping docs push")
        return False

    if not _ensure_workdir():
        return False

    (WORKDIR / filename).write_text(content)

    add = _run(["git", "add", filename], cwd=WORKDIR)
    if add.returncode != 0:
        print(f"  docs_sync: git add failed — {add.stderr.strip()[:300]}")
        return False

    commit = _run(["git", "commit", "-m", f"sync: {filename}"], cwd=WORKDIR)
    if commit.returncode != 0:
        if "nothing to commit" in commit.stdout:
            print(f"  docs_sync: {filename} unchanged, nothing to push")
            return True
        print(f"  docs_sync: git commit failed — {commit.stderr.strip()[:300]}")
        return False

    header = _auth_header(token)
    push = _run(["git", "-c", f"http.extraheader={header}", "push", "-u", "origin", "main"], cwd=WORKDIR)
    if push.returncode != 0:
        print(f"  docs_sync: git push failed — {push.stderr.strip()[:300]}")
        return False

    print(f"  docs_sync: pushed {filename} -> {RAW_URLS.get(filename, REPO_URL)}")
    return True
