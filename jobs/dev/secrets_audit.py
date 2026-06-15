"""jobs/dev/secrets_audit.py — scan jobs/ for env var references and audit against .env."""
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
ENV_FILE = REPO / ".env"
JOBS_DIR = REPO / "jobs"

load_dotenv(ENV_FILE)

_ENV_PATTERN = re.compile(
    r'os\.(?:getenv|environ\.get)\(\s*["\']([^"\']+)["\']'
    r'|os\.environ\[\s*["\']([^"\']+)["\']'
)

_FALLBACK_PATTERN = re.compile(
    r'os\.(?:getenv|environ\.get)\(\s*["\']([^"\']+)["\'][^)]*\)'
    r'\s*or\s*'
    r'os\.(?:getenv|environ\.get)\(\s*["\']([^"\']+)["\']'
)

_ENV_LINE = re.compile(r'^([A-Z_][A-Z0-9_]*)=')


def _parse_env_file(path: Path) -> list[str]:
    names = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if m:
            name = m.group(1)
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def _scan_jobs(jobs_dir: Path) -> tuple[set, set]:
    """Return (all_refs, fallback_pairs) across all .py files under jobs/."""
    all_refs: set[str] = set()
    fallback_pairs: list[tuple[str, str]] = []

    for py_file in sorted(jobs_dir.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        for m in _FALLBACK_PATTERN.finditer(text):
            fallback_pairs.append((m.group(1), m.group(2)))
        for m in _ENV_PATTERN.finditer(text):
            ref = m.group(1) or m.group(2)
            all_refs.add(ref)

    return all_refs, fallback_pairs


def _telegram(text: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        print("Warning: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping Telegram.", file=sys.stderr)
        return
    if len(text) > 4000:
        text = text[:3950] + "\n…[truncated]"
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=30,
        )
    except Exception as exc:
        print(f"Telegram failed: {exc}", file=sys.stderr)


def run(message=None) -> str:
    env_names = _parse_env_file(ENV_FILE)
    env_set = set(env_names)

    all_refs, fallback_pairs = _scan_jobs(JOBS_DIR)

    # Determine which refs are satisfied by a fallback pair where at least one side is in .env
    satisfied: set[str] = set()
    for a, b in fallback_pairs:
        if a in env_set or b in env_set:
            satisfied.add(a)
            satisfied.add(b)

    missing = sorted(v for v in all_refs if v not in satisfied and v not in env_set)
    dead = sorted(v for v in env_set if v not in all_refs)

    lines = ["=== Watson Secrets Audit ===\n"]

    lines.append(f"1. MISSING FROM .env ({len(missing)} vars referenced in code but not set)")
    if missing:
        for v in missing:
            lines.append(f"   - {v}")
    else:
        lines.append("   (none)")

    lines.append(f"\n2. SET IN .env BUT NOT REFERENCED IN JOBS ({len(dead)} potential dead vars)")
    if dead:
        for v in dead:
            lines.append(f"   - {v}")
    else:
        lines.append("   (none)")

    lines.append(f"\n3. FULL .env INVENTORY ({len(env_names)} vars)")
    for v in env_names:
        lines.append(f"   {v}")

    report = "\n".join(lines)
    print(report)

    tg_lines = [
        "Watson Secrets Audit",
        f"Missing from .env: {len(missing)}",
    ]
    if missing:
        tg_lines.extend(f"  - {v}" for v in missing)
    tg_lines.append(f"\nPotential dead vars: {len(dead)}")
    if dead:
        tg_lines.extend(f"  - {v}" for v in dead)
    tg_lines.append(f"\nTotal .env vars: {len(env_names)}")
    _telegram("\n".join(tg_lines))

    return report


if __name__ == "__main__":
    run()
