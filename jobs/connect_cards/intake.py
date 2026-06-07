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
import re
import sqlite3
from datetime import datetime, timedelta
from html.parser import HTMLParser

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

NEXT_STEP_MAP = {
    "I want to start following Jesus":    "follow_jesus",
    "I want to get baptized":             "baptism",
    "I want help growing in my faith":    "grow_faith",
    "I want to become a Catalyst Partner": "catalyst_partner",
    "I want to join a small group":       "small_group",
    "I want to join a ministry team":     "ministry_team",
}


# ── HTML → plain text ─────────────────────────────────────────────────────────

class _HtmlToText(HTMLParser):
    _BLOCK = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
    _SKIP  = {"style", "script"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _strip_html(html_text: str) -> str:
    p = _HtmlToText()
    try:
        p.feed(html_text)
    except Exception:
        pass
    return p.get_text()


# ── Email body extraction ─────────────────────────────────────────────────────

def _decode_part(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _get_body(msg) -> str:
    plain = None
    html_body = None

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = part.get("Content-Disposition", "")
            if "attachment" in cd:
                continue
            if ct == "text/plain" and plain is None:
                plain = _decode_part(part)
            elif ct == "text/html" and html_body is None:
                html_body = _decode_part(part)
    else:
        text = _decode_part(msg)
        if msg.get_content_type() == "text/html":
            html_body = text
        else:
            plain = text

    if plain is not None:
        return plain
    if html_body is not None:
        return _strip_html(html_body)
    return ""


# ── Body parser ───────────────────────────────────────────────────────────────

def _parse_body(text: str) -> dict | None:
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    if "http://snappages.com" not in text or "Where did you attend with us?" not in text:
        return None

    copyright_idx = text.find("© 2022")
    if copyright_idx != -1:
        text = text[:copyright_idx]

    blank = re.search(r"\n\n+", text)
    body = text[blank.end():] if blank else text
    body = re.sub(r"[\r\n]+", " ", body).strip()

    fields = {
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

    if "Please restrict my request to leadership only." in body:
        fields["prayer_leadership_only"] = True

    def between(start_label, end_label):
        m = re.search(re.escape(start_label) + r"(.*?)" + re.escape(end_label), body, re.DOTALL)
        return m.group(1).strip() if m else None

    def after(start_label):
        m = re.search(re.escape(start_label) + r"(.*?)$", body, re.DOTALL)
        return m.group(1).strip() if m else None

    campus_raw = between("Where did you attend with us?", "First Name")
    if campus_raw is not None:
        fields["campus"] = CAMPUS_MAP.get(campus_raw, campus_raw)

    val = between("First Name", "Last Name")
    if val is not None:
        fields["first_name"] = val

    val = between("Last Name", "Email")
    if val is not None:
        fields["last_name"] = val

    val = between("Email", "Phone Number")
    if val is not None:
        fields["email"] = val

    val = between("Phone Number", "Do you have a question/comment?")
    if val is not None:
        fields["phone"] = val

    val = between("Do you have a question/comment?", "Are you ready to take a Next Step this week?")
    if val:
        fields["questions_comments"] = val

    ns_raw = between("Are you ready to take a Next Step this week?", "Is this your first Sunday with us?")
    if ns_raw is not None:
        parts = [s.strip() for s in ns_raw.split(",")]
        fields["next_steps"] = [p for p in parts if p in NEXT_STEP_MAP]

    fv_raw = between("Is this your first Sunday with us?", "How can we pray for you this week?")
    if fv_raw is None:
        fv_raw = after("Is this your first Sunday with us?")
    if fv_raw is not None:
        fields["is_first_visit"] = "Yes it is!" in fv_raw

    prayer_raw = after("How can we pray for you this week?")
    if prayer_raw:
        prayer_raw = prayer_raw.replace("Please restrict my request to leadership only.", "").strip()
        fields["prayer_request"] = prayer_raw or None

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

    body = _get_body(msg)
    fields = _parse_body(body)
    if fields is None:
        log.warning("Skipped (parse failed): subject=%r", subject)
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
          (member_id, service_date, campus, raw_text, questions_comments)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            member_id,
            svc_date,
            fields.get("campus") or "",
            body,
            fields.get("questions_comments"),
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
        step_key = NEXT_STEP_MAP.get(ns_label)
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
    log.info("Inserted: %r service_date=%s card_id=%d", name, svc_date, card_id)
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
            f'(UNSEEN FROM "{EXPECTED_SENDER}" SUBJECT "{EXPECTED_SUBJECT}")',
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
