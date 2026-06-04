"""
Connect card Gmail intake parser.

Polls Watson's Gmail inbox (IMAP) for connect card submission emails from
no-reply@snappages.com, parses each one, inserts into connect_cards, updates
congregation records, marks email as read.

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

DB_PATH = os.path.expanduser("~/watson/data/watson.db")

EXPECTED_SENDER     = "no-reply@snappages.com"
EXPECTED_SUBJECT    = "Catalyst Connect Card - Submission"
EXPECTED_FIRST_LINE = "http://snappages.com"

CAMPUS_MAP = {
    "Wilmington Campus": "Wilmington",
    "Online Campus":     "Online",
}

NEXT_STEP_VALUES = {
    "I want to start following Jesus",
    "I want to get baptized",
    "I want help growing in my faith",
    "I want to become a Catalyst Partner",
    "I want to join a small group",
    "I want to join a ministry team",
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
    """Return plain-text body; strips HTML if no text/plain part found."""
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
    """
    Parse connect card body using regex extraction between known label boundaries.
    Returns None if the first non-empty line is not the Subsplash URL identifier.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    first_non_empty = next((l.strip() for l in text.split("\n") if l.strip()), "")
    if first_non_empty != EXPECTED_FIRST_LINE:
        return None

    # Strip copyright footer
    copyright_idx = text.find("© 2022 Subsplash")
    if copyright_idx != -1:
        text = text[:copyright_idx]

    # Content starts after the first blank line
    blank = re.search(r"\n\n+", text)
    body = text[blank.end():] if blank else text

    # Collapse remaining newlines — the form body is one run-on string
    body = re.sub(r"[\r\n]+", " ", body).strip()

    fields = {
        "campus":                None,
        "first_name":            "",
        "last_name":             "",
        "email":                 "",
        "phone":                 "",
        "question_comment":      None,
        "next_steps":            [],
        "is_first_visit":        False,
        "prayer_leadership_only": False,
        "prayer_request":        None,
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
        fields["question_comment"] = val

    ns_raw = between("Are you ready to take a Next Step this week?", "Is this your first Sunday with us?")
    if ns_raw is not None:
        parts = [s.strip() for s in ns_raw.split(",")]
        fields["next_steps"] = [p for p in parts if p in NEXT_STEP_VALUES]

    fv_raw = between("Is this your first Sunday with us?", "How can we pray for you this week?")
    if fv_raw is None:
        fv_raw = after("Is this your first Sunday with us?")
    if fv_raw is not None:
        fields["is_first_visit"] = "Yes it is!" in fv_raw

    prayer_raw = after("How can we pray for you this week?")
    if prayer_raw:
        prayer_raw = prayer_raw.replace("Please restrict my request to leadership only.", "").strip()
        fields["prayer_request"] = prayer_raw or None

    fields["next_steps"] = ", ".join(fields["next_steps"]) or None
    return fields


# ── Service date ──────────────────────────────────────────────────────────────

def _service_date(received_dt) -> str:
    """Most recent Sunday on or before the received date."""
    d = received_dt.date() if hasattr(received_dt, "date") else received_dt
    days_back = (d.weekday() + 1) % 7
    return (d - timedelta(days=days_back)).isoformat()


# ── Congregation upsert ───────────────────────────────────────────────────────

def _upsert_congregation(conn, fields: dict, service_date: str, dry_run: bool) -> tuple:
    """Look up congregation by email then name; update or create. Returns (id, is_new)."""
    email_addr = (fields.get("email") or "").strip()
    name = f"{fields.get('first_name', '')} {fields.get('last_name', '')}".strip()
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    cong = None
    if email_addr:
        cong = conn.execute(
            "SELECT id FROM congregation WHERE email = ?", (email_addr,)
        ).fetchone()
    if cong is None and name:
        cong = conn.execute(
            "SELECT id FROM congregation WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()

    if cong:
        if not dry_run:
            conn.execute(
                """
                UPDATE congregation
                SET last_seen  = ?,
                    email      = CASE WHEN TRIM(COALESCE(email, '')) = '' THEN ? ELSE email END,
                    phone      = CASE WHEN TRIM(COALESCE(phone, '')) = '' THEN ? ELSE phone END,
                    updated_at = ?
                WHERE id = ?
                """,
                (service_date, email_addr, fields.get("phone") or "", now, cong["id"]),
            )
        return cong["id"], False

    status = "first-time visitor" if fields.get("is_first_visit") else "regular"
    cong_id = -1
    if not dry_run:
        conn.execute(
            """
            INSERT INTO congregation
              (name, email, phone, status, campus, first_seen, last_seen, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                email_addr,
                fields.get("phone") or "",
                status,
                fields.get("campus") or "",
                service_date,
                service_date,
                now,
                now,
            ),
        )
        cong_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return cong_id, True


# ── Process one email ─────────────────────────────────────────────────────────

def _process_email(msg, dry_run: bool, conn) -> bool:
    """Parse and insert one email. Returns True if inserted (or would insert in dry-run)."""

    # Exact sender check
    from_addr = email.utils.parseaddr(msg.get("From", ""))[1].lower()
    if from_addr != EXPECTED_SENDER:
        log.info("Skipped (sender mismatch): %r", from_addr)
        return False

    # Exact subject check (decode encoded headers)
    raw_subject = msg.get("Subject", "")
    parts = email.header.decode_header(raw_subject)
    subject = "".join(
        p.decode(c or "utf-8") if isinstance(p, bytes) else p
        for p, c in parts
    )
    if subject != EXPECTED_SUBJECT:
        log.info("Skipped (subject mismatch): %r", subject)
        return False

    # Body extraction and first-line check
    body = _get_body(msg)
    fields = _parse_body(body)
    if fields is None:
        log.warning("Skipped (first-line mismatch or parse failed): subject=%r", subject)
        return False

    # Received date → service_date
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

    # Duplicate check (by email+date, fallback to name+date)
    dup = conn.execute(
        "SELECT id FROM connect_cards WHERE email = ? AND service_date = ?",
        (email_addr, svc_date),
    ).fetchone()
    if not dup and not email_addr:
        dup = conn.execute(
            "SELECT id FROM connect_cards WHERE first_name = ? AND last_name = ? AND service_date = ?",
            (fields["first_name"], fields["last_name"], svc_date),
        ).fetchone()
    if dup:
        log.info("Skipped duplicate: email=%r service_date=%s", email_addr, svc_date)
        return False

    # Congregation upsert
    cong_id, is_new = _upsert_congregation(conn, fields, svc_date, dry_run)
    if is_new:
        log.info("New congregation record: %r", name)
    else:
        log.info("Updated congregation record: %r", name)

    prayer        = fields.get("prayer_request")
    prayer_public = 1 if (prayer and not fields.get("prayer_leadership_only")) else 0

    if not dry_run:
        conn.execute(
            """
            INSERT INTO connect_cards
              (congregation_id, first_name, last_name, email, phone, campus,
               service_date, is_first_visit, next_steps, question_comment,
               prayer_request, prayer_request_public, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                cong_id,
                fields["first_name"],
                fields["last_name"],
                email_addr,
                fields.get("phone") or "",
                fields.get("campus") or "",
                svc_date,
                1 if fields.get("is_first_visit") else 0,
                fields.get("next_steps"),
                fields.get("question_comment"),
                prayer,
                prayer_public,
            ),
        )
        conn.commit()
        log.info("Inserted connect card: %r service_date=%s", name, svc_date)
    else:
        log.info("[dry-run] Would insert: %r service_date=%s", name, svc_date)

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
        mail.select("INBOX")
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and log; do not insert into DB or mark emails as read.",
    )
    run(dry_run=parser.parse_args().dry_run)
