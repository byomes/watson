"""Member lookup utility — searches congregation.db then watson.db people table."""
import os
import sqlite3

CONG_DB = os.path.expanduser("~/watson/data/congregation.db")
WATSON_DB = os.path.expanduser("~/watson/data/watson.db")


def _cascade(conn, table, query: str) -> list[dict]:
    """Three-step name cascade against a single connection/table."""
    def _q(term):
        return conn.execute(
            f"SELECT name, email, phone, carrier FROM {table}"
            " WHERE name LIKE ? COLLATE NOCASE ORDER BY name",
            (f"%{term}%",),
        ).fetchall()

    words = query.split()
    rows = _q(query)
    if not rows and len(words) > 1:
        rows = _q(words[-1])
    if not rows:
        rows = _q(words[0])
    return [dict(r) for r in rows]


def lookup_member(query: str) -> list[dict]:
    cong = sqlite3.connect(CONG_DB)
    cong.row_factory = sqlite3.Row
    try:
        results = _cascade(cong, "members", query)
    finally:
        cong.close()

    if results:
        return results

    watson = sqlite3.connect(WATSON_DB)
    watson.row_factory = sqlite3.Row
    try:
        return _cascade(watson, "people", query)
    finally:
        watson.close()


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
            parts = [r["name"] or "(no name)", r["email"] or "", r["phone"] or ""]
            print(" | ".join(p for p in parts if p))
