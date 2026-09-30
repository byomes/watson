"""Shared natural-language formatting for "when did X last attend/miss
church" answers.

Used by both bot.py's DM fast path (_format_team_lookup_reply) and the Team
Chat pattern-match path (jobs/analytics/data_chat.py's _format_rows via
cdb_query.py's SQL), so the two surfaces answer these questions identically
instead of drifting apart. Added 2026-09-15 per Bill's request: both answers
need a "we last saw them / they last attended N weeks ago on [date]" sentence,
plus a second sentence naming the campus when it's known for the attendance
(never applicable to a miss -- there's no campus for a service someone wasn't
at).
"""
from datetime import date

_NEVER_SEEN = "1900-01-01"


def format_last_attended_reply(name: str, last_attended: str | None, campus: str | None = None) -> str:
    if not last_attended or last_attended == _NEVER_SEEN:
        return f"{name} has no recorded attendance."
    try:
        seen_date = date.fromisoformat(last_attended)
    except (ValueError, TypeError):
        return f"We last saw {name} on {last_attended}."
    pretty_date = seen_date.strftime("%B %-d, %Y")
    weeks_since = (date.today() - seen_date).days // 7
    if weeks_since <= 0:
        sentence = f"We last saw {name} on {pretty_date}, less than a week ago."
    else:
        weeks_word = "week" if weeks_since == 1 else "weeks"
        sentence = f"We last saw {name} on {pretty_date}. It's been {weeks_since} {weeks_word} since we've seen them."
    if campus:
        sentence += f" They last attended the {campus} campus."
    return sentence


def format_period_attendance_reply(
    span_label: str, combined_total: int, unique_individuals: int, campus: str | None = None
) -> str:
    """Plain-English answer to "attendance for the last N weeks/months".
    cdb_query.py's _pattern_match COMBINED + CUMULATIVE ATTENDANCE block
    returns both numbers rather than guessing which one the asker meant;
    this spells out what each one means so the reply is self-explanatory
    without a follow-up question. span_label is a ready-made phrase from
    that block, e.g. "the last 6 weeks", "the last month", "the current
    month"."""
    scope = f" at the {campus} campus" if campus else ""
    person_word = "person" if unique_individuals == 1 else "people"
    return (
        f"Over {span_label}{scope}: {combined_total} combined check-ins "
        f"(every Sunday's headcount added together) and {unique_individuals} cumulative, unique "
        f"{person_word} who attended at least once."
    )


def format_period_attendance_breakdown(rows: list[dict]) -> str:
    """Plain-English answer to multi-week/month attendance question with
    campus breakdown (Online, Wilmington, Kids). Called when cdb_query.py's
    _pattern_match COMBINED + CUMULATIVE block returns multiple rows (one per
    campus/group) instead of one per-campus-filtered row."""
    if not rows:
        return "No attendance recorded for that period."
    span_label = rows[0].get("span_label", "over this period")
    adults = [r for r in rows if r.get("campus") in ("Online", "Wilmington")]
    kids_row = next((r for r in rows if r.get("campus") == "Kids"), None)
    adult_combined = sum(r.get("combined_total", 0) or 0 for r in adults)
    kids_combined = kids_row.get("combined_total") if kids_row else None
    grand_combined = adult_combined + (kids_combined or 0)
    adult_unique = sum(r.get("unique_individuals", 0) or 0 for r in adults)
    kids_unique = kids_row.get("unique_individuals") if kids_row else None
    grand_unique = adult_unique + (kids_unique or 0)
    lines = [f"Over {span_label}: {grand_combined} combined check-ins and {grand_unique} cumulative, unique people. Here's the breakdown:"]
    for r in adults:
        combined = r.get("combined_total") or 0
        unique = r.get("unique_individuals") or 0
        lines.append(f"{r['campus']}: {combined} check-ins, {unique} unique people")
    if kids_row:
        combined = kids_row.get("combined_total") or 0
        unique = kids_row.get("unique_individuals") or 0
        lines.append(f"Kids: {combined} check-ins, {unique} unique kids")
    return "\n".join(lines)


def format_weekly_attendance_reply(rows: list[dict]) -> str:
    """Plain-English answer to a single-Sunday (or today/yesterday/specific-
    date) headcount question -- cdb_query.py's _pattern_match plain HOW MANY
    ATTENDED block (no campus filter), which returns one row per campus plus
    a 'Kids' row. Bill's 2026-09-30 request: this shape used to fall through
    to data_chat.py's generic "col: val" row dump; it now gets the same kind
    of plain-English total sentence the month/year span already gets via
    format_period_attendance_reply, with campuses and kids broken out one per
    line beneath it rather than announced up front."""
    if not rows:
        return "No attendance recorded for that date."
    service_date = rows[0].get("service_date")
    try:
        period = f"on {date.fromisoformat(service_date).strftime('%B %-d, %Y')}" if service_date else "for that date"
    except (ValueError, TypeError):
        period = f"on {service_date}" if service_date else "for that date"
    kids_row = next((r for r in rows if r.get("campus") == "Kids"), None)
    campus_rows = [r for r in rows if r.get("campus") != "Kids"]
    adult_total = sum(r["total"] for r in campus_rows if r.get("total") is not None)
    kids_total = kids_row.get("total") if kids_row else None
    grand_total = adult_total + (kids_total or 0)
    lines = [f"We saw a total of {grand_total} people {period}. Here's a breakdown:"]
    for r in campus_rows:
        lines.append(f"{r['campus']}: {r.get('total') if r.get('total') is not None else 0}")
    if kids_row:
        lines.append(f"Kids: {kids_total if kids_total is not None else 'no data'}")
    return "\n".join(lines)


def format_last_missed_reply(name: str, last_missed: str | None) -> str:
    if not last_missed:
        return f"{name} hasn't missed a service on record."
    try:
        missed_date = date.fromisoformat(last_missed)
    except (ValueError, TypeError):
        return f"{name} last missed church on {last_missed}."
    pretty_date = missed_date.strftime("%B %-d, %Y")
    weeks_since = (date.today() - missed_date).days // 7
    if weeks_since <= 0:
        return f"{name} last missed church on {pretty_date}, less than a week ago."
    weeks_word = "week" if weeks_since == 1 else "weeks"
    return f"{name} last missed church on {pretty_date}. It's been {weeks_since} {weeks_word} since they missed."
