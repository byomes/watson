"""jobs/migrate/twj_kv_to_db.py — one-time migration of TWJ reader/feedback records
from Upstash KV (watson-admin era) into watson.db (`twj_readers`, `twj_feedback`).

Read-only against KV: never deletes or modifies KV records. Run this repeatedly
until the printed counts match before touching anything else.

Usage:
    python jobs/migrate/twj_kv_to_db.py            # dry run: reports counts only
    python jobs/migrate/twj_kv_to_db.py --execute  # inserts into watson.db

KV shape (Upstash REST always returns stored values as strings):
    twj:readers:index      LIST of usernames
    twj:reader:<username>  JSON-encoded {name, email, username, password, createdAt}
    twj:feedback:all       LIST of JSON-encoded {username, name, chapter, text, submittedAt}

Known bug in the old bulk-upload route: it called JSON.stringify() before handing
the value to the @upstash/redis client, which stringifies again on its own — so
bulk-uploaded reader records are double-encoded. `parse_record` unwraps up to two
layers to handle both that path and the normal single-add path.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

from jobs.publishing import bootstrap_db, get_db

KV_URL = os.getenv("VERCEL_KV_REST_API_URL")
KV_TOKEN = os.getenv("VERCEL_KV_REST_API_TOKEN")


def kv_command(*args):
    resp = requests.post(
        KV_URL,
        headers={"Authorization": f"Bearer {KV_TOKEN}"},
        json=list(args),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("result")


def parse_record(raw):
    value = raw
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return value if isinstance(value, dict) else None


def fetch_readers():
    usernames = kv_command("LRANGE", "twj:readers:index", 0, -1) or []
    readers, unparsed = [], []
    for username in usernames:
        record = parse_record(kv_command("GET", f"twj:reader:{username}"))
        if record is None:
            unparsed.append(username)
        else:
            readers.append(record)
    return readers, unparsed


def fetch_feedback():
    raw_items = kv_command("LRANGE", "twj:feedback:all", 0, -1) or []
    items, unparsed = [], 0
    for raw in raw_items:
        record = parse_record(raw)
        if record is None:
            unparsed += 1
        else:
            items.append(record)
    return items, unparsed


def migrate(execute: bool) -> None:
    if not (KV_URL and KV_TOKEN):
        print("VERCEL_KV_REST_API_URL / VERCEL_KV_REST_API_TOKEN not set — aborting.")
        return

    readers, unparsed_readers = fetch_readers()
    feedback, unparsed_feedback = fetch_feedback()

    print(f"KV readers found:  {len(readers) + len(unparsed_readers)} (index entries)")
    print(f"  parsed cleanly:  {len(readers)}")
    if unparsed_readers:
        print(f"  FAILED TO PARSE: {len(unparsed_readers)} -> {unparsed_readers}")
    print(f"KV feedback found: {len(feedback) + unparsed_feedback}")
    print(f"  parsed cleanly:  {len(feedback)}")
    if unparsed_feedback:
        print(f"  FAILED TO PARSE: {unparsed_feedback}")

    if not execute:
        print("\nDry run only — no writes made. Re-run with --execute to insert into watson.db.")
        return

    bootstrap_db()
    inserted_readers = 0
    inserted_feedback = 0
    skipped_feedback = 0
    reader_ids_by_username = {}

    with get_db() as conn:
        for r in readers:
            username, password = r.get("username"), r.get("password")
            if not username or not password:
                print(f"  skipping reader with missing username/password: {r}")
                continue
            cur = conn.execute(
                """INSERT INTO twj_readers (username, email, password_hash, created_at, name)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET name = excluded.name
                   WHERE twj_readers.name IS NULL""",
                (username, r.get("email"), password, r.get("createdAt"), r.get("name")),
            )
            if cur.rowcount > 0:
                inserted_readers += 1
            row = conn.execute(
                "SELECT id FROM twj_readers WHERE username = ?", (username,)
            ).fetchone()
            if row:
                reader_ids_by_username[username] = row["id"]

        for f in feedback:
            reader_id = reader_ids_by_username.get(f.get("username"))
            if reader_id is None:
                skipped_feedback += 1
                print(f"  skipping feedback for unknown reader '{f.get('username')}': {f}")
                continue
            conn.execute(
                """INSERT INTO twj_feedback (reader_id, chapter, feedback, created_at)
                   VALUES (?, ?, ?, ?)""",
                (reader_id, f.get("chapter"), f.get("text"), f.get("submittedAt")),
            )
            inserted_feedback += 1
        conn.commit()

    print(
        f"\nInserted {inserted_readers} readers, {inserted_feedback} feedback rows "
        f"({skipped_feedback} feedback rows skipped for unknown reader)."
    )
    if inserted_readers != len(readers):
        print("WARNING: inserted reader count does not match parsed KV reader count — investigate before deleting KV data.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Write into watson.db (default is dry-run report only)")
    args = parser.parse_args()
    migrate(execute=args.execute)
