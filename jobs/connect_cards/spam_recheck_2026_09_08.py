"""
One-time follow-up check: did the 2026-09-05 connect-card spam fix hold?

Context: congregation.db's members table was repeatedly hit by a spam bot
via the wtsn.me/cat/connect form (ids 265/266, "Alluverr Alluvert" /
ziecr@aol.com / (800) 669-6607, removed 2026-09-05). A honeypot field +
minimum fill-time check was deployed the same day (watson-tools commit
508a782) to generalize past the single hardcoded phone-number blocklist
that came before it. This job checks whether any new spam slipped through
in the days since, then reports the result via Telegram and updates the
project memory file so a future session knows the current status without
re-investigating from scratch.

This is a ONE-OFF, not a recurring job: it removes its own crontab line
after running (see _deregister_self below), since a bare day/month cron
field with no year would otherwise fire again next September 8th.

Usage:
  PYTHONPATH=/home/billyomes/watson python jobs/connect_cards/spam_recheck_2026_09_08.py

Cron (fires once, 2026-09-08 09:00 America/New_York = 13:00 UTC):
  0 13 8 9 * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \\
    /home/billyomes/watson/jobs/connect_cards/spam_recheck_2026_09_08.py \\
    >> /home/billyomes/watson/logs/spam_recheck_2026_09_08.log 2>&1
"""

import logging
import os
import re
import sqlite3
import subprocess

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [spam_recheck] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")
CRON_MARKER = "spam_recheck_2026_09_08.py"

# Deploy time of the honeypot/fill-time fix (watson-tools commit 508a782) --
# anything created at or after this is in scope for the check.
FIX_DEPLOYED_AT = "2026-09-05 20:00:00"

TOLL_FREE_AREA_CODES = ("800", "888", "877", "866", "855", "844")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def _send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.connect_cards.spam_recheck_2026_09_08", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set -- skipping notification.")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)


def _looks_like_spam_name(name: str) -> bool:
    """Near-identical first/last name pair, e.g. 'Alluverr Alluvert'."""
    parts = name.strip().split()
    if len(parts) != 2:
        return False
    first, last = parts[0].lower(), parts[1].lower()
    if first == last:
        return True
    shorter, longer = sorted((first, last), key=len)
    return len(shorter) >= 4 and longer.startswith(shorter[:len(shorter) - 1])


def _phone_is_toll_free(phone: str) -> bool:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return len(digits) == 10 and digits[:3] in TOLL_FREE_AREA_CODES


def find_suspects() -> list[sqlite3.Row]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, email, phone, created_at FROM members WHERE created_at >= ?",
            (FIX_DEPLOYED_AT,),
        ).fetchall()
    finally:
        conn.close()
    return [
        r for r in rows
        if _looks_like_spam_name(r["name"] or "") or _phone_is_toll_free(r["phone"] or "")
    ]


def _deregister_self() -> None:
    """Remove this job's own line from crontab so it doesn't fire again next year."""
    try:
        current = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, check=True
        ).stdout
    except subprocess.CalledProcessError:
        return
    remaining = "\n".join(
        line for line in current.splitlines() if CRON_MARKER not in line
    )
    subprocess.run(["crontab", "-"], input=remaining + "\n", text=True, check=True)
    log.info("Removed own crontab entry (%s) -- one-off check complete.", CRON_MARKER)


MEMORY_PATH = os.path.expanduser(
    "~/.claude/projects/-home-billyomes/memory/project_congregation_db_spam.md"
)
INDEX_PATH = os.path.expanduser(
    "~/.claude/projects/-home-billyomes/memory/MEMORY.md"
)


def _update_memory(suspects: list[sqlite3.Row]) -> None:
    """Best-effort memory update -- log and continue on any failure, never
    block the Telegram report on filesystem issues in the memory dir."""
    try:
        if suspects:
            outcome = (
                f"**2026-09-08 recheck: fix did NOT fully hold** -- "
                f"{len(suspects)} new suspected-spam member row(s) found "
                f"since the honeypot/fill-time fix "
                f"(ids: {', '.join(str(r['id']) for r in suspects)}). "
                "Needs further investigation -- likely bot is rendering the "
                "real page/JS (defeating honeypot + timing checks), or a "
                "new spam pattern entirely."
            )
            index_desc = "recurring spam bot NOT fully stopped by 2026-09-05 fix -- new rows found 2026-09-08, needs more work"
        else:
            outcome = (
                "**2026-09-08 recheck: fix held** -- no new suspected-spam "
                "member rows found since the honeypot/fill-time fix deployed "
                "2026-09-05 (commit 508a782). Treating this as resolved "
                "unless new spam reappears."
            )
            index_desc = "recurring spam bot stopped by honeypot+fill-time filter (commit 508a782), confirmed clean as of 2026-09-08"

        with open(MEMORY_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n{outcome}\n")

        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            index = f.read()
        index = re.sub(
            r"- \[Congregation DB Spam\]\(project_congregation_db_spam\.md\) — .*",
            f"- [Congregation DB Spam](project_congregation_db_spam.md) — {index_desc}",
            index,
        )
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            f.write(index)
    except Exception as exc:
        log.warning("Memory update failed (non-fatal): %s", exc)


def run() -> None:
    suspects = find_suspects()
    if suspects:
        lines = [
            f"  id={r['id']} name={r['name']!r} email={r['email']!r} phone={r['phone']!r} created_at={r['created_at']}"
            for r in suspects
        ]
        message = (
            "Connect-card spam recheck (2026-09-08): the honeypot/fill-time fix "
            f"did NOT fully hold -- {len(suspects)} new suspected-spam row(s) in "
            "congregation.db members:\n" + "\n".join(lines) +
            "\n\nThese have NOT been deleted -- review and clean up (also check "
            "attendance, connect_cards, follow_ups, next_steps by member_id, no "
            "FK cascade exists)."
        )
        log.warning(message)
    else:
        message = (
            "Connect-card spam recheck (2026-09-08): no new suspected-spam rows "
            "found since the 2026-09-05 honeypot/fill-time fix. Looks like it held."
        )
        log.info(message)

    _send_telegram(message)
    _update_memory(suspects)
    _deregister_self()


if __name__ == "__main__":
    run()
