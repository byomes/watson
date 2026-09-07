"""Add deacon_pins table (per-deacon PIN login) and author_deacon column
on deacon_notes (attribution for who logged a note), for the Deacon App's
move from one shared PIN to per-deacon identity.

Interim state (2026-09-07): every current deacon value is seeded with the
SAME shared PIN "1303" -- Bill is handing out individual PINs to each
elder tomorrow and will set them via set_deacon_pin.py. Login already
supports a PIN matching more than one deacon (shows a name picker), so
this table works unchanged before and after that swap -- see
deacons_web.py's verify_pin route.

Usage:
  cd ~/watson && PYTHONPATH=. python3 jobs/congregation/migrate_deacon_pins.py
"""
import os
import sqlite3
from hashlib import scrypt
from secrets import token_hex

from jobs.congregation.deacon_reports import list_deacons

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

SHARED_PIN = "1303"


def hash_pin(pin: str) -> str:
    salt = token_hex(16)
    digest = scrypt(pin.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=32)
    return f"{salt}:{digest.hex()}"


conn = sqlite3.connect(DB_PATH)
try:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deacon_pins (
            deacon_name TEXT PRIMARY KEY,
            pin_hash    TEXT NOT NULL
        )
        """
    )

    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(deacon_notes)")}
    if "author_deacon" not in existing_cols:
        conn.execute("ALTER TABLE deacon_notes ADD COLUMN author_deacon TEXT")

    # list_deacons() (not a raw distinct query) so reserved bucket labels --
    # "Elders & Deacons", "~ Admin", "P Bill Yomes", "Inactive" -- never get
    # seeded as pickable login identities.
    names = list_deacons()
    seeded = 0
    for name in names:
        existing = conn.execute(
            "SELECT 1 FROM deacon_pins WHERE deacon_name = ?", (name,)
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO deacon_pins (deacon_name, pin_hash) VALUES (?, ?)",
            (name, hash_pin(SHARED_PIN)),
        )
        seeded += 1

    conn.commit()
    print(f"Done: deacon_pins table ready, {seeded} deacon(s) seeded with shared PIN {SHARED_PIN}.")
except Exception as e:
    print(f"Failed: {e}")
    raise
finally:
    conn.close()
