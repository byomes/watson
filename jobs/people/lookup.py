"""Member lookup utility — searches congregation.db then watson.db people table."""
import os
import sqlite3

CONG_DB = os.path.expanduser("~/watson/data/congregation.db")
WATSON_DB = os.path.expanduser("~/watson/data/watson.db")


def _cascade(conn, table, query: str) -> list[dict]:
    """Four-step name cascade: exact → full phrase → last name → first name."""
    def _q(term):
        return conn.execute(
            f"SELECT name, email, phone, carrier FROM {table}"
            " WHERE name LIKE ? COLLATE NOCASE ORDER BY name",
            (f"%{term}%",),
        ).fetchall()

    def _exact(term):
        return conn.execute(
            f"SELECT name, email, phone, carrier FROM {table}"
            " WHERE name = ? COLLATE NOCASE",
            (term,),
        ).fetchall()

    words = query.split()

    # Step 1: exact full name match
    rows = _exact(query)
    if rows:
        return [dict(r) for r in rows]

    # Step 2: partial full phrase match
    rows = _q(query)
    if not rows and len(words) > 1:
        rows = _q(words[-1])
    if not rows:
        rows = _q(words[0])
    return [dict(r) for r in rows]


def _merge(cong_rows: list[dict], watson_rows: list[dict]) -> list[dict]:
    watson_by_name = {r["name"].lower(): r for r in watson_rows}
    seen: set[str] = set()
    merged = []
    for c in cong_rows:
        key = c["name"].lower()
        w = watson_by_name.get(key)
        if w:
            seen.add(key)
            merged.append({k: (w[k] if w.get(k) else c.get(k)) for k in set(c) | set(w)})
        else:
            merged.append(c)
    for w in watson_rows:
        if w["name"].lower() not in seen:
            merged.append(w)
    return merged


def lookup_member(query: str) -> list[dict]:
    cong = sqlite3.connect(CONG_DB)
    cong.row_factory = sqlite3.Row
    try:
        cong_results = _cascade(cong, "members", query)
    finally:
        cong.close()

    # If we got an exact match from congregation.db, return it immediately
    # without polluting results with watson.db partial matches
    if cong_results and cong_results[0]["name"].lower() == query.lower():
        return cong_results

    watson = sqlite3.connect(WATSON_DB)
    watson.row_factory = sqlite3.Row
    try:
        watson_results = _cascade(watson, "people", query)
    finally:
        watson.close()

    return _merge(cong_results, watson_results)


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
