"""core/db_backup.py — prune old ad-hoc .bak-<timestamp> database snapshots.

Added 2026-09-05: jobs/dashboard/app.py's member-import route and
migrate_links.py both took a defensive shutil.copy2() backup before writing,
with no retention — 67MB across 13 files accumulated with zero cleanup
(2026-09-05 cleanup sweep). Real long-term coverage for this data is the
nightly restic (jobs/backup_local.py, 14 daily/8 weekly/6 monthly) and
OneDrive (jobs/backup.py) legs, both of which already back up data/ with a
real retention policy — these ad-hoc snapshots are just a same-session undo
safety net, not the actual backup, so a short local retention is enough.
"""
import glob
import os


def prune_old_backups(db_path: str, keep: int = 5) -> None:
    """Keep the `keep` most recent `<db_path>.bak-*` snapshots, delete the
    rest. Best-effort — a failed removal (e.g. permissions) is skipped, not
    raised, since this runs as a side effect of an unrelated write path."""
    backups = sorted(glob.glob(f"{db_path}.bak-*"))
    for stale in backups[:-keep] if keep > 0 else backups:
        try:
            os.remove(stale)
        except OSError:
            pass
