"""
One-time/rerunnable import of the deacon directory spreadsheet into
congregation.db.

Reads deacon_directory_clean.csv (household_id, first_name, last_name,
full_name, phone, email, address, deacon, deacon_status, notes) and, for
each row:

  1. Match against an existing member via jobs/congregation/member_match.py's
     priority-ordered logic (email -> phone -> fuzzy name >= 0.82).
  2. On match: always overwrite address/household_id/deacon/deacon_status
     from the spreadsheet (it's the authoritative deacon-care source);
     phone/email only fill blanks, per member_match.py's existing
     find_or_create_member behavior.
  3. On no match: insert a new member (name = full_name) via
     find_or_create_member, then set the four new fields the same way.

Adjustment vs. plain find_or_create_member(): this source is sparser on
email/phone than connect-card data. For rows with BOTH blank, a fuzzy name
match below FUZZY_THRESHOLD is not auto-created as a new member -- that
risks a silent duplicate with no email/phone to ever reconcile it against
later. Instead it's surfaced as "needs manual match" and left untouched.

A first dry run against the real spreadsheet (2026-08-24) surfaced three
rows that DO have their own email/phone (so exact match correctly found no
existing member) but got merged into an unrelated existing member anyway,
because find_or_create_member's own step 3 falls back to fuzzy name
matching whenever exact match fails -- e.g. "Dan Jr Barry" fuzzy-matched
onto "Dan Sr Barry" at a 0.92 ratio. Bill confirmed all three are real,
distinct people, not the same person under a different spelling. Since
member_match.py is shared with other intake jobs and out of scope to
change here, FORCE_NEW_MEMBER below routes those three specific names
around find_or_create_member entirely and inserts them directly.

Safe by default: dry-run unless --apply is passed. Dry-run runs the exact
same matching/update code inside a transaction that gets rolled back, so
the report reflects the real decisions, not a separate simulation. A
markdown report is always written to data/imports/, in both modes.

Usage:
  python3 jobs/congregation/import_deacon_directory.py path/to/deacon_directory_clean.csv
  python3 jobs/congregation/import_deacon_directory.py path/to/deacon_directory_clean.csv --apply
"""

import argparse
import csv
import difflib
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from jobs.congregation.member_match import FUZZY_THRESHOLD, find_or_create_member

DB_PATH = Path.home() / "watson" / "data" / "congregation.db"
REPORT_DIR = Path.home() / "watson" / "data" / "imports"

_ZIP_RE = re.compile(r"(\d{5})(?:-\d{4})?\s*$")
_DE_ZIP_PREFIX = "19"

FORCE_NEW_MEMBER = {"Eric Johnson", "Dan Jr Barry", "Catherine Beardsley"}

OPEN_PRODUCT_QUESTIONS = [
    "Identify the \"shut in\" population and find a way to track them "
    "(not a per-member field in this batch -- raised from the original "
    "spreadsheet's general notes, not carried into the cleaned CSV).",
    "Decide whether regular attenders (not just Partners) should be "
    "tracked through the deacon-care system too.",
]


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _normalise_headers(row: dict) -> dict:
    return {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}


def _read_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        return [_normalise_headers(row) for row in csv.DictReader(fh)]


def _email_flag(email: str) -> str | None:
    if email and email.count("@") != 1:
        return f"malformed email (expected exactly one '@'): {email!r}"
    return None


def _address_flag(address: str) -> str | None:
    if not address:
        return "no address on file -- household grouping unavailable for this person"
    m = _ZIP_RE.search(address)
    if m and ", de" in address.lower() and not m.group(1).startswith(_DE_ZIP_PREFIX):
        return (
            f"zip {m.group(1)!r} doesn't match a DE address -- possible "
            f"transposed digits, needs manual review"
        )
    return None


def _notes_flag(notes: str) -> str | None:
    if notes and "?" in notes:
        return f"notes raise an open question -- review before finalizing: {notes!r}"
    return None


def _best_fuzzy_match(conn: sqlite3.Connection, name: str) -> tuple[int | None, float]:
    """Mirrors member_match.find_or_create_member's step 3 fuzzy check, as a
    read-only pre-check so we can decide routing before calling into it."""
    name = (name or "").strip()
    if not name:
        return None, 0.0
    best_ratio, best_id = 0.0, None
    for mid, mname in conn.execute("SELECT id, name FROM members").fetchall():
        ratio = difflib.SequenceMatcher(None, name.lower(), (mname or "").lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best_id = ratio, mid
    return best_id, best_ratio


def run(csv_path: str, apply: bool = False) -> None:
    path = Path(csv_path)
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    rows = _read_rows(path)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    stats = {"matched": 0, "inserted": 0, "needs_manual_match": 0, "duplicates_flagged": 0, "name_mismatches": 0}
    log_lines: list[str] = []
    manual_matches: list[tuple[str, str, float]] = []
    data_flags: list[tuple[str, str]] = []
    name_mismatches: list[tuple[str, str, int, bool]] = []

    members_before = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]

    try:
        for row in rows:
            household_id = (row.get("household_id") or "").strip() or None
            first_name = row.get("first_name", "")
            last_name = row.get("last_name", "")
            full_name = (row.get("full_name") or f"{first_name} {last_name}").strip()
            phone = (row.get("phone") or "").strip()
            email = (row.get("email") or "").strip()
            address = (row.get("address") or "").strip() or None
            deacon = (row.get("deacon") or "").strip() or None
            deacon_status = (row.get("deacon_status") or "").strip() or None
            notes = (row.get("notes") or "").strip() or None

            if not full_name:
                log_lines.append(f"WARN: skipping row with no name (household_id={household_id!r})")
                continue

            for flag in filter(None, [_email_flag(email), _address_flag(address), _notes_flag(notes)]):
                data_flags.append((full_name, flag))

            if not email and not phone:
                best_id, best_ratio = _best_fuzzy_match(conn, full_name)
                if best_id is None or best_ratio < FUZZY_THRESHOLD:
                    stats["needs_manual_match"] += 1
                    manual_matches.append((full_name, household_id or "", best_ratio))
                    log_lines.append(
                        f"NEEDS MANUAL MATCH: {full_name!r} -- no email/phone, "
                        f"best fuzzy ratio {best_ratio:.2f} (< {FUZZY_THRESHOLD})"
                    )
                    continue

            if full_name in FORCE_NEW_MEMBER:
                conn.execute(
                    "INSERT INTO members (name, email, phone, updated_at) VALUES (?, ?, ?, ?)",
                    (full_name, email or None, phone or None, _now()),
                )
                member_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                stats["inserted"] += 1
                log_lines.append(
                    f"INSERT (forced -- confirmed distinct person, bypassed fuzzy match): "
                    f"{full_name!r} (member_id={member_id})"
                )
            else:
                count_before = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
                flags_before = conn.execute("SELECT COUNT(*) FROM duplicate_flags").fetchone()[0]

                member_id = find_or_create_member(conn, full_name, email, phone, service_date=None)

                count_after = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
                flags_after = conn.execute("SELECT COUNT(*) FROM duplicate_flags").fetchone()[0]

                is_fuzzy = flags_after > flags_before

                if count_after > count_before:
                    stats["inserted"] += 1
                    log_lines.append(f"INSERT: {full_name!r} (member_id={member_id})")
                else:
                    stats["matched"] += 1
                    db_name = conn.execute("SELECT name FROM members WHERE id = ?", (member_id,)).fetchone()[0]
                    same_name = (db_name or "").strip().lower() == full_name.strip().lower()
                    log_lines.append(f"MATCH:  {full_name!r} -> member_id={member_id} (DB name: {db_name!r})")
                    if not same_name:
                        stats["name_mismatches"] += 1
                        name_mismatches.append((full_name, db_name, member_id, is_fuzzy))
                        risk = "HIGH RISK (matched by name similarity alone)" if is_fuzzy else \
                               "likely fine (matched by shared email/phone -- couple or nickname)"
                        log_lines.append(f"  ** NAME MISMATCH -- {risk} **")

                if is_fuzzy:
                    stats["duplicates_flagged"] += flags_after - flags_before
                    log_lines.append("  fuzzy-matched -- flagged in duplicate_flags for review")

            conn.execute(
                """
                UPDATE members
                SET address = ?, household_id = ?, deacon = ?, deacon_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (address, household_id, deacon, deacon_status, _now(), member_id),
            )
            log_lines.append(
                f"  set address/household_id/deacon/deacon_status "
                f"(deacon={deacon!r}, status={deacon_status!r})"
            )

        members_after = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]

        if apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()

    _print_and_write_report(
        apply=apply,
        source_rows=len(rows),
        stats=stats,
        log_lines=log_lines,
        manual_matches=manual_matches,
        data_flags=data_flags,
        name_mismatches=name_mismatches,
        members_before=members_before,
        members_after=members_after,
    )


def _print_and_write_report(
    apply: bool,
    source_rows: int,
    stats: dict,
    log_lines: list[str],
    manual_matches: list[tuple[str, str, float]],
    data_flags: list[tuple[str, str]],
    name_mismatches: list[tuple[str, str, int, bool]],
    members_before: int,
    members_after: int,
) -> None:
    mode = "APPLY (written)" if apply else "DRY RUN (rolled back -- no changes written)"
    accounted_for = stats["matched"] + stats["inserted"] + stats["needs_manual_match"]

    lines = []
    lines.append("# Deacon Directory Import Report")
    lines.append("")
    lines.append(f"Mode: {mode}")
    lines.append(f"Generated: {_now()} UTC")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Source rows: {source_rows}")
    lines.append(f"- Matched to existing members: {stats['matched']}")
    lines.append(f"- New members inserted: {stats['inserted']}")
    lines.append(f"- Needs manual match (no email/phone, fuzzy below threshold): {stats['needs_manual_match']}")
    lines.append(f"- Fuzzy duplicate_flags raised for review: {stats['duplicates_flagged']}")
    lines.append(f"- Matches where the DB name differs from the spreadsheet name: {stats['name_mismatches']}")
    lines.append(f"- Rows accounted for: {accounted_for} / {source_rows}"
                  + ("  [OK]" if accounted_for == source_rows else "  [MISMATCH -- investigate]"))
    lines.append(f"- members count before: {members_before}, after: {members_after} "
                  f"(net new: {members_after - members_before}, expected: {stats['inserted']})")
    lines.append("")

    if manual_matches:
        lines.append("## Needs Manual Match")
        lines.append("")
        lines.append("No email/phone in source; fuzzy name match was below the "
                      f"{FUZZY_THRESHOLD} threshold (or no members to compare). "
                      "Not touched -- resolve by hand.")
        lines.append("")
        for name, household_id, ratio in manual_matches:
            lines.append(f"- {name} (household_id={household_id or 'n/a'}) -- best fuzzy ratio {ratio:.2f}")
        lines.append("")

    if name_mismatches:
        high_risk = [m for m in name_mismatches if m[3]]
        likely_fine = [m for m in name_mismatches if not m[3]]

        if high_risk:
            lines.append("## HIGH RISK -- Matched By Name Similarity Alone, Different Name In DB")
            lines.append("")
            lines.append("No shared email or phone corroborated this match -- it's name-similarity "
                          "only (>= 0.82 ratio). In this batch some of these are two different "
                          "people (e.g. a Jr/Sr pair, or an unrelated person with a similar name) "
                          "getting merged into one member row, which would overwrite that "
                          "person's address/deacon/deacon_status with the wrong row's data. "
                          "Check each of these by hand before running --apply.")
            lines.append("")
            for csv_name, db_name, member_id, _ in high_risk:
                lines.append(f"- spreadsheet {csv_name!r} matched member_id={member_id}, whose DB name is {db_name!r}")
            lines.append("")

        if likely_fine:
            lines.append("## Likely Fine -- Name Differs But Matched By Shared Email/Phone")
            lines.append("")
            lines.append("These matched on an exact email or phone already in congregation.db, "
                          "so the contact info corroborates it -- usually a nickname/maiden name, "
                          "or a couple sharing one household contact and being represented by one "
                          "member row (an existing, pre-import convention in this database). "
                          "Listed for completeness, not expected to need action.")
            lines.append("")
            for csv_name, db_name, member_id, _ in likely_fine:
                lines.append(f"- spreadsheet {csv_name!r} matched member_id={member_id}, whose DB name is {db_name!r}")
            lines.append("")

    if data_flags:
        lines.append("## Data Quality Flags")
        lines.append("")
        for name, flag in data_flags:
            lines.append(f"- {name}: {flag}")
        lines.append("")

    lines.append("## Open Product Questions (from the original spreadsheet, not per-member data)")
    lines.append("")
    for q in OPEN_PRODUCT_QUESTIONS:
        lines.append(f"- {q}")
    lines.append("")

    lines.append("## Verification Checklist")
    lines.append("")
    lines.append("- [ ] `PRAGMA table_info(members)` shows address/household_id/deacon/deacon_status")
    lines.append("- [ ] Rows accounted for matches source row count (see Summary above)")
    lines.append("- [ ] Spot-check in dashboard Member Management: Donna Redman, Jim Bouchat, Bill Crook")
    lines.append("")

    lines.append("## Per-row Log")
    lines.append("")
    lines.append("```")
    lines.extend(log_lines)
    lines.append("```")
    lines.append("")

    report_text = "\n".join(lines)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"deacon_directory_report_{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.md"
    report_path.write_text(report_text, encoding="utf-8")

    print(f"Mode: {mode}")
    print(f"Source rows: {source_rows}")
    print(f"Matched: {stats['matched']}  Inserted: {stats['inserted']}  "
          f"Needs manual match: {stats['needs_manual_match']}  "
          f"Duplicate flags: {stats['duplicates_flagged']}  "
          f"Name mismatches: {stats['name_mismatches']}")
    print(f"Rows accounted for: {accounted_for} / {source_rows}")
    high_risk = [m for m in name_mismatches if m[3]]
    if high_risk:
        print()
        print(f"** {len(high_risk)} HIGH RISK name mismatch(es) -- matched by name similarity "
              f"alone, no shared email/phone -- review before --apply **")
        for csv_name, db_name, member_id, _ in high_risk:
            print(f"  spreadsheet {csv_name!r} matched member_id={member_id}, DB name {db_name!r}")
    if not apply:
        print()
        for line in log_lines:
            print(line)
    print()
    print(f"Report written: {report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import deacon directory CSV into congregation.db")
    parser.add_argument("csv_path", help="Path to deacon_directory_clean.csv")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run only, rolled back)")
    args = parser.parse_args()
    run(args.csv_path, apply=args.apply)
