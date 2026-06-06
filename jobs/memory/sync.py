"""jobs/memory/sync.py — sync memory flat files to watson.db."""
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DB_PATH = os.getenv("WATSON_DB", str(REPO / "data" / "watson.db"))
MEMORY = REPO / "memory"


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _bootstrap(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_core (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_projects (
            slug         TEXT PRIMARY KEY,
            name         TEXT,
            status       TEXT,
            file_path    TEXT,
            last_updated TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_coding (
            slug         TEXT PRIMARY KEY,
            domain       TEXT,
            file_path    TEXT,
            last_updated TEXT
        )
    """)
    conn.commit()


def _parse_md_table(text):
    """Return list of dicts from the first markdown table found in text."""
    rows = []
    lines = text.splitlines()
    header = None
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = [c.lower().replace(" ", "_") for c in cells]
            continue
        if all(re.fullmatch(r"[-:]+", c) for c in cells):
            continue
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    return rows


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sync_projects(conn):
    index_path = MEMORY / "projects" / "_index.md"
    if not index_path.exists():
        return
    rows = _parse_md_table(index_path.read_text(encoding="utf-8"))
    for row in rows:
        slug = row.get("slug", "").strip()
        name = row.get("name", "").strip()
        status = row.get("status", "").strip()
        last_updated = row.get("last_updated", "").strip()
        if not slug:
            continue
        file_path = str(MEMORY / "projects" / slug / f"{slug}.md")
        conn.execute(
            """
            INSERT INTO memory_projects (slug, name, status, file_path, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name,
                status=excluded.status,
                file_path=excluded.file_path,
                last_updated=excluded.last_updated
            """,
            (slug, name, status, file_path, last_updated),
        )
    conn.commit()


def sync_coding(conn):
    index_path = MEMORY / "coding" / "_index.md"
    if not index_path.exists():
        return
    rows = _parse_md_table(index_path.read_text(encoding="utf-8"))
    for row in rows:
        slug = row.get("slug", "").strip()
        domain = row.get("domain", "").strip()
        last_updated = row.get("last_updated", "").strip()
        if not slug:
            continue
        file_path = str(MEMORY / "coding" / f"{slug}.md")
        conn.execute(
            """
            INSERT INTO memory_coding (slug, domain, file_path, last_updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                domain=excluded.domain,
                file_path=excluded.file_path,
                last_updated=excluded.last_updated
            """,
            (slug, domain, file_path, last_updated),
        )
    conn.commit()


def sync_core(conn):
    core_path = MEMORY / "core.md"
    if not core_path.exists():
        return
    content = core_path.read_text(encoding="utf-8")
    last_updated = ""
    m = re.search(r"\*Last updated:\s*([^\*\n]+)\*", content)
    if m:
        last_updated = m.group(1).strip()
    now = _now()
    for key, value in (("last_updated", last_updated), ("content", content)):
        conn.execute(
            """
            INSERT INTO memory_core (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now),
        )
    conn.commit()


def main():
    conn = _db()
    _bootstrap(conn)
    sync_core(conn)
    sync_projects(conn)
    sync_coding(conn)
    conn.close()


if __name__ == "__main__":
    main()
