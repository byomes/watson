"""Shared scrypt hashing/verification for Deacon App login PINs
(deacon_pins table in congregation.db). Consolidates logic that was
previously duplicated in set_deacon_pin.py, migrate_deacon_pins.py, and
deacons_web.py's private _check_pin -- all three used the same params by
convention, not by shared code, which made it easy for one to drift.

pin_in_use() is new: nothing before jobs/congregation/pin_collection.py
(2026-09-17) ever checked a candidate PIN against existing rows before
accepting it. Because each row has its own scrypt salt, hashes can't be
compared directly -- checking for a collision means re-hashing the
candidate PIN against every stored salt, the same work verify_pin already
does at login time.
"""
import os
import sqlite3
from hashlib import scrypt
from hmac import compare_digest
from secrets import token_hex

CONGREGATION_DB = os.path.expanduser("~/watson/data/congregation.db")

_PIN_SCRYPT_PARAMS = dict(n=16384, r=8, p=1, dklen=32)


def hash_pin(pin: str) -> str:
    salt = token_hex(16)
    digest = scrypt(pin.encode(), salt=salt.encode(), **_PIN_SCRYPT_PARAMS)
    return f"{salt}:{digest.hex()}"


def check_pin(pin: str, stored_hash: str) -> bool:
    """stored_hash is `salt_hex:digest_hex`, matching the format written by
    hash_pin() above."""
    salt, _, expected_hex = stored_hash.partition(":")
    if not salt or not expected_hex:
        return False
    try:
        expected = bytes.fromhex(expected_hex)
    except ValueError:
        return False
    candidate = scrypt(pin.encode(), salt=salt.encode(), **_PIN_SCRYPT_PARAMS)
    return compare_digest(candidate, expected)


def pin_in_use(pin: str, exclude_deacon_name: str | None = None) -> bool:
    """True if `pin` matches any existing deacon_pins row (other than
    exclude_deacon_name, for the reset-your-own-PIN case)."""
    conn = sqlite3.connect(CONGREGATION_DB)
    try:
        rows = conn.execute("SELECT deacon_name, pin_hash FROM deacon_pins").fetchall()
    finally:
        conn.close()
    for deacon_name, pin_hash in rows:
        if deacon_name == exclude_deacon_name:
            continue
        if check_pin(pin, pin_hash):
            return True
    return False
