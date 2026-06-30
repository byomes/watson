"""
Campus classifier — assigns campus_preference to each active member based on
their last 8 weeks (56 days) of connect card history.

Classification order (applied in sequence):
  1. online_count >= 2 AND wilm_count >= 2  → 'Hybrid'
  2. online_count >= 5                       → 'Online'
  3. wilm_count >= 5                         → 'Wilmington'
  4. whichever count is higher               → that campus; tied → 'Wilmington'
  5. no cards in last 8 weeks                → 'Wilmington' (defaulted)

Only active members with member_status IS NULL or 'active' are updated.

Usage:
  PYTHONPATH=/home/billyomes/watson python jobs/connect_cards/campus_classifier.py

Cron (Monday 5:45am):
  45 5 * * 1 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/connect_cards/campus_classifier.py \
    >> /home/billyomes/watson/logs/campus_classifier.log 2>&1
"""

import logging
import os
import sqlite3
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [campus_classifier] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)


def _classify(online_count: int, wilm_count: int) -> tuple[str, bool]:
    """Return (campus_preference, defaulted). defaulted=True when no cards in window."""
    if online_count == 0 and wilm_count == 0:
        return "Wilmington", True
    if online_count >= 2 and wilm_count >= 2:
        return "Hybrid", False
    if online_count >= 5:
        return "Online", False
    if wilm_count >= 5:
        return "Wilmington", False
    return ("Online" if online_count > wilm_count else "Wilmington"), False


def run() -> None:
    cutoff = (date.today() - timedelta(days=56)).isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT m.id,
                   COALESCE(SUM(CASE WHEN cc.campus = 'Online'     THEN 1 ELSE 0 END), 0) AS online_count,
                   COALESCE(SUM(CASE WHEN cc.campus = 'Wilmington' THEN 1 ELSE 0 END), 0) AS wilm_count
            FROM members m
            LEFT JOIN connect_cards cc
              ON cc.member_id = m.id
             AND cc.service_date >= ?
            WHERE m.active = 1
              AND (m.member_status IS NULL OR m.member_status = 'active')
            GROUP BY m.id
            """,
            (cutoff,),
        ).fetchall()

        assignments: dict[str, list[int]] = {"Wilmington": [], "Online": [], "Hybrid": []}
        defaulted = 0

        for row in rows:
            campus, was_defaulted = _classify(row["online_count"], row["wilm_count"])
            assignments[campus].append(row["id"])
            if was_defaulted:
                defaulted += 1

        for campus, ids in assignments.items():
            if ids:
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE members SET campus_preference = ? WHERE id IN ({placeholders})",
                    [campus, *ids],
                )
                log.info("Set %d members → %s", len(ids), campus)

        conn.commit()
    finally:
        conn.close()

    wilm   = len(assignments["Wilmington"])
    online = len(assignments["Online"])
    hybrid = len(assignments["Hybrid"])

    log.info(
        "Done — %d Wilmington, %d Online, %d Hybrid, %d defaulted",
        wilm, online, hybrid, defaulted,
    )
    _send_telegram(
        f"📍 Campus classification complete — {wilm} Wilmington, {online} Online, "
        f"{hybrid} Hybrid, {defaulted} defaulted to Wilmington"
    )


if __name__ == "__main__":
    run()
