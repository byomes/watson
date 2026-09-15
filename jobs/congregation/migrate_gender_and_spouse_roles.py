"""One-time migration (2026-09-15): add a `gender` column to congregation.db's
members table, and convert the generic 'head'/'spouse' household_role pair
into explicit 'husband'/'wife' per Bill's request -- 'head' is retained
going forward, but now means specifically a single parent (no spouse on
file), not "whichever half of a couple got processed first" as before.

Gender is set from Subsplash import data where available (today's contacts
export), else from Bill's manually-confirmed name-based mapping for the
remaining pairs (2026-09-15 chat). See family_edit.py's _mark_spouse_core
for the ongoing behavior: marking a spouse relationship going forward
always sets both household_role and gender together.

Usage:
  python3 jobs/congregation/migrate_gender_and_spouse_roles.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

# (household_id, husband_name, wife_name) -- all 20 currently-married
# couples on file as of 2026-09-15, confirmed with Bill.
COUPLES = [
    ("H001", "Barry Balderson", "Janet Balderson"),
    ("H010", "Phil Spinelli", "Rose Spinelli"),
    ("H013", "Shawn Hale", "Lucie Hale"),
    ("H021", "Jim Bouchat", "Lisa Bouchat"),
    ("H022", "Bill Crook", "Juanita Crook"),
    ("H024", "Jesse Franco", "Megan Franco"),
    ("H027", "Dino Mathena", "Tara Mathena"),
    ("H029", "Ray Williams", "Bree Williams"),
    ("H030", "Pastor Bill Yomes", "Melanie Yomes"),
    ("H042", "John Beardsley", "Catherine Beardsley"),
    ("H044", "Rob Border", "Hannah Border"),
    ("H045", "Steven Cox", "Kassia Cox"),
    ("H046", "Steve Glass", "June Glass"),
    ("H051", "Tyler McCauley", "Kayla McCauley"),
    ("H056", "Terry Eaton", "Julie Eaton"),
    ("H057", "Doug Taylor", "Kathryn Taylor"),
    ("H062", "Fred Palmer", "Letha Palmer"),
    ("H064", "Ken Silva", "Monique Silva"),
    ("H065", "Fred Boling", "Brenda Boling"),
    ("H068", "Jim Hurst", "Sharon Hurst"),
]


def run():
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(members)").fetchall()}
        if "gender" not in existing:
            conn.execute("ALTER TABLE members ADD COLUMN gender TEXT")
            print("Added gender column.")
        else:
            print("gender column already present.")

        for household_id, husband_name, wife_name in COUPLES:
            husband = conn.execute(
                "SELECT id FROM members WHERE household_id = ? AND name = ?", (household_id, husband_name)
            ).fetchone()
            wife = conn.execute(
                "SELECT id FROM members WHERE household_id = ? AND name = ?", (household_id, wife_name)
            ).fetchone()
            if not husband or not wife:
                print(f"SKIP {household_id}: couldn't find both {husband_name!r} and {wife_name!r}")
                continue
            conn.execute(
                "UPDATE members SET household_role = 'husband', gender = 'male' WHERE id = ?", (husband[0],)
            )
            conn.execute(
                "UPDATE members SET household_role = 'wife', gender = 'female' WHERE id = ?", (wife[0],)
            )
            print(f"{household_id}: {husband_name} -> husband, {wife_name} -> wife")

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    run()
