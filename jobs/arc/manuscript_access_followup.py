"""
jobs/arc/manuscript_access_followup.py — built, NOT run as part of this task.

For every arc_email_opens row in campaign "manuscript_access_round1" that
still shows opened_at IS NULL, prints a ready-to-copy draft email (To: Bill's
own address only) so Bill can manually follow up. Reads the recovered
password from arc_readers.plaintext_password_recovery directly — no need to
re-read the log report, since that column is now the current source.

Safe by default: --dry-run defaults to true. --dry-run=false is the only
path that would actually send (To: BILL_EMAIL only — never a reader) rather
than just printing the draft.

Usage:
  python jobs/arc/manuscript_access_followup.py                  # print drafts
  python jobs/arc/manuscript_access_followup.py --dry-run=false  # email drafts to Bill
"""
import argparse
import os
import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "watson.db"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("WATSON_GMAIL_ADDRESS", "")
SMTP_PASS = os.getenv("WATSON_GMAIL_APP_PASSWORD", "")

_FROM = "Watson <watson@williamckyomes.com>"
_BILL_EMAIL = os.getenv("BILL_EMAIL", "bill.yomes@gmail.com")
_LOGIN_URL = "williamckyomes.com/arc/login"
_CAMPAIGN = "manuscript_access_round1"


def _get_non_openers(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        """
        SELECT r.first_name, r.last_name, r.email, r.plaintext_password_recovery AS password,
               o.tracking_token, o.sent_at
        FROM arc_email_opens o
        JOIN arc_readers r ON r.id = o.reader_id
        WHERE o.campaign = ? AND o.opened_at IS NULL
        ORDER BY r.first_name, r.last_name
        """,
        (_CAMPAIGN,),
    ).fetchall()
    return [dict(r) for r in rows]


def _build_draft_body(first_name: str, email: str, password: str) -> str:
    return (
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


def _send_draft_to_bill(subject: str, body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["To"] = _BILL_EMAIL
    msg["From"] = _FROM
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(SMTP_USER, [_BILL_EMAIL], msg.as_string())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", type=str, default="true")
    args = parser.parse_args()
    dry_run = args.dry_run.strip().lower() != "false"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    non_openers = _get_non_openers(conn)
    conn.close()

    print(f"Campaign: {_CAMPAIGN}")
    print(f"Non-openers: {len(non_openers)}")
    print(f"Mode: {'LIVE — drafts emailed to Bill' if not dry_run else 'DRY RUN (print only)'}")
    print()

    missing_password = [r for r in non_openers if not r["password"]]
    if missing_password:
        print("WARNING: no plaintext_password_recovery on file for:")
        for r in missing_password:
            print(f"  - {r['first_name']} {r['last_name']} <{r['email']}>")
        print()

    for r in non_openers:
        if not r["password"]:
            continue
        subject = f"[Draft follow-up] {r['first_name']} {r['last_name']} — no manuscript email open"
        body = _build_draft_body(r["first_name"], r["email"], r["password"])
        header = (
            f"--- Draft for {r['first_name']} {r['last_name']} <{r['email']}> "
            f"(sent {r['sent_at']}, never opened) ---"
        )

        if dry_run:
            print(header)
            print(f"To: {r['email']}")
            print(f"Subject: Your access to The Wrong Jesus manuscript")
            print(body)
            print()
            continue

        _send_draft_to_bill(subject, f"{header}\n\n{body}")
        print(f"Draft emailed to Bill for {r['first_name']} {r['last_name']} <{r['email']}>")


if __name__ == "__main__":
    main()
