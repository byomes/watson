"""jobs/congregation/fluro_common.py -- tiny shared bits between
fluro_pull.py (classifies) and fluro_apply.py (writes), split out so
neither has to import the other (fluro_pull calls into fluro_apply for
the auto-fill path, so the reverse dependency would be circular).
"""

# Placeholder/sentinel values used on either side that mean "no real data",
# not an actual value to compare or overwrite with -- found live 2026-09-26:
# congregation.db uses '--' for an unset gender, Fluro uses 'unknown' and
# '(000) 000-0000' for a blank phone. Both must be treated as blank on
# BOTH sides, or every never-filled-in field reads as a false conflict.
BLANK_SENTINELS = {"--", "-", "unknown", "n/a", "na", "none", "", "(000) 000-0000"}


def is_blank(value) -> bool:
    return (value or "").strip().lower() in BLANK_SENTINELS
