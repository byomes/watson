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
        sentence = f"We last saw {name} on {pretty_date} — less than a week ago."
    else:
        weeks_word = "week" if weeks_since == 1 else "weeks"
        sentence = f"We last saw {name} on {pretty_date} — it's been {weeks_since} {weeks_word} since we've seen them."
    if campus:
        sentence += f" They last attended the {campus} campus."
    return sentence


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
        return f"{name} last missed church on {pretty_date} — less than a week ago."
    weeks_word = "week" if weeks_since == 1 else "weeks"
    return f"{name} last missed church on {pretty_date} — it's been {weeks_since} {weeks_word} since they missed."
