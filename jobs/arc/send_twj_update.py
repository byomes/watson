"""
jobs/arc/send_twj_update.py — one-off "The Wrong Jesus update" email to every
active ARC reader (excludes the test account, id 25).

Passwords come from arc_readers.plaintext_password_recovery — the current
password source, per manuscript_access_followup.py.

Safe by default: --dry-run defaults to true. Only --dry-run=false sends email.
--reader-id restricts the entire run to a single arc_readers.id for scoped
test sends.
"""
import argparse
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "watson.db"

_FROM_EMAIL = "watson@williamckyomes.com"
_FROM_NAME = "Watson"
_SUBJECT = "The Wrong Jesus update"
_LOGIN_URL = "williamckyomes.com/arc/login"
_EXCLUDE_ID = 25


def _get_active_readers(conn: sqlite3.Connection, reader_id: int | None = None) -> list:
    if reader_id is not None:
        rows = conn.execute(
            "SELECT id, first_name, last_name, email, plaintext_password_recovery AS password "
            "FROM arc_readers WHERE status = 'active' AND id != ? AND id = ?",
            (_EXCLUDE_ID, reader_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, first_name, last_name, email, plaintext_password_recovery AS password "
            "FROM arc_readers WHERE status = 'active' AND id != ?",
            (_EXCLUDE_ID,),
        ).fetchall()
    return [dict(r) for r in rows]


def _mask(password: str) -> str:
    if not password:
        return "(none)"
    return password[:2] + "***"


def _build_body(first_name: str, email: str, password: str) -> str:
    return (
        f"Dear {first_name},\n\n"
        "The ARC manuscript for The Wrong Jesus is live, and I hope you have been "
        "able to login and begin reading it. If you tried earlier and ran into "
        "trouble, that was on our end — we had an email server issue that kept "
        "some of you from getting through. That's fixed now, so if you were one "
        "of the ones affected, please try again.\n\n"
        "Here are your current login credentials:\n\n"
        f"Email: {email}\n"
        f"Password: {password}\n"
        f"Login: {_LOGIN_URL}\n\n"
        "While you're reading, you'll also see a new feedback feature built "
        "right into the manuscript. As you go, I'd love your honest reactions — "
        "what's landing, what's surprising you, what you'd tell a friend. We're "
        "planning to use some of these early reader comments as part of the "
        "marketing push toward launch, so if something you write really captures "
        "how the book hit you, it may end up featured (we'll always ask first "
        "before using your name).\n\n"
        "As always, please be praying for this book and its impact on behalf of "
        "Dr. Bill. I'll be in touch with more as we get closer to launch.\n\n"
        "Thanks for being part of this.\n\n"
        "Watson\n"
        "Digital Assistant to Dr. Bill Yomes\n"
        "williamckyomes.com/start"
    )


def _send_email(to_email: str, body: str) -> None:
    result = send_email(
        to_email=to_email,
        to_name="",
        subject=_SUBJECT,
        text_body=body,
        from_email=_FROM_EMAIL,
        from_name=_FROM_NAME,
        include_signature=False,
    )
    if not result["success"]:
        raise RuntimeError(f"Brevo send failed: {result['error']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", type=str, default="true")
    parser.add_argument("--reader-id", type=int, default=None,
                         help="Restrict the entire run to this single arc_readers.id.")
    args = parser.parse_args()
    dry_run = args.dry_run.strip().lower() != "false"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    readers = _get_active_readers(conn, reader_id=args.reader_id)
    if args.reader_id is not None:
        print(f"Scoped to reader_id={args.reader_id} only ({len(readers)} match(es))")

    to_send, missing = [], []
    for r in readers:
        (to_send if r["password"] else missing).append(r)

    if missing:
        print()
        print("=" * 70)
        print(f"WARNING: {len(missing)} active reader(s) have NO password on file.")
        print("Skipping these — NOT sending to them:")
        for r in missing:
            print(f"  - {r['first_name']} {r['last_name']} <{r['email']}>")
        print("=" * 70)
        print()

    print(f"Active readers (excl. id={_EXCLUDE_ID}): {len(readers)}")
    print(f"Ready to send: {len(to_send)}")
    print(f"Skipped (no password on file): {len(missing)}")
    print(f"Mode: {'LIVE SEND' if not dry_run else 'DRY RUN (no send)'}")
    print()

    sent_count = 0
    for r in to_send:
        body = _build_body(r["first_name"], r["email"], r["password"])

        if dry_run:
            print(
                f"[DRY RUN] Would send to {r['first_name']} {r['last_name']} "
                f"<{r['email']}> — password: {_mask(r['password'])}"
            )
            continue

        try:
            _send_email(r["email"], body)
        except Exception as exc:
            print(f"FAILED to send to {r['email']}: {exc}")
            continue

        sent_count += 1
        print(f"Sent to {r['first_name']} {r['last_name']} <{r['email']}>")

    conn.close()

    print()
    if dry_run:
        print(f"DRY RUN complete. {len(to_send)} email(s) would have been sent. No email sent.")
    else:
        print(f"Live run complete. {sent_count}/{len(to_send)} email(s) sent.")


if __name__ == "__main__":
    main()
