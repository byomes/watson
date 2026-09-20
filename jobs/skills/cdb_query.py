"""cdb_query.py — natural language query against congregation.db via Ollama."""
import calendar
import re
import sqlite3
from pathlib import Path
from datetime import date, timedelta

import requests
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py

OLLAMA_URL  = "http://localhost:11434/api/generate"
MODEL       = "qwen2.5-coder:7b"
CONG_DB     = Path(__file__).resolve().parents[2] / "data" / "congregation.db"
MAX_ROWS    = 20

_TABLES = [
    "members", "connect_cards", "attendance", "follow_ups", "deacon_notes",
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


_MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


# Matches "how many people/members/folks (have) attended/came/showed up" --
# the literal-substring trigger lists elsewhere in this file (e.g. 'how many
# attended') miss this whenever a word sits between "how many" and the verb,
# which is a completely normal way to phrase the question ("how many people
# have attended..."). Found live 2026-09-16: exactly this phrasing fell
# through the COMBINED + CUMULATIVE ATTENDANCE block below and the single-
# Sunday HOW MANY ATTENDED block, landing on the Ollama SQL-generation
# fallback, which is far less reliable (returned a bare "0" in production).
_COUNT_ATTENDED_RE = re.compile(r"how many\b.{0,25}\b(attended|came|showed up|were there)\b")

# Number words up to twelve, for "how many attended in the past six weeks" --
# spelled-out counts are just as common in speech as digits.
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUMBER_ALT = "|".join(_NUMBER_WORDS)
# Was hardcoded to only the literal phrases "2 week"/"two week" through
# "6 week"/"six week" (2026-09-16) -- fixed 2026-09-16 to accept any digit or
# spelled-out count 1-999 so "the last 8 weeks" / "the last 12 months" work
# too, not just the 6 spans someone happened to hardcode first.
_WEEK_SPAN_RE = re.compile(rf"\b(\d{{1,3}}|{_NUMBER_ALT})[- ]?weeks?\b")
_MONTH_SPAN_RE = re.compile(rf"\b(\d{{1,2}}|{_NUMBER_ALT})[- ]?months?\b")
_BARE_LAST_MONTH_RE = re.compile(r"\b(last|past|previous)\s+month\b")
_THIS_MONTH_RE = re.compile(r"\b(this|current)\s+month\b")


def _span_number(match: re.Match) -> int:
    raw = match.group(1)
    return _NUMBER_WORDS.get(raw, int(raw) if raw.isdigit() else 0)


def _months_ago(d: date, n: int) -> date:
    """Calendar-correct 'n calendar months before d', clamping the day of
    month for shorter target months (e.g. Mar 31 minus 1 month -> Feb 28)."""
    months_total = d.month - 1 - n
    year = d.year + months_total // 12
    month = months_total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _last_sunday() -> str:
    today = date.today()
    days_since_saturday = (today.weekday() - 5) % 7
    days_back = (today.weekday() + 1) % 7 or 7
    last_sun = today - timedelta(days=days_back)
    return last_sun.strftime("%Y-%m-%d")


def _pattern_match(question: str, last_sun: str, weeks: list) -> str | None:
    """Return a SQL query for common patterns — bypasses Ollama entirely."""
    q = question.lower().strip()

    # Campus filter
    campus = None
    if any(w in q for w in ['online', 'virtual', 'remote', 'stream', 'streaming']):
        campus = 'Online'
    elif any(w in q for w in ['wilmington', 'in person', 'in-person', 'physical', 'building', 'church building']):
        campus = 'Wilmington'

    # Date range — a_date uses alias prefix for main queries; s_date is bare for
    # subqueries. _span_label tracks a plain-English name ("the last 6 weeks",
    # "the last month", "the current month") for the COMBINED/CUMULATIVE
    # ATTENDANCE block below -- None for a single-Sunday question, where
    # combined and cumulative are the same number and that block doesn't apply.
    #
    # Was hardcoded to only literal '2 week'..'6 week' phrases and had no
    # concept of calendar months at all -- found live 2026-09-16 asking
    # "how many people attended last month", which silently fell back to
    # counting just last Sunday and reported that as the answer. Fixed to
    # accept any week count via _WEEK_SPAN_RE, and real calendar-month ranges
    # via _MONTH_SPAN_RE / _BARE_LAST_MONTH_RE / _THIS_MONTH_RE.
    _span_label = None
    today = date.today()
    _week_m = _WEEK_SPAN_RE.search(q)
    _month_m = _MONTH_SPAN_RE.search(q)
    if any(w in q for w in ['this past sunday', 'last sunday', 'this sunday']):
        a_date = f"a.service_date = '{last_sun}'"
        s_date = f"service_date = '{last_sun}'"
    elif _THIS_MONTH_RE.search(q):
        start = today.replace(day=1).isoformat()
        a_date = f"a.service_date >= '{start}' AND a.service_date <= '{today.isoformat()}'"
        s_date = f"service_date >= '{start}' AND service_date <= '{today.isoformat()}'"
        _span_label = "the current month"
    elif _week_m and _span_number(_week_m) >= 1:
        n = _span_number(_week_m)
        start = (today - timedelta(weeks=n)).isoformat()
        a_date = f"a.service_date >= '{start}' AND a.service_date <= '{last_sun}'"
        s_date = f"service_date >= '{start}' AND service_date <= '{last_sun}'"
        _span_label = "the last week" if n == 1 else f"the last {n} weeks"
    elif _month_m and _span_number(_month_m) >= 1:
        n = _span_number(_month_m)
        start = _months_ago(today, n).isoformat()
        a_date = f"a.service_date >= '{start}' AND a.service_date <= '{last_sun}'"
        s_date = f"service_date >= '{start}' AND service_date <= '{last_sun}'"
        _span_label = "the last month" if n == 1 else f"the last {n} months"
    elif _BARE_LAST_MONTH_RE.search(q):
        first_this_month = today.replace(day=1)
        last_day_prev = first_this_month - timedelta(days=1)
        first_day_prev = last_day_prev.replace(day=1)
        a_date = f"a.service_date >= '{first_day_prev.isoformat()}' AND a.service_date <= '{last_day_prev.isoformat()}'"
        s_date = f"service_date >= '{first_day_prev.isoformat()}' AND service_date <= '{last_day_prev.isoformat()}'"
        _span_label = "the last month"
    else:
        a_date = f"a.service_date = '{last_sun}'"
        s_date = f"service_date = '{last_sun}'"

    campus_sub = f" AND campus = '{campus}'" if campus else ""

    # Check order: slipping → hybrid → missed_count → missed → trend → count → attended

    # SLIPPING AWAY / NEEDS SHEPHERDING
    if any(w in q for w in ['slipping', 'falling off', 'not coming', 'stopped coming', 'needs attention', 'shepherding', 'missing recently', 'fading', 'drifting', 'losing touch', 'falling away', 'at risk of leaving']):
        w5  = weeks[4]  if len(weeks) > 4  else weeks[-1]
        w12 = weeks[11] if len(weeks) > 11 else weeks[-1]
        w4  = weeks[3]  if len(weeks) > 3  else weeks[-1]
        return (
            f"SELECT m.name, MAX(a.service_date) as last_seen "
            f"FROM members m JOIN attendance a ON a.member_id = m.id "
            f"WHERE m.active = 1 AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' "
            f"AND m.id IN (SELECT DISTINCT member_id FROM attendance WHERE service_date >= '{w12}' AND service_date <= '{w5}'{campus_sub}) "
            f"AND m.id NOT IN (SELECT DISTINCT member_id FROM attendance WHERE service_date >= '{w4}'{campus_sub}) "
            f"GROUP BY m.id, m.name ORDER BY last_seen DESC"
        )

    # HYBRID MEMBERS
    # 'both campus'/'both campuses' removed from this trigger list 2026-09-14
    # -- found live colliding with an ordinary combined-total question ("how
    # many people ... attended church on both campuses this past Sunday?"),
    # which this file's own docstring convention (see feedback memory on
    # fast-path phrasing collisions) says to fix by dropping the generic
    # trigger rather than trying to out-guess it with more regex. The
    # remaining triggers are all genuinely hybrid-specific -- nobody asking
    # a simple weekly headcount says "hybrid", "switches", or "multi campus".
    if any(w in q for w in ['hybrid', 'online and wilmington', 'wilmington and online', 'switches', 'multi campus']):
        w12 = weeks[11] if len(weeks) > 11 else weeks[-1]
        return (
            f"SELECT m.name, "
            f"SUM(CASE WHEN a.campus='Online' THEN 1 ELSE 0 END) as online_count, "
            f"SUM(CASE WHEN a.campus='Wilmington' THEN 1 ELSE 0 END) as wilm_count "
            f"FROM attendance a JOIN members m ON a.member_id = m.id "
            f"WHERE a.service_date >= '{w12}' AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' "
            f"GROUP BY m.name HAVING online_count >= 2 AND wilm_count >= 2 ORDER BY m.name"
        )

    # HOW MANY MISSED (count)
    if any(w in q for w in ["how many missed", "how many didn't", "how many were absent", "how many did not", "how many were missing", "miss count"]):
        return (
            f"SELECT COUNT(DISTINCT m.id) as missed_count FROM members m "
            f"WHERE m.active = 1 AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' AND m.id NOT IN ("
            f"SELECT DISTINCT member_id FROM attendance WHERE {s_date}{campus_sub})"
        )

    # WHO MISSED
    if any(w in q for w in ["who missed", "who didn't attend", "who wasn't there", "who was absent", "who didn't come", "who did not attend", "who did not come", "absent", "who no-showed", "no shows", "didn't make it"]):
        return (
            f"SELECT m.name FROM members m "
            f"WHERE m.active = 1 AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' AND m.id NOT IN ("
            f"SELECT DISTINCT member_id FROM attendance WHERE {s_date}{campus_sub}) "
            f"ORDER BY m.name"
        )

    # ATTENDANCE TREND
    if any(w in q for w in ['how many people have been to church in the last six weeks', 'last six weeks', 'trend', 'trending', 'attendance over', 'attendance by week', 'weekly attendance', 'how has attendance', 'campus breakdown', 'attendance history', 'attendance pattern']):
        w8 = weeks[7] if len(weeks) > 7 else weeks[-1]
        return (
            f"SELECT a.service_date, "
            f"SUM(CASE WHEN a.campus='Online' THEN 1 ELSE 0 END) as online_count, "
            f"SUM(CASE WHEN a.campus='Wilmington' THEN 1 ELSE 0 END) as wilm_count "
            f"FROM attendance a WHERE a.service_date >= '{w8}' "
            f"GROUP BY a.service_date ORDER BY a.service_date"
        )

    # COMBINED + CUMULATIVE ATTENDANCE OVER A SPAN (weeks or calendar months)
    # Bill's 2026-09-16 request: a plain "attendance for the last N weeks/
    # months" question is ambiguous between two real numbers -- combined
    # (every check-in across those Sundays added together, so someone who
    # came every week counts once per week) and cumulative (how many
    # different people came at least once, each counted only once). Rather
    # than guess which one he means, return both with a plain-English
    # explanation. Only fires when _span_label names an actual multi-Sunday
    # range (see date-range block above); a single-Sunday question has no
    # such ambiguity (the two numbers are identical) and stays on the
    # simpler COUNT below. Requires the noun "attendance" or a "how many ...
    # attended/came" count shape (see _COUNT_ATTENDED_RE) and excludes
    # "who"/"list" so a "who attended in the last 3 weeks" or "list
    # attendance for the last 4 weeks" question still falls through to WHO
    # ATTENDED below instead of being swallowed as a count -- see the
    # fast-path phrasing collision feedback memory this file already follows
    # elsewhere (e.g. the HYBRID MEMBERS trigger comment above).
    if _span_label and 'who' not in q and 'list' not in q and ('attendance' in q or _COUNT_ATTENDED_RE.search(q)):
        campus_filter = f"a.campus = '{campus}' AND " if campus else ""
        campus_literal = f"'{campus}'" if campus else "NULL"
        return (
            f"SELECT '{_span_label}' as span_label, {campus_literal} as campus, "
            f"COUNT(a.member_id) as combined_total, COUNT(DISTINCT a.member_id) as unique_individuals "
            f"FROM attendance a WHERE {campus_filter}{a_date}"
        )

    # HOW MANY ATTENDED (count)
    # _COUNT_ATTENDED_RE catches phrasings like "how many people have
    # attended" that the substring list below misses (word between "how
    # many" and the verb) -- same fix as the multi-week block above.
    if _COUNT_ATTENDED_RE.search(q) or any(w in q for w in ['nursery attendance', "what's the attendance count?", 'how many attended', 'how many came', 'total attendance', 'attendance count', 'number who attended', 'sunday attendance', 'service attendance', 'how many showed up', 'how many people were there']):
        if campus:
            return f"SELECT COUNT(DISTINCT a.member_id) as total FROM attendance a WHERE a.campus = '{campus}' AND {a_date}"
        else:
            return (
                f"SELECT a.campus, COUNT(DISTINCT a.member_id) as total "
                f"FROM attendance a WHERE {a_date} GROUP BY a.campus ORDER BY a.campus"
            )

    # WHO ATTENDED
    if any(w in q for w in ['who attended', 'who came', 'who was there', 'who showed up', 'list attendance', 'attendee list', 'who was in service', 'who was at church']):
        if campus:
            return (
                f"SELECT DISTINCT m.name FROM attendance a "
                f"JOIN members m ON a.member_id = m.id "
                f"WHERE a.campus = '{campus}' AND {a_date} AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' ORDER BY m.name"
            )
        else:
            return (
                f"SELECT DISTINCT m.name, a.campus FROM attendance a "
                f"JOIN members m ON a.member_id = m.id "
                f"WHERE {a_date} AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' ORDER BY a.campus, m.name"
            )

    # MEMBERS NOT SEEN RECENTLY
    # (The literal 'when was the last time [name] missed church' trigger —
    # brackets and all — used to live in this list, added 2026-09-15 by an
    # auto-applied fast-path suggestion (commit ee8c4d9). Same class of dead
    # trigger already cleaned up elsewhere in this function 2026-09-12: a
    # real question never contains the literal substring "[name]", so it
    # could never fire. Removed; LAST ATTENDED / LAST MISSED BY NAME below
    # is the real, working pattern for that shape of question.)
    if any(w in q for w in ['not seen', "haven't seen", 'not attended', 'not been', 'missing for',
                             'inactive', 'not come in', "haven't attended", "haven't come",
                             "haven't shown up", 'off the radar']):
        if '2 week' in q or 'two week' in q or '14 day' in q:
            days = 14
        elif '60 day' in q:
            days = 60
        elif '90 day' in q or '3 month' in q or 'three month' in q:
            days = 90
        else:
            days = 30
        cutoff = (date.today() - timedelta(days=days)).strftime('%Y-%m-%d')
        return (
            f"SELECT m.name, m.email, m.phone "
            f"FROM members m "
            f"WHERE m.active = 1 AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' "
            f"AND m.id NOT IN ("
            f"SELECT DISTINCT member_id FROM attendance "
            f"WHERE service_date >= '{cutoff}'"
            f") ORDER BY m.name"
        )

    # FIRST-TIME VISITORS (checked before new-members to catch "first time visitor" specifically)
    if any(w in q for w in ['first time visitor', 'first-time visitor', 'visitors this',
                             'new visitor', 'guests', 'newcomers']):
        today = date.today()
        cutoff = today.replace(day=1).strftime('%Y-%m-%d') if 'this month' in q else (today - timedelta(days=14)).strftime('%Y-%m-%d')
        return (
            f"SELECT name, email, phone, first_visit_date "
            f"FROM members "
            f"WHERE status = 'visitor' AND name NOT LIKE '%CAMPUS%' AND name NOT LIKE '%SYSTEM%' AND name NOT LIKE '%TEST%' "
            f"AND first_visit_date >= '{cutoff}' "
            f"ORDER BY first_visit_date DESC"
        )

    # NEW MEMBERS / RECENT JOINS
    if any(w in q for w in ['new member', 'new people', 'new person', 'joined',
                             'recently joined', 'added this', 'new this month', 'new this week',
                             'new last month', 'first visit', 'first time', 'first-time',
                             'newest members', 'recent additions', 'who joined recently']):
        today = date.today()
        if 'this week' in q:
            start = today - timedelta(days=today.weekday())
            return (
                f"SELECT name, email, phone, created_at FROM members "
                f"WHERE active = 1 AND name NOT LIKE '%CAMPUS%' AND name NOT LIKE '%SYSTEM%' AND name NOT LIKE '%TEST%' AND created_at >= '{start.strftime('%Y-%m-%d')}' "
                f"ORDER BY created_at DESC"
            )
        elif 'last month' in q:
            first_of_this = today.replace(day=1)
            lm_start = (first_of_this - timedelta(days=1)).replace(day=1)
            lm_end = first_of_this - timedelta(days=1)
            return (
                f"SELECT name, email, phone, created_at FROM members "
                f"WHERE active = 1 AND name NOT LIKE '%CAMPUS%' AND name NOT LIKE '%SYSTEM%' AND name NOT LIKE '%TEST%' "
                f"AND created_at >= '{lm_start.strftime('%Y-%m-%d')}' "
                f"AND created_at <= '{lm_end.strftime('%Y-%m-%d')}' "
                f"ORDER BY created_at DESC"
            )
        else:
            start = today.replace(day=1) if 'this month' in q else today - timedelta(days=30)
            return (
                f"SELECT name, email, phone, created_at FROM members "
                f"WHERE active = 1 AND name NOT LIKE '%CAMPUS%' AND name NOT LIKE '%SYSTEM%' AND name NOT LIKE '%TEST%' AND created_at >= '{start.strftime('%Y-%m-%d')}' "
                f"ORDER BY created_at DESC"
            )

    # DEACON GROUP MEMBERSHIP -- checked before MEMBER LOOKUP BY NAME below,
    # whose 'who is' trigger would otherwise swallow "who is in X's deacon
    # group" as a (wrong, always-empty) direct name search instead of a
    # group lookup, silently falling through to an LLM call every time.
    # members.deacon holds the free-text name of the deacon shepherding
    # that member -- "X's (deacon) group" means WHERE deacon LIKE '%X%'
    # directly (see jobs/analytics/data_chat.py's schema comment, same rule).
    _deacon_m = re.search(r"(\w+(?:\s+\w+)?)'s\s+(?:deacon\s+)?group\b", q)
    if not _deacon_m:
        _deacon_m = re.search(r"who\s+does\s+(\w+(?:\s+\w+)?)\s+shepherd", q)
    if _deacon_m:
        # The optional second-word group above can greedily pull in a
        # leading preposition ("members OF kaci's group" -> "of kaci")
        # since it doesn't know which of the (up to) two captured words is
        # actually part of the name -- drop any leading stopword instead of
        # trying to make the regex itself smarter about it.
        _deacon_words = [w for w in _deacon_m.group(1).strip().split()]
        _deacon_stopwords = ('of', 'in', 'the', 'a', 'is', 'for', 'list', 'show', 'me', 'give', 'display', 'tell')
        while _deacon_words and _deacon_words[0].lower() in _deacon_stopwords:
            _deacon_words.pop(0)
        deacon_name = " ".join(_deacon_words)
        if deacon_name:
            return (
                f"SELECT name FROM members "
                f"WHERE deacon LIKE '%{deacon_name}%' AND active = 1 "
                f"AND name NOT LIKE '%CAMPUS%' AND name NOT LIKE '%SYSTEM%' AND name NOT LIKE '%TEST%' "
                f"ORDER BY name"
            )

    # SPOUSE LOOKUP -- checked before MEMBER LOOKUP BY NAME below, whose
    # generic 'who is' trigger would otherwise swallow "who is X married to"
    # as a literal (always-empty) name search for someone named "X married
    # to". Uses household_id/household_role (added 2026-09-12, see
    # jobs/congregation/family_edit.py) via the same self-join pattern as
    # jobs/analytics/data_chat.py's spouse examples. Found 2026-09-12
    # reviewing this file's fast-path phrasing: the trigger list below used
    # to contain the literal string 'who is [name] married to' (brackets and
    # all), which can never match real text containing an actual name --
    # this question fell all the way through to the generic name-search
    # fallback instead, which itself mishandled it the same way. Replaced
    # with an actual working pattern below.
    _spouse_m = re.search(r"who\s+is\s+(\w+(?:\s+\w+)?)\s+married\s+to\b", q)
    if not _spouse_m:
        _spouse_m = re.search(r"is\s+(\w+(?:\s+\w+)?)\s+married\b", q)
    if not _spouse_m:
        _spouse_m = re.search(r"(\w+(?:\s+\w+)?)'s\s+spouse\b", q)
    if _spouse_m:
        spouse_name = _spouse_m.group(1).strip()
        if spouse_name:
            return (
                f"SELECT m2.name FROM members m1 JOIN members m2 ON m2.household_id = m1.household_id "
                f"AND m2.id != m1.id WHERE m1.name LIKE '%{spouse_name}%' AND m1.active = 1 "
                f"AND m1.household_role IN ('husband','wife') AND m2.household_role IN ('husband','wife')"
            )

    # MEMBER'S OWN DEACON -- checked before MEMBER LOOKUP BY NAME for the
    # same reason as SPOUSE LOOKUP above. Distinct from DEACON GROUP
    # MEMBERSHIP earlier in this function, which lists everyone a GIVEN
    # deacon shepherds -- this answers who shepherds ONE member. Also
    # replaces a dead 'does [name] have a deacon' literal-bracket trigger
    # found the same review pass as SPOUSE LOOKUP above.
    _member_deacon_m = re.search(r"does\s+(\w+(?:\s+\w+)?)\s+have\s+a\s+deacon\b", q)
    if not _member_deacon_m:
        _member_deacon_m = re.search(r"who\s+is\s+(\w+(?:\s+\w+)?)'s\s+deacon\b", q)
    if _member_deacon_m:
        member_name = _member_deacon_m.group(1).strip()
        if member_name:
            return f"SELECT name, deacon FROM members WHERE name LIKE '%{member_name}%' AND active = 1"

    # LAST ATTENDED / LAST MISSED BY NAME -- checked before MEMBER LOOKUP BY
    # NAME for the same reason as SPOUSE LOOKUP above. Distinct from MEMBERS
    # NOT SEEN RECENTLY earlier in this function, which lists EVERY inactive
    # member -- this answers "when did/was the last time ONE named person
    # attended/missed church", the Team Chat equivalent of bot.py's DM-only
    # _extract_team_lookup "last_seen"/"last_missed" fields (that fast path
    # isn't reachable from Team Chat, which only ever calls this file's
    # _pattern_match).
    #
    # Split into two branches 2026-09-15: the miss verbs used to share the
    # SAME regex/SQL as the attend verbs, so "when did X last miss church"
    # silently returned X's last ATTENDED date mislabeled as an answer to a
    # miss question. Also added campus (via a correlated subquery over the
    # attendance/connect_cards union, so it names whichever record actually
    # produced the max date) and a real last-missed calc: the most recent
    # service_date the whole church held (distinct dates in attendance) that
    # doesn't appear among this member's own attendance rows. No campus for
    # a miss -- there's no campus for a service someone wasn't at.
    # jobs/analytics/data_chat.py's _format_rows special-cases these exact
    # column shapes to route through jobs/analytics/attendance_reply.py
    # instead of the generic "col: val" dump.
    _last_missed_m = re.search(
        r"when\s+(?:was|is|did)\s+(?:the\s+last\s+time\s+)?(\w+(?:\s+\w+)??)\s+"
        r"(?:last\s+)?miss(?:ed)?(?:\s+church)?\b",
        q,
    )
    if _last_missed_m:
        name = _last_missed_m.group(1).strip()
        if name:
            return (
                f"SELECT m.name, "
                f"(SELECT MAX(d.service_date) FROM (SELECT DISTINCT service_date FROM attendance) d "
                f" WHERE d.service_date NOT IN (SELECT service_date FROM attendance WHERE member_id = m.id)"
                f") as last_missed "
                f"FROM members m "
                f"WHERE m.name LIKE '%{name}%' AND m.active = 1"
            )

    _last_seen_m = re.search(
        r"when\s+(?:was|is|did)\s+(?:the\s+last\s+time\s+)?(\w+(?:\s+\w+)??)\s+"
        r"(?:last\s+)?(?:come|came|attend(?:ed)?|visit(?:ed)?|showed?\s+up|"
        r"was\s+(?:here|at\s+church))\b",
        q,
    )
    if _last_seen_m:
        name = _last_seen_m.group(1).strip()
        if name:
            # deacon_visible_connect_cards, not the raw connect_cards table --
            # jobs/analytics/data_chat.py's _ALLOWED_TABLES["attendance"]
            # whitelists only the view (it nulls out a non-public
            # prayer_request), so a query naming the raw table gets rejected
            # by _validate_sql and silently falls through to the paid LLM
            # path -- same service_date/campus columns either way, this
            # query never touches prayer_request.
            return (
                f"SELECT m.name, "
                f"(SELECT MAX(service_date) FROM ("
                f"  SELECT service_date FROM attendance WHERE member_id = m.id"
                f"  UNION ALL SELECT service_date FROM deacon_visible_connect_cards WHERE member_id = m.id"
                f")) as last_attended, "
                f"(SELECT campus FROM ("
                f"  SELECT service_date, campus FROM attendance WHERE member_id = m.id"
                f"  UNION ALL SELECT service_date, campus FROM deacon_visible_connect_cards WHERE member_id = m.id"
                f") ORDER BY service_date DESC LIMIT 1) as campus "
                f"FROM members m "
                f"WHERE m.name LIKE '%{name}%' AND m.active = 1"
            )

    # PHONE NUMBER LOOKUP -- checked before MEMBER LOOKUP BY NAME for the
    # same reason as SPOUSE LOOKUP/MEMBER'S OWN DEACON above: "X phone
    # number" (no possessive, name BEFORE the field word) doesn't fit
    # MEMBER LOOKUP BY NAME's prefix-strip mechanism, which assumes the
    # trigger phrase is a prefix left behind once removed -- it needs its
    # own name-then-field extraction instead. Added 2026-09-15 after two
    # fast-path suggestions for this exact shape ("What is Mark Barbour
    # phone number", "What is bill crook phone number") were flagged as
    # needing new logic, correctly -- MEMBER LOOKUP BY NAME structurally
    # cannot handle this shape, no phrase addition alone would fix it.
    _phone_m = re.search(r"what(?:'s| is)\s+(\w+(?:\s+\w+)?)'s\s+(?:phone\s+)?number\b", q)
    if not _phone_m:
        # No possessive at all ("bills number", "Mark Barbour phone number").
        _phone_m = re.search(r"what(?:'s| is)\s+(\w+(?:\s+\w+)?)\s+(?:phone\s+)?number\b", q)
    if not _phone_m:
        _phone_m = re.search(r"(?:phone\s+number|number)\s+(?:for|of)\s+(\w+(?:\s+\w+)?)\b", q)
    if _phone_m:
        name = _phone_m.group(1).strip()
        if name:
            if name.endswith('s') and len(name) > 1 and not name.endswith('ss'):
                # Try both the literal capture and an informal-possessive-
                # stripped form ("bills" -> "bill") since there's no way to
                # tell from text alone whether the trailing s is part of the
                # real name or a dropped apostrophe.
                return (
                    f"SELECT name, phone FROM members WHERE "
                    f"(name LIKE '%{name}%' OR name LIKE '%{name[:-1]}%') AND active = 1"
                )
            return f"SELECT name, phone FROM members WHERE name LIKE '%{name}%' AND active = 1"

    # ADDRESS LOOKUP -- checked before MEMBER LOOKUP BY NAME for the same
    # reason as PHONE NUMBER LOOKUP above: a bare "X's address" or "X
    # address" (no "who is"/"look up" trigger phrase) doesn't fit MEMBER
    # LOOKUP BY NAME's prefix-strip mechanism, which assumes the trigger
    # phrase is a prefix left behind once removed -- it needs its own
    # name-then-field extraction instead. members.address is already a
    # whitelisted contact column for Team Chat (see
    # jobs/analytics/data_chat.py's _CONTACT_COLUMN_WORDS) -- same tier as
    # phone, just a different field. Added 2026-09-17 after a real Team Chat
    # message, "Maybe Andrea valentines address" (no "what is", no
    # possessive apostrophe, just a name run straight into "address"), fell
    # through to the paid LLM path with no fast, free answer. Excludes
    # 'email address'/'ip address', which name no person and aren't this
    # column.
    if 'email address' not in q and 'ip address' not in q:
        _address_m = re.search(r"what(?:'s| is)\s+(\w+(?:\s+\w+)?)'s\s+(?:home\s+|mailing\s+|street\s+)?address\b", q)
        if not _address_m:
            _address_m = re.search(r"what(?:'s| is)\s+(\w+(?:\s+\w+)?)\s+(?:home\s+|mailing\s+|street\s+)?address\b", q)
        if not _address_m:
            _address_m = re.search(r"(?:home\s+|mailing\s+|street\s+)?address\s+(?:for|of)\s+(\w+(?:\s+\w+)?)\b", q)
        if not _address_m:
            _address_m = re.search(r"(\w+(?:\s+\w+)?)'s\s+(?:home\s+|mailing\s+|street\s+)?address\b", q)
        if not _address_m:
            # Bare "X address" with no possessive apostrophe and no "what
            # is"/"for"/"of" -- the exact shape of the message that
            # motivated this block. Anchored to the end of the message (up
            # to 3 words captured) so it can't fire mid-sentence on an
            # unrelated later mention of the word "address".
            _address_m = re.search(r"(\w+(?:\s+\w+){0,2})\s+(?:home\s+|mailing\s+|street\s+)?address\W*$", q)
        if _address_m:
            name = _address_m.group(1).strip()
            # Strip leading filler words picked up by the bare end-anchored
            # pattern above (e.g. "Maybe Andrea Valentines address") -- same
            # spirit as DEACON GROUP MEMBERSHIP's stopword strip elsewhere
            # in this file.
            _address_stopwords = ('maybe', 'the', 'a', 'an', 'is', 'does', 'anyone', 'know',
                                   'have', 'get', 'send', 'me', 'please', 'looking', 'for', 'need')
            name = " ".join(w for w in name.split() if w not in _address_stopwords)
        if _address_m and name:
            if name.endswith('s') and len(name) > 1 and not name.endswith('ss'):
                # Try both the literal capture and an informal-possessive-
                # stripped form ("valentines" -> "valentine") since there's
                # no way to tell from text alone whether the trailing s is
                # part of the real name or a dropped apostrophe -- same
                # PHONE NUMBER LOOKUP fallback above.
                return (
                    f"SELECT name, address FROM members WHERE "
                    f"(name LIKE '%{name}%' OR name LIKE '%{name[:-1]}%') AND active = 1"
                )
            return f"SELECT name, address FROM members WHERE name LIKE '%{name}%' AND active = 1"

    # AGE LOOKUP -- mirrors bot.py's DM-only "how old is X" -> age field
    # (_extract_team_lookup, added 2026-09-12); missing here meant this
    # question fell through to MEMBER LOOKUP BY NAME instead, which returns
    # the raw birthdate -- correct data, wrong question answered, the exact
    # bug already fixed for the DM path per that function's docstring. Added
    # 2026-09-15 after a fast-path suggestion for "How old is John Valentine"
    # proposed adding "how old is" as a MEMBER LOOKUP BY NAME trigger phrase,
    # which would have reintroduced that same bug in Team Chat.
    _age_m = re.search(r"how\s+old\s+is\s+(\w+(?:\s+\w+)?)\b", q)
    if not _age_m:
        _age_m = re.search(r"when\s+was\s+(\w+(?:\s+\w+)??)\s+born\b", q)
    if _age_m:
        name = _age_m.group(1).strip()
        if name:
            return (
                f"SELECT name, birthdate, "
                f"CAST(strftime('%Y', 'now') AS INTEGER) - CAST(strftime('%Y', birthdate) AS INTEGER) "
                f"- (CAST(strftime('%m%d', 'now') AS INTEGER) < CAST(strftime('%m%d', birthdate) AS INTEGER)) AS age "
                f"FROM members WHERE name LIKE '%{name}%' AND active = 1 AND birthdate IS NOT NULL"
            )

    # BIRTHDAYS -- a month-wide list ("birthdays in October", "who has a
    # birthday this month"), distinct from a single person's own birthday
    # (that's bot.py's _extract_team_lookup "X's birthday" fast path instead).
    if 'birthday' in q or 'birthdate' in q or 'born in' in q:
        month_num = None
        for _name, _num in _MONTH_NAMES.items():
            if _name in q:
                month_num = _num
                break
        if month_num is None and any(w in q for w in ['this month', 'coming up', 'upcoming']):
            month_num = date.today().month
        if month_num:
            return (
                f"SELECT name, birthdate FROM members "
                f"WHERE active = 1 AND name NOT LIKE '%CAMPUS%' AND name NOT LIKE '%SYSTEM%' AND name NOT LIKE '%TEST%' "
                f"AND birthdate IS NOT NULL AND CAST(strftime('%m', birthdate) AS INTEGER) = {month_num} "
                f"ORDER BY CAST(strftime('%d', birthdate) AS INTEGER)"
            )

    # MEMBER LOOKUP BY NAME
    # (Two dead trigger phrases removed here 2026-09-12: 'what is [name]
    # birthday' and 'who is [name] married to' and 'does [name] have a
    # deacon' were literal strings with brackets in them that could never
    # match real text -- a person's actual name would never contain the
    # literal substring "[name]". "who is X married to"/"does X have a
    # deacon" now have real, working patterns above instead; a single
    # person's own birthday is bot.py's _extract_team_lookup fast path, not
    # this file, per the BIRTHDAYS block's comment above.)
    if any(w in q for w in ['look up', 'find member', 'search for', 'who is', 'tell me about',
                             'get info on', 'member info', 'pull up', 'details on', 'info for']):
        name = q
        for trigger in ['tell me about', 'get info on', 'find member', 'member info',
                        'search for', 'look up', 'who is', 'pull up', 'details on', 'info for']:
            if trigger in name:
                name = name.replace(trigger, '', 1).strip()
                break
        for prefix in ['the member', 'a member', 'member', 'the person', 'a person']:
            if name.startswith(prefix):
                name = name[len(prefix):].strip()
        name = name.strip('.,?! ')
        if name:
            return (
                f"SELECT m.name, m.email, m.phone, m.status, m.campus_preference, m.first_visit_date "
                f"FROM members m "
                f"WHERE m.name LIKE '%{name}%' AND m.active = 1 AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' "
                f"ORDER BY m.name"
            )

    # PRAYER REQUESTS
    if any(w in q for w in ['prayer request', 'prayer list', 'who needs prayer',
                             'prayer wall', 'praying for', 'prayer needs',
                             "who's asking for prayer", 'who is asking for prayer']):
        today = date.today()
        if 'this week' in q or 'last week' in q or '7 day' in q:
            cutoff = (today - timedelta(days=7)).strftime('%Y-%m-%d')
        elif 'this month' in q or '30 day' in q:
            cutoff = (today - timedelta(days=30)).strftime('%Y-%m-%d')
        else:
            cutoff = (today - timedelta(days=14)).strftime('%Y-%m-%d')
        return (
            f"SELECT m.name, cc.prayer_request, cc.service_date "
            f"FROM connect_cards cc "
            f"JOIN members m ON cc.member_id = m.id "
            f"WHERE cc.prayer_request IS NOT NULL "
            f"AND cc.prayer_request != '' "
            f"AND cc.service_date >= '{cutoff}' "
            f"AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' "
            f"ORDER BY cc.service_date DESC"
        )

    # FOLLOW-UPS
    if any(w in q for w in ['follow up', 'follow-up', 'needs follow', 'who to follow',
                             'follow up list', 'who needs a follow up', 'pending follow ups']):
        return (
            f"SELECT m.name, f.note, f.created_at, f.status "
            f"FROM follow_ups f "
            f"JOIN members m ON f.member_id = m.id "
            f"WHERE f.status = 'pending' AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' "
            f"ORDER BY f.created_at DESC"
        )

    # NEXT STEPS
    if any(w in q for w in ['next step', 'next steps', 'who filled out', 'connection card next']):
        cutoff = (date.today() - timedelta(days=14)).strftime('%Y-%m-%d')
        return (
            f"SELECT m.name, cc.next_steps, cc.service_date "
            f"FROM connect_cards cc "
            f"JOIN members m ON cc.member_id = m.id "
            f"WHERE cc.next_steps IS NOT NULL "
            f"AND cc.next_steps != '' "
            f"AND cc.service_date >= '{cutoff}' "
            f"AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' "
            f"ORDER BY cc.service_date DESC"
        )

    # BARE NAME LOOKUP -- checked last, after every trigger-phrase pattern
    # above (including MEMBER LOOKUP BY NAME) has missed. A Team Chat leader
    # sometimes just types a person's name with no verb at all ("Melanie
    # Yomes") -- MEMBER LOOKUP BY NAME above requires an explicit trigger
    # phrase ('who is', 'look up', 'tell me about', ...), which a bare name
    # doesn't have, so this used to fall all the way through to the paid LLM
    # path for a question that isn't really a data question at all -- it's
    # just "look this person up". Recognize the shape on the ORIGINAL
    # (un-lowered) question instead of adding more trigger phrases: 2-4
    # Title-Case words, letters/apostrophe/hyphen only, nothing else in the
    # message. Reuses MEMBER LOOKUP BY NAME's exact query -- same read-only
    # lookup, just a different way of triggering it. A false hit on a
    # capitalized non-name phrase (e.g. "Good Morning") just returns "No
    # results found" since no member has that name -- no write, no risk.
    if re.fullmatch(r"[A-Z][a-z'-]*(?:\s+[A-Z][a-z'-]*){1,3}", question.strip()):
        name = question.strip().strip('.,?! ')
        if name:
            return (
                f"SELECT m.name, m.email, m.phone, m.status, m.campus_preference, m.first_visit_date "
                f"FROM members m "
                f"WHERE m.name LIKE '%{name}%' AND m.active = 1 AND m.name NOT LIKE '%CAMPUS%' AND m.name NOT LIKE '%SYSTEM%' AND m.name NOT LIKE '%TEST%' "
                f"ORDER BY m.name"
            )

    # ACTIVE MEMBERS COUNT OR LIST
    if any(w in q for w in ['how many members', 'how many active', 'total members', 'member count',
                             'list all members', 'all active members', 'active members',
                             'membership count', 'roster', 'how many people do we have']):
        if any(w in q for w in ['how many', 'total', 'count']):
            return "SELECT COUNT(*) as total FROM members WHERE active = 1"
        return "SELECT name, email, campus_preference FROM members WHERE active = 1 AND name NOT LIKE '%CAMPUS%' AND name NOT LIKE '%SYSTEM%' AND name NOT LIKE '%TEST%' ORDER BY name"

    return None

def run(question: str) -> str:
    question = question.strip()
    if not question:
        return "No question provided."

    # Batch member update directive — 'mark ...' / 'pick ...' / 'confirm ...' /
    # 'cancel ...' — never routed through Ollama, always resolved/previewed
    # before any write.
    from jobs.connect_cards import batch_update as _bu

    if question.lower().startswith("mark "):
        return _bu.handle_mark_command(question, interface="dashboard")

    if question.lower().startswith("alias "):
        return _bu.handle_alias_command(question, actor="Bill (Dashboard)")

    _m = re.match(r"^pick\s+(\d+)\s+(\d+|skip)\s*$", question, re.IGNORECASE)
    if _m:
        return _bu.handle_pick_command(int(_m.group(1)), _m.group(2), interface="dashboard")

    _m = re.match(r"^confirm\s+(\d+)\s*$", question, re.IGNORECASE)
    if _m:
        return _bu.handle_confirm_command(int(_m.group(1)), actor="Bill (Dashboard)")

    _m = re.match(r"^cancel\s+(\d+)\s*$", question, re.IGNORECASE)
    if _m:
        return _bu.handle_cancel_command(int(_m.group(1)))

    # Try pattern match first — bypasses Ollama for common attendance queries
    from datetime import date as _date, timedelta as _td
    _pm_sql = _pattern_match(question, _last_sunday(), [(_date.today() - _td(weeks=i)).strftime('%Y-%m-%d') for i in range(1, 13)])
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
