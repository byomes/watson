"""
Connect card Gmail intake parser.

Polls Watson's Gmail inbox (IMAP) for connect card submission emails from
no-reply@snappages.com, parses each one, inserts into congregation.db,
marks email as read.

Configuration:
  WATSON_GMAIL_ADDRESS      Gmail login address
  WATSON_GMAIL_APP_PASSWORD Gmail app password (not account password)

Usage:
  python3 -m jobs.connect_cards.intake
  python3 -m jobs.connect_cards.intake --dry-run

Cron (every 30 minutes):
  */30 * * * * cd /home/billyomes/watson && PYTHONPATH=/home/billyomes/watson \\
    python3 -m jobs.connect_cards.intake >> /home/billyomes/watson/logs/connect_cards.log 2>&1
"""

import argparse
import email
import email.header
import email.utils
import imaplib
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from jobs.congregation.member_match import find_or_create_member

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [intake] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
GMAIL_ADDR = os.getenv("WATSON_GMAIL_ADDRESS", "")
GMAIL_PASS = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

EXPECTED_SENDER     = "no-reply@snappages.com"
EXPECTED_SUBJECT    = "Catalyst Connect Card - Submission"
EXPECTED_FIRST_LINE = "http://snappages.com"

CAMPUS_MAP = {
    "Wilmington Campus": "Wilmington",
    "Online Campus":     "Online",
}

NEXT_STEP_SUBSTRINGS = [
    ("start following jesus",    "follow_jesus"),
    ("get baptized",             "baptism"),
    ("help growing in my faith", "grow_faith"),
    ("become a catalyst partner","catalyst_partner"),
    ("join a small group",       "small_group"),
    ("join a ministry team",     "ministry_team"),
]

def _match_next_step(value: str) -> str | None:
    v = value.lower()
    for substr, key in NEXT_STEP_SUBSTRINGS:
        if substr in v:
            return key
    return None


# ── HTML extraction ───────────────────────────────────────────────────────────

def _decode_part(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _get_html_part(msg) -> str | None:
    """Return the text/html MIME part, or None if not found."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html" and "attachment" not in part.get("Content-Disposition", ""):
                return _decode_part(part)
    elif msg.get_content_type() == "text/html":
        return _decode_part(msg)
    return None


# ── HTML parser ───────────────────────────────────────────────────────────────

def _parse_html(html: str) -> dict | None:
    """
    Parse a Subsplash connect card HTML email.
    Structure: <b>Label</b><br>Value<br><br>  repeated inside the content div.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find the form content div; fall back to the full body if not found
    div = soup.find("div", attrs={"role": "module-content", "bgcolor": "#ffffff"})
    if div is None:
        div = soup

    # Sanity check — must look like a connect card
    if not div.find("b", string=lambda t: t and "Where did you attend" in t):
        return None

    # Build label → [values] map by walking siblings after each <b> tag
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
        """Case-insensitive substring match on label; returns value list or []."""
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


# ── Service date ──────────────────────────────────────────────────────────────

def _service_date(received_dt) -> str:
    d = received_dt.date() if hasattr(received_dt, "date") else received_dt
    days_back = (d.weekday() + 1) % 7
    return (d - timedelta(days=days_back)).isoformat()


# ── Process one email ─────────────────────────────────────────────────────────

def _process_email(msg, dry_run: bool, conn: sqlite3.Connection) -> bool:
    from_addr = email.utils.parseaddr(msg.get("From", ""))[1].lower()
    if from_addr != EXPECTED_SENDER:
        log.info("Skipped (sender mismatch): %r", from_addr)
        return False

    raw_subject = msg.get("Subject", "")
    parts = email.header.decode_header(raw_subject)
    subject = "".join(
        p.decode(c or "utf-8") if isinstance(p, bytes) else p
        for p, c in parts
    )
    if subject != EXPECTED_SUBJECT:
        log.info("Skipped (subject mismatch): %r", subject)
        return False

    html = _get_html_part(msg)
    if not html:
        log.warning("Skipped (no HTML part): subject=%r", subject)
        return False
    fields = _parse_html(html)
    if fields is None:
        log.warning("Skipped (parse failed): subject=%r", subject)
        return False

    email_id = msg.get("Message-ID", "").strip()

    if email_id:
        existing = conn.execute(
            "SELECT id FROM connect_cards WHERE email_id = ?", (email_id,)
        ).fetchone()
        if existing:
            log.info("Skipped (duplicate): email_id=%r", email_id)
            return False

    try:
        received_dt = email.utils.parsedate_to_datetime(msg.get("Date", ""))
    except Exception:
        received_dt = datetime.utcnow()
    svc_date = _service_date(received_dt)

    name       = f"{fields['first_name']} {fields['last_name']}".strip()
    email_addr = (fields.get("email") or "").strip()

    log.info(
        "Processing: name=%r campus=%r service_date=%s first_visit=%s email=%r",
        name, fields.get("campus"), svc_date, fields.get("is_first_visit"), email_addr,
    )

    if dry_run:
        log.info("[dry-run] Would insert: %r service_date=%s", name, svc_date)
        return True

    member_id = find_or_create_member(conn, name, email_addr, fields.get("phone") or "", svc_date)

    # connect_cards record
    conn.execute(
        """
        INSERT INTO connect_cards
          (member_id, service_date, campus, raw_text, questions_comments, email_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            member_id,
            svc_date,
            fields.get("campus") or "",
            html,
            fields.get("questions_comments"),
            email_id or None,
        ),
    )
    card_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # attendance (one row per card)
    conn.execute(
        """
        INSERT INTO attendance (member_id, service_date, campus, card_id)
        VALUES (?, ?, ?, ?)
        """,
        (member_id, svc_date, fields.get("campus") or "", card_id),
    )

    # next_steps
    for ns_label in fields.get("next_steps") or []:
        step_key = _match_next_step(ns_label)
        if step_key:
            conn.execute(
                "INSERT INTO next_steps (member_id, card_id, step, date) VALUES (?, ?, ?, ?)",
                (member_id, card_id, step_key, svc_date),
            )

    # prayer_request
    prayer = fields.get("prayer_request")
    if prayer:
        conn.execute(
            "INSERT INTO prayer_requests (member_id, card_id, request_text, date) VALUES (?, ?, ?, ?)",
            (member_id, card_id, prayer, svc_date),
        )

    # follow_up (first-time visitor flag)
    if fields.get("is_first_visit"):
        conn.execute(
            "INSERT INTO follow_ups (member_id, card_id, note) VALUES (?, ?, ?)",
            (member_id, card_id, "First-time visitor"),
        )

    conn.commit()
    log.info("Inserted (new): name=%r service_date=%s card_id=%d email_id=%r", name, svc_date, card_id, email_id)
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    if not GMAIL_ADDR or not GMAIL_PASS:
        log.error("WATSON_GMAIL_ADDRESS and WATSON_GMAIL_APP_PASSWORD must be set.")
        return

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(GMAIL_ADDR, GMAIL_PASS)
    except Exception as exc:
        log.error("IMAP login failed: %s", exc)
        return

    try:
        mail.select('"connect-cards"')
        status, data = mail.search(
            None,
            f'(FROM "{EXPECTED_SENDER}" SUBJECT "{EXPECTED_SUBJECT}")',
        )
        if status != "OK":
            log.error("IMAP search failed: %s", status)
            return

        ids = data[0].split()
        log.info("Found %d candidate email(s).", len(ids))
        if not ids:
            return

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        processed = inserted = 0

        try:
            for eid in ids:
                status, msg_data = mail.fetch(eid, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    log.warning("Failed to fetch email id %s", eid)
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                try:
                    result = _process_email(msg, dry_run, conn)
                except Exception as exc:
                    log.exception("Error processing email id %s: %s", eid, exc)
                    result = False

                processed += 1
                if result:
                    inserted += 1
                    if not dry_run:
                        mail.store(eid, "+FLAGS", "\\Seen")
                        log.info("Marked email %s as read.", eid)
        finally:
            conn.close()

        log.info("Done: %d processed, %d inserted.", processed, inserted)

    finally:
        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Poll Gmail for connect card submissions.")
    parser.add_argument("--dry-run", action="store_true")
    run(dry_run=parser.parse_args().dry_run)
