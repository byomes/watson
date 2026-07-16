#!/usr/bin/env python3
"""
jobs/dev/git_sync.py — Nightly multi-repo git sync check.

For each repo: fetch origin, compare local main to origin/main.
  - Ahead only (not behind): push. Push failure surfaces as a batched
    error alert — no auto-retry.
  - Behind (any amount) or diverged: never auto-pull/rebase/merge. Queues
    a Telegram "needs decision" message with Pull/Skip buttons via
    tg_pending_actions (type='git_sync_resolve'), resolved by bot.py's
    gs_ callback handler.
  - Clean (nothing ahead, nothing behind): silent.

Read-only on the working tree — only ever runs fetch / rev-list / push.
Never git add, git stash, or anything that touches untracked/uncommitted
changes.

Cron: 10 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/dev/git_sync.py
"""
import subprocess
from pathlib import Path

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate
from jobs.telegram.pending import store_pending_action

REPOS = [
    "/home/billyomes/watson",
    "/home/billyomes/wcky",
    "/home/billyomes/watson-admin",
    "/home/billyomes/watson-ui",
    "/home/billyomes/fms",
]


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def _send_telegram(text: str, reply_markup: dict | None = None) -> None:
    if vacation_gate("system_failure", "jobs.dev.git_sync", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json=payload, timeout=10,
    )
    resp.raise_for_status()


def _check_repo(path: str) -> dict:
    name = Path(path).name

    fetch = _git(["fetch", "origin"], cwd=path)
    if fetch.returncode != 0:
        return {"status": "fetch_failed", "name": name, "path": path, "error": fetch.stderr.strip()}

    counts = _git(["rev-list", "--left-right", "--count", "origin/main...main"], cwd=path)
    if counts.returncode != 0:
        return {"status": "fetch_failed", "name": name, "path": path, "error": counts.stderr.strip()}

    parts = counts.stdout.split()
    behind, ahead = int(parts[0]), int(parts[1])

    if ahead and not behind:
        # Explicit local-ref -> remote-ref push, regardless of whatever
        # branch happens to be currently checked out.
        push = _git(["push", "origin", "main"], cwd=path)
        if push.returncode != 0:
            return {"status": "push_failed", "name": name, "path": path, "error": push.stderr.strip()}
        return {"status": "pushed", "name": name, "path": path}

    if behind:
        return {"status": "needs_decision", "name": name, "path": path, "ahead": ahead, "behind": behind}

    return {"status": "clean", "name": name, "path": path}


def main():
    results = []
    for path in REPOS:
        p = Path(path)
        if not p.is_dir() or not (p / ".git").is_dir():
            continue  # e.g. fms not cloned yet — skip silently, no alert
        results.append(_check_repo(path))

    needs_decision = [r for r in results if r["status"] == "needs_decision"]
    failures = [r for r in results if r["status"] in ("fetch_failed", "push_failed")]

    for r in needs_decision:
        payload = {
            "repo_path": r["path"],
            "repo_name": r["name"],
            "ahead": r["ahead"],
            "behind": r["behind"],
        }
        pending_id = store_pending_action("git_sync_resolve", 0, payload)
        text = f"⚠️ {r['name']} — {r['ahead']} ahead, {r['behind']} behind"
        reply_markup = {
            "inline_keyboard": [[
                {"text": "Pull --rebase & Push", "callback_data": f"gs_pull:{pending_id}"},
                {"text": "Skip", "callback_data": f"gs_skip:{pending_id}"},
            ]]
        }
        _send_telegram(text, reply_markup)

    if failures:
        lines = ["⚠️ Git sync errors:"]
        for r in failures:
            label = "fetch failed" if r["status"] == "fetch_failed" else "push failed"
            lines.append(f"\n{r['name']} — {label}:\n{r['error'][:300]}")
        _send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
