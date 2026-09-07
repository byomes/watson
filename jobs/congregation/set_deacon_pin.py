"""One-off CLI to set (or reset) a single deacon's Deacon App login PIN.

Usage:
  python3 jobs/congregation/set_deacon_pin.py "Deacon Name" 4821

Run this once per deacon when individual PINs replace the shared 1303
code seeded by migrate_deacon_pins.py. INSERT OR REPLACE means this also
works later to reset a PIN if someone forgets theirs. The name must match
a members.deacon value exactly (see /api/cat/deacons/list or the deacon
column in the roster) -- a typo here creates an unused row rather than an
error, since deacon_pins.deacon_name isn't foreign-keyed to anything.
"""
import os
import sqlite3
import sys
from hashlib import scrypt
from secrets import token_hex

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")


def hash_pin(pin: str) -> str:
    salt = token_hex(16)
    digest = scrypt(pin.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=32)
    return f"{salt}:{digest.hex()}"


def main():
    if len(sys.argv) != 3:
        print('Usage: python3 set_deacon_pin.py "Deacon Name" 1234')
        sys.exit(1)
    name, pin = sys.argv[1], sys.argv[2]
    if not pin.isdigit() or len(pin) != 4:
        print("PIN must be exactly 4 digits")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO deacon_pins (deacon_name, pin_hash) VALUES (?, ?)",
            (name, hash_pin(pin)),
        )
        conn.commit()
        print(f"PIN set for {name!r}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
