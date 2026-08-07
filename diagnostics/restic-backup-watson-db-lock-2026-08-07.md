# Local (restic) backup failure — `watson.db` — 2026-08-07 02:30

**Status:** Diagnosis complete. Root cause confirmed. No fix applied (diagnosis-only session).
**Alert seen:** `❌ Local (restic) backup failed — check logs / Failed steps: watson.db`
**Job:** `jobs/backup_local.py` (cron 2:30am → `/mnt/family-storage/watson/restic-repo`)

## Root cause

The `sqlite3 .backup` snapshot of `watson.db` failed with **`Error: database is
locked`** at `2026-08-07 02:30:02`. Another process held a write lock on
`watson.db` at that instant, and the snapshot command gave up immediately.

This is a transient lock-contention failure — **not** a mount, permission,
restic-repo, or corruption problem. Three things line up to make `watson.db`
(and only `watson.db`) fail:

1. **`watson.db` is in `delete` (rollback) journal mode, not WAL.**
   `PRAGMA journal_mode` → `delete`. In rollback mode a writer takes an
   EXCLUSIVE lock that blocks *all* readers, including the online-backup
   reader. (WAL mode would let the backup read concurrently with a writer.)
2. **The snapshot has no busy timeout.** `PRAGMA busy_timeout` on the DB is
   `0`, and `jobs/backup_local.py` calls `sqlite3 <db> ".backup <dst>"` with
   no `.timeout` / `busy_timeout`. So on any contention it returns
   "database is locked" instantly instead of waiting and retrying.
3. **2:30:00 is a high-collision minute.** The backup fires in the same
   minute as a large stack of other cron jobs — `email_intake.py` (every
   minute), `devdispatch/poller.py` (every 2 min), and ~7 `*/5` + ~6 `*/15`
   jobs, several of which write `watson.db` — on top of the always-on
   `watson-bot` and `watson-dashboard` services that touch it continuously.

`watson.db` is the hottest database, so it lost the race. The three cold DBs
(`congregation.db`, `donors.db`, `curator.db`) snapshotted fine the same
second, which is exactly the fingerprint of momentary write contention rather
than a systemic fault.

## Evidence

- **Log** (`logs/backup_local.log`):
  ```
  [2026-08-07 02:30:02] Snapshotting watson.db...
  [2026-08-07 02:30:02] ERROR on watson.db snapshot: Error: database is locked
  ...
  [2026-08-07 02:30:09] OK: restic backup
  [2026-08-07 02:30:10] === Local backup completed WITH ERRORS: ['watson.db'] ===
  ```
  The two prior runs (2026-08-05 22:36 manual, 2026-08-06 02:30) succeeded —
  this is the first occurrence, but it is a structural/recurring risk, not a
  one-off fluke.
- **Mount:** `/mnt/family-storage` mounted, `/dev/sda1`, 1.7T free (1% used). OK.
- **Permissions:** `/mnt/family-storage/watson` = `drwx------ billyomes billyomes`
  (chmod 700, correct); `restic-repo` owned by `billyomes`. OK.
- **Restic locks:** `restic list locks` → empty. Repo is **not** stuck/locked
  from a prior run. No `restic unlock` needed.
- **DB state now:** `PRAGMA journal_mode=delete`, `busy_timeout=0`. DB opens
  fine; not currently locked.
- **Not previously tracked:** no matching `bug_tracker` row.

## Impact — read this part

The failure of the `.backup` step meant `watson.db` was **never added to the
restic source list**, so last night's restic snapshot silently omitted it:

- Snapshot `b64bbfa6` (2026-08-07 02:30) — **watson.db MISSING**; all other
  data present.
- Snapshot `3b781b2c` (2026-08-06 02:30) — watson.db present. This is the most
  recent local restic copy of `watson.db` (~24h stale).

The independent **OneDrive offsite leg** (`jobs/backup.py`, 3:00am) snapshotted
`watson.db` cleanly last night (`03:00:02 → 03:00:11 OK`, no lock), so a
current off-machine copy of `watson.db` from 2026-08-07 **does exist**. The gap
is only in the local restic leg.

Secondary concern: a DB-snapshot failure currently still commits a restic
snapshot that is missing the single most important database, then runs
`forget --prune` as normal. The ❌ Telegram alert fired (good), but the partial
snapshot is retained silently.

## Proposed fix (for Bill's approval — not applied)

Minimal, targeted:

1. **Add a busy timeout + a couple of retries to the DB snapshot step** in
   `jobs/backup_local.py` — e.g. `sqlite3 -cmd '.timeout 60000' <db> ".backup
   <dst>"`, or do the `.backup` via Python `sqlite3` with
   `PRAGMA busy_timeout=60000` and retry 2–3× with backoff. This makes the
   snapshot wait for a brief writer to finish instead of failing instantly.
   *(Primary fix — smallest change, directly addresses the mechanism.)*
2. **Move the cron off the 2:30:00 collision minute** to an odd, low-collision
   minute (e.g. `33 2 * * *`) so it stops firing alongside the `*/2`, `*/5`,
   `*/15` stack; only the every-minute `email_intake` would remain. Zero code,
   big contention reduction. *(Cheap secondary mitigation.)*

Optional / more structural:

3. Consider switching `watson.db` to **WAL** journal mode so backup reads never
   block on writers — broader change, affects all access patterns; evaluate
   separately.
4. Treat a DB-snapshot failure as fail-loud: don't retain/prune a snapshot
   that is missing a core DB (or at least surface the omission distinctly),
   so a partial backup can't quietly become the retained copy.

Also recommend opening a `bug_tracker` row (currently untracked) per the
CLAUDE.md bug-logging convention — left for Bill to open or approve, since this
session is diagnosis-only and does not write to live `watson.db`.
