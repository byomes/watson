"""Shared utilities for the attendance intake pipeline."""

import re
from datetime import date, datetime, timedelta


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
