"""Add partner, active_v2, residency to congregation.db's members table, and
backfill them from the legacy partnership_status/member_status/deacon fields.

Part of the Partner/Connected/Active/Deacon/Residency column redesign (see
~/.claude/plans/zesty-cuddling-robin.md) that replaces status, member_status,
partnership_status, deacon_status, status_reason, status_since, status_note,
and snowbird_return. Those old columns are NOT touched or dropped here --
this migration is purely additive so nothing breaks mid-rollout. A later
migrate_drop_legacy_status_columns.py removes them once every read/write site
has been switched over and verified.

New columns:
  partner    TEXT  'partner' | 'np'
  active_v2  TEXT  'active' | 'non-active' | 'disconnected' | 'deceased'
             (temporary name -- renamed to 'active' in the drop migration,
             after the old boolean 'active' column is gone, so there's never
             a window where 'active' has an ambiguous boolean/text meaning)
  residency  TEXT  'local' | 'non-local' | 'snowbird'

deacon is cleaned up in place (no schema change): blanks and the old
'Inactive' bucket value collapse to 'Unassigned'. 'Elders & Deacons' and
'P Bill Yomes' are deliberately left alone -- both are non-name bucket
values Bill wants kept distinct rather than folded into Unassigned.

Usage:
  python3 jobs/congregation/migrate_partner_connected_active.py
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/watson/data/congregation.db")

_NON_ACTIVE_CUTOFF_DAYS = 56  # 8 weeks

_NEW_COLUMNS = ("partner", "active_v2", "residency")


def _add_columns(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(members)").fetchall()}
    for col in _NEW_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE members ADD COLUMN {col} TEXT")
            print(f"  [migrated] members.{col}")
        else:
            print(f"  [exists]   members.{col}")
    conn.commit()


def _backfill_partner(conn):
    conn.execute(
        "UPDATE members SET partner = CASE WHEN partnership_status = 'Partner' "
        "THEN 'partner' ELSE 'np' END WHERE partner IS NULL"
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM members WHERE partner = 'partner'").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM members WHERE partner IS NOT NULL").fetchone()[0]
    print(f"  [backfilled] partner: {n} partner / {total - n} np")


def _backfill_active_v2(conn):
    conn.execute(
        """
        UPDATE members SET active_v2 = CASE
            WHEN member_status = 'disconnected' THEN 'disconnected'
            WHEN member_status = 'deceased' THEN 'deceased'
            ELSE 'active'
        END
        WHERE active_v2 IS NULL
        """
    )
    conn.commit()

    # Second pass: anyone currently 'active' but silent 56+ days becomes
    # 'non-active'. Same last-seen definition (attendance UNION connect_cards)
    # as elder_shepherding_report.py's _raw_rows(), so this agrees with the
    # nightly recompute job (jobs/congregation/recompute_active_status.py).
    rows = conn.execute(
        """
        SELECT m.id,
               MAX(
                 COALESCE((SELECT MAX(service_date) FROM connect_cards WHERE member_id = m.id), '1900-01-01'),
                 COALESCE((SELECT MAX(service_date) FROM attendance  WHERE member_id = m.id), '1900-01-01')
               ) AS last_seen
        FROM members m
        WHERE m.active_v2 = 'active'
        """
    ).fetchall()

    non_active_ids = []
    for member_id, last_seen in rows:
        days_since = (
            _days_since(last_seen) if last_seen and last_seen != "1900-01-01" else None
        )
        if days_since is None or days_since >= _NON_ACTIVE_CUTOFF_DAYS:
            non_active_ids.append(member_id)

    if non_active_ids:
        placeholders = ",".join("?" for _ in non_active_ids)
        conn.execute(
            f"UPDATE members SET active_v2 = 'non-active' WHERE id IN ({placeholders})",
            non_active_ids,
        )
        conn.commit()

    counts = dict(
        conn.execute(
            "SELECT active_v2, COUNT(*) FROM members GROUP BY active_v2"
        ).fetchall()
    )
    print(f"  [backfilled] active_v2: {counts}")


def _days_since(iso_date):
    from datetime import date

    return (date.today() - date.fromisoformat(iso_date)).days


def _backfill_deacon(conn):
    before = dict(conn.execute("SELECT deacon, COUNT(*) FROM members GROUP BY deacon").fetchall())
    conn.execute(
        "UPDATE members SET deacon = 'Unassigned' "
        "WHERE deacon IS NULL OR TRIM(deacon) = '' OR deacon = 'Inactive'"
    )
    conn.commit()
    after_unassigned = conn.execute(
        "SELECT COUNT(*) FROM members WHERE deacon = 'Unassigned'"
    ).fetchone()[0]
    print(f"  [backfilled] deacon: {after_unassigned} now Unassigned (was {before})")


def _backfill_residency(conn):
    conn.execute(
        """
        UPDATE members SET residency = CASE
            WHEN member_status = 'non_local' THEN 'non-local'
            WHEN member_status = 'snowbird' THEN 'snowbird'
            ELSE 'local'
        END
        WHERE residency IS NULL
        """
    )
    conn.commit()
    counts = dict(
        conn.execute("SELECT residency, COUNT(*) FROM members GROUP BY residency").fetchall()
    )
    print(f"  [backfilled] residency: {counts}")


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        _add_columns(conn)
        _backfill_partner(conn)
        _backfill_active_v2(conn)
        _backfill_deacon(conn)
        _backfill_residency(conn)
        print("Done: partner/active_v2/residency ready, deacon cleaned up.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
