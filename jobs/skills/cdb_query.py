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


def _pattern_match(question: str, last_sun: str, weeks: list) -> str | None:
    """Return a SQL query for common patterns — bypasses Ollama entirely."""
    q = question.lower().strip()

    # Determine campus filter
    campus = None
    if any(w in q for w in ['online', 'virtual', 'remote']):
        campus = 'Online'
    elif any(w in q for w in ['wilmington', 'in person', 'in-person', 'physical']):
        campus = 'Wilmington'

    # Determine date range
    date_filter = None
    if any(w in q for w in ['this past sunday', 'last sunday', 'this sunday', 'yesterday']):
        date_filter = f"a.service_date = '{last_sun}'"
    elif '6 week' in q or 'six week' in q:
        date_filter = f"a.service_date >= '{weeks[5]}' AND a.service_date <= '{last_sun}'"
    elif '5 week' in q or 'five week' in q:
        date_filter = f"a.service_date >= '{weeks[4]}' AND a.service_date <= '{last_sun}'"
    elif '4 week' in q or 'four week' in q:
        date_filter = f"a.service_date >= '{weeks[3]}' AND a.service_date <= '{last_sun}'"
    elif '3 week' in q or 'three week' in q:
        date_filter = f"a.service_date >= '{weeks[2]}' AND a.service_date <= '{last_sun}'"
    elif '2 week' in q or 'two week' in q:
        date_filter = f"a.service_date >= '{weeks[1]}' AND a.service_date <= '{last_sun}'"

    # Determine query type
    is_count = any(w in q for w in ['how many', 'count', 'total', 'number of'])
    is_list = any(w in q for w in ['who', 'list', 'names', 'show me', 'give me'])

    # Need at least a date or campus to build a reliable query
    if not date_filter and not campus:
        return None

    where_parts = []
    if campus:
        where_parts.append(f"a.campus = '{campus}'")
    if date_filter:
        where_parts.append(date_filter)
    where = ' AND '.join(where_parts)

    if is_count:
        return f"SELECT COUNT(DISTINCT a.member_id) as total FROM attendance a WHERE {where}"
    elif is_list or campus or date_filter:
        return f"SELECT DISTINCT m.name, a.campus, a.service_date FROM attendance a JOIN members m ON a.member_id = m.id WHERE {where} ORDER BY m.name"
    return None

def run(question: str) -> str:
    question = question.strip()
    if not question:
        return "No question provided."

    # Try pattern match first — bypasses Ollama for common attendance queries
    from datetime import date as _date, timedelta as _td
    _pm_sql = _pattern_match(question, _last_sunday(), [(_date.today() - _td(weeks=i)).strftime('%Y-%m-%d') for i in range(1, 7)])
    if _pm_sql:
        try:
            uri = f"file:{CONG_DB}?mode=ro"
            with sqlite3.connect(uri, uri=True) as _conn:
                _cur = _conn.execute(_pm_sql)
                rows = _cur.fetchall()
                cols = [d[0] for d in _cur.description]
            return _format_rows(rows, cols)
        except Exception as e:
            return f"SQL error: {e}\n\nGenerated query:\n{_pm_sql}"

    schema = _build_schema()
    from datetime import date
    today_str = date.today().strftime('%Y-%m-%d')
    last_sun = _last_sunday()
    weeks = [(date.today() - __import__('datetime').timedelta(weeks=i)).strftime('%Y-%m-%d') for i in range(1, 7)]
    date_hint = f"Today is {today_str}. Last Sunday was {last_sun}. Previous Sundays (most recent first): {', '.join(weeks)}. Dates stored as TEXT YYYY-MM-DD. NEVER use INTERVAL, DATE_SUB, or any date math functions — use only the literal dates provided above. For 'last 3 weeks' use: service_date >= '{weeks[2]}' AND service_date <= '{last_sun}'. For 'last 6 weeks' use: service_date >= '{weeks[5]}' AND service_date <= '{last_sun}'."
    join_hints = """
IMPORTANT JOIN RULES:
- To get member names from attendance: JOIN members m ON a.member_id = m.id — use m.name
- attendance has columns: id, member_id, service_date, campus, card_id, created_at
- members has columns: id, name, email, phone, campus_preference, status, active
- connect_cards has columns: id, member_id, service_date, campus, prayer_request, next_steps
- NEVER use t1.name or t2.name — attendance and connect_cards have no name column
- campus values are exactly 'Online' or 'Wilmington' (capital first letter) — always use exact case
- For attendance counts or lists, NEVER join to connect_cards — use attendance table directly or join members only
- connect_cards join is only needed when accessing prayer_request or next_steps fields
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
