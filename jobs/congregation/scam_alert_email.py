"""
jobs/congregation/scam_alert_email.py — one-off "someone is impersonating Dr. Bill
asking for gift cards" scam warning to every active congregation member with an
email on file.

Safe by default: --dry-run defaults to true. Only --dry-run=false sends email.
--member-id restricts the entire run to a single members.id for a scoped test send.
Recipients are deduped by lowercased/trimmed email so shared household addresses
only get one copy.
"""
import argparse
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "congregation.db"

_FROM_EMAIL = "watson@williamckyomes.com"
_FROM_NAME = "Watson"
_SUBJECT = "Scam Alert: Someone Is Impersonating Dr. Bill"

_BODY = """Dear Church Family,

We want to make you aware of a scam that is currently targeting our congregation. Someone is impersonating Dr. Bill by text, email, or social media, claiming to be him and asking people to purchase gift cards on his behalf.

This is not Dr. Bill. It is a scam.

Please keep the following in mind:

- Dr. Bill will never ask you to purchase gift cards, wire money, or send payment codes through text or email.
- Scammers often create fake profiles or spoof phone numbers and email addresses to look legitimate, sometimes using his real name and photo.
- If you receive a message like this, do not reply, do not send any money or gift card codes, and do not click any links.
- If you're ever unsure whether a message is really from Dr. Bill, contact the church office directly to confirm before doing anything.

If you've already received one of these messages, please let the church office know so we can track the scope of this and warn others.

Thank you for looking out for one another and for our church family.

Grace and peace,
The Office of Dr. Bill Yomes
"""


def _get_recipients(conn: sqlite3.Connection, member_id: int | None = None) -> list:
    if member_id is not None:
        rows = conn.execute(
            "SELECT id, name, email FROM members "
            "WHERE active NOT IN ('disconnected', 'deceased') AND email IS NOT NULL AND trim(email) != '' AND id = ?",
            (member_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, email FROM members "
            "WHERE active NOT IN ('disconnected', 'deceased') AND email IS NOT NULL AND trim(email) != ''"
        ).fetchall()

    seen = set()
    deduped = []
    for r in rows:
        key = r["email"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(r))
    return deduped


def _send_email(to_email: str, to_name: str) -> None:
    result = send_email(
        to_email=to_email,
        to_name=to_name,
        subject=_SUBJECT,
        text_body=_BODY,
        from_email=_FROM_EMAIL,
        from_name=_FROM_NAME,
        include_signature=False,
    )
    if not result["success"]:
        raise RuntimeError(f"Brevo send failed: {result['error']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", type=str, default="true")
    parser.add_argument("--member-id", type=int, default=None,
                         help="Restrict the entire run to this single members.id.")
    args = parser.parse_args()
    dry_run = args.dry_run.strip().lower() != "false"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    recipients = _get_recipients(conn, member_id=args.member_id)
    if args.member_id is not None:
        print(f"Scoped to member_id={args.member_id} only ({len(recipients)} match(es))")

    print(f"Active members with email on file (deduped): {len(recipients)}")
    print(f"Mode: {'LIVE SEND' if not dry_run else 'DRY RUN (no send)'}")
    print()

    sent_count = 0
    for r in recipients:
        if dry_run:
            print(f"[DRY RUN] Would send to {r['name']} <{r['email']}>")
            continue

        try:
            _send_email(r["email"], r["name"])
        except Exception as exc:
            print(f"FAILED to send to {r['email']}: {exc}")
            continue

        sent_count += 1
        print(f"Sent to {r['name']} <{r['email']}>")

    conn.close()

    print()
    if dry_run:
        print(f"DRY RUN complete. {len(recipients)} email(s) would have been sent. No email sent.")
    else:
        print(f"Live run complete. {sent_count}/{len(recipients)} email(s) sent.")


if __name__ == "__main__":
    main()
