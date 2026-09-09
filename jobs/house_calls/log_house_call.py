"""jobs/house_calls/log_house_call.py — log a funeral home house call from a text.

Bill texts Watson naturally — "Watson, I just finished a house call for John
Smith. Please log the date and time for the record." — or with the terser
"house call: Smith" shorthand. "Home removal" is the same job, industry term
for the same thing, and works identically everywhere "house call" does.
Either way this logs the name with the current date/time and the flat $100
rate, so jobs/house_calls/monthly_report.py can email Jim the list once a
month for pay.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from jobs.house_calls.db import RATE_PER_CALL, add_house_call, count_unreported, init_db, unreported_total

NY = ZoneInfo("America/New_York")

# "house call" and "home removal" are interchangeable terms for the same job.
_TERM = r'(?:house\s+calls?|home\s+removals?)'

# Shorthand: "house call: Smith" / "home removal: Smith" — name is
# everything after the colon/dash, up to the next sentence break.
_COLON_RE = re.compile(
    rf'{_TERM}\s*[:\-]\s*(?P<name>.+?)(?=[.!?,]|$)',
    re.IGNORECASE,
)

# Natural language: "... a house call for John Smith. Please log ..." — name
# is whatever follows "for" near the "house call"/"home removal" mention, up
# to the next sentence break or a trailing clause word.
_FOR_RE = re.compile(
    rf'{_TERM}\b.{{0,20}}?\bfor\b\s+(?:the\s+)?(?P<name>.+?)'
    r'(?=\s+family\b|[.!?,]|\s+please\b|\s+so\b|\s+which\b|\s+is\b|\s+was\b|$)',
    re.IGNORECASE,
)


def _extract_family_name(text: str) -> str:
    m = _COLON_RE.search(text) or _FOR_RE.search(text)
    name = m.group("name") if m else ""
    name = re.sub(r'^the\s+', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+family\s*$', '', name, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', name).strip(" ,.;:!?").strip()


def run(message: str = None) -> str:
    if not message:
        return "Whose house call was that? (e.g. \"house call: Smith\")"

    family_last_name = _extract_family_name(message)
    if not family_last_name:
        return "Couldn't find a name in that — try \"house call: Smith\"."

    init_db()
    now = datetime.now(NY)
    add_house_call(family_last_name, called_at=now.strftime("%Y-%m-%d %H:%M"))
    pending = count_unreported()
    total = unreported_total()

    when = now.strftime("%-I:%M %p on %b %-d")
    return (
        f"House call logged: {family_last_name} at {when} (${RATE_PER_CALL:.0f}). "
        f"{pending} logged (${total:.0f} total), not yet reported to Jim."
    )


if __name__ == "__main__":
    import sys
    print(run(" ".join(sys.argv[1:]) or "house call: Smith"))
