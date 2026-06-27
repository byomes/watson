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
