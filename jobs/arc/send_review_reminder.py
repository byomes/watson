"""
jobs/arc/send_review_reminder.py — gentle Amazon-review reminder to active ARC
readers who haven't yet reviewed. Excludes Bill's admin-preview account, the
test account, and any reader IDs passed via --exclude-reader-id (used here for
readers who have already posted, so they aren't nudged again).

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
_SUBJECT = "A quick note on behalf of Dr. Bill Yomes"


def _get_active_readers(conn: sqlite3.Connection, reader_id: int | None = None,
                         exclude_ids: list[int] | None = None) -> list:
    exclude_ids = exclude_ids or []
    if reader_id is not None:
        rows = conn.execute(
            "SELECT id, first_name, last_name, email FROM arc_readers "
            "WHERE status = 'active' AND is_admin_preview = 0 AND email != 'test@test.com' "
            "AND id = ?",
            (reader_id,),
        ).fetchall()
    else:
        placeholders = ",".join("?" for _ in exclude_ids) if exclude_ids else None
        query = (
            "SELECT id, first_name, last_name, email FROM arc_readers "
            "WHERE status = 'active' AND is_admin_preview = 0 AND email != 'test@test.com'"
        )
        params: list = []
        if placeholders:
            query += f" AND id NOT IN ({placeholders})"
            params = exclude_ids
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


_REVIEW_LINK = (
    "https://www.amazon.com/review/create-review/ref=cm_cr_arp_mb_wr_but"
    "?ie=UTF8&channel=awUDPv3&asin=B0HFDND93G"
)


def _build_body(first_name: str) -> str:
    return (
        f"Hi {first_name},\n\n"
        "I'm reaching out on behalf of Dr. Bill Yomes to share some exciting "
        "news: The Wrong Jesus has officially launched and is now available "
        "for purchase!\n\n"
        "A few fellow ARC readers have already posted their reviews, and it's "
        "made a real difference in helping new readers discover the book. If "
        "you've had a chance to finish it, Dr. Bill would be so grateful if "
        "you could leave an honest review here:\n"
        f"{_REVIEW_LINK}\n\n"
        "No pressure at all if you're not there yet or things have been busy. "
        "Even a short review goes a long way toward helping more people find "
        "the book, and your early support means a great deal.\n\n"
        "Thank you again for being one of the first to read it.\n\n"
        "Warmly,\n"
        "Watson\n"
        "On behalf of Dr. Bill Yomes"
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
    parser.add_argument("--exclude-reader-id", type=int, action="append", default=[],
                         help="arc_readers.id to skip (already reviewed). Repeatable.")
    args = parser.parse_args()
    dry_run = args.dry_run.strip().lower() != "false"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    readers = _get_active_readers(conn, reader_id=args.reader_id,
                                   exclude_ids=args.exclude_reader_id)
    if args.reader_id is not None:
        print(f"Scoped to reader_id={args.reader_id} only ({len(readers)} match(es))")
    if args.exclude_reader_id:
        print(f"Excluding reader IDs: {args.exclude_reader_id}")

    print(f"Active ARC readers (excl. admin-preview + test + excluded): {len(readers)}")
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
