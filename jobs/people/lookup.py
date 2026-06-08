"""Member lookup utility — searches congregation.db by name."""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")


def lookup_member(query: str) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT name, email, phone, campus_preference, first_visit_date, status
            FROM members
            WHERE name LIKE ? COLLATE NOCASE
            ORDER BY name
            """,
            (f"%{query}%",),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 -m jobs.people.lookup <name>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    results = lookup_member(query)
    if not results:
        print(f"No members found matching '{query}'.")
    else:
        for r in results:
            parts = [
                r["name"] or "(no name)",
                r["email"] or "",
                r["phone"] or "",
                r["campus_preference"] or "",
                r["first_visit_date"] or "",
                r["status"] or "",
            ]
            print(" | ".join(p for p in parts))
