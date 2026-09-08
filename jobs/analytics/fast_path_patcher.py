"""jobs/analytics/fast_path_patcher.py — safe, narrowly-scoped auto-apply
for approved fast-path phrase suggestions (see jobs/analytics/
fast_path_suggestions.py).

Deliberately does ONE thing only: append one new literal phrase string
into an EXISTING `any(w in q for w in [...])` trigger list inside
jobs/skills/cdb_query.py's _pattern_match(). Never invents new query
logic, never picks a value for a dict-valued mapping (bot.py's web-metric/
classroom/contact-field lookups) -- those require a human judgment call on
which existing value the new phrase should map to, and a wrong automated
guess there would silently return the WRONG data, worse than just falling
through to an LLM call. Suggestions that don't fit one of the categories
below, or would need a value pick, are queued for a real coding session
(Bill + Claude Code) instead of auto-applied -- see CDB_CATEGORY_TARGETS.

Safety: every edit is validated with ast.parse() on the FULL patched file
before it's ever written to disk -- a syntax error (or anything else that
makes the patch not parse) means the original file is left untouched and
the caller is told it failed, never left half-applied. Paired with a git
commit per successful apply (done by the caller), so `git revert` is also
always available as a second line of defense.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CDB_QUERY_PATH = REPO / "jobs" / "skills" / "cdb_query.py"

# target_id -> the exact anchor comment (sans leading "# ") immediately
# above the `any(w in q for w in [...])` block it identifies in
# jobs/skills/cdb_query.py's _pattern_match(). Only single-line-comment,
# flat-string-list categories are listed here -- DEACON GROUP MEMBERSHIP
# and BIRTHDAYS use custom regex extraction, not a phrase list, so a new
# phrase can't just be appended for those; they're not in this registry on
# purpose, and a suggestion that would need something like them gets
# queued for a real coding session instead of an attempted auto-patch.
CDB_CATEGORY_TARGETS = {
    "slipping_away": "SLIPPING AWAY / NEEDS SHEPHERDING",
    "hybrid_members": "HYBRID MEMBERS",
    "how_many_missed": "HOW MANY MISSED (count)",
    "who_missed": "WHO MISSED",
    "attendance_trend": "ATTENDANCE TREND",
    "how_many_attended": "HOW MANY ATTENDED (count)",
    "who_attended": "WHO ATTENDED",
    "not_seen_recently": "MEMBERS NOT SEEN RECENTLY",
    "first_time_visitors": 'FIRST-TIME VISITORS (checked before new-members to catch "first time visitor" specifically)',
    "new_members": "NEW MEMBERS / RECENT JOINS",
    "member_lookup": "MEMBER LOOKUP BY NAME",
    "prayer_requests": "PRAYER REQUESTS",
    "follow_ups": "FOLLOW-UPS",
    "next_steps": "NEXT STEPS",
    "active_members_count": "ACTIVE MEMBERS COUNT OR LIST",
}

# Plain-English label for each target, shown in the Telegram suggestion
# message so Bill can tell what he's approving without reading code.
CDB_CATEGORY_LABELS = {
    "slipping_away": "members slipping away / needing shepherding",
    "hybrid_members": "members who attend both campuses",
    "how_many_missed": "count of who missed a service",
    "who_missed": "who missed a service",
    "attendance_trend": "attendance trend over time",
    "how_many_attended": "count of who attended a service",
    "who_attended": "who attended a service",
    "not_seen_recently": "members not seen recently",
    "first_time_visitors": "first-time visitors",
    "new_members": "new members / recent joins",
    "member_lookup": "looking up a specific member",
    "prayer_requests": "prayer requests",
    "follow_ups": "pending follow-ups",
    "next_steps": "connect-card next steps",
    "active_members_count": "active member count / roster",
}


def _validate_python(text: str) -> tuple[bool, str]:
    try:
        ast.parse(text)
        return True, ""
    except SyntaxError as e:
        return False, str(e)


def append_cdb_phrase(target_id: str, new_phrase: str) -> tuple[bool, str]:
    """Appends new_phrase into the trigger list for target_id (a key of
    CDB_CATEGORY_TARGETS). Returns (ok, message) -- ok=False always means
    nothing was written."""
    anchor_comment = CDB_CATEGORY_TARGETS.get(target_id)
    if not anchor_comment:
        return False, f"unknown or unsupported target_id {target_id!r}"
    if not new_phrase or not isinstance(new_phrase, str):
        return False, "new_phrase must be a non-empty string"
    new_phrase = new_phrase.strip().lower()
    if not new_phrase:
        return False, "new_phrase must be a non-empty string"

    text = CDB_QUERY_PATH.read_text()
    anchor = f"# {anchor_comment}"
    anchor_pos = text.find(anchor)
    if anchor_pos == -1:
        return False, f"anchor comment {anchor!r} not found in {CDB_QUERY_PATH.name} -- file may have changed"

    list_marker = "for w in ["
    list_start = text.find(list_marker, anchor_pos)
    if list_start == -1 or list_start - anchor_pos > 600:
        return False, "could not find this category's 'for w in [...]' list near its anchor comment"
    bracket_pos = list_start + len(list_marker) - 1  # index of the opening '['

    depth = 0
    end = None
    for i in range(bracket_pos, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        return False, "could not find the matching ']' for this category's list"

    if new_phrase in text[bracket_pos:end].lower():
        return False, f"phrase {new_phrase!r} (or something very like it) is already in this category's list"

    insertion = f"{new_phrase!r}, "
    new_text = text[: bracket_pos + 1] + insertion + text[bracket_pos + 1 :]

    ok, err = _validate_python(new_text)
    if not ok:
        return False, f"patched file failed to parse, nothing written: {err}"

    CDB_QUERY_PATH.write_text(new_text)
    return True, "applied"
