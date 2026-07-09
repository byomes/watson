"""
Member conflict report — sends pending member_conflicts to Telegram for review.

Each conflict is sent as a separate Telegram message with inline buttons.
Watson never modifies any data until a button is tapped in bot.py.

Cron (Sunday 5pm):
  0 17 * * 0  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python \
    /home/billyomes/watson/jobs/connect_cards/conflict_report.py \
    >> /home/billyomes/watson/logs/conflict_report.log 2>&1

Usage:
  python3 /home/billyomes/watson/jobs/connect_cards/conflict_report.py
"""

import logging
import os
import sqlite3
import time

import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [conflict_report] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CONG_DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

# Import via config.settings so WATSON_BOT_TOKEN / TELEGRAM_BOT_TOKEN both work
from config.settings import WATSON_BOT_TOKEN, WATSON_CHAT_ID
from core.vacation import vacation_gate

CONFLICT_LABELS = {
    "shared_email": "Shared Email",
    "same_name_diff_email": "Name Match, Different Email",
    "fuzzy_duplicate": "Possible Duplicate (Fuzzy Match)",
}

NAME_SCORE_THRESHOLD = 85
DOMAIN_DISTANCE_THRESHOLD = 2


def _fetch_active_members(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, email FROM members WHERE member_status = 'active'"
    ).fetchall()


def _existing_conflict_pairs(conn: sqlite3.Connection) -> set[tuple[int, int]]:
    """Sorted (member_id, member_id) pairs already logged in member_conflicts, any status."""
    pairs = set()
    for row in conn.execute(
        "SELECT existing_member_id, new_member_id FROM member_conflicts "
        "WHERE existing_member_id IS NOT NULL AND new_member_id IS NOT NULL"
    ).fetchall():
        a, b = row["existing_member_id"], row["new_member_id"]
        pairs.add((a, b) if a < b else (b, a))
    return pairs


def _fuzzy_reason(name_a: str, name_b: str, email_a: str | None, email_b: str | None) -> str | None:
    """Return a human-readable reason if the pair looks like a duplicate, else None."""
    reasons = []

    name_score = fuzz.token_sort_ratio(name_a, name_b)
    if name_score >= NAME_SCORE_THRESHOLD:
        reasons.append(f"name similarity {name_score:.0f}%")

    if email_a and email_b and "@" in email_a and "@" in email_b:
        local_a, domain_a = email_a.lower().split("@", 1)
        local_b, domain_b = email_b.lower().split("@", 1)
        if local_a == local_b:
            domain_distance = Levenshtein.distance(domain_a, domain_b)
            if domain_distance == 0:
                reasons.append("email addresses match (case-insensitive)")
            elif domain_distance <= DOMAIN_DISTANCE_THRESHOLD:
                reasons.append(f"email domain differs by {domain_distance} character(s)")

    return "; ".join(reasons) if reasons else None


def find_fuzzy_duplicates(conn: sqlite3.Connection) -> list[dict]:
    """
    Pairwise-scan active members for likely duplicates not already logged in
    member_conflicts (any status, any resolution). Read-only — does not insert.
    """
    members = _fetch_active_members(conn)
    seen_pairs = _existing_conflict_pairs(conn)

    candidates = []
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            a, b = members[i], members[j]
            pair = (a["id"], b["id"]) if a["id"] < b["id"] else (b["id"], a["id"])
            if pair in seen_pairs:
                continue

            older, newer = (a, b) if a["id"] == pair[0] else (b, a)
            reason = _fuzzy_reason(older["name"] or "", newer["name"] or "", older["email"], newer["email"])
            if reason:
                candidates.append({
                    "existing_member_id": older["id"],
                    "existing_name": older["name"],
                    "existing_email": older["email"],
                    "new_member_id": newer["id"],
                    "new_name": newer["name"],
                    "new_email": newer["email"],
                    "reason": reason,
                })
    return candidates


def insert_fuzzy_duplicates(conn: sqlite3.Connection, candidates: list[dict]) -> int:
    """Insert candidate fuzzy_duplicate rows into member_conflicts. Returns count inserted."""
    for c in candidates:
        conn.execute(
            """
            INSERT INTO member_conflicts
              (conflict_type, existing_member_id, existing_name, existing_email,
               new_member_id, new_name, new_email, notes)
            VALUES ('fuzzy_duplicate', ?, ?, ?, ?, ?, ?, ?)
            """,
            (c["existing_member_id"], c["existing_name"], c["existing_email"],
             c["new_member_id"], c["new_name"], c["new_email"], c["reason"]),
        )
    conn.commit()
    return len(candidates)


def _send(text: str, keyboard: list | None = None) -> None:
    if vacation_gate("normal", "jobs.connect_cards.conflict_report", text):
        return
    payload: dict = {"chat_id": WATSON_CHAT_ID, "text": text}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    resp = requests.post(
        f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()


def run() -> None:
    if not WATSON_BOT_TOKEN or not WATSON_CHAT_ID:
        log.error("WATSON_BOT_TOKEN and WATSON_CHAT_ID must be set.")
        return

    conn = sqlite3.connect(CONG_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        candidates = find_fuzzy_duplicates(conn)
        if candidates:
            insert_fuzzy_duplicates(conn, candidates)
            log.info("Fuzzy duplicate pass flagged %d new candidate(s).", len(candidates))

        rows = conn.execute(
            "SELECT * FROM member_conflicts WHERE status = 'pending' ORDER BY detected_at ASC"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        _send("✅ No member conflicts to review this week.")
        log.info("No pending conflicts.")
        return

    log.info("Sending %d pending conflict(s) to Telegram.", len(rows))

    for row in rows:
        cid = row["id"]

        conflict_label = CONFLICT_LABELS.get(row["conflict_type"], row["conflict_type"])
        text = (
            f"⚠️ Member Conflict — {conflict_label}\n\n"
            f"OLD: {row['existing_name']} | {row['existing_email'] or 'no email'}\n"
            f"NEW: {row['new_name']} | {row['new_email'] or 'no email'}\n"
        )
        if row["notes"]:
            text += f"Reason: {row['notes']}\n"
        text += "\nWhich record should be canonical?"
        keyboard = [[
            {"text": "Keep Old ✓",  "callback_data": f"merge_old_{cid}"},
            {"text": "Keep New ✓",  "callback_data": f"merge_new_{cid}"},
            {"text": "Skip",        "callback_data": f"skip_{cid}"},
        ]]

        try:
            _send(text, keyboard)
            log.info("Sent conflict id=%d type=%s", cid, row["conflict_type"])
        except Exception as exc:
            log.error("Failed to send conflict id=%d: %s", cid, exc)

        time.sleep(2)


if __name__ == "__main__":
    run()
