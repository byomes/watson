"""
Recompute Active Status -- daily auto-maintenance of members.active's
'active' <-> 'non-active' states, based on attendance.

Part of the Partner/Connected/Active/Deacon/Residency column redesign (see
~/.claude/plans/zesty-cuddling-robin.md and migrate_partner_connected_active.py,
which creates active and does the one-time initial backfill). This job is
the ongoing equivalent, run nightly so the value stays current as attendance
changes day to day.

Scope: only touches rows where active is currently 'active' or
'non-active'. Never touches 'disconnected' or 'deceased' -- those are
manual-only (set by an elder/pastor via the catalystdb admin screen) and must
stay sticky; this job must never silently flip someone back to 'active' just
because they showed up to a service after being deliberately disconnected.

Rule: 56+ days (8 weeks) since last attendance/connect card -> 'non-active'.
Under 56 days -> 'active'. Self-correcting in both directions, same last-seen
definition (attendance UNION connect_cards) as elder_shepherding_report.py's
_raw_rows() and the migration's initial backfill, so all three agree.

Cron (6am daily, ahead of birthday_daily_alert/other reports that read
active):
  0 6 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.recompute_active_status \
    >> /home/billyomes/watson/logs/recompute_active_status.log 2>&1

Usage:
  python3 -m jobs.congregation.recompute_active_status
"""

import logging
from datetime import date

from jobs.connect_cards.reports import _conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_NON_ACTIVE_CUTOFF_DAYS = 56


def _last_seen_rows(conn):
    return conn.execute(
        """
        SELECT m.id, m.active,
               MAX(
                 COALESCE((SELECT MAX(service_date) FROM connect_cards WHERE member_id = m.id), '1900-01-01'),
                 COALESCE((SELECT MAX(service_date) FROM attendance  WHERE member_id = m.id), '1900-01-01')
               ) AS last_seen
        FROM members m
        WHERE m.active IN ('active', 'non-active')
        """
    ).fetchall()


def recompute() -> tuple[int, int]:
    """Returns (n_marked_non_active, n_marked_active)."""
    today = date.today()
    to_non_active = []
    to_active = []

    with _conn() as conn:
        for member_id, current, last_seen in _last_seen_rows(conn):
            days_since = (
                None if last_seen == "1900-01-01" else (today - date.fromisoformat(last_seen)).days
            )
            should_be_non_active = days_since is None or days_since >= _NON_ACTIVE_CUTOFF_DAYS
            target = "non-active" if should_be_non_active else "active"
            if target != current:
                (to_non_active if target == "non-active" else to_active).append(member_id)

        if to_non_active:
            placeholders = ",".join("?" for _ in to_non_active)
            conn.execute(
                f"UPDATE members SET active = 'non-active' WHERE id IN ({placeholders})",
                to_non_active,
            )
        if to_active:
            placeholders = ",".join("?" for _ in to_active)
            conn.execute(
                f"UPDATE members SET active = 'active' WHERE id IN ({placeholders})",
                to_active,
            )
        conn.commit()

    return len(to_non_active), len(to_active)


if __name__ == "__main__":
    n_non_active, n_active = recompute()
    log.info(
        "recompute_active_status: %d -> non-active, %d -> active",
        n_non_active,
        n_active,
    )
