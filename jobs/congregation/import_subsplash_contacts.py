"""
One-time contacts import from a Subsplash "Contacts" people export.

Unlike batch_intake.py (which reads a Subsplash *connect card* export and
writes attendance/next_steps/prayer_requests), this reads a Subsplash
*contacts* export whose "Tags" column is a mix of attendance dates
(MM-DD-YY) and free-text tags. This script does NOT import attendance
(no campus is present in the source) -- it only creates/updates member
profile records, filling blank fields only.

The CSV has duplicate column headers (an artifact of the Subsplash export):
columns 2/17 are both "First Name" (2 = nickname-style, 17 = clean legal
name -- we use 17), 3/14 are both "Last Name" (identical), 5/13 both
"Gender" (identical, unused -- no gender column in members).

Matching deliberately does NOT reuse member_match.find_or_create_member's
fuzzy branch, which merges a fuzzy hit straight into the existing record
(flagging member_id_a == member_id_b as a "double check this" marker, not
a reviewable pair). Instead, a fuzzy-only hit here creates a brand-new
member row and inserts a real duplicate_flags PAIR (member_id_a = new row,
member_id_b = existing match) so Dr. Bill can review it via the existing
dupf_ Telegram buttons (jobs/congregation/duplicate_review.py's
merge_members) and choose merge vs. "these are two different people"
himself, rather than the import silently guessing.

Usage:
  python3 jobs/congregation/import_subsplash_contacts.py --file /path.csv
  python3 jobs/congregation/import_subsplash_contacts.py --file /path.csv --apply
"""

import argparse
import csv
import difflib
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from jobs.congregation.member_match import FUZZY_THRESHOLD

DB_PATH = Path.home() / "watson" / "data" / "congregation.db"

PARTNERSHIP_ENUM = ("Partner", "Regular Attender", "Guest")
PARTNERSHIP_PRIORITY = ["Partner", "Regular Attender", "Guest"]

FUZZY_IMPORT_REASON = "subsplash_import_fuzzy"


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _parse_created(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def _normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"


def _pick_partnership(raw: str) -> tuple[str | None, str | None]:
    """Return (enum_value_or_None, leftover_note_or_None)."""
    tokens = [t.strip() for t in (raw or "").split(";") if t.strip()]
    if not tokens:
        return None, None
    enum_hits = [t for t in tokens if t in PARTNERSHIP_ENUM]
    leftover = [t for t in tokens if t not in PARTNERSHIP_ENUM]
    chosen = None
    for candidate in PARTNERSHIP_PRIORITY:
        if candidate in enum_hits:
            chosen = candidate
            break
    note = f"Subsplash tag: {', '.join(leftover)}" if leftover else None
    return chosen, note


def _build_address(zip_code, state, city, line2, line1) -> str | None:
    parts = [p.strip() for p in (line1, line2, city) if p and p.strip()]
    tail = " ".join(p.strip() for p in (state, zip_code) if p and p.strip())
    if tail:
        parts.append(tail)
    return ", ".join(parts) if parts else None


def _find_by_email_or_phone(conn, email, phone):
    if email:
        row = conn.execute("SELECT id FROM members WHERE LOWER(email) = LOWER(?)", (email,)).fetchone()
        if row:
            return row[0], "email"
    if phone:
        row = conn.execute("SELECT id FROM members WHERE phone = ?", (phone,)).fetchone()
        if row:
            return row[0], "phone"
    return None, None


def _find_fuzzy(conn, name):
    best_ratio, best_id = 0.0, None
    for mid, mname in conn.execute("SELECT id, name FROM members").fetchall():
        ratio = difflib.SequenceMatcher(None, name.lower(), (mname or "").lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best_id = ratio, mid
    if best_id is not None and best_ratio >= FUZZY_THRESHOLD:
        return best_id
    return None


def _insert_member(conn, name, email, phone, dob, address, partnership, note, first_visit_date) -> int:
    # partner (2026-09-24, see ~/.claude/plans/zesty-cuddling-robin.md) is
    # the new binary column replacing partnership_status -- same mapping
    # rule as the original backfill: only the literal 'Partner' value maps
    # to 'partner', everything else (including no Subsplash data at all)
    # maps to 'np'.
    partner_val = "partner" if partnership == "Partner" else "np"
    conn.execute(
        """
        INSERT INTO members
            (name, email, phone, birthdate, address, partnership_status, partner,
             notes, first_visit_date, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, email or None, phone or None, dob, address, partnership, partner_val,
         note, first_visit_date, _now()),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _fill_blanks(conn, member_id, email, phone, dob, address, partnership, note_addition):
    row = conn.execute(
        "SELECT email, phone, birthdate, address, partnership_status, partner, notes "
        "FROM members WHERE id = ?",
        (member_id,),
    ).fetchone()

    updates = {}
    if not (row["email"] or "").strip() and email:
        updates["email"] = email
    if not (row["phone"] or "").strip() and phone:
        updates["phone"] = phone
    if not (row["birthdate"] or "").strip() and dob:
        updates["birthdate"] = dob
    if not (row["address"] or "").strip() and address:
        updates["address"] = address
    if not (row["partnership_status"] or "").strip() and partnership:
        updates["partnership_status"] = partnership
    # partner: only ever fills a genuinely NULL value (every existing member
    # already has 'partner'/'np' from the 2026-09-24 backfill, so this is
    # mainly defensive) -- same mapping rule as _insert_member above.
    if row["partner"] is None and partnership:
        updates["partner"] = "partner" if partnership == "Partner" else "np"

    existing_notes = row["notes"] or ""
    if note_addition and note_addition not in existing_notes:
        updates["notes"] = (existing_notes + ("\n" if existing_notes else "") + note_addition)

    if not updates:
        return {}

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE members SET {set_clause}, updated_at = ? WHERE id = ?",
        (*updates.values(), _now(), member_id),
    )
    return updates


def run(csv_path: str, apply: bool) -> None:
    path = Path(csv_path)
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    stats = {
        "total_rows": 0,
        "members_matched_email": 0,
        "members_matched_phone": 0,
        "members_created_new": 0,
        "members_created_fuzzy_flagged": 0,
        "fields_filled": 0,
        "birthdates_filled": 0,
        "addresses_filled": 0,
        "partnership_status_filled": 0,
        "skipped_no_name": 0,
        "connecting_point_invite_tags_seen": 0,
    }
    fuzzy_flag_ids = []

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        next(reader)  # header row (duplicate column names, read positionally)

        for row in reader:
            stats["total_rows"] += 1

            first = row[17].strip()
            last = row[3].strip()
            name = f"{first} {last}".strip()
            if not name:
                stats["skipped_no_name"] += 1
                continue

            email = ""
            for candidate in row[12].split(";"):
                candidate = candidate.strip()
                if candidate:
                    email = candidate
                    break

            phone = _normalize_phone(row[15])
            dob = row[11].strip() or None
            address = _build_address(row[18], row[19], row[21], row[22], row[23])
            partnership, leftover_note = _pick_partnership(row[16])
            created_date = _parse_created(row[9])

            if "Connecting Point Invite" in row[6]:
                stats["connecting_point_invite_tags_seen"] += 1

            existing_id, matched_via = _find_by_email_or_phone(conn, email, phone)

            if existing_id:
                if matched_via == "email":
                    stats["members_matched_email"] += 1
                else:
                    stats["members_matched_phone"] += 1
                updates = _fill_blanks(conn, existing_id, email, phone, dob, address, partnership, leftover_note)
            else:
                fuzzy_id = _find_fuzzy(conn, name)
                new_id = _insert_member(
                    conn, name, email, phone, dob, address, partnership, leftover_note, created_date,
                )
                if fuzzy_id:
                    stats["members_created_fuzzy_flagged"] += 1
                    conn.execute(
                        "INSERT INTO duplicate_flags (member_id_a, member_id_b, reason, status) "
                        "VALUES (?, ?, ?, 'pending')",
                        (new_id, fuzzy_id, FUZZY_IMPORT_REASON),
                    )
                    fuzzy_flag_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                else:
                    stats["members_created_new"] += 1
                updates = {
                    k: v for k, v in {
                        "email": email, "phone": phone, "birthdate": dob, "address": address,
                        "partnership_status": partnership,
                    }.items() if v
                }

            if updates:
                stats["fields_filled"] += len(updates)
                if "birthdate" in updates:
                    stats["birthdates_filled"] += 1
                if "address" in updates:
                    stats["addresses_filled"] += 1
                if "partnership_status" in updates:
                    stats["partnership_status_filled"] += 1

            if apply:
                conn.commit()
            else:
                conn.rollback()
                fuzzy_flag_ids = []  # dry run never really inserted these

    conn.close()

    print()
    print(f"── Subsplash contacts import {'(APPLIED)' if apply else '(DRY RUN)'} ──")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if apply and fuzzy_flag_ids:
        print(f"  duplicate_flags inserted (reason={FUZZY_IMPORT_REASON}): {fuzzy_flag_ids}")
    print("────────────────────────────────────────────────")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    args = parser.parse_args()
    run(args.file, apply=args.apply)
