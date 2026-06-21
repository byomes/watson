import csv, os, sqlite3, sys

WATSON_DB = os.path.expanduser("~/watson/data/watson.db")

def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS people (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, email TEXT, phone TEXT,
        address TEXT, organization TEXT, notes TEXT, carrier TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    existing = {row[1] for row in conn.execute("PRAGMA table_info(people)")}
    for col in ["address", "organization"]:
        if col not in existing:
            conn.execute(f"ALTER TABLE people ADD COLUMN {col} TEXT")
    conn.commit()

def import_contacts(csv_path):
    conn = sqlite3.connect(WATSON_DB)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    inserted = updated = skipped = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            first = row.get("First Name","").strip()
            last = row.get("Last Name","").strip()
            org = row.get("Organization Name","").strip()
            name = f"{first} {last}".strip() or org
            if not name:
                skipped += 1
                continue
            email = row.get("E-mail 1 - Value","").strip()
            phone = row.get("Phone 1 - Value","").strip()
            address = row.get("Address 1 - Formatted","").strip()
            notes = row.get("Notes","").strip()
            existing = conn.execute("SELECT id FROM people WHERE name = ? COLLATE NOCASE",(name,)).fetchone()
            if existing:
                conn.execute("UPDATE people SET email=COALESCE(NULLIF(?,\"\"),email), phone=COALESCE(NULLIF(?,\"\"),phone), address=COALESCE(NULLIF(?,\"\"),address), organization=COALESCE(NULLIF(?,\"\"),organization), notes=COALESCE(NULLIF(?,\"\"),notes), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (email,phone,address,org,notes,existing["id"]))
                updated += 1
            else:
                conn.execute("INSERT INTO people (name,email,phone,address,organization,notes) VALUES (?,?,?,?,?,?)",
                    (name,email,phone,address,org,notes))
                inserted += 1
    conn.commit()
    conn.close()
    print(f"Done. Inserted: {inserted} | Updated: {updated} | Skipped: {skipped}")

import_contacts(sys.argv[1])
