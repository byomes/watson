# Memory Sync — Cron Entry

Add this to crontab (`crontab -e`) to sync memory flat files to watson.db every minute:

```
PYTHONPATH=/home/billyomes/watson * * * * * /home/billyomes/watson/venv/bin/python3 -m jobs.memory.sync >> /home/billyomes/watson/logs/memory_sync.log 2>&1
```

(Every 5 minutes)

---

# Capability Gap Audit — Cron Entry

Add this to crontab (`crontab -e`) to run the weekly capability audit every Monday at 7am:

```
PYTHONPATH=/home/billyomes/watson 0 7 * * 1 /home/billyomes/watson/venv/bin/python3 -m jobs.skillbuilder.audit >> /home/billyomes/watson/logs/audit.log 2>&1
```

(Weekly, Mondays 7am)

---

# Google Contacts Sync — Cron Entry

Add this to crontab (`crontab -e`) to sync Google Contacts into the People Registry every Sunday at 3am:

```
PYTHONPATH=/home/billyomes/watson 0 3 * * 0 /home/billyomes/watson/venv/bin/python3 -m jobs.people.google_contacts sync >> /home/billyomes/watson/logs/contacts_sync.log 2>&1
```

(Weekly, Sundays 3am)

---

# Dev Loop Cleanup — Cron Entry

Add this to crontab (`crontab -e`) to purge dev loop projects older than 7 days every Monday at 4am:

```
PYTHONPATH=/home/billyomes/watson 0 4 * * 1 /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/dev_loop/cleanup.py >> /home/billyomes/watson/logs/devloop_cleanup.log 2>&1
```

(Weekly, Mondays 4am)

---

# Thesis Tracker Token Health — Cron Entry (TEMPORARY)

Added to crontab daily at 8am to check whether the Digital Commons/bepress
dashboard auth token (`DC_DASHBOARD_LINK`) is still alive. Telegram alert
fires only on failure. Remove after 2026-07-18 (two-week trial run).

```
10 8 * * 6 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/thesis_tracker/scrape.py >> /home/billyomes/watson/logs/thesis_scrape.log 2>&1  # weekly Sat, changed from daily 2026-07-07, token_health.py retired
```

(Daily, 8am — remove 2026-07-18)

---

# Adelphos Academy — New Account Security Monitor — Cron Entry

Priority 1 build (2026-07-31) in response to active fraudulent signups on
www.adelphosonline.com. Add this to crontab (`crontab -e`) to poll for new
Moodle account signups every 5 minutes and alert Bill via Telegram with
Suspend/Allow buttons.

**Blocked as of 2026-07-31:** `core_user_get_users` and `core_user_update_users`
are not yet enabled on the Moodle external service tied to
`ADELPHOS_MOODLE_TOKEN` — the job will log a `MoodleAPIError` and exit
without alerting until those functions are added in Moodle admin (Site
administration > Server > Web services > External services). Safe to install
the cron entry now; it'll start working the moment the Moodle side is fixed.

```
*/5 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/adelphos/security_monitor.py >> /home/billyomes/watson/logs/adelphos_security_monitor.log 2>&1
```

(Every 5 minutes)

---

# 2am Doc/KB Job Stagger — Cron Change (2026-08-03)

`jobs/dev/file_map.py`, `jobs/dev/bugs_backlog_sync.py`, `jobs/dev/update_arch.py`,
and `jobs/kb/sync_and_index.py` were all previously cron'd at the identical `0 2 * * *`
slot, each independently running its own `git commit` + `git push origin main` against
the same working tree with no shared lock between them (`sync_and_index.py`'s file lock
only serializes against its own immediate-trigger sibling, not these three). This already
caused a real failure once: 2026-07-23's `sync_and_index.log` shows a `git pull --ff-only`
abort ("Your local changes to memory/FILE_MAP.md would be overwritten") from exactly this
collision. Staggered by a few minutes each instead of extending the lock to be repo-wide —
`sync_and_index.py` runs first on a clean tree since it's the one with real content
(transcripts) at stake, the doc jobs follow serially after it.

```
0 2 * * *  jobs/kb/sync_and_index.py
5 2 * * *  jobs/dev/file_map.py
10 2 * * * jobs/dev/bugs_backlog_sync.py
15 2 * * * jobs/dev/update_arch.py
```

(Daily, staggered 5 minutes apart starting 2am)
