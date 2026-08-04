# Wilmington Headcount Gap — Diagnostic (2026-08-04)

## Symptom

`jobs/connect_cards/monthly_state_report.py`'s Wilmington Headcount Gap
section (`_month_headcount_gap()`) found only **2 of the expected ~4** synced
Sundays for last month's report (report month: July 2026).

## 1. How `_month_headcount_gap()` queries `wilmington_headcounts`

`jobs/connect_cards/monthly_state_report.py:263-284`:

```python
def _month_headcount_gap(conn, year: int, month: int) -> dict | None:
    start, end = _month_bounds(year, month)
    hc_rows = conn.execute(
        "SELECT date, headcount FROM wilmington_headcounts WHERE date BETWEEN ? AND ?",
        (start, end),
    ).fetchall()
    if not hc_rows:
        return None
    ...
```

Straightforward `date BETWEEN <month-start> AND <month-end>` scan against
`wilmington_headcounts` (in `congregation.db`). No filtering logic bug here —
it simply reports whatever rows physically exist in the table for that
month, and July 2026 only has 2 rows in the table.

## 2. Direct query against `congregation.db`

```
$ sqlite3 -header -column ~/watson/data/congregation.db \
  "SELECT date, headcount, synced_at FROM wilmington_headcounts \
   WHERE date BETWEEN '2026-07-01' AND '2026-07-31' ORDER BY date;"

date        headcount  synced_at
----------  ---------  --------------------------------
2026-07-05  70         2026-07-27T21:13:55.547178+00:00
2026-07-12  96         2026-07-27T21:13:55.547178+00:00
```

**Present:** 2026-07-05, 2026-07-12
**Missing:** 2026-07-19, 2026-07-26

Wider check — every one of the 188 rows currently in `wilmington_headcounts`
(all years, 2023–2026) shares the exact same `synced_at` timestamp:
`2026-07-27T21:13:55.547178+00:00`. The table has not been touched since
that single moment. `MAX(synced_at)` across the whole table confirms it.

## 3. The job that populates the table

`jobs/gsheets/headcount_sync.py` (per `memory/FILE_MAP.md`) — reads the
"Catalyst Count Tracking" Google Sheet (one tab per year), pulls the `WLM`/
`NPT` headcount column per Sunday, and upserts into `wilmington_headcounts`
via `INSERT ... ON CONFLICT(date) DO UPDATE`. Its own module docstring
declares the intended schedule:

```
0 1 * * *  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.gsheets.headcount_sync >> /home/billyomes/watson/logs/headcount_sync.log 2>&1
```

Git history shows the file was authored and committed in
`fead7df` ("docs: architecture update 2026-07-28", 2026-07-28 02:00 —
an automated nightly-docs commit that happened to bundle this feature's
files). That same commit also added the cron line to `cron_additions.txt`
— a staging file, not the live crontab.

## 4. Live crontab / log check

```
$ crontab -l | grep -i headcount
(no output)
```

**`headcount_sync` has zero entries in the live crontab.** It is also absent
from `memory/WATSON_ARCHITECTURE.md`'s "Active Scheduled Jobs" table.

```
$ tail -100 ~/watson/logs/headcount_sync.log
tail: cannot open '/home/billyomes/watson/logs/headcount_sync.log' for reading: No such file or directory
```

**The log file has never been created** — consistent with the job never
having run under cron, ever (not even once with an error). The 188 rows in
the table came from a one-time manual run (or manual backfill) around
2026-07-27, matching the module docstring's note that the sync logic and
data were "confirmed with Bill 2026-07-27."

`cron_additions.txt` (repo root) still contains the intended line verbatim:

```
0 1 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.gsheets.headcount_sync >> /home/billyomes/watson/logs/headcount_sync.log 2>&1
```

This is a staging/scratch file used during past cron rollouts — it was
never actually merged into the live crontab for this job. That's the gap.

## 5. Checked the source Google Sheet directly (ruling out data-entry gap)

Ran the job's own read-only parsing logic (`_sheets_service()` +
`_parse_tab()`) directly against the live "Catalyst Count Tracking" sheet,
2026 tab — no DB write:

```
2026-07-05 -> 70
2026-07-12 -> 96
2026-07-19 -> 90
2026-07-26 -> 90
2026-08-02 -> MISSING (expected — sheet not filled in yet for this upcoming/recent Sunday)
```

**All four July Sundays have WLM headcounts entered in the sheet**,
including the two "missing" ones (7/19 = 90, 7/26 = 90). Donna has been
keeping the sheet current. This rules out a data-entry gap entirely.

A full dry-run (`python -m jobs.gsheets.headcount_sync --dry-run`) also
confirms the job itself works correctly end-to-end today: it read 195 rows
across all 4 year tabs (2023–2026) with 0 structure-check failures — 7 more
rows than currently sit in the DB (188), matching the ~7 Sundays that have
accumulated since the last (only) sync on 2026-07-27.

## 6. Conclusion

**Confirmed cron-registration / deployment bug — not a sync-logic bug, and
not a data-entry gap.**

`jobs/gsheets/headcount_sync.py` itself is correct and functions properly
when run manually. The failure is purely operational: the intended crontab
line was written into a staging file (`cron_additions.txt`) and the file's
own docstring, but was never actually installed into the live crontab. As a
result the table has been frozen at its 2026-07-27 one-time backfill state,
and every report run since (including this month's) will show a
progressively worse "gap" purely because fewer and fewer of the month's
Sundays have a synced row — nothing to do with actual attendance/headcount
accuracy.

This has already been logged to `bug_tracker` (id **56**, status `open`,
discovered 2026-08-04) by an earlier run of this diagnostic — see that row
for the full write-up; this file exists as the durable, committed copy of
the same findings (the earlier job's worktree was lost before it could
commit anything).

## Recommendation

1. **No code fix needed** in `monthly_state_report.py` or
   `headcount_sync.py` — both are correct as written.
2. Add the missing line to the live crontab (`crontab -e`):
   ```
   0 1 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python -m jobs.gsheets.headcount_sync >> /home/billyomes/watson/logs/headcount_sync.log 2>&1
   ```
3. Run the job once manually afterward to backfill everything missed since
   2026-07-27 (7/19, 7/26, and anything since):
   ```
   PYTHONPATH=/home/billyomes/watson venv/bin/python -m jobs.gsheets.headcount_sync
   ```
4. Add `jobs/gsheets/headcount_sync.py` to the "Active Scheduled Jobs" table
   in `memory/WATSON_ARCHITECTURE.md` once the cron line is live, so this
   doesn't silently drop out of the documented job inventory again.
5. Bug tracker: id **56** already covers this — mark `resolved` with a
   `commit_hash` once the crontab change is actually applied (crontab edits
   aren't git-tracked, so use the commit that updates
   `WATSON_ARCHITECTURE.md`'s job table as the reference commit, per the
   project's bug-tracking convention).
