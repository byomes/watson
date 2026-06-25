"""cdb_query.py — natural language query against congregation.db via Ollama."""
import re
import sqlite3
from pathlib import Path

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
    "The database is congregation.db. Here is the schema:\n{schema}"
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


def run(question: str) -> str:
    question = question.strip()
    if not question:
        return "No question provided."

    schema = _build_schema()
    prompt = _SYSTEM.format(schema=schema) + f"\n\nQuestion: {question}"

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
