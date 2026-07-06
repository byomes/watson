#!/usr/bin/env python3
"""
contact_sync.py — Match team_members missing email/phone to congregation.db and watson.db.

Usage:
  python jobs/team/contact_sync.py
  python jobs/team/contact_sync.py --dry-run

Cron:
  0 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/team/contact_sync.py >> /home/billyomes/watson/logs/team_contact_sync.log 2>&1
"""
import argparse
import logging
import os
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.vacation import vacation_gate

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

WATSON_DB = BASE_DIR / "data" / "watson.db"
CONG_DB   = BASE_DIR / "data" / "congregation.db"
LOG_PATH  = BASE_DIR / "logs" / "team_contact_sync.log"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("WATSON_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")   or os.getenv("WATSON_CHAT_ID", "")

log = logging.getLogger(__name__)


def _send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.team.contact_sync", text):
        return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    ).raise_for_status()


def _lookup_congregation(name: str) -> dict | None:
    if not CONG_DB.exists():
        return None
    try:
        conn = sqlite3.connect(CONG_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT email, phone FROM members WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (name,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as exc:
        log.warning("congregation.db lookup failed for %r: %s", name, exc)
        return None


def _lookup_people(name: str) -> dict | None:
    try:
        conn = sqlite3.connect(WATSON_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT email, phone FROM people WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (name,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as exc:
        log.warning("watson.db people lookup failed for %r: %s", name, exc)
        return None


def run(dry_run: bool = False) -> None:
    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row

    members = conn.execute("""
        SELECT id, name, email, phone FROM team_members
        WHERE active = 1
          AND (email IS NULL OR email = '' OR phone IS NULL OR phone = '')
    """).fetchall()
    members = [dict(m) for m in members]
    conn.close()

    if not members:
        log.info("All active team members already have email and phone — nothing to sync.")
        _send_telegram("📋 Team contact sync: all members already have email + phone on file.")
        return

    log.info("Found %d member(s) with missing contact info.", len(members))

    matched   = []
    unmatched = []

    for member in members:
        name     = member["name"]
        has_email = bool((member["email"] or "").strip())
        has_phone = bool((member["phone"] or "").strip())

        found = _lookup_congregation(name) or _lookup_people(name)

        if not found:
            unmatched.append(name)
            log.info("No match: %s", name)
            continue

        new_email = None if has_email else (found.get("email") or "").strip() or None
        new_phone = None if has_phone else (found.get("phone") or "").strip() or None

        if not new_email and not new_phone:
            unmatched.append(name)
            log.info("Match found for %s but no new data to fill in.", name)
            continue

        fields_added = []
        if new_email: fields_added.append("email")
        if new_phone: fields_added.append("phone")

        log.info("%s%s: %s → adding %s", "[DRY RUN] " if dry_run else "", name, found, ", ".join(fields_added))

        if not dry_run:
            try:
                update_conn = sqlite3.connect(WATSON_DB)
                if new_email and new_phone:
                    update_conn.execute(
                        "UPDATE team_members SET email=?, phone=? WHERE id=?",
                        (new_email, new_phone, member["id"]),
                    )
                elif new_email:
                    update_conn.execute(
                        "UPDATE team_members SET email=? WHERE id=?",
                        (new_email, member["id"]),
                    )
                else:
                    update_conn.execute(
                        "UPDATE team_members SET phone=? WHERE id=?",
                        (new_phone, member["id"]),
                    )
                update_conn.commit()
                update_conn.close()
            except Exception as exc:
                log.error("Failed to update %s: %s", name, exc)
                unmatched.append(name)
                continue

        label = " + ".join(fields_added)
        matched.append((name, label))

    # Build Telegram message
    lines = [f"📋 Team contact sync {'(DRY RUN) ' if dry_run else ''}complete:"]
    lines.append(f"✅ Matched: {len(matched)}")
    for name, label in matched:
        lines.append(f"  {name} — {label}")

    if unmatched:
        lines.append(f"\n⚠️ No match found:")
        for name in unmatched:
            lines.append(f"  {name}")
        lines.append("\nUnmatched leaders need email/phone added manually in /team.")

    text = "\n".join(lines)
    log.info(text)

    try:
        _send_telegram(text)
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)


if __name__ == "__main__":
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(),
        ],
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without writing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
