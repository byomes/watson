"""jobs/house_calls/log_house_call.py — log a funeral home house call from a text.

Bill texts Watson something like "house call: Smith" whenever he's called
out for the funeral home. This logs the family's last name with today's
date, so jobs/house_calls/monthly_report.py can email his boss the list once
a month for pay.
"""
import re

from jobs.house_calls.db import add_house_call, count_unreported, init_db

_PREFIX_RE = re.compile(
    r'^(?:log\s+(?:a\s+)?house\s+call|house\s+calls?)\s*(?:for|to)?\s*[:\s]+',
    re.IGNORECASE,
)


def _extract_family_name(text: str) -> str:
    name = _PREFIX_RE.sub("", text).strip()
    name = re.sub(r'^the\s+', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+family\s*$', '', name, flags=re.IGNORECASE)
    return name.strip(" ,.;:").strip()


def run(message: str = None) -> str:
    if not message:
        return "Whose family was the house call for?"

    family_last_name = _extract_family_name(message)
    if not family_last_name:
        return "Couldn't find a family name in that — try \"house call: Smith\"."

    init_db()
    add_house_call(family_last_name)
    pending = count_unreported()

    return (
        f"House call logged: {family_last_name}. "
        f"{pending} logged, not yet reported to your boss."
    )


if __name__ == "__main__":
    import sys
    print(run(" ".join(sys.argv[1:]) or "house call: Smith"))
