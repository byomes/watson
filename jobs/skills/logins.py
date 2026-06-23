"""jobs/skills/logins.py — look up saved login credentials by natural language query."""
import re
import sqlite3

DB = "/home/billyomes/watson/data/watson.db"

_PATTERNS = [
    r"(?:what'?s my password for|my password for|password for)\s+(.+)",
    r"login for\s+(.+)",
    r"credentials for\s+(.+)",
]


def handle(message: str) -> str:
    msg = message.lower().strip()
    keyword = None
    for pattern in _PATTERNS:
        m = re.search(pattern, msg)
        if m:
            keyword = m.group(1).strip().rstrip("?.").strip()
            break
    if not keyword:
        keyword = msg

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT label, username, password, url, notes FROM logins WHERE label LIKE ?",
        (f"%{keyword}%",),
    ).fetchall()
    conn.close()

    if not rows:
        return f"No login found matching '{keyword}'."

    parts = []
    for r in rows:
        lines = [r["label"]]
        if r["username"]:
            lines.append(f"Username: {r['username']}")
        if r["password"]:
            lines.append(f"Password: {r['password']}")
        if r["url"]:
            lines.append(f"URL: {r['url']}")
        if r["notes"]:
            lines.append(f"Notes: {r['notes']}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)
