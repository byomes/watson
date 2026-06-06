"""jobs/utilities/date_helper.py — Parse dates and calculate time differences."""
import logging
import re

log = logging.getLogger(__name__)

_DATE_PATTERNS = [
    r'\d{4}-\d{2}-\d{2}',
    r'\d{1,2}/\d{1,2}/\d{4}',
    r'\d{1,2}\s+\w+\s+\d{4}',
    r'\w+\s+\d{1,2},?\s+\d{4}',
]
_DATE_RE = re.compile('|'.join(_DATE_PATTERNS), re.IGNORECASE)


def parse_date(text: str) -> str:
    import arrow
    try:
        dt = arrow.get(text, ["YYYY-MM-DD", "M/D/YYYY", "D MMMM YYYY", "MMMM D, YYYY", "MMMM D YYYY"])
        return dt.format("dddd, MMMM D, YYYY")
    except Exception as exc:
        log.warning("parse_date failed: %s", exc)
        return f"Could not parse date: {text}"


def days_until(date_str: str) -> int:
    import arrow
    try:
        target = arrow.get(date_str, ["YYYY-MM-DD", "M/D/YYYY", "D MMMM YYYY", "MMMM D, YYYY", "MMMM D YYYY"])
        now = arrow.now()
        delta = (target.date() - now.date()).days
        return delta
    except Exception as exc:
        log.warning("days_until failed: %s", exc)
        return 0


def run(message: str = None) -> str:
    if not message:
        return "Date helper ready. Ask about a date or how many days until an event."
    import arrow
    import humanize
    match = _DATE_RE.search(message)
    if not match:
        return "No recognizable date found in message."
    date_str = match.group(0)
    try:
        dt = arrow.get(date_str, ["YYYY-MM-DD", "M/D/YYYY", "D MMMM YYYY", "MMMM D, YYYY", "MMMM D YYYY"])
        now = arrow.now()
        delta_days = (dt.date() - now.date()).days
        formatted = dt.format("dddd, MMMM D, YYYY")
        if delta_days == 0:
            rel = "today"
        elif delta_days > 0:
            rel = humanize.naturaldelta(dt.datetime - now.datetime) + " from now"
        else:
            rel = humanize.naturaldelta(now.datetime - dt.datetime) + " ago"
        return f"{formatted} — {rel} ({abs(delta_days)} days)"
    except Exception as exc:
        return f"Could not process date '{date_str}': {exc}"
