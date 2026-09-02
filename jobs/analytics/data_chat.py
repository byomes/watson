"""jobs/analytics/data_chat.py — open-ended attendance / web-traffic / contact
Q&A for the Telegram team-chat path in bot/bot.py.

Per Bill's 2026-09-02 decisions: (1) attendance and web-traffic questions
should have NOTHING off limits for team-chat users — not just the phrasings
anticipated in advance elsewhere in bot.py; (2) since only key Catalyst
leaders get Watson access at all, don't distinguish staff/elders/deacons —
every onboarded leader gets the same full access, including contact info,
via allow_contact_info (bot.py always passes True; the parameter exists so
a narrower future caller isn't required to reintroduce the plumbing). Two
domains, two separate SQLite files (never joined in one query):

  attendance — data/congregation.db: attendance, classroom_attendance,
               members (name/deacon/status/campus columns always; email/
               phone/address/birthdate included only when allow_contact_info
               is True). notes/status_note stay locked regardless -- per
               Bill's 2026-09-02 explicit call, those can hold prayer-
               request/pastoral content well beyond plain contact info.
  web        — data/watson.db: engagement_sheet_metrics only.

Reuses jobs.skills.cdb_query's battle-tested pattern-match layer (Bill's own
`cdb:` skill) as a free, LLM-free fast path for the common attendance
phrasings it already recognizes — its output is still run through the same
table/column validator below, so a branch that selects a blocked PII column
(a couple of its follow-up-list branches do, fine for Bill's own chat but
not here) or an out-of-scope table just falls through to this module's own
LLM-generated query instead of being trusted blind.

For anything pattern-match doesn't recognize, a single Ollama call
(qwen2.5-coder:7b — the same model cdb_query.py uses for SQL) both
classifies the question's domain and writes the SQL in one pass. The actual
result rows are formatted directly into the reply — no second LLM pass
restates the numbers, so a wrong answer can only come from a wrong query,
never a mis-remembered one.

Safety: whatever produces the SQL, it must be exactly one SELECT statement
referencing only whitelisted tables/columns for its domain — anything else
(a second statement, a write keyword, ATTACH/PRAGMA, an out-of-scope table,
a blocked PII column) is rejected outright, never "fixed up" or retried
blind. Execution is always against a mode=ro connection as defense in depth
even if a bad query somehow slipped past validation.
"""

import logging
import os
import re
import sqlite3
from datetime import date, timedelta

import requests

from config.settings import DB_PATH as _WATSON_DB_PATH

log = logging.getLogger(__name__)

CONGREGATION_DB_PATH = os.path.expanduser("~/watson/data/congregation.db")
WATSON_DB_PATH = str(_WATSON_DB_PATH)

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5-coder:7b"  # same model jobs/skills/cdb_query.py uses for SQL

_DB_PATH = {"attendance": CONGREGATION_DB_PATH, "web": WATSON_DB_PATH}

_ALLOWED_TABLES = {
    "attendance": {"attendance", "classroom_attendance", "members"},
    "web": {"engagement_sheet_metrics"},
}

# Per Bill's 2026-09-02 follow-ups ("only key leaders get Watson access, we
# don't need to worry too much about access" -> "allow birthdates, keep
# notes locked"), email/phone/address/birthdate are allowed for team-chat
# (allow_contact_info=True) -- toggled per-caller in bot.py, not a blanket
# unblock. notes/status_note stay blocked unconditionally -- explicitly kept
# locked since they can hold pastoral/prayer content well beyond contact
# info. carrier/household_id/snowbird_return/deacon_status/status_reason
# have no contact-info or attendance meaning either way, so they stay
# blocked too.
_CONTACT_COLUMN_WORDS = {"email", "phone", "address", "birthdate"}
_ALWAYS_BLOCKED_COLUMN_WORDS = {
    "notes", "carrier", "household_id", "status_note",
    "snowbird_return", "deacon_status", "status_reason", "ssn",
}


def _attendance_schema(allow_contact_info: bool) -> str:
    contact_cols = ", email TEXT, phone TEXT, address TEXT, birthdate TEXT" if allow_contact_info else ""
    return (
        "attendance(member_id INTEGER, service_date TEXT, campus TEXT)\n"
        "  -- one row per person per service actually attended. campus is 'Wilmington' or 'Online'.\n"
        "classroom_attendance(date TEXT, kids_nursery, adults_nursery, kids_toddlers, adults_toddlers, kids_prek, adults_prek, kids_elementary, adults_elementary INTEGER)\n"
        "  -- one row per Sunday with headcounts for each of the 4 kids' classrooms.\n"
        f"members(id INTEGER, name TEXT, deacon TEXT, status TEXT, member_status TEXT, campus_preference TEXT, first_visit_date TEXT, active INTEGER, partnership_status TEXT{contact_cols})\n"
        "  -- deacon holds the NAME of the deacon shepherding that member -- \"a deacon's group\" is every member row with that deacon value.\n"
        "  -- join attendance.member_id = members.id for a specific person's or group's attendance."
    )

_WEB_SCHEMA = """
engagement_sheet_metrics(tab TEXT, section TEXT, metric_label TEXT, month TEXT, value_numeric REAL, value_raw TEXT)
  -- month is 'YYYY-MM-01', one row per metric per month. section/metric_label pairs:
  --   'E Mails/Website': New Web Users, Active Web Users, Avg Engagement Time (sec), Event Count, Email Campaigns Sent, Emails Opened, Email Links Clicked, Total Emails Sent
  --   'Aquisitions': Direct Link, Organic Search, Social/Referrals
  --   'Social Media': Facebook Post Likes, Facebook Post Shares, Facebook Followers, Total Facebook Posts, Instagram Post Likes, Instagram Post Shares, Instagram Followers, Total Instagram Posts
  --   'Catalyt App Engagement' (sic, matches the sheet as-is): App Downloads, App Impressions, App Launches
  --   'Top Page Views': metric_label is 'Top Page 1'..'Top Page 5', value_raw is the page name, value_numeric is its share (0-1)
""".strip()

_SYSTEM_TEMPLATE = """You are a SQL query generator for a church's internal Telegram assistant. \
Decide whether the question falls into the ATTENDANCE domain or the WEB domain, or neither. ATTENDANCE \
covers not just worship-service/classroom attendance counts but ANY question about a member's own record \
in the members table below -- contact info, birthdate, their deacon/group, status -- since that table lives \
in the same domain. WEB covers social-media/website traffic metrics. If it's one of those two, write ONE \
single-line read-only SQLite SELECT statement that answers it exactly, using ONLY the tables and columns \
listed below -- never invent a table or column, never write anything but SELECT. If the question has no \
month/date range and asks for a current or total count ("how many X do we have", "what's our X"), use the \
single most recent row (ORDER BY month DESC LIMIT 1 for web metrics) rather than every historical row.
Today's date is {today}.

ATTENDANCE tables (file: congregation.db):
{attendance_schema}

WEB tables (file: watson.db -- a different file, never mixed with attendance tables in one query):
{web_schema}

Reply with EXACTLY this format and nothing else:
DOMAIN: attendance|web|none
SQL: <single-line SELECT -- omit this line entirely if DOMAIN is none>

Q: what is the average attendance for the last four weeks?
DOMAIN: attendance
SQL: SELECT AVG(cnt) FROM (SELECT service_date, COUNT(*) AS cnt FROM attendance GROUP BY service_date ORDER BY service_date DESC LIMIT 4)

Q: how many app downloads did we get in August?
DOMAIN: web
SQL: SELECT value_raw FROM engagement_sheet_metrics WHERE section = 'Catalyt App Engagement' AND metric_label = 'App Downloads' AND month LIKE '2026-08%'

Q: how many facebook followers do we have?
DOMAIN: web
SQL: SELECT value_raw FROM engagement_sheet_metrics WHERE section = 'Social Media' AND metric_label = 'Facebook Followers' ORDER BY month DESC LIMIT 1

Q: how is Jim Bouchat's group doing for attendance?
DOMAIN: attendance
SQL: SELECT COUNT(*) FROM attendance WHERE member_id IN (SELECT id FROM members WHERE deacon = 'Jim Bouchat') AND service_date >= date('now', '-4 weeks')
{contact_example}"""

_CONTACT_ALLOWED_EXAMPLE = """
Q: what's Kaci's phone number?
DOMAIN: attendance
SQL: SELECT phone FROM members WHERE name = 'Kaci Gravatt'

Q: when is Tara Mathena's birthday?
DOMAIN: attendance
SQL: SELECT birthdate FROM members WHERE name = 'Tara Mathena'
"""

_CONTACT_BLOCKED_EXAMPLE = """
Q: what's Kaci's phone number?
DOMAIN: none
"""

# Found 2026-09-02 testing: passing asker_name only via the system prompt's
# "the person asking is named X" context is NOT reliable -- qwen2.5-coder:7b
# repeatedly ignored it and hallucinated an unrelated name (e.g. Jim Bouchat
# asking "how is my group doing" produced `deacon = 'Kaci'`, someone else
# entirely, with no error and a confident-looking answer). Substituting the
# literal name into the question text itself before generation is far more
# reliable -- the model only has to read a concrete name out of the
# question, not resolve an abstract pronoun using separately-supplied
# context it's free to ignore.
_FIRST_PERSON_RE = re.compile(r"\b(my|our|mine|ours)\b", re.IGNORECASE)
_FIRST_PERSON_SUBJECT_RE = re.compile(r"\bI\b")


def _resolve_first_person(question: str, asker_name: str) -> str:
    q = _FIRST_PERSON_RE.sub(f"{asker_name}'s", question)
    q = _FIRST_PERSON_SUBJECT_RE.sub(asker_name, q)
    return q


_DOMAIN_RE = re.compile(r"DOMAIN:\s*(attendance|web|none)", re.IGNORECASE)
_SQL_RE = re.compile(r"SQL:\s*(.+)", re.IGNORECASE | re.DOTALL)
_FORBIDDEN_SQL_RE = re.compile(
    r";|--|/\*|\b(insert|update|delete|drop|alter|attach|detach|pragma|create|replace|vacuum|reindex)\b",
    re.IGNORECASE,
)
_TABLE_RE = re.compile(r"\bfrom\s+([a-zA-Z_]\w*)|\bjoin\s+([a-zA-Z_]\w*)", re.IGNORECASE)


def _generate(question: str, asker_name: str, allow_contact_info: bool) -> tuple[str | None, str | None]:
    """Returns (domain, sql). domain is None if the call failed or the
    model's output couldn't be parsed at all; "none" if it parsed fine but
    the question isn't attendance/web-traffic."""
    system = _SYSTEM_TEMPLATE.format(
        today=date.today().isoformat(),
        attendance_schema=_attendance_schema(allow_contact_info),
        web_schema=_WEB_SCHEMA,
        contact_example=_CONTACT_ALLOWED_EXAMPLE if allow_contact_info else _CONTACT_BLOCKED_EXAMPLE,
    )
    resolved_question = _resolve_first_person(question, asker_name)
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": resolved_question},
                ],
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=90,
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"].strip()
    except Exception as exc:
        log.error("data_chat: generation call failed: %s", exc)
        return None, None

    dmatch = _DOMAIN_RE.search(content)
    if not dmatch:
        log.warning("data_chat: no DOMAIN line in model output: %r", content)
        return None, None
    domain = dmatch.group(1).lower()
    if domain == "none":
        return "none", None

    smatch = _SQL_RE.search(content)
    if not smatch:
        log.warning("data_chat: DOMAIN=%s but no SQL line in: %r", domain, content)
        return domain, None

    sql = smatch.group(1).strip().strip("`")
    sql = re.sub(r"^sql\s*\n", "", sql, flags=re.IGNORECASE)
    sql = " ".join(sql.split())  # flatten to one line even if the model wrapped anyway
    return domain, sql.rstrip(";")


def _validate_sql(domain: str, sql: str | None, allow_contact_info: bool) -> str | None:
    """Returns sql unchanged if it's a single safe SELECT scoped to
    `domain`'s whitelisted tables/columns, else None."""
    if not sql or not sql.lower().lstrip().startswith("select"):
        return None
    if _FORBIDDEN_SQL_RE.search(sql):
        log.warning("data_chat: rejected SQL (forbidden token), domain=%s: %s", domain, sql)
        return None
    tables = {(m.group(1) or m.group(2)).lower() for m in _TABLE_RE.finditer(sql)}
    if not tables or not tables.issubset(_ALLOWED_TABLES[domain]):
        log.warning("data_chat: rejected SQL (tables %s not subset of %s): %s", tables, _ALLOWED_TABLES[domain], sql)
        return None
    if "members" in tables:
        blocked = _ALWAYS_BLOCKED_COLUMN_WORDS if allow_contact_info else _ALWAYS_BLOCKED_COLUMN_WORDS | _CONTACT_COLUMN_WORDS
        lowered = sql.lower()
        for word in blocked:
            if re.search(rf"\b{word}\b", lowered):
                log.warning("data_chat: rejected SQL (blocked column %r): %s", word, sql)
                return None
    return sql


def _run(domain: str, sql: str) -> list[dict] | None:
    path = _DB_PATH[domain]
    exec_sql = sql if re.search(r"\blimit\b", sql, re.IGNORECASE) else sql + " LIMIT 50"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(exec_sql)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        log.error("data_chat: query failed (%s): %s", exc, exec_sql)
        return None


def _fmt_value(v):
    if isinstance(v, float):
        v = round(v, 1)
    return "—" if v is None else v


def _format_rows(rows: list[dict]) -> str:
    if not rows:
        return "That didn't turn up any matching data."
    if len(rows) == 1 and len(rows[0]) == 1:
        return str(_fmt_value(next(iter(rows[0].values()))))
    if len(rows) == 1:
        return ", ".join(f"{k}: {_fmt_value(v)}" for k, v in rows[0].items())
    lines = [", ".join(f"{k}: {_fmt_value(v)}" for k, v in r.items()) for r in rows[:25]]
    if len(rows) > 25:
        lines.append(f"... and {len(rows) - 25} more rows.")
    return "\n".join(lines)


def _try_pattern_match(question: str) -> str | None:
    """Bill's own cdb_query.py pattern-match layer, reused as a free
    LLM-free fast path for the common attendance phrasings it already
    recognizes. Its output still goes through _validate_sql() in the
    caller before being trusted."""
    try:
        from jobs.skills.cdb_query import _pattern_match, _last_sunday
    except Exception:
        return None
    weeks = [(date.today() - timedelta(weeks=i)).strftime("%Y-%m-%d") for i in range(1, 13)]
    return _pattern_match(question, _last_sunday(), weeks)


def answer_data_question(
    question: str, asker_name: str, allow_contact_info: bool = True
) -> tuple[bool, str | None]:
    """Try to answer an attendance/web-traffic (and, per Bill's 2026-09-02
    decision that all Catalyst leaders get full access with no distinction
    between staff/elders/deacons, contact-info) question with a real query.

    Returns (on_topic, reply). on_topic=False means the question wasn't one
    of those at all -- the caller decides what to do with an off-topic
    question. on_topic=True always comes with a reply (an answer, or an
    apologetic failure message) and should be sent back as-is.
    """
    pm_sql = _validate_sql("attendance", _try_pattern_match(question), allow_contact_info)
    if pm_sql:
        rows = _run("attendance", pm_sql)
        if rows is not None:
            log.info("data_chat: pattern-match hit, asker=%s q=%r sql=%r rows=%d", asker_name, question, pm_sql, len(rows))
            return True, _format_rows(rows)

    domain, sql = _generate(question, asker_name, allow_contact_info)
    if domain in (None, "none"):
        return False, None

    validated = _validate_sql(domain, sql, allow_contact_info)
    if not validated:
        return True, "I couldn't work out a safe way to answer that from the data I have — try rephrasing, or ask Dr. Bill."

    rows = _run(domain, validated)
    if rows is None:
        return True, "I hit an error pulling that data — try again in a moment."

    log.info("data_chat: domain=%s asker=%s q=%r sql=%r rows=%d", domain, asker_name, question, validated, len(rows))
    return True, _format_rows(rows)
