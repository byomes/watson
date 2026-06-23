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

def _migrate_columns() -> None:
    """Add parsed columns to connect_cards if not present, and create member_conflicts table."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS member_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conflict_type TEXT NOT NULL,
                existing_member_id INTEGER,
                existing_name TEXT,
                existing_email TEXT,
                new_member_id INTEGER,
                new_card_id INTEGER,
                new_name TEXT,
                new_email TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                detected_at TEXT DEFAULT (datetime('now')),
                resolved_at TEXT
            )
        """)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(connect_cards)").fetchall()}
        for col, defn in [
            ("prayer_request",        "TEXT"),
            ("next_steps",            "TEXT"),
            ("is_first_visit",        "INTEGER NOT NULL DEFAULT 0"),
            ("prayer_request_public", "INTEGER NOT NULL DEFAULT 1"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE connect_cards ADD COLUMN {col} {defn}")
                log.info("Migration: added column connect_cards.%s", col)
        conn.commit()
    finally:
        conn.close()


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
        "prayer_request_public":  1,
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

    leadership_vals = get("leadership only")
    if any(v.strip() for v in leadership_vals):
        fields["prayer_request_public"] = 0

    return fields


# ── Service date ──────────────────────────────────────────────────────────────

def _service_date(received_dt) -> str:
    d = received_dt.date() if hasattr(received_dt, "date") else received_dt
    days_back = (d.weekday() + 1) % 7
    return (d - timedelta(days=days_back)).isoformat()


# ── Member conflict detection ──────────────────────────────────────────────────

def _resolve_member(
    conn: sqlite3.Connection,
    name: str,
    email_addr: str,
    phone: str,
    svc_date: str,
) -> tuple[int, int | None]:
    """
    Find or create a member, detecting and logging conflicts.

    Returns (member_id, conflict_row_id).  If conflict_row_id is not None,
    the caller must UPDATE member_conflicts.new_card_id after inserting the card.
    """
    email_lower = email_addr.lower() if email_addr else ""
    name_key    = name.lower().strip()
    now         = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    if email_lower:
        by_email = conn.execute(
            "SELECT id, name, email FROM members WHERE LOWER(email) = ?", (email_lower,)
        ).fetchone()
        if by_email:
            if (by_email["name"] or "").lower().strip() != name_key:
                # Shared email — create a new member; log conflict
                cur_m = conn.execute(
                    "INSERT INTO members (name, email, phone, first_visit_date, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (name, email_addr, phone or None, svc_date, now),
                )
                new_member_id = cur_m.lastrowid
                cur_c = conn.execute(
                    """
                    INSERT INTO member_conflicts
                      (conflict_type, existing_member_id, existing_name, existing_email,
                       new_member_id, new_name, new_email)
                    VALUES ('shared_email', ?, ?, ?, ?, ?, ?)
                    """,
                    (by_email["id"], by_email["name"], by_email["email"],
                     new_member_id, name, email_addr),
                )
                log.warning(
                    "Conflict (shared_email): existing=%r new=%r email=%r conflict_id=%d",
                    by_email["name"], name, email_addr, cur_c.lastrowid,
                )
                return new_member_id, cur_c.lastrowid
            # Same name + same email — clean match; delegate
            return find_or_create_member(conn, name, email_addr, phone, svc_date), None

    if name_key:
        by_name = conn.execute(
            "SELECT id, name, email FROM members WHERE LOWER(TRIM(name)) = ?", (name_key,)
        ).fetchone()
        if by_name:
            existing_email = (by_name["email"] or "").lower().strip()
            if email_lower and email_lower != existing_email:
                # Same name, different email — use existing member; log conflict
                cur_c = conn.execute(
                    """
                    INSERT INTO member_conflicts
                      (conflict_type, existing_member_id, existing_name, existing_email,
                       new_member_id, new_name, new_email)
                    VALUES ('same_name_diff_email', ?, ?, ?, NULL, ?, ?)
                    """,
                    (by_name["id"], by_name["name"], by_name["email"],
                     name, email_addr),
                )
                log.warning(
                    "Conflict (same_name_diff_email): name=%r existing_email=%r new_email=%r conflict_id=%d",
                    name, by_name["email"], email_addr, cur_c.lastrowid,
                )
                return by_name["id"], cur_c.lastrowid

    return find_or_create_member(conn, name, email_addr, phone, svc_date), None


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

    member_id, conflict_row_id = _resolve_member(
        conn, name, email_addr, fields.get("phone") or "", svc_date
    )

    # connect_cards record
    conn.execute(
        """
        INSERT INTO connect_cards
          (member_id, service_date, campus, raw_text, questions_comments, email_id,
           prayer_request, next_steps, is_first_visit, prayer_request_public)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            member_id,
            svc_date,
            fields.get("campus") or "",
            html,
            fields.get("questions_comments"),
            email_id or None,
            fields.get("prayer_request"),
            ", ".join(fields.get("next_steps") or []) or None,
            1 if fields.get("is_first_visit") else 0,
            fields.get("prayer_request_public", 1),
        ),
    )
    card_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    if conflict_row_id is not None:
        conn.execute(
            "UPDATE member_conflicts SET new_card_id = ? WHERE id = ?",
            (card_id, conflict_row_id),
        )

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
            "INSERT INTO prayer_requests (member_id, card_id, request_text, date, leadership_only)"
            " VALUES (?, ?, ?, ?, ?)",
            (member_id, card_id, prayer, svc_date,
             1 if fields.get("prayer_leadership_only") else 0),
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


# ── Backfill ──────────────────────────────────────────────────────────────────

def backfill_new_columns() -> None:
    """Re-parse stored raw_text for all rows and populate prayer_request, next_steps, is_first_visit."""
    _migrate_columns()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, raw_text FROM connect_cards WHERE raw_text IS NOT NULL AND raw_text != ''"
        ).fetchall()
        updated = skipped = 0
        for row in rows:
            fields = _parse_html(row["raw_text"])
            if fields is None:
                skipped += 1
                continue
            conn.execute(
                """
                UPDATE connect_cards
                SET prayer_request        = ?,
                    next_steps            = ?,
                    is_first_visit        = ?,
                    prayer_request_public = ?
                WHERE id = ?
                """,
                (
                    fields.get("prayer_request"),
                    ", ".join(fields.get("next_steps") or []) or None,
                    1 if fields.get("is_first_visit") else 0,
                    fields.get("prayer_request_public", 1),
                    row["id"],
                ),
            )
            updated += 1
        conn.commit()
        log.info("Backfill complete: %d updated, %d skipped (parse failed).", updated, skipped)
    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    _migrate_columns()
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
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--backfill", action="store_true",
                        help="Populate prayer_request/next_steps/is_first_visit from stored raw_text")
    args = parser.parse_args()
    if args.backfill:
        backfill_new_columns()
    else:
        run(dry_run=args.dry_run)
