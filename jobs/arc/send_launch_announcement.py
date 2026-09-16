"""
jobs/arc/send_launch_announcement.py — one-off "The Wrong Jesus is out" email to
every active ARC reader (excludes Bill's own admin-preview account and the
test account), announcing the launch and asking for an honest Amazon review.

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
_SUBJECT = "The Wrong Jesus is officially out"
_PAPERBACK_URL = "https://www.amazon.com/Wrong-Jesus-When-Worship-Right/dp/B0HFDND93G"


def _get_active_readers(conn: sqlite3.Connection, reader_id: int | None = None) -> list:
    if reader_id is not None:
        rows = conn.execute(
            "SELECT id, first_name, last_name, email FROM arc_readers "
            "WHERE status = 'active' AND is_admin_preview = 0 AND email != 'test@test.com' "
            "AND id = ?",
            (reader_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, first_name, last_name, email FROM arc_readers "
            "WHERE status = 'active' AND is_admin_preview = 0 AND email != 'test@test.com'"
        ).fetchall()
    return [dict(r) for r in rows]


def _build_body(first_name: str) -> str:
    return (
        f"Dear {first_name},\n\n"
        "The Wrong Jesus is officially out. Thank you for being part of the ARC "
        "team, for reading it early, and for praying for it along the way. This "
        "book would not be what it is without readers like you.\n\n"
        "Now that it's live, here is where to find it:\n\n"
        f"{_PAPERBACK_URL}\n\n"
        "If you have a few minutes, would you kindly leave an honest review on "
        "Amazon? Early reviews make a real difference in how many people the "
        "book reaches, and hearing your honest reaction, good or hard, would "
        "mean a lot to Dr. Bill.\n\n"
        "Thank you again for walking through this with us.\n\n"
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

    print(f"Active ARC readers (excl. admin-preview + test): {len(readers)}")
    print(f"Mode: {'LIVE SEND' if not dry_run else 'DRY RUN (no send)'}")
    print()

    sent_count = 0
    for r in readers:
        body = _build_body(r["first_name"])

        if dry_run:
            print(f"[DRY RUN] Would send to {r['first_name']} {r['last_name']} <{r['email']}>")
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
        print(f"DRY RUN complete. {len(readers)} email(s) would have been sent. No email sent.")
    else:
        print(f"Live run complete. {sent_count}/{len(readers)} email(s) sent.")


if __name__ == "__main__":
    main()
