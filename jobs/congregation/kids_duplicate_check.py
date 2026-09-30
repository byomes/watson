"""jobs/congregation/kids_duplicate_check.py -- candidate-duplicate scan
for the `kids` table, same spirit as jobs/congregation/duplicate_review.py
but for kids (Subsplash checkin-only children, see project_kids_checkin_
backlog memory) rather than adult `members`. Each `kids` row is already
unique per Subsplash's own profile id (kids_checkin_import.py upserts on
subsplash_profile_id), so a duplicate here means Subsplash itself has two
different profile ids for what's really the same child -- a parent
accidentally creating a second profile, a name-spelling variant, etc.

Signal used: exact or fuzzy (difflib, same FUZZY_THRESHOLD as
member_match.py) normalized full-name match, narrowed to pairs that also
share a household_id OR share a guardian phone/email seen on their
kids_checkin rows -- two different real kids with the same first+last name
and no other connection are left alone (plausible for a big church, e.g.
two unrelated "Ava Smith"s), but a name match plus a shared parent is a
strong signal.

Read-only / reporting only -- no flags table, no Telegram, no merge
action. Prints candidate pairs for a human to look at. If this turns up
real duplicates often enough to be worth a standing review flow, extend it
to a flags table + dupf_-style Telegram review like duplicate_review.py's,
but that's more than a one-off check needs.

Usage:
  python3 -m jobs.congregation.kids_duplicate_check
"""
import difflib
import re
import sqlite3
from pathlib import Path

from jobs.congregation.member_match import FUZZY_THRESHOLD

CONGREGATION_DB = Path.home() / "watson" / "data" / "congregation.db"


def _norm_name(n):
    return re.sub(r"\s+", " ", (n or "").strip().lower())


def _connect():
    conn = sqlite3.connect(CONGREGATION_DB)
    conn.row_factory = sqlite3.Row
    return conn


def find_candidate_duplicates() -> list[dict]:
    conn = _connect()
    try:
        kids = conn.execute("SELECT id, first_name, last_name, household_id FROM kids").fetchall()

        guardians: dict[int, set[str]] = {}
        for row in conn.execute(
            "SELECT kid_id, guardian_phone, guardian_email FROM kids_checkin "
            "WHERE guardian_phone IS NOT NULL OR guardian_email IS NOT NULL"
        ):
            s = guardians.setdefault(row["kid_id"], set())
            if row["guardian_phone"]:
                s.add(f"phone:{row['guardian_phone']}")
            if row["guardian_email"]:
                s.add(f"email:{row['guardian_email'].strip().lower()}")

        pairs = []
        for i in range(len(kids)):
            for j in range(i + 1, len(kids)):
                a, b = kids[i], kids[j]
                name_a = _norm_name(f"{a['first_name']} {a['last_name'] or ''}")
                name_b = _norm_name(f"{b['first_name']} {b['last_name'] or ''}")
                if not name_a or not name_b:
                    continue

                exact = name_a == name_b
                fuzzy = not exact and difflib.SequenceMatcher(None, name_a, name_b).ratio() >= FUZZY_THRESHOLD
                if not (exact or fuzzy):
                    continue

                same_household = (
                    a["household_id"] is not None
                    and a["household_id"] == b["household_id"]
                )
                shared_guardian = bool(guardians.get(a["id"], set()) & guardians.get(b["id"], set()))

                if not (same_household or shared_guardian):
                    continue  # name match alone isn't enough -- could be two different kids

                pairs.append({
                    "kid_id_a": a["id"], "name_a": f"{a['first_name']} {a['last_name'] or ''}".strip(),
                    "kid_id_b": b["id"], "name_b": f"{b['first_name']} {b['last_name'] or ''}".strip(),
                    "match_type": "exact_name" if exact else "fuzzy_name",
                    "same_household": same_household,
                    "shared_guardian": shared_guardian,
                })

        return pairs
    finally:
        conn.close()


def _kid_checkin_count(conn, kid_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM kids_checkin WHERE kid_id = ?", (kid_id,)).fetchone()[0]


def merge_kids(conn, keep_id: int, merge_id: int) -> dict:
    """Reassigns merge_id's checkin history onto keep_id, fills keep_id's
    household_id from merge_id if keep_id doesn't have one yet, then
    deletes the merge_id kids row. Same defensive pattern as
    duplicate_review.merge_members: kids_checkin has no (kid_id,
    event_date) unique constraint, so a blanket UPDATE could double-count
    a Sunday both ids already have a row for -- merge_id's row for any
    such date is dropped first, keep_id's is kept."""
    if keep_id == merge_id:
        raise ValueError("keep_id and merge_id must differ")

    keep = conn.execute("SELECT * FROM kids WHERE id = ?", (keep_id,)).fetchone()
    merge = conn.execute("SELECT * FROM kids WHERE id = ?", (merge_id,)).fetchone()
    if not keep or not merge:
        raise ValueError("both kids must exist")

    conn.execute(
        """DELETE FROM kids_checkin WHERE kid_id = ? AND event_date IN
           (SELECT event_date FROM kids_checkin WHERE kid_id = ?)""",
        (merge_id, keep_id),
    )
    conn.execute("UPDATE kids_checkin SET kid_id = ? WHERE kid_id = ?", (keep_id, merge_id))

    # A live (non-resolved) review-queue row on BOTH ids would leave two
    # open Donna/Bill approval prompts for the same real kid -- keep_id's
    # takes priority, merge_id's open one is just dropped (already-resolved
    # rows on either side are left alone as history).
    keep_has_open = conn.execute(
        "SELECT 1 FROM kids_household_review_queue WHERE kid_id = ? AND status != 'resolved'", (keep_id,)
    ).fetchone()
    if keep_has_open:
        conn.execute(
            "DELETE FROM kids_household_review_queue WHERE kid_id = ? AND status != 'resolved'", (merge_id,)
        )
    conn.execute("UPDATE kids_household_review_queue SET kid_id = ? WHERE kid_id = ?", (keep_id, merge_id))

    if keep["household_id"] is None and merge["household_id"] is not None:
        conn.execute(
            "UPDATE kids SET household_id = ?, updated_at = datetime('now') WHERE id = ?",
            (merge["household_id"], keep_id),
        )

    conn.execute("DELETE FROM kids WHERE id = ?", (merge_id,))
    conn.commit()

    return dict(conn.execute("SELECT * FROM kids WHERE id = ?", (keep_id,)).fetchone())


def merge_cluster(conn, kid_ids: list[int]) -> dict:
    """Merges a whole cluster of same-child kid ids down to one, keeping
    whichever id has the most kids_checkin history (ties favor the lower/
    older id, same tie-break duplicate_review.py uses for members)."""
    ranked = sorted(kid_ids, key=lambda kid_id: (-_kid_checkin_count(conn, kid_id), kid_id))
    keep_id = ranked[0]
    for merge_id in ranked[1:]:
        merge_kids(conn, keep_id, merge_id)
    return dict(conn.execute("SELECT * FROM kids WHERE id = ?", (keep_id,)).fetchone())


if __name__ == "__main__":
    results = find_candidate_duplicates()
    if not results:
        print("No candidate duplicate kids found.")
    else:
        print(f"{len(results)} candidate duplicate pair(s):")
        for p in results:
            signal = "same household" if p["same_household"] else "shared guardian phone/email"
            print(f"  kid #{p['kid_id_a']} \"{p['name_a']}\" <-> kid #{p['kid_id_b']} \"{p['name_b']}\" "
                  f"({p['match_type']}, {signal})")
