"""Short-lived per-asker memory of an ambiguous person lookup, so a bare
follow-up reply ("Jennifer") can be connected back to the original question
instead of being treated as a brand new, context-free query.

Bug found 2026-09-14: Bill asked "When was the last time Jen DiMatteo came to
church?", got a 3-way ambiguous match (Gerry/Jennifer/Sophia DiMatteo),
replied "Jennifer" to narrow it down, and Watson answered as if "Jennifer"
were an unrelated new question ("I don't have a record of recent activity
related to 'Jennifer'") instead of resuming the last-time-at-church question
for the now-identified person.

Keyed per asker identity (leader name, or "Bill Yomes" for Dr. Bill's own
chat) with a TTL -- per the standing rule that any Watson conversational
state must be per-chat/asker and time-bounded, not global/unbounded. State
here is a handful of small dicts at a time, so no separate size cap is
needed beyond the TTL sweep in pop_pending."""
import time

_TTL_SECONDS = 5 * 60
_pending: dict[str, dict] = {}


def set_pending(asker: str, field: str, candidates: list[dict]) -> None:
    _pending[asker] = {
        "field": field,
        "candidates": candidates,
        "expires": time.time() + _TTL_SECONDS,
    }


def pop_pending(asker: str) -> dict | None:
    """Removes and returns the pending entry for `asker`, or None if there
    isn't one or it's expired. Always removes on read -- a resolved or
    stale entry should never be reused for a later, unrelated message."""
    entry = _pending.pop(asker, None)
    if not entry:
        return None
    if time.time() > entry["expires"]:
        return None
    return entry


def clear_pending(asker: str) -> None:
    _pending.pop(asker, None)
