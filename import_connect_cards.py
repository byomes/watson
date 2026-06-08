"""
Backfill connect cards from CSV export into congregation.db.

Usage:
    python3 import_connect_cards.py --csv /path/to/file.csv [--dry-run]

Skips rows already present (matched by first+last name + service_date).
Inserts members, connect_cards, attendance, next_steps, prayer_requests,
and follow_ups (first-time visitors).
"""

import argparse
import csv
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

NEXT_STEP_MAP = {
    "start following jesus":    "follow_jesus",
    "get baptized":             "baptism",
    "help growing in my faith": "grow_faith",
    "become a catalyst partner":"catalyst_partner",
    "join a small group":       "small_group",
    "join a ministry team":     "ministry_team",
}

CAMPUS_MAP = {
    "Wilmington Campus": "Wilmington",
    "Online Campus":     "Online",
}

COL_CAMPUS    = "Where did you attend with us? "
COL_FIRST     = "First Name"
COL_LAST      = "Last Name"
COL_EMAIL     = "Email"
COL_PHONE     = "Phone Number"
COL_COMMENT   = "Do you have a question/comment?"
COL_NEXT_STEP = "Are you ready to take a Next Step this week?"
COL_FIRST_VIS = "Is this your first Sunday with us? "
COL_LEADERSHIP= "Prayer requests are shared with our church family. Please let us know if you want your request shared with leadership only. "
COL_PRAYER    = "How can we pray for you this week? "
COL_DATE      = "Submission date"


def service_date(submission_str: str) -> str:
    dt = datetime.strptime(submission_str[:19], "%Y-%m-%d %H:%M:%S")
    days_back = (dt.weekday() + 1) % 7
    return (dt - timedelta(days=days_back)).date().isoformat()


def parse_next_steps(val: str) -> list[str]:
    if not val.strip():
        return []
    steps = []
    for item in val.split(","):
        item_lower = item.strip().lower()
        for substr, key in NEXT_STEP_MAP.items():
            if substr in item_lower:
                steps.append(key)
                break
    return steps


def find_or_create_member(conn, name: str, email: str, phone: str, svc_date: str) -> int:
    # Match by email first, then name
    if email:
        row = conn.execute("SELECT id FROM members WHERE email = ?", (email,)).fetchone()
        if row:
            # Update phone/name if blank
            conn.execute(
                "UPDATE members SET "
                "name = CASE WHEN TRIM(COALESCE(name,''))='' THEN ? ELSE name END, "
                "phone = CASE WHEN TRIM(COALESCE(phone,''))='' THEN ? ELSE phone END, "
                "updated_at = datetime('now') WHERE id = ?",
                (name, phone, row[0])
            )
            return row[0]

    if name:
        row = conn.execute(
            "SELECT id FROM members WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE members SET "
                "email = CASE WHEN TRIM(COALESCE(email,''))='' THEN ? ELSE email END, "
                "phone = CASE WHEN TRIM(COALESCE(phone,''))='' THEN ? ELSE phone END, "
                "updated_at = datetime('now') WHERE id = ?",
                (email, phone, row[0])
            )
            return row[0]

    # Create new
    conn.execute(
        "INSERT INTO members (name, email, phone, first_visit_date, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'visitor', datetime('now'), datetime('now'))",
        (name, email or None, phone or None, svc_date)
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def run(csv_path: str, dry_run: bool = False) -> None:
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # Filter to Jan 1 2026+
    rows = [r for r in rows if r[COL_DATE] >= "2026-01-01"]
    print(f"Rows to process: {len(rows)}")

    if dry_run:
        print("[dry-run] No changes will be written.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    inserted = skipped = errors = 0

    try:
        for i, r in enumerate(rows):
            try:
                first     = r[COL_FIRST].strip()
                last      = r[COL_LAST].strip()
                name      = f"{first} {last}".strip()
                email     = r[COL_EMAIL].strip().lower()
                phone     = r[COL_PHONE].strip()
                campus    = CAMPUS_MAP.get(r[COL_CAMPUS].strip(), r[COL_CAMPUS].strip())
                svc_date  = service_date(r[COL_DATE])
                comment   = r[COL_COMMENT].strip() or None
                prayer    = r[COL_PRAYER].strip() or None
                steps     = parse_next_steps(r[COL_NEXT_STEP])
                first_vis = r[COL_FIRST_VIS].strip().lower() == "yes"
                leadership_only = "leadership only" in r[COL_LEADERSHIP].lower()

                if not name.strip():
                    skipped += 1
                    continue

                # Skip if card already exists for this member + service_date
                existing_member = conn.execute(
                    "SELECT id FROM members WHERE "
                    "(email = ? AND email != '') OR (name = ? COLLATE NOCASE)",
                    (email, name)
                ).fetchone()

                if existing_member:
                    existing_card = conn.execute(
                        "SELECT id FROM connect_cards WHERE member_id = ? AND service_date = ?",
                        (existing_member[0], svc_date)
                    ).fetchone()
                    if existing_card:
                        skipped += 1
                        continue

                if dry_run:
                    print(f"  [dry-run] Would insert: {name} | {campus} | {svc_date} | steps={steps} | prayer={bool(prayer)}")
                    inserted += 1
                    continue

                member_id = find_or_create_member(conn, name, email, phone, svc_date)

                # connect_cards
                conn.execute(
                    "INSERT INTO connect_cards (member_id, service_date, campus, questions_comments, processed_at) "
                    "VALUES (?, ?, ?, ?, datetime('now'))",
                    (member_id, svc_date, campus, comment)
                )
                card_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                # attendance
                conn.execute(
                    "INSERT INTO attendance (member_id, service_date, campus, card_id) VALUES (?, ?, ?, ?)",
                    (member_id, svc_date, campus, card_id)
                )

                # next_steps
                for step in steps:
                    conn.execute(
                        "INSERT INTO next_steps (member_id, card_id, step, date) VALUES (?, ?, ?, ?)",
                        (member_id, card_id, step, svc_date)
                    )

                # prayer_requests
                if prayer:
                    conn.execute(
                        "INSERT INTO prayer_requests (member_id, card_id, request_text, date, leadership_only) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (member_id, card_id, prayer, svc_date, 1 if leadership_only else 0)
                    )

                # follow_ups (first-time visitor)
                if first_vis:
                    conn.execute(
                        "INSERT INTO follow_ups (member_id, card_id, note) VALUES (?, ?, ?)",
                        (member_id, card_id, "First-time visitor")
                    )

                inserted += 1

                if inserted % 100 == 0:
                    conn.commit()
                    print(f"  Progress: {inserted} inserted, {skipped} skipped...")

            except Exception as exc:
                errors += 1
                print(f"  ERROR row {i}: {exc}")

        if not dry_run:
            conn.commit()

    finally:
        conn.close()

    print(f"\nDone: {inserted} inserted, {skipped} skipped, {errors} errors.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.csv, dry_run=args.dry_run)
