"""One-time migration: encrypt plaintext `logins.password` values with WATSON_VAULT_KEY.

Run once after WATSON_VAULT_KEY is added to .env and app.py's encrypt/decrypt
helpers are deployed. Safe to re-run — it skips rows that already decrypt
successfully with the current key (i.e. are already Fernet tokens).
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/watson"))

import sqlite3
from config.settings import BASE_DIR  # noqa: F401  (ensures .env is loaded)
from cryptography.fernet import Fernet, InvalidToken

DB = os.path.expanduser("~/watson/data/watson.db")

key = os.getenv("WATSON_VAULT_KEY")
if not key:
    raise SystemExit("WATSON_VAULT_KEY not set in .env — aborting.")
fernet = Fernet(key.encode())

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, password FROM logins WHERE password IS NOT NULL AND password != ''").fetchall()

migrated = 0
already_encrypted = 0
for row in rows:
    pw = row["password"]
    try:
        fernet.decrypt(pw.encode())
        already_encrypted += 1
        continue
    except InvalidToken:
        pass
    enc = fernet.encrypt(pw.encode()).decode()
    conn.execute("UPDATE logins SET password = ? WHERE id = ?", (enc, row["id"]))
    migrated += 1

conn.commit()
conn.close()

print(f"Rows scanned:      {len(rows)}")
print(f"Already encrypted: {already_encrypted}")
print(f"Newly encrypted:   {migrated}")
