"""jobs/analytics/conversion_report.py — LLM-free fast path for "guest
conversion report" / "retention report" questions in team chat
(jobs/analytics/data_chat.py).

Per Bill's 2026-09-27 request: for a cohort of first-time guests, report how
many (and what %) went on to become a 2nd-time guest, a "regular", and a
partner, as of today -- regardless of how long ago the period was. This
mirrors the guest-stage definitions from the 2026-09-25 assimilation-pathway
planning session (see memory/project_assimilation_pathway.md), applied here
for the first time as an actual computed report rather than a proposal.

Definitions (confirmed with Bill 2026-09-27, matching the planning session):
  - "Visit" = a row in attendance (member_id, service_date). Not connect_cards
    -- that table isn't in data_chat.py's attendance-domain allowlist and a
    card submission doesn't always correspond 1:1 with a physical visit.
  - 2nd-time guest: >=2 lifetime attendance rows, ever (as of today).
  - Regular: at some point had >=6 distinct attendance dates within some
    56-day (8-week) rolling window -- the "graduation" threshold from the
    planning session. Checked over the member's whole attendance history,
    not just the cohort period, since the cohort period only defines who's
    in the report, not the window the threshold is evaluated over.
  - Partner: members.partner = 'partner' (the other observed value is 'np' /
    not-partner -- see live data check 2026-09-27).
  - Cohort membership: MIN(attendance.service_date) per member within
    [start, end] of the requested period -- NOT members.first_visit_date.
    Found live 2026-09-27 (Bill: "watsons stats are wrong hes marking
    everyone as first visit from when they were imported into the db"):
    first_visit_date disagrees with the member's actual earliest attendance
    row for 85 of 208 members who have one set, and for 52 of those (all
    long-time members/elders, e.g. Jim Bouchat) it's dated months LATER
    than their real first attendance (2026-06-07, vs. real first-attendance
    dates back in January 2026) -- some bulk backfill evidently stamped a
    run date onto members missing the column rather than a real historical
    date. The assimilation-pathway planning session already flagged this
    exact failure mode for connect_cards.is_first_visit ("must derive first
    visit from absence of prior attendance rows, not that flag") -- the
    same distrust turns out to apply to first_visit_date too, so this
    module derives "first visit" straight from the attendance table itself,
    the same source of truth the 2nd-time/regular checks below already use,
    instead of trusting a separately-stored column that can drift from it.

This does NOT touch/build any of the still-unbuilt "stuck" / "1st-visit
lapse" states or the outreach-drafting job from the planning session --
those remain planning-only. This module only answers the conversion-COUNT
question Bill asked for.
"""

import re
import sqlite3
from datetime import date, timedelta

_REGULAR_WINDOW_DAYS = 56  # 8 weeks
_REGULAR_THRESHOLD = 6

_TRIGGER_RE = re.compile(
    r"\b(guest\s+)?(conversion|retention)\s+report\b", re.IGNORECASE
)

# "How many first-time guests..." was landing on the LLM-generated-SQL
# fallback (no fast path recognized it) and coming back as a full row list
# instead of a number -- found live 2026-09-27 in Bill's Telegram log
# (two separate "how many first time guest(s)" questions both answered with
# a dozen-plus name/email/phone rows). A "how many" question shape should
# always get a count, never a list, regardless of domain -- this fast path
# exists so that holds here even before the LLM gets involved.
_COUNT_TRIGGER_RE = re.compile(
    r"how\s+(?:many|much)\b[^?.!]{0,60}\bfirst[- ]?time\b", re.IGNORECASE
)

# Bill's explicit counterpart rule (2026-09-27): "who are"/"who were" wants
# names and contact info, never just a number -- the mirror image of the
# "how many" fast path above. Handled as its own fast path (rather than
# just leaving "who" questions to the LLM path) so the list is built from
# the same corrected attendance-based cohort as the count/report, instead
# of a generated query against the unreliable first_visit_date column.
_WHO_TRIGGER_RE = re.compile(
    r"who\s+(?:are|were)\b[^?.!]{0,60}\bfirst[- ]?time\b", re.IGNORECASE
)


def _resolve_period(question: str) -> tuple[date, date, str] | None:
    """Best-effort period resolution for the phrasings Bill is likely to
    use alongside "conversion report" / "retention report". Reuses
    cdb_query.py's month-name table and month-arithmetic helper rather than
    redefining them -- returns None (never guesses) for anything it doesn't
    recognize, same policy as cdb_query.py's own date-range block."""
    from jobs.skills.cdb_query import _MONTH_NAMES, _months_ago

    q = question.lower()
    today = date.today()

    m = re.search(r"\b(\d{1,2})[- ]?months?\b", q)
    if m:
        n = int(m.group(1))
        start = _months_ago(today, n)
        return start, today, f"the last {n} month{'s' if n != 1 else ''}"

    m = re.search(r"\b(\d{1,3})[- ]?weeks?\b", q)
    if m:
        n = int(m.group(1))
        start = today - timedelta(weeks=n)
        return start, today, f"the last {n} week{'s' if n != 1 else ''}"

    if re.search(r"\b(this|current)\s+month\b", q):
        return today.replace(day=1), today, "the current month"

    if re.search(r"\b(last|past|previous)\s+month\b", q):
        first_of_this = today.replace(day=1)
        end = first_of_this - timedelta(days=1)
        start = end.replace(day=1)
        return start, end, "last month"

    if re.search(r"\b(this|current)\s+year\b", q):
        return today.replace(month=1, day=1), today, "this year"

    if re.search(r"\b(last|past|previous)\s+year\b", q):
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), "last year"

    month_alt = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
    m = re.search(
        rf"\b(?:in|during|of|for)\s+(?P<month>{month_alt})\b(?:,?\s*(?P<year>\d{{4}}))?",
        q,
    )
    if m:
        import calendar
        month_num = _MONTH_NAMES[m.group("month")]
        year_num = int(m.group("year")) if m.group("year") else today.year
        start = date(year_num, month_num, 1)
        end = date(year_num, month_num, calendar.monthrange(year_num, month_num)[1])
        return start, end, f"{m.group('month').capitalize()} {year_num}"

    return None


def _conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ever_hit_regular(dates: list[str]) -> bool:
    """dates: sorted ISO service_date strings for one member (deduped
    upstream via SELECT DISTINCT). True if any 56-day window contains
    _REGULAR_THRESHOLD or more of them."""
    parsed = [date.fromisoformat(d) for d in dates]
    left = 0
    for right in range(len(parsed)):
        while (parsed[right] - parsed[left]).days > _REGULAR_WINDOW_DAYS:
            left += 1
        if right - left + 1 >= _REGULAR_THRESHOLD:
            return True
    return False


def _cohort(conn: sqlite3.Connection, start: date, end: date) -> list[sqlite3.Row]:
    # Cohort = members whose EARLIEST attendance row falls in [start, end] --
    # not members.first_visit_date, which is unreliable (see module
    # docstring). A member with no attendance rows at all can't be a
    # first-time-guest cohort member by this definition, so the JOIN
    # (not LEFT JOIN) correctly drops them.
    return conn.execute(
        """
        SELECT m.id, m.name, m.partner, m.email, m.phone,
               MIN(a.service_date) AS first_visit
        FROM members m
        JOIN attendance a ON a.member_id = m.id
        GROUP BY m.id
        HAVING first_visit >= ? AND first_visit <= ?
        ORDER BY first_visit
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()


def build_report(conn: sqlite3.Connection, start: date, end: date) -> str:
    cohort = _cohort(conn, start, end)
    total = len(cohort)
    if total == 0:
        return f"No first-time guests found with a first visit between {start.isoformat()} and {end.isoformat()}."

    n_2nd = 0
    n_regular = 0
    n_partner = 0
    for m in cohort:
        rows = conn.execute(
            "SELECT DISTINCT service_date FROM attendance WHERE member_id = ? ORDER BY service_date",
            (m["id"],),
        ).fetchall()
        visit_dates = [r["service_date"] for r in rows]
        if len(visit_dates) >= 2:
            n_2nd += 1
        if _ever_hit_regular(visit_dates):
            n_regular += 1
        if (m["partner"] or "").strip().lower() == "partner":
            n_partner += 1

    def pct(n: int) -> str:
        return f"{round(100 * n / total)}%"

    return (
        f"First-time guests (first visit {start.isoformat()} to {end.isoformat()}): {total}\n"
        f"Became 2nd-time guests: {n_2nd} ({pct(n_2nd)})\n"
        f"Became regulars (6+ attendances in a rolling 8-week window): {n_regular} ({pct(n_regular)})\n"
        f"Became partners: {n_partner} ({pct(n_partner)})"
    )


def try_conversion_report(question: str, congregation_db_path: str) -> str | None:
    """Entry point for data_chat.py. Returns a formatted reply if `question`
    is a conversion/retention report request, else None (falls through to
    the caller's next handler). Never raises -- any DB error is swallowed
    into None so the caller's LLM fallback gets a chance instead."""
    if not _TRIGGER_RE.search(question):
        return None

    period = _resolve_period(question)
    if period is None:
        return (
            "What time period should I use for the conversion report? "
            "(e.g. \"this year\", \"last 3 months\", \"in March\")"
        )
    start, end, label = period

    try:
        conn = _conn(congregation_db_path)
        try:
            return build_report(conn, start, end)
        finally:
            conn.close()
    except Exception:
        return None


def try_first_time_guest_count(question: str, congregation_db_path: str) -> str | None:
    """Entry point for data_chat.py. Answers a "how many first-time
    guests..." question with just a number (never a list) — see
    _COUNT_TRIGGER_RE's comment for why this exists as its own fast path
    separate from try_conversion_report. Same None-if-not-a-match /
    None-on-any-error contract as that function."""
    if not _COUNT_TRIGGER_RE.search(question):
        return None

    period = _resolve_period(question)
    if period is None:
        return (
            "What time period? (e.g. \"this year\", \"last 3 months\", \"in March\")"
        )
    start, end, label = period

    try:
        conn = _conn(congregation_db_path)
        try:
            n = len(_cohort(conn, start, end))
        finally:
            conn.close()
    except Exception:
        return None

    guest_word = "guest" if n == 1 else "guests"
    return f"{n} first-time {guest_word} in {label} (first visit {start.isoformat()} to {end.isoformat()})."


def try_first_time_guest_list(
    question: str, congregation_db_path: str, allow_contact_info: bool = True
) -> str | None:
    """Entry point for data_chat.py. Answers a "who are/were the
    first-time guests..." question with names (and, per Bill's 2026-09-27
    "who are/who were... names and contact info" rule, email/phone when
    allow_contact_info is set) — the mirror image of
    try_first_time_guest_count. Same None-if-not-a-match / None-on-any-error
    contract as the other entry points here."""
    if not _WHO_TRIGGER_RE.search(question):
        return None

    period = _resolve_period(question)
    if period is None:
        return (
            "What time period? (e.g. \"this year\", \"last 3 months\", \"in March\")"
        )
    start, end, label = period

    try:
        conn = _conn(congregation_db_path)
        try:
            cohort = _cohort(conn, start, end)
        finally:
            conn.close()
    except Exception:
        return None

    if not cohort:
        return f"No first-time guests in {label} (first visit {start.isoformat()} to {end.isoformat()})."

    lines = [f"First-time guests in {label} ({len(cohort)}):"]
    for m in cohort:
        if allow_contact_info:
            email = m["email"] or "—"
            phone = m["phone"] or "—"
            lines.append(f"{m['name']} - first visit {m['first_visit']}, email: {email}, phone: {phone}")
        else:
            lines.append(f"{m['name']} - first visit {m['first_visit']}")
    return "\n".join(lines)
