"""Shared utilities for the attendance intake pipeline."""

import re
from datetime import date, datetime, timedelta

# Small set of lowercase name particles that stay lowercase mid-name (best-effort).
_LOWERCASE_PARTICLES = {"de", "la", "van", "von", "der", "den", "du", "el", "al"}
_ROMAN_SUFFIXES = {"ii": "II", "iii": "III", "iv": "IV", "v": "V", "vi": "VI"}
_NAME_SUFFIXES = {"jr": "Jr", "sr": "Sr"}


def _title_case_word(word: str) -> str:
    core = word.rstrip(".")
    suffix_dot = "." if word.endswith(".") and not word.endswith("..") else ""
    lower = core.lower()
    if lower in _ROMAN_SUFFIXES:
        return _ROMAN_SUFFIXES[lower] + suffix_dot
    if lower in _NAME_SUFFIXES:
        return _NAME_SUFFIXES[lower] + suffix_dot
    return word.title()


def _display_name(raw_name: str) -> str:
    """
    Display-layer name normalization ONLY — never write this back to the database.

    Title-cases names currently stored fully lowercase or fully uppercase (e.g.
    "ro cretella" -> "Ro Cretella"). Names already in mixed case are left untouched,
    on the assumption that an existing mixed-case value is intentional formatting.
    Handles apostrophes/hyphens (O'Brien, Mary-Jane) via str.title()'s word-boundary
    behavior, keeps a small set of lowercase particles lowercase when not the first
    word (de, la, van, von, ...), and preserves common generational suffixes
    (II, III, IV, Jr, Sr).

    Known limitation: this is a best-effort formatter, not a full name-parsing
    solution — Mc/Mac-prefixed names (McDonald -> "Mcdonald") and other less common
    conventions will still render imperfectly. Not solved here by design.
    """
    if not raw_name:
        return raw_name
    if not (raw_name.islower() or raw_name.isupper()):
        return raw_name  # mixed case already — assume intentional, leave as-is

    words = raw_name.split()
    out_words = []
    for i, w in enumerate(words):
        if i > 0 and w.lower() in _LOWERCASE_PARTICLES:
            out_words.append(w.lower())
        else:
            out_words.append(_title_case_word(w))
    return " ".join(out_words)


def most_recent_sunday() -> date:
    """Return the most recent Sunday as a date object (returns today if today is Sunday)."""
    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    if days_since_sunday == 0:
        return today
    return today - timedelta(days=days_since_sunday)


def format_date_for_subject(d: date) -> str:
    """Format date as 'June 8, 2026' for email subjects."""
    return d.strftime("%B %-d, %Y")


def parse_date_from_subject(subject: str) -> date | None:
    """
    Try to extract a date from a subject like 'Missed — June 8, 2026',
    'Attendance 6/8', or 'Attendance June 8'.
    Returns a date object or None.
    """
    # Try "Month Day, Year" format
    match = re.search(r"([A-Za-z]+ \d{1,2},? \d{4})", subject)
    if match:
        for fmt in ("%B %d, %Y", "%B %d %Y"):
            try:
                return datetime.strptime(
                    match.group(1).replace(",", ""), fmt.replace(",", "")
                ).date()
            except ValueError:
                pass

    # Try M/D format (assume current year)
    match = re.search(r"(\d{1,2}/\d{1,2})", subject)
    if match:
        try:
            return datetime.strptime(
                f"{match.group(1)}/{date.today().year}", "%m/%d/%Y"
            ).date()
        except ValueError:
            pass

    return None
