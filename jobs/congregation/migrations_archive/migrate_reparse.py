"""
Re-parse already-processed connect card emails and backfill congregation.db.

For each row in connect_cards that has an email_id, fetches the original email
from Gmail (searching ALL mail, not just UNSEEN), re-parses the HTML body with
the current parser, and writes any data the old plain-text parser missed:
  - raw_text and questions_comments on connect_cards
  - prayer_requests (if absent for this card)
  - next_steps (if absent for this card)

Usage:
  python3 jobs/congregation/migrate_reparse.py
  python3 jobs/congregation/migrate_reparse.py --dry-run
"""

import argparse
import email
import email.utils
from email.message import Message
import imaplib
import os
import sqlite3

from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

IMAP_HOST  = "imap.gmail.com"
IMAP_PORT  = 993
GMAIL_ADDR = os.getenv("WATSON_GMAIL_ADDRESS", "")
GMAIL_PASS = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

CAMPUS_MAP = {
    "Wilmington Campus": "Wilmington",
    "Online Campus":     "Online",
}

NEXT_STEP_SUBSTRINGS = [
    ("start following jesus",     "follow_jesus"),
    ("get baptized",              "baptism"),
    ("help growing in my faith",  "grow_faith"),
    ("become a catalyst partner", "catalyst_partner"),
    ("join a small group",        "small_group"),
    ("join a ministry team",      "ministry_team"),
]


# ── Parser (mirrors jobs/connect_cards/intake.py) ────────────────────────────

def _match_next_step(value: str) -> str | None:
    v = value.lower()
    for substr, key in NEXT_STEP_SUBSTRINGS:
        if substr in v:
            return key
    return None


def _decode_part(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _get_html_part(msg) -> str | None:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html" and "attachment" not in part.get("Content-Disposition", ""):
                return _decode_part(part)
    elif msg.get_content_type() == "text/html":
        return _decode_part(msg)
    return None


def _parse_html(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")

    div = soup.find("div", attrs={"role": "module-content", "bgcolor": "#ffffff"})
    if div is None:
        div = soup

    if not div.find("b", string=lambda t: t and "Where did you attend" in t):
        return None

    raw: dict[str, list[str]] = {}
    for b_tag in div.find_all("b"):
        label = b_tag.get_text(strip=True)
        if not label:
            continue
        values: list[str] = []
        for sibling in b_tag.next_siblings:
            if getattr(sibling, "name", None) == "b":
                break
            text = (
                sibling.get_text(strip=True)
                if hasattr(sibling, "get_text")
                else str(sibling).strip()
            )
            if text:
                values.append(text)
        raw[label] = values

    def get(substring: str) -> list[str]:
        sub = substring.lower()
        for label, vals in raw.items():
            if sub in label.lower():
                return vals
        return []

    def get_one(substring: str) -> str:
        vals = get(substring)
        return vals[0] if vals else ""

    fields: dict = {
        "campus":                 None,
        "first_name":             "",
        "last_name":              "",
        "email":                  "",
        "phone":                  "",
        "questions_comments":     None,
        "next_steps":             [],
        "is_first_visit":         False,
        "prayer_leadership_only": False,
        "prayer_request":         None,
    }

    campus_raw = get_one("where did you attend")
    fields["campus"] = CAMPUS_MAP.get(campus_raw, campus_raw) if campus_raw else None

    fields["first_name"] = get_one("first name")
    fields["last_name"]  = get_one("last name")
    fields["email"]      = get_one("email")
    fields["phone"]      = get_one("phone number")

    qc = get_one("question/comment")
    fields["questions_comments"] = qc or None

    ns_values = get("next step")
    fields["next_steps"] = [v for v in ns_values if _match_next_step(v)]

    fv_vals = get("first sunday")
    if fv_vals:
        fields["is_first_visit"] = any("yes" in v.lower() for v in fv_vals)

    prayer_vals = get("pray for you")
    prayer_parts = []
    for v in prayer_vals:
        if "restrict my request to leadership only" in v.lower():
            fields["prayer_leadership_only"] = True
        else:
            prayer_parts.append(v)
    fields["prayer_request"] = " ".join(prayer_parts).strip() or None

    return fields


# ── IMAP fetch ────────────────────────────────────────────────────────────────

def _fetch_email_by_message_id(mail: imaplib.IMAP4_SSL, message_id: str) -> Message | None:
    """Search ALL mail for a specific Message-ID header and return the parsed message."""
    # Strip surrounding angle brackets if present; add them for the search
    mid = message_id.strip()
    if not mid.startswith("<"):
        mid = f"<{mid}>"

    # Search across all mail (not just UNSEEN) in the connect-cards folder
    status, data = mail.search(None, f'HEADER Message-ID "{mid}"')
    if status != "OK" or not data or not data[0]:
        return None

    ids = data[0].split()
    if not ids:
        return None

    # Use the first (should be only) match
    status, msg_data = mail.fetch(ids[0], "(RFC822)")
    if status != "OK" or not msg_data or not msg_data[0]:
        return None

    return email.message_from_bytes(msg_data[0][1])


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    if not GMAIL_ADDR or not GMAIL_PASS:
        print("ERROR: WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")
        return

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(GMAIL_ADDR, GMAIL_PASS)
    except Exception as exc:
        print(f"ERROR: IMAP login failed: {exc}")
        return

    try:
        mail.select('"connect-cards"', readonly=True)
    except Exception as exc:
        print(f"ERROR: Could not select connect-cards folder: {exc}")
        mail.logout()
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        cards = conn.execute(
            "SELECT id, email_id, member_id, service_date FROM connect_cards ORDER BY id"
        ).fetchall()

        print(f"Found {len(cards)} connect card(s) to reprocess.\n")

        stats = {
            "cards_updated":    0,
            "questions_found":  0,
            "prayers_added":    0,
            "next_steps_added": 0,
        }

        for card in cards:
            card_id     = card["id"]
            email_id    = (card["email_id"] or "").strip()
            member_id   = card["member_id"]
            service_date = card["service_date"]

            # Resolve member name for display
            member_row = conn.execute("SELECT name FROM members WHERE id = ?", (member_id,)).fetchone()
            name = member_row["name"] if member_row else f"member_id={member_id}"

            if not email_id:
                print(f"  [{card_id:3d}] {name!r:30s}  SKIP (no email_id stored)")
                continue

            msg = _fetch_email_by_message_id(mail, email_id)
            if msg is None:
                print(f"  [{card_id:3d}] {name!r:30s}  SKIP (email not found in Gmail)")
                continue

            html = _get_html_part(msg)
            if not html:
                print(f"  [{card_id:3d}] {name!r:30s}  SKIP (no HTML part in email)")
                continue

            fields = _parse_html(html)
            if fields is None:
                print(f"  [{card_id:3d}] {name!r:30s}  SKIP (HTML parse failed)")
                continue

            # ── Determine what's new ──────────────────────────────────────
            has_questions  = bool(fields.get("questions_comments"))
            prayer_text    = fields.get("prayer_request")
            new_next_steps = [
                _match_next_step(v) for v in (fields.get("next_steps") or [])
                if _match_next_step(v)
            ]

            existing_prayer = conn.execute(
                "SELECT id FROM prayer_requests WHERE card_id = ?", (card_id,)
            ).fetchone()
            existing_steps = conn.execute(
                "SELECT step FROM next_steps WHERE card_id = ?", (card_id,)
            ).fetchall()
            existing_step_keys = {r["step"] for r in existing_steps}

            steps_to_add = [s for s in new_next_steps if s not in existing_step_keys]
            add_prayer   = bool(prayer_text and not existing_prayer)

            print(
                f"  [{card_id:3d}] {name!r:30s}  "
                f"questions={'Y' if has_questions else 'n'}  "
                f"prayer={'Y' if add_prayer else ('exists' if existing_prayer and prayer_text else 'n')}  "
                f"next_steps={len(steps_to_add)} new"
            )

            if dry_run:
                # Still tally for summary
                if has_questions:
                    stats["questions_found"] += 1
                if add_prayer:
                    stats["prayers_added"] += 1
                stats["next_steps_added"] += len(steps_to_add)
                stats["cards_updated"] += 1
                continue

            # ── Apply updates ─────────────────────────────────────────────
            conn.execute(
                """
                UPDATE connect_cards
                SET raw_text           = ?,
                    questions_comments = COALESCE(NULLIF(questions_comments, ''), ?)
                WHERE id = ?
                """,
                (html, fields.get("questions_comments"), card_id),
            )

            if add_prayer:
                conn.execute(
                    "INSERT INTO prayer_requests (member_id, card_id, request_text, date) VALUES (?, ?, ?, ?)",
                    (member_id, card_id, prayer_text, service_date),
                )
                stats["prayers_added"] += 1

            for step_key in steps_to_add:
                conn.execute(
                    "INSERT INTO next_steps (member_id, card_id, step, date) VALUES (?, ?, ?, ?)",
                    (member_id, card_id, step_key, service_date),
                )
                stats["next_steps_added"] += 1

            if has_questions:
                stats["questions_found"] += 1

            stats["cards_updated"] += 1
            conn.commit()

        print()
        if dry_run:
            print("── Reparse summary (dry run) ─────────────────────────")
        else:
            print("── Reparse summary ───────────────────────────────────")
        print(f"  Cards updated:        {stats['cards_updated']}")
        print(f"  Questions found:      {stats['questions_found']}")
        print(f"  Prayer requests added:{stats['prayers_added']}")
        print(f"  Next steps added:     {stats['next_steps_added']}")
        print("──────────────────────────────────────────────────────")

    finally:
        conn.close()
        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-parse connect card emails into congregation.db")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change; no DB writes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
