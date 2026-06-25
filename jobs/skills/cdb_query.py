"""cdb_query.py — natural language query against congregation.db via Ollama."""
import re
import sqlite3
from pathlib import Path
from datetime import date, timedelta

import requests

OLLAMA_URL  = "http://localhost:11434/api/generate"
MODEL       = "llama3.2:3b"
CONG_DB     = Path(__file__).resolve().parents[2] / "data" / "congregation.db"
MAX_ROWS    = 20

_TABLES = [
    "members", "connect_cards", "attendance", "follow_ups",
    "prayer_requests", "next_steps", "duplicate_flags",
    "audit_exemptions", "member_conflicts",
]

_SYSTEM = (
    "You are a SQLite query generator. Return ONLY a valid SELECT statement. "
    "No explanation. No markdown. No extra text. "
    "Query the tables directly using their table names only — do not prefix with any filename or database name. Here is the schema:\n{schema}"
)


def _build_schema() -> str:
    conn = sqlite3.connect(str(CONG_DB))
    parts = []
    for table in _TABLES:
        try:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            col_defs = ", ".join(f"{c[1]} {c[2]}" for c in cols)
            parts.append(f"{table}({col_defs})")
        except Exception:
            pass
    conn.close()
    return "\n".join(parts)


def _extract_sql(raw: str) -> str:
    # Strip markdown fences
    raw = re.sub(r"```[a-z]*", "", raw).replace("```", "").strip()
    # Find the SELECT statement
    match = re.search(r"(SELECT\b.+)", raw, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    sql = match.group(1).strip()
    # Drop anything after a bare semicolon
    sql = re.split(r";\s*$", sql)[0].strip()
    return sql


def _format_rows(rows: list[sqlite3.Row], description) -> str:
    total = len(rows)
    display = rows[:MAX_ROWS]
    col_names = [d[0] for d in description]
    lines = []
    for row in display:
        parts = [f"{col}: {val}" for col, val in zip(col_names, row) if val is not None]
        lines.append("• " + " | ".join(parts))
    result = "\n".join(lines)
    if total > MAX_ROWS:
        result += f"\n\nShowing {MAX_ROWS} of {total} results."
    return result


def _last_sunday() -> str:
    today = date.today()
    days_since_saturday = (today.weekday() - 5) % 7
    days_back = (today.weekday() + 1) % 7 or 7
    last_sun = today - timedelta(days=days_back)
    return last_sun.strftime("%Y-%m-%d")

def run(question: str) -> str:
    question = question.strip()
    if not question:
        return "No question provided."

    schema = _build_schema()
    from datetime import date
    today_str = date.today().strftime('%Y-%m-%d')
    last_sun = _last_sunday()
    weeks = [(date.today() - __import__('datetime').timedelta(weeks=i)).strftime('%Y-%m-%d') for i in range(1, 7)]
    date_hint = f"Today is {today_str}. Last Sunday was {last_sun}. Previous Sundays: {', '.join(weeks)}. Dates stored as TEXT YYYY-MM-DD. For ranges use: service_date >= 'YYYY-MM-DD' AND service_date <= 'YYYY-MM-DD'."
    join_hints = """
IMPORTANT JOIN RULES:
- To get member names from attendance: JOIN members m ON a.member_id = m.id — use m.name
- attendance has columns: id, member_id, service_date, campus, card_id, created_at
- members has columns: id, name, email, phone, campus_preference, status, active
- connect_cards has columns: id, member_id, service_date, campus, prayer_request, next_steps
- NEVER use t1.name or t2.name — attendance and connect_cards have no name column
- campus values are exactly 'Online' or 'Wilmington' (capital first letter) — always use exact case
"""
    prompt = _SYSTEM.format(schema=schema) + f"\n\n{date_hint}\n\n{join_hints}\n\nQuestion: {question}"

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw_sql = resp.json().get("response", "").strip()
    except Exception as exc:
        return f"Ollama error: {exc}"

    sql = _extract_sql(raw_sql)
    if not sql:
        return f"Could not extract a valid SELECT statement from model response:\n{raw_sql}"

    # Safety: only allow SELECT
    if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
        return "Query rejected: only SELECT statements are permitted."

    try:
        uri = f"file:{CONG_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql)
        rows = cur.fetchall()
        desc = cur.description
        conn.close()
    except sqlite3.OperationalError as exc:
        return f"SQL error: {exc}\n\nGenerated query:\n{sql}"

    if not rows:
        return "No results found."

    return _format_rows(rows, desc)
