# Memory Sync — Cron Entry

Add this to crontab (`crontab -e`) to sync memory flat files to watson.db every minute:

```
PYTHONPATH=/home/billyomes/watson * * * * * /home/billyomes/watson/venv/bin/python3 -m jobs.memory.sync >> /home/billyomes/watson/logs/memory_sync.log 2>&1
```

(Every 5 minutes)
