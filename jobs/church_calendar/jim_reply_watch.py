"""jobs/church_calendar/jim_reply_watch.py -- one-off: tell Bill (Telegram) the
first time Jim Bouchat replies after Watson's 2026-10-06 ask about posting the
Billiards Outing invite in the Men's Fraternity group.

Relay only: it does not answer Jim and does not post anything. When it notifies
it removes its own cron line (matched by the marker below, since the generic
one-off self-delete matches bare filenames and misses -m module paths).

Cron: */2 * * * * ... -m jobs.church_calendar.jim_reply_watch  # jim_reply_watch
"""
import subprocess

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection

BASELINE_LOG_ID = 941  # highest telegram_log id when Watson's ask went out
MARKER = "# jim_reply_watch"


def _remove_cron() -> None:
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    kept = "\n".join(l for l in cur.splitlines() if MARKER not in l) + "\n"
    subprocess.run(["crontab", "-"], input=kept, text=True)


def run() -> None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, message, created_at FROM telegram_log "
            "WHERE recipient = 'Jim Bouchat' AND direction = 'in' AND id > ? ORDER BY id LIMIT 1",
            (BASELINE_LOG_ID,)).fetchone()
    if not row:
        return
    text = (f"Jim replied at {row['created_at']} about the Men's Fraternity post:\n\n"
            f"\"{row['message'][:700]}\"\n\n"
            "I have not posted anything in the Men's Fraternity group.")
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15)
    if r.ok:
        _remove_cron()


if __name__ == "__main__":
    run()
