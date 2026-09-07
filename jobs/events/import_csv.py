#!/usr/bin/env python3
"""jobs/events/import_csv.py — one-off/manual import of a signup-platform CSV
export (SignUpGenius / Church Center / Subsplash ticket-registration exports,
the format Bill's picnic export uses) into event_registrations.

Usage:
  PYTHONPATH=/home/billyomes/watson venv/bin/python -m jobs.events.import_csv \\
      <csv_path> --event-name "Church Picnic" --start-date 2026-09-20

Recognized headers (case-insensitive; anything else is kept verbatim in
extra_fields so a platform-specific custom question, e.g. "bring a side dish
or dessert", is never silently dropped):
  submission date, first name, last name, email, phone number / phone,
  ticket type, ticket price, number of tickets / # of tickets / tickets
"""
import argparse
import csv
import json
import sqlite3
import sys

from config.settings import DB_PATH
from jobs.events.matching import find_member_id
from jobs.events.schema import create_tables

_HEADER_MAP = {
    "submission date": "submitted_at",
    "first name": "first_name",
    "last name": "last_name",
    "email": "email",
    "phone number": "phone",
    "phone": "phone",
    "ticket type": "ticket_type",
    "ticket price": "ticket_price",
    "number of tickets": "num_tickets",
    "# of tickets": "num_tickets",
    "tickets": "num_tickets",
}


def _map_row(raw_row: dict) -> tuple[dict, dict]:
    """Split a raw CSV row into (known_fields, extra_fields)."""
    known: dict = {}
    extra: dict = {}
    for header, value in raw_row.items():
        key = _HEADER_MAP.get((header or "").strip().lower())
        if key:
            known[key] = (value or "").strip()
        else:
            if (value or "").strip():
                extra[(header or "").strip()] = value.strip()
    return known, extra


def _find_or_create_event(conn: sqlite3.Connection, event_name: str, start_date: str) -> int:
    row = conn.execute(
        "SELECT id FROM church_events WHERE LOWER(event_name) = LOWER(?)", (event_name,)
    ).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO church_events (event_name, start_date, tracking_active) VALUES (?, ?, 1)",
        (event_name, start_date),
    )
    return cur.lastrowid


def import_csv(csv_path: str, event_name: str, start_date: str) -> dict:
    create_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    event_id = _find_or_create_event(conn, event_name, start_date)

    existing_emails = {
        (r["email"] or "").lower()
        for r in conn.execute(
            "SELECT email FROM event_registrations WHERE event_id = ? AND email IS NOT NULL AND email != ''",
            (event_id,),
        ).fetchall()
    }

    imported = 0
    skipped_dup = 0
    total_tickets = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            known, extra = _map_row(raw_row)
            email = known.get("email", "")
            if email and email.lower() in existing_emails:
                skipped_dup += 1
                continue

            try:
                num_tickets = int(known.get("num_tickets") or 1)
            except ValueError:
                num_tickets = 1

            member_id = find_member_id(email, known.get("phone", ""))

            conn.execute(
                """INSERT INTO event_registrations
                   (event_id, first_name, last_name, email, phone, ticket_type,
                    ticket_price, num_tickets, extra_fields, member_id, source, submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'csv_import', ?)""",
                (
                    event_id,
                    known.get("first_name", ""),
                    known.get("last_name", ""),
                    email or None,
                    known.get("phone", ""),
                    known.get("ticket_type", ""),
                    known.get("ticket_price", ""),
                    num_tickets,
                    json.dumps(extra) if extra else None,
                    member_id,
                    known.get("submitted_at", ""),
                ),
            )
            if email:
                existing_emails.add(email.lower())
            imported += 1
            total_tickets += num_tickets

    conn.commit()
    conn.close()
    return {
        "event_id": event_id,
        "imported": imported,
        "skipped_dup": skipped_dup,
        "total_tickets": total_tickets,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    result = import_csv(args.csv_path, args.event_name, args.start_date)
    print(
        f"Event #{result['event_id']} '{args.event_name}': "
        f"{result['imported']} registrations imported "
        f"({result['total_tickets']} tickets), "
        f"{result['skipped_dup']} duplicate email(s) skipped.",
        file=sys.stderr,
    )
