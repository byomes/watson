"""jobs/contacts/vcf_importer.py — Import contacts from .vcf vCard files into watson.db."""
import logging
import re
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DB_PATH = REPO / "data" / "watson.db"

log = logging.getLogger(__name__)

_PATH_RE = re.compile(r'[\w/~.\-]+\.vcf', re.IGNORECASE)


def parse_vcf(path: str) -> list:
    import vobject
    contacts = []
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()
    for vcard in vobject.readComponents(content):
        name = ""
        email = ""
        phone = ""
        try:
            name = str(vcard.fn.value).strip()
        except Exception:
            pass
        try:
            email = str(vcard.email.value).strip()
        except Exception:
            pass
        try:
            phone = str(vcard.tel.value).strip()
        except Exception:
            pass
        if name or email:
            contacts.append({"name": name, "email": email, "phone": phone})
    return contacts


def import_vcf(path: str) -> str:
    try:
        contacts = parse_vcf(path)
    except Exception as exc:
        log.error("parse_vcf failed: %s", exc)
        return f"Failed to parse VCF: {exc}"
    if not contacts:
        return "No contacts found in VCF file."
    inserted = updated = 0
    with sqlite3.connect(DB_PATH) as conn:
        for c in contacts:
            row = conn.execute(
                "SELECT id, phone FROM people WHERE email = ? COLLATE NOCASE OR name = ? COLLATE NOCASE",
                (c["email"], c["name"]),
            ).fetchone()
            if row:
                if c["phone"] and not row[1]:
                    conn.execute("UPDATE people SET phone = ?, updated_at = datetime('now') WHERE id = ?", (c["phone"], row[0]))
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO people (name, email, phone, created_at, updated_at) VALUES (?, ?, ?, datetime('now'), datetime('now'))",
                    (c["name"], c["email"], c["phone"]),
                )
                inserted += 1
    return f"VCF import complete: {inserted} added, {updated} already existed."


def run(message: str = None) -> str:
    if not message:
        return "VCF importer ready. Provide a .vcf file path."
    match = _PATH_RE.search(message)
    if not match:
        return "No .vcf file path found in message."
    import os
    path = match.group(0).replace("~", os.path.expanduser("~"))
    return import_vcf(path)
