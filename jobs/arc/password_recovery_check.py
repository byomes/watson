"""
jobs/arc/password_recovery_check.py — one-off, READ-ONLY diagnostic.

Recovers each active ARC reader's current plaintext password by searching
Watson's own Gmail Sent Mail for the welcome/reset email that contains it
(templates in jobs/arc/send_signup_confirmation.py). This is the only place
the plaintext ever exists — arc_readers.password_hash is a one-way hash.

Does NOT modify arc_readers, writing_room_partners, or any other table.
Does NOT send any email. Does NOT reset any password. Not a cron job —
run manually only.

Usage:
  python jobs/arc/password_recovery_check.py
"""
import email
import imaplib
import logging
import os
import re
import sqlite3
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "watson.db"
LOG_DIR = REPO_ROOT / "logs"

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SENT_FOLDER = '"[Gmail]/Sent Mail"'

WELCOME_SUBJECT = "ARC Team Login"
RESET_SUBJECT = "New Password"

PASSWORD_RE = re.compile(r"Password:\s*(\S+)")


def _connect() -> imaplib.IMAP4_SSL:
    address = os.environ["WATSON_GMAIL_ADDRESS"]
    password = os.environ["WATSON_GMAIL_APP_PASSWORD"]
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(address, password)
    mail.select(SENT_FOLDER, readonly=True)
    return mail


def _get_active_readers() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, first_name, last_name, email FROM arc_readers WHERE status = 'active'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _search(mail: imaplib.IMAP4_SSL, subject_substr: str, to_email: str) -> list:
    term = f'(HEADER SUBJECT "{subject_substr}" HEADER TO "{to_email}")'
    status, data = mail.search(None, term)
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _fetch_message(mail: imaplib.IMAP4_SSL, msg_id: bytes):
    status, data = mail.fetch(msg_id, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        return None, None
    raw = data[0][1]
    msg = email.message_from_bytes(raw)
    try:
        dt = parsedate_to_datetime(msg["Date"])
    except Exception:
        dt = None
    return msg, dt


def _extract_password(msg) -> str | None:
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            body = part.get_payload(decode=True).decode(errors="replace")
            m = PASSWORD_RE.search(body)
            if m:
                return m.group(1)
    return None


def find_reader_credential(mail: imaplib.IMAP4_SSL, email_addr: str) -> dict | None:
    """Most recent welcome/reset email to this address, by Date header."""
    candidates = []
    for template, subject in (("welcome", WELCOME_SUBJECT), ("reset", RESET_SUBJECT)):
        for msg_id in _search(mail, subject, email_addr):
            msg, dt = _fetch_message(mail, msg_id)
            if msg is None or dt is None:
                continue
            candidates.append((dt, template, msg))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[0])
    dt, template, msg = candidates[-1]
    return {"template": template, "date": dt, "password": _extract_password(msg)}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    readers = _get_active_readers()
    log.info("Loaded %d active ARC reader(s)", len(readers))

    mail = _connect()
    results = []
    for reader in readers:
        cred = find_reader_credential(mail, reader["email"])
        results.append({**reader, "credential": cred})
    mail.logout()

    matched_welcome = sum(1 for r in results if r["credential"] and r["credential"]["template"] == "welcome")
    matched_reset = sum(1 for r in results if r["credential"] and r["credential"]["template"] == "reset")
    no_match = sum(1 for r in results if not r["credential"])

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOG_DIR / f"arc_password_recovery_{datetime.now().strftime('%Y-%m-%d')}.txt"

    lines = [
        f"ARC Password Recovery Report — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Active readers: {len(readers)}",
        f"Matched (welcome): {matched_welcome}",
        f"Matched (reset): {matched_reset}",
        f"No match: {no_match}",
        "",
        "-" * 70,
    ]
    for r in results:
        name = f"{r['first_name']} {r['last_name']}"
        cred = r["credential"]
        if cred:
            date_str = cred["date"].strftime("%Y-%m-%d %H:%M:%S %Z") if cred["date"] else "unknown date"
            if cred["password"]:
                lines.append(
                    f"{name} <{r['email']}> — FOUND ({cred['template']}, {date_str})\n"
                    f"  Password: {cred['password']}"
                )
            else:
                lines.append(
                    f"{name} <{r['email']}> — matched email ({cred['template']}, {date_str}) "
                    f"but could not parse password — needs manual handling"
                )
        else:
            lines.append(f"{name} <{r['email']}> — no match found — needs manual handling")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(report_path, 0o600)
    log.info("Report written: %s", report_path)

    print()
    print("=== ARC Password Recovery Summary ===")
    print(f"Active readers checked: {len(readers)}")
    print(f"Matched via welcome email: {matched_welcome}")
    print(f"Matched via reset email:   {matched_reset}")
    print(f"No match found:            {no_match}")
    print(f"Report written to: {report_path} (chmod 600)")
    print()


if __name__ == "__main__":
    main()
