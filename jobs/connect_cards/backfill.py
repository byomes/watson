"""
Connect card CSV backfill importer.

Reads a CSV file and inserts rows into the connect_cards table using the same
congregation lookup/create logic as the Gmail intake parser.

Column names matched case-insensitively. Supported aliases:
  date, service_date              → service_date
  first_name, firstname           → first_name
  last_name, lastname             → last_name
  email                           → email
  phone                           → phone
  campus                          → campus
  is_first_visit, first_visit     → is_first_visit
  next_step, next_steps           → next_steps
  prayer_request                  → prayer_request
  prayer_leadership_only,
    leadership_only               → prayer_leadership_only
  question_comment, comment       → question_comment

Usage:
  PYTHONPATH=/home/billyomes/watson python3 jobs/connect_cards/backfill.py --file cards.csv
  PYTHONPATH=/home/billyomes/watson python3 jobs/connect_cards/backfill.py --file cards.csv --dry-run
"""

import argparse
import csv
import logging
import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [backfill] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/watson/data/watson.db")

COL_MAP = {
    "date":                   "service_date",
    "service_date":           "service_date",
    "first_name":             "first_name",
    "firstname":              "first_name",
    "last_name":              "last_name",
    "lastname":               "last_name",
    "email":                  "email",
    "phone":                  "phone",
    "campus":                 "campus",
    "is_first_visit":         "is_first_visit",
    "first_visit":            "is_first_visit",
    "next_step":              "next_steps",
    "next_steps":             "next_steps",
    "prayer_request":         "prayer_request",
    "prayer_leadership_only": "prayer_leadership_only",
    "leadership_only":        "prayer_leadership_only",
    "question_comment":       "question_comment",
    "comment":                "question_comment",
}

CAMPUS_MAP = {
    "wilmington campus": "Wilmington",
    "wilmington":        "Wilmington",
    "online campus":     "Online",
    "online":            "Online",
}

_DATE_FMTS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d-%b-%Y",
)

_BOOL_TRUE = {"y", "yes", "true", "1"}


def _normalize_campus(val: str) -> str:
    return CAMPUS_MAP.get(val.strip().lower(), val.strip())


def _normalize_bool(val: str) -> bool:
    return val.strip().lower() in _BOOL_TRUE


def _parse_date(val: str) -> str:
    val = val.strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(val, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {val!r}")


def _upsert_congregation(
    conn,
    first_name: str,
    last_name: str,
    email_addr: str,
    phone: str,
    campus: str,
    service_date: str,
    is_first_visit: bool,
    dry_run: bool,
) -> tuple:
    """Look up congregation by email then name; update or create. Returns (id, is_new)."""
    name = f"{first_name} {last_name}".strip()
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    cong = None
    if email_addr:
        cong = conn.execute(
            "SELECT id FROM congregation WHERE email = ?", (email_addr,)
        ).fetchone()
    if cong is None and name:
        cong = conn.execute(
            "SELECT id FROM congregation WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()

    if cong:
        if not dry_run:
            conn.execute(
                """
                UPDATE congregation
                SET last_seen  = ?,
                    email      = CASE WHEN TRIM(COALESCE(email, '')) = '' THEN ? ELSE email END,
                    phone      = CASE WHEN TRIM(COALESCE(phone, '')) = '' THEN ? ELSE phone END,
                    updated_at = ?
                WHERE id = ?
                """,
                (service_date, email_addr, phone, now, cong["id"]),
            )
        return cong["id"], False

    status = "first-time visitor" if is_first_visit else "regular"
    cong_id = -1
    if not dry_run:
        conn.execute(
            """
            INSERT INTO congregation
              (name, email, phone, status, campus, first_seen, last_seen, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, email_addr, phone, status, campus, service_date, service_date, now, now),
        )
        cong_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return cong_id, True


def run(csv_file: str, dry_run: bool = False) -> None:
    counts = {
        "processed":           0,
        "congregation_new":    0,
        "congregation_updated": 0,
        "inserted":            0,
        "skipped_dup":         0,
        "errors":              0,
    }

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        with open(csv_file, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            # Map CSV column names to canonical field names
            header_map = {
                col: COL_MAP[col.strip().lower()]
                for col in (reader.fieldnames or [])
                if col.strip().lower() in COL_MAP
            }

            for row_num, raw_row in enumerate(reader, start=2):
                counts["processed"] += 1
                try:
                    r = {
                        canonical: (raw_row.get(csv_col) or "").strip()
                        for csv_col, canonical in header_map.items()
                    }

                    first_name   = r.get("first_name", "")
                    last_name    = r.get("last_name", "")
                    email_addr   = r.get("email", "")
                    phone        = r.get("phone", "")
                    campus       = _normalize_campus(r.get("campus", ""))
                    next_steps   = r.get("next_steps") or None
                    prayer       = r.get("prayer_request") or None
                    q_comment    = r.get("question_comment") or None
                    is_first     = _normalize_bool(r.get("is_first_visit", ""))
                    pr_only      = _normalize_bool(r.get("prayer_leadership_only", ""))
                    prayer_public = 1 if (prayer and not pr_only) else 0

                    raw_date = r.get("service_date", "")
                    if not raw_date:
                        log.warning("Row %d: missing date — skipping.", row_num)
                        counts["errors"] += 1
                        continue
                    service_date = _parse_date(raw_date)

                    name = f"{first_name} {last_name}".strip()

                    # Duplicate check (email+date, fallback name+date)
                    dup = conn.execute(
                        "SELECT id FROM connect_cards WHERE email = ? AND service_date = ?",
                        (email_addr, service_date),
                    ).fetchone()
                    if not dup and not email_addr:
                        dup = conn.execute(
                            """
                            SELECT id FROM connect_cards
                            WHERE first_name = ? AND last_name = ? AND service_date = ?
                            """,
                            (first_name, last_name, service_date),
                        ).fetchone()
                    if dup:
                        log.info("Row %d: skipped duplicate — %r %s", row_num, name, service_date)
                        counts["skipped_dup"] += 1
                        continue

                    # Congregation upsert
                    cong_id, is_new = _upsert_congregation(
                        conn, first_name, last_name, email_addr, phone,
                        campus, service_date, is_first, dry_run,
                    )
                    if is_new:
                        counts["congregation_new"] += 1
                        log.info("Row %d: new congregation record — %r", row_num, name)
                    else:
                        counts["congregation_updated"] += 1
                        log.info("Row %d: updated congregation record — %r", row_num, name)

                    if not dry_run:
                        conn.execute(
                            """
                            INSERT INTO connect_cards
                              (congregation_id, first_name, last_name, email, phone, campus,
                               service_date, is_first_visit, next_steps, question_comment,
                               prayer_request, prayer_request_public, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                            """,
                            (
                                cong_id, first_name, last_name, email_addr, phone, campus,
                                service_date, 1 if is_first else 0,
                                next_steps, q_comment, prayer, prayer_public,
                            ),
                        )
                        conn.commit()

                    log.info(
                        "Row %d: %s — %r %s",
                        row_num,
                        "[dry-run] would insert" if dry_run else "inserted",
                        name,
                        service_date,
                    )
                    counts["inserted"] += 1

                except Exception as exc:
                    log.exception("Row %d: error — %s", row_num, exc)
                    counts["errors"] += 1
    finally:
        conn.close()

    dryrun_note = " (dry-run)" if dry_run else ""
    print(f"\n{counts['processed']} rows processed")
    print(f"{counts['congregation_new']} new congregation records created")
    print(f"{counts['congregation_updated']} congregation records updated")
    print(f"{counts['inserted']} connect card rows inserted{dryrun_note}")
    print(f"{counts['skipped_dup']} skipped (duplicate)")
    print(f"{counts['errors']} errors (logged above)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import connect card history from CSV.")
    parser.add_argument("--file",    required=True, help="Path to CSV file")
    parser.add_argument("--dry-run", action="store_true", help="Parse and log; do not write to database.")
    args = parser.parse_args()
    run(args.file, dry_run=args.dry_run)
