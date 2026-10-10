"""jobs/congregation/kids_merge_duplicates.py -- one-off (2026-10-09, Bill's go-ahead): the full Subsplash backfill created a second `kids` row
for children who already existed under another profile id (manual-export "csv:" ids, or a second Subsplash profile). Merge each newer duplicate
into the oldest same-name kid: record its profile id as an alias, move its check-ins (one row per kid per Sunday), repoint/delete its Donna
review row and its importer-created `members` row. Dry run by default; --apply writes."""
import argparse
import collections
import sqlite3
from pathlib import Path

DB = Path.home() / "watson" / "data" / "congregation.db"


def merge(apply: bool) -> dict:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS kids_profile_alias (subsplash_profile_id TEXT PRIMARY KEY, kid_id INTEGER NOT NULL REFERENCES kids(id))")
    member_ref_tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0] not in ("members", "kids") and any(
        col[1] == "member_id" for col in c.execute(f"PRAGMA table_info({r[0]})"))]
    groups = collections.defaultdict(list)
    for k in c.execute("SELECT * FROM kids ORDER BY id"):
        groups[(k["first_name"].strip().lower(), (k["last_name"] or "").strip().lower())].append(k)
    out = collections.Counter()
    for ks in groups.values():
        keep = ks[0]
        for d in ks[1:]:
            out["merged_kids"] += 1
            c.execute("INSERT OR REPLACE INTO kids_profile_alias VALUES (?, ?)", (d["subsplash_profile_id"], keep["id"]))
            have = {r["event_date"]: r for r in c.execute("SELECT * FROM kids_checkin WHERE kid_id=?", (keep["id"],))}
            for r in c.execute("SELECT * FROM kids_checkin WHERE kid_id=?", (d["id"],)).fetchall():
                if r["event_date"] in have:  # same Sunday already recorded: keep the older row, fill its blanks
                    k0 = have[r["event_date"]]
                    c.execute("UPDATE kids_checkin SET guardian_name=COALESCE(guardian_name,?), guardian_phone=COALESCE(guardian_phone,?), "
                              "guardian_email=COALESCE(guardian_email,?), checked_in_at=COALESCE(checked_in_at,?) WHERE id=?",
                              (r["guardian_name"], r["guardian_phone"], r["guardian_email"], r["checked_in_at"], k0["id"]))
                    c.execute("DELETE FROM kids_checkin WHERE id=?", (r["id"],))
                    out["checkins_deduped"] += 1
                else:
                    c.execute("UPDATE kids_checkin SET kid_id=? WHERE id=?", (keep["id"], r["id"]))
                    have[r["event_date"]] = r
                    out["checkins_moved"] += 1
            # Donna's review: keep one pending row on the keeper only if the keeper still needs a household link.
            pend = c.execute("SELECT id FROM kids_household_review_queue WHERE kid_id=? AND status!='resolved'", (d["id"],)).fetchall()
            keeper_open = c.execute("SELECT 1 FROM kids_household_review_queue WHERE kid_id=? AND status!='resolved'", (keep["id"],)).fetchone()
            for i, q in enumerate(pend):
                if i == 0 and keep["household_id"] is None and not keeper_open:
                    c.execute("UPDATE kids_household_review_queue SET kid_id=? WHERE id=?", (keep["id"], q["id"]))
                else:
                    c.execute("DELETE FROM kids_household_review_queue WHERE id=?", (q["id"],))
                    out["queue_rows_dropped"] += 1
            c.execute("DELETE FROM kids_household_review_queue WHERE kid_id=?", (d["id"],))  # resolved rows of the duplicate
            if d["member_id"] and d["member_id"] != keep["member_id"]:
                m = c.execute("SELECT * FROM members WHERE id=?", (d["member_id"],)).fetchone()
                refs = sum(c.execute(f"SELECT COUNT(*) FROM {t} WHERE member_id=?", (d["member_id"],)).fetchone()[0] for t in member_ref_tables)
                if m and (m["notes"] or "").startswith("Added via Kids Checkin") and not refs and not m["household_id"]:
                    c.execute("DELETE FROM members WHERE id=?", (d["member_id"],))
                    out["members_deleted"] += 1
                else:
                    out["members_kept_needs_review"] += 1
            c.execute("DELETE FROM kids WHERE id=?", (d["id"],))
    if apply:
        c.commit()
    else:
        c.rollback()
    return dict(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    print(("APPLIED " if a.apply else "DRY RUN ") + str(merge(a.apply)))
