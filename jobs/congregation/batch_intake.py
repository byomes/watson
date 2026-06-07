"""
CSV batch intake for congregation.db.

Reads a Subsplash connect card export CSV and writes members, attendance,
connect_cards, next_steps, prayer_requests, and follow_ups to congregation.db.

Usage:
  python3 jobs/congregation/batch_intake.py --file /path/to/export.csv
  python3 jobs/congregation/batch_intake.py --file /path/to/export.csv --dry-run
"""

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from jobs.congregation.member_match import find_or_create_member

DB_PATH = Path.home() / "watson" / "data" / "congregation.db"

NEXT_STEP_MAP = {
    "I want to start following Jesus":     "follow_jesus",
    "I want to get baptized":              "baptism",
    "I want help growing in my faith":     "grow_faith",
    "I want to become a Catalyst Partner": "catalyst_partner",
    "I want to join a small group":        "small_group",
    "I want to join a ministry team":      "ministry_team",
}


def _parse_date(raw: str) -> str:
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Cannot parse date: {raw!r}")


def _normalise_headers(row: dict) -> dict:
    return {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}


def run(csv_path: str, dry_run: bool = False) -> None:
    path = Path(csv_path)
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    stats = {
        "members_created":    0,
        "members_matched":    0,
        "duplicates_flagged": 0,
        "attendance_written": 0,
        "attendance_skipped": 0,
        "next_steps":         0,
    }

    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for raw_row in reader:
                row = _normalise_headers(raw_row)

                # ── Parse fields ──────────────────────────────────────────
                try:
                    service_date = _parse_date(row.get("date", ""))
                except ValueError as exc:
                    print(f"WARN: skipping row — {exc}")
                    continue

                campus     = row.get("campus", "").strip()
                first_name = row.get("first name", "").strip()
                last_name  = row.get("last name", "").strip()
                name       = f"{first_name} {last_name}".strip()
                email_addr = row.get("email", "").strip()
                phone      = row.get("phone", "").strip()
                prayer_req = row.get("prayer request", "").strip() or None
                follow_up  = row.get("follow up", "").strip() or None
                questions  = row.get("questions/comments", "").strip() or None
                ns_raw     = row.get("next steps", "").strip()

                next_step_keys = []
                if ns_raw:
                    for label in [s.strip() for s in ns_raw.split(",")]:
                        key = NEXT_STEP_MAP.get(label)
                        if key:
                            next_step_keys.append(key)

                if dry_run:
                    print(f"[dry-run] {name!r} {service_date} {campus}")
                    continue

                # ── Member match ──────────────────────────────────────────
                flags_before = conn.execute("SELECT COUNT(*) FROM duplicate_flags").fetchone()[0]
                member_id    = find_or_create_member(conn, name, email_addr, phone, service_date)
                flags_after  = conn.execute("SELECT COUNT(*) FROM duplicate_flags").fetchone()[0]

                # Determine created vs matched by checking if member existed before
                # find_or_create_member already handled the upsert; inspect updated_at vs created_at
                member_row = conn.execute(
                    "SELECT created_at, updated_at FROM members WHERE id = ?", (member_id,)
                ).fetchone()
                is_new = (member_row["created_at"] == member_row["updated_at"])

                if is_new:
                    stats["members_created"] += 1
                else:
                    stats["members_matched"] += 1

                if flags_after > flags_before:
                    stats["duplicates_flagged"] += flags_after - flags_before
                    print(
                        f"DUPLICATE FLAG: {name!r} (member_id={member_id}) fuzzy-matched "
                        f"on {service_date} — please review"
                    )

                # ── Attendance (skip if already recorded) ─────────────────
                dup_att = conn.execute(
                    """
                    SELECT id FROM attendance
                    WHERE member_id = ? AND service_date = ? AND campus = ?
                    """,
                    (member_id, service_date, campus),
                ).fetchone()
                if dup_att:
                    print(
                        f"WARN: duplicate attendance skipped — "
                        f"{name!r} {service_date} {campus}"
                    )
                    stats["attendance_skipped"] += 1
                    continue

                # ── connect_cards ─────────────────────────────────────────
                conn.execute(
                    """
                    INSERT INTO connect_cards
                      (member_id, service_date, campus, questions_comments)
                    VALUES (?, ?, ?, ?)
                    """,
                    (member_id, service_date, campus, questions),
                )
                card_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                # ── attendance ────────────────────────────────────────────
                conn.execute(
                    """
                    INSERT INTO attendance (member_id, service_date, campus, card_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (member_id, service_date, campus, card_id),
                )
                stats["attendance_written"] += 1

                # ── next_steps ────────────────────────────────────────────
                for step_key in next_step_keys:
                    conn.execute(
                        "INSERT INTO next_steps (member_id, card_id, step, date) VALUES (?, ?, ?, ?)",
                        (member_id, card_id, step_key, service_date),
                    )
                    stats["next_steps"] += 1

                # ── prayer_requests ───────────────────────────────────────
                if prayer_req:
                    conn.execute(
                        "INSERT INTO prayer_requests (member_id, card_id, request_text, date) VALUES (?, ?, ?, ?)",
                        (member_id, card_id, prayer_req, service_date),
                    )

                # ── follow_ups ────────────────────────────────────────────
                if follow_up:
                    conn.execute(
                        "INSERT INTO follow_ups (member_id, card_id, note) VALUES (?, ?, ?)",
                        (member_id, card_id, follow_up),
                    )

                conn.commit()

    finally:
        conn.close()

    if not dry_run:
        print()
        print("── Batch intake summary ─────────────────────────────")
        print(f"  Members created:        {stats['members_created']}")
        print(f"  Existing matched:       {stats['members_matched']}")
        print(f"  Duplicates flagged:     {stats['duplicates_flagged']}")
        print(f"  Attendance records:     {stats['attendance_written']}")
        print(f"  Attendance skipped:     {stats['attendance_skipped']}")
        print(f"  Next steps recorded:    {stats['next_steps']}")
        print("─────────────────────────────────────────────────────")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch CSV intake into congregation.db")
    parser.add_argument("--file", required=True, help="Path to Subsplash CSV export")
    parser.add_argument("--dry-run", action="store_true", help="Parse only; no DB writes")
    args = parser.parse_args()
    run(args.file, dry_run=args.dry_run)
