"""
jobs/arc/send_manuscript_access_batch.py — one-off manuscript-access reminder
send to every active ARC reader (excludes test@test.com), with an open-
tracking pixel embedded in the HTML body.

Passwords come from the most recent logs/arc_password_recovery_*.txt report
(jobs/arc/password_recovery_check.py). As part of a LIVE run only, this also
backfills arc_readers.plaintext_password_recovery for each reader actually
sent to — the recovery report becomes redundant once that column is
populated (see logs/arc_password_recovery_*.txt retirement note once
confirmed).

Safe by default: --dry-run defaults to true. Only --dry-run=false sends
email, inserts arc_email_opens rows, or writes plaintext_password_recovery.
Any active (non-test) reader with no recovered password is skipped and
loudly flagged — never aborts the rest of the batch.

Usage:
  python jobs/arc/send_manuscript_access_batch.py                  # dry run
  python jobs/arc/send_manuscript_access_batch.py --dry-run=false  # live send
"""
import argparse
import os
import re
import smtplib
import sqlite3
import sys
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "watson.db"
LOG_DIR = REPO_ROOT / "logs"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("WATSON_GMAIL_ADDRESS", "")
SMTP_PASS = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")

_FROM = "FMS Team <watson@faithmakessense.com>"
_SUBJECT = "Your access to The Wrong Jesus manuscript"
_LOGIN_URL = "williamckyomes.com/arc/login"
_PIXEL_BASE = "https://watson.tail0243ff.ts.net/api/arc/pixel"
_CAMPAIGN = "manuscript_access_round1"
_EXCLUDE_EMAIL = "test@test.com"

_REPORT_LINE_RE = re.compile(r"<([^>]+)>\s+—\s+FOUND")
_PASSWORD_RE = re.compile(r"Password:\s*(\S+)")


def _latest_report_path() -> Path | None:
    candidates = sorted(
        LOG_DIR.glob("arc_password_recovery_*.txt"), key=lambda p: p.stat().st_mtime
    )
    return candidates[-1] if candidates else None


def _parse_report(path: Path) -> dict:
    """Return {email: password} for every FOUND row with a parsed password."""
    lines = path.read_text(encoding="utf-8").splitlines()
    result = {}
    for i, line in enumerate(lines):
        m = _REPORT_LINE_RE.search(line)
        if not m:
            continue
        email = m.group(1).strip().lower()
        if i + 1 < len(lines):
            pw_match = _PASSWORD_RE.search(lines[i + 1])
            if pw_match:
                result[email] = pw_match.group(1)
    return result


def _mask(password: str) -> str:
    if not password:
        return "(none)"
    return password[:2] + "***"


def _get_active_readers(conn: sqlite3.Connection, reader_id: int | None = None) -> list:
    if reader_id is not None:
        rows = conn.execute(
            "SELECT id, first_name, last_name, email FROM arc_readers "
            "WHERE status = 'active' AND email != ? AND id = ?",
            (_EXCLUDE_EMAIL, reader_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, first_name, last_name, email FROM arc_readers "
            "WHERE status = 'active' AND email != ?",
            (_EXCLUDE_EMAIL,),
        ).fetchall()
    return [dict(r) for r in rows]


def _build_email(first_name: str, email: str, password: str, tracking_token: str):
    plain = (
        f"Dear {first_name},\n\n"
        "The ARC manuscript for The Wrong Jesus went live last week. I hope you're "
        "enjoying it so far. If you haven't had a chance to start yet, here's your "
        "link and login to get going whenever you're ready:\n\n"
        f"Link: {_LOGIN_URL}\n"
        f"Email: {email}\n"
        f"Password: {password}\n\n"
        "Don't forget to keep the book in your prayers as you read. We'll be "
        "working on marketing and sharing as we get closer to launch. For now, "
        "just enjoy it.\n\n"
        "Thanks for being part of this.\n\n"
        "Bill"
    )
    pixel_url = f"{_PIXEL_BASE}/{tracking_token}"
    html = (
        "<html><body style='font-family:Georgia,serif;font-size:16px;line-height:1.7;"
        "color:#1a1a1a;max-width:600px;margin:0 auto;padding:40px;'>"
        f"{plain.replace(chr(10), '<br>')}"
        f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:none;">'
        "</body></html>"
    )
    return plain, html


def _send_email(to_email: str, plain: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["To"] = to_email
    msg["From"] = _FROM
    msg["Subject"] = _SUBJECT
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(SMTP_USER, [to_email], msg.as_string())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", type=str, default="true")
    parser.add_argument("--reader-id", type=int, default=None,
                         help="Restrict the entire run (query, send, tracking insert, "
                              "plaintext backfill) to this single arc_readers.id.")
    args = parser.parse_args()
    dry_run = args.dry_run.strip().lower() != "false"

    report_path = _latest_report_path()
    if report_path is None:
        print("No arc_password_recovery_*.txt report found in logs/ — aborting.")
        sys.exit(1)
    print(f"Using recovery report: {report_path}")
    recovered = _parse_report(report_path)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    readers = _get_active_readers(conn, reader_id=args.reader_id)
    if args.reader_id is not None:
        print(f"Scoped to reader_id={args.reader_id} only ({len(readers)} match(es))")

    to_send, missing = [], []
    for r in readers:
        pw = recovered.get(r["email"].lower())
        (to_send if pw else missing).append({**r, "password": pw} if pw else r)

    if missing:
        print()
        print("=" * 70)
        print(f"WARNING: {len(missing)} active reader(s) have NO recovered password on file.")
        print("Skipping these — NOT sending to them:")
        for r in missing:
            print(f"  - {r['first_name']} {r['last_name']} <{r['email']}>")
        print("=" * 70)
        print()

    print(f"Active readers (excl. {_EXCLUDE_EMAIL}): {len(readers)}")
    print(f"Ready to send: {len(to_send)}")
    print(f"Skipped (no password on file): {len(missing)}")
    print(f"Mode: {'LIVE SEND' if not dry_run else 'DRY RUN (no send, no writes)'}")
    print()

    sent_count = 0
    for r in to_send:
        tracking_token = uuid.uuid4().hex
        plain, html = _build_email(r["first_name"], r["email"], r["password"], tracking_token)

        if dry_run:
            print(
                f"[DRY RUN] Would send to {r['first_name']} {r['last_name']} "
                f"<{r['email']}> — password: {_mask(r['password'])} "
                f"— tracking_token: {tracking_token}"
            )
            continue

        try:
            _send_email(r["email"], plain, html)
        except Exception as exc:
            print(f"FAILED to send to {r['email']}: {exc}")
            continue

        conn.execute(
            "INSERT INTO arc_email_opens (reader_id, email, tracking_token, campaign) "
            "VALUES (?, ?, ?, ?)",
            (r["id"], r["email"], tracking_token, _CAMPAIGN),
        )
        conn.execute(
            "UPDATE arc_readers SET plaintext_password_recovery = ? WHERE id = ?",
            (r["password"], r["id"]),
        )
        conn.commit()
        sent_count += 1
        print(f"Sent to {r['first_name']} {r['last_name']} <{r['email']}>")

    conn.close()

    print()
    if dry_run:
        print(f"DRY RUN complete. {len(to_send)} email(s) would have been sent. No DB writes, no email sent.")
    else:
        print(f"Live run complete. {sent_count}/{len(to_send)} email(s) sent and logged.")


if __name__ == "__main__":
    main()
