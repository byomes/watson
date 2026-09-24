"""jobs/congregation/age_groups.py -- kid/teen/adult headcounts for
events. Age is computed from birthdate at query time (kid <13, teen
13-17, adult 18+), never stored, since a static label goes stale every
birthday. Uses the same age formula as cdb_query.py's "how old is X"
lookup for consistency.

"Still lives at home" is a separate question from age and isn't
handled here -- that's whatever a specific event's headcount wants to
do with household_role/household_id (a member with household_role
'child' is grouped in a household; one who moved out just shouldn't
carry that role/household_id anymore).
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "watson" / "data" / "congregation.db"

_AGE_EXPR = (
    "CAST(strftime('%Y', 'now') AS INTEGER) - CAST(strftime('%Y', birthdate) AS INTEGER) "
    "- (CAST(strftime('%m%d', 'now') AS INTEGER) < CAST(strftime('%m%d', birthdate) AS INTEGER))"
)


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def age_group_counts(member_ids: list[int] | None = None) -> dict:
    """Returns {'kid': n, 'teen': n, 'adult': n, 'unknown': n} for active
    members, or a specific member_ids subset (e.g. one event's RSVP
    list). A missing birthdate falls back to 'adult', except for
    household_role='child' with no birthdate, which falls back to
    'unknown' -- that role alone doesn't say kid vs teen."""
    where = ["active_v2 NOT IN ('disconnected', 'deceased')"]
    params: list = []
    if member_ids:
        where.append(f"id IN ({','.join('?' * len(member_ids))})")
        params.extend(member_ids)

    with _conn() as conn:
        rows = conn.execute(
            f"SELECT birthdate, household_role, {_AGE_EXPR} AS age "
            f"FROM members WHERE {' AND '.join(where)}",
            params,
        ).fetchall()

    counts = {"kid": 0, "teen": 0, "adult": 0, "unknown": 0}
    for r in rows:
        if r["birthdate"]:
            age = r["age"]
            if age < 13:
                counts["kid"] += 1
            elif age < 18:
                counts["teen"] += 1
            else:
                counts["adult"] += 1
        elif r["household_role"] == "child":
            counts["unknown"] += 1
        else:
            counts["adult"] += 1
    return counts


def find_implausible_marriages() -> list[dict]:
    """Active members tagged household_role 'husband'/'wife' whose birthdate
    computes to under 18. Per Bill (2026-09-16, after Kathryn Taylor and
    Rose Spinelli both showed up this way): a married role is trustworthy,
    so a young age here means the birth *year* was mistyped, not the role
    -- these get flagged for Donna to correct rather than auto-"fixed"."""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, name, birthdate, household_role, {_AGE_EXPR} AS age "
            f"FROM members WHERE active_v2 NOT IN ('disconnected', 'deceased') AND household_role IN ('husband', 'wife') "
            f"AND birthdate IS NOT NULL AND {_AGE_EXPR} < 18"
        ).fetchall()
    return [dict(r) for r in rows]
