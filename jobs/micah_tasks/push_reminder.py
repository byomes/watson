"""jobs/micah_tasks/push_reminder.py -- web-push reminders for Micah's Tasks.

Micah's Tasks (micah-tasks.vercel.app, wtsn.me/m/task) is its own Next.js
app with its own Neon Postgres -- Watson has no other role in it. This job
exists only because Vercel's free-tier cron can fire at most once a day,
which isn't fine-grained enough for per-task reminder times, so it reuses
Watson's existing cron instead. It talks to that Postgres DB directly and
sends real Web Push notifications (VAPID) straight to the subscriber's
browser -- no Telegram, no HTTP call into the Vercel app.

Two kinds of reminder, both deduped per calendar day so a 5-minute cron
firing repeatedly inside the same time window doesn't double-send:
  - "digest": once per user at their configured digest_time, listing
    whatever's still due and incomplete today.
  - "task": once per task, at that task's own reminder_time, fanned out to
    every subscribed device (tasks have no per-user assignee -- see
    2026-09-02 removal of assigned_to).

Cron: */5 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/micah_tasks/push_reminder.py
"""
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
from pywebpush import WebPushException, webpush

from config.settings import (
    MICAH_TASKS_DATABASE_URL,
    MICAH_TASKS_VAPID_PRIVATE_KEY,
    MICAH_TASKS_VAPID_SUBJECT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Matches lib/queries.ts APP_TIMEZONE -- day boundaries and "now" follow the
# family's home timezone, not the server's.
APP_TIMEZONE = ZoneInfo("America/New_York")
WINDOW_MINUTES = 3  # tolerance either side of a target HH:MM, > half the 5-min cron cadence
APP_URL = "/m/task"


def within_window(now: datetime, target_hhmm: str) -> bool:
    try:
        h, m = (int(x) for x in target_hhmm.split(":"))
    except (ValueError, AttributeError):
        return False
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return abs((now - target).total_seconds()) <= WINDOW_MINUTES * 60


def due_tasks(conn, today: str):
    """Active tasks due today (recurring match or one-off) with no completion yet."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            select t.id, t.title, t.reminder_time
            from tasks t
            left join completions c on c.task_id = t.id and c.date = %(today)s
            where t.active and c.id is null
              and (
                t.recurrence = 'daily'
                or (t.recurrence = 'weekly' and t.weekday = extract(dow from %(today)s::date)::int)
                or (t.recurrence = 'once' and t.due_date = %(today)s)
              )
            order by t.category, t.created_at
            """,
            {"today": today},
        )
        return cur.fetchall()


def subscriptions_by_user(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("select user_id, endpoint, p256dh, auth from push_subscriptions")
        rows = cur.fetchall()
    by_user: dict[str, list[dict]] = {}
    for row in rows:
        by_user.setdefault(row["user_id"], []).append(row)
    return by_user


def digest_times(conn) -> dict[str, str]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("select id, digest_time from users")
        return {row["id"]: row["digest_time"] for row in cur.fetchall()}


def send_push(sub: dict, payload: dict) -> bool:
    """Returns False if the subscription is gone and should be deleted."""
    try:
        webpush(
            subscription_info={
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            },
            data=json.dumps(payload),
            vapid_private_key=MICAH_TASKS_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": MICAH_TASKS_VAPID_SUBJECT},
        )
        return True
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (404, 410):
            return False
        log.error("Push failed (endpoint %s...): %s", sub["endpoint"][-12:], exc)
        return True  # transient — keep the subscription


def prune_subscription(conn, endpoint: str):
    with conn.cursor() as cur:
        cur.execute("delete from push_subscriptions where endpoint = %s", (endpoint,))
    conn.commit()


def already_logged(conn, table: str, where: str, params: tuple) -> bool:
    with conn.cursor() as cur:
        cur.execute(f"select 1 from {table} where {where}", params)
        return cur.fetchone() is not None


def log_sent(conn, table: str, columns: str, values: tuple):
    with conn.cursor() as cur:
        cur.execute(
            f"insert into {table} ({columns}) values ({','.join(['%s'] * len(values))}) on conflict do nothing",
            values,
        )
    conn.commit()


def run_digests(conn, now: datetime, today: str, tasks, subs_by_user, times_by_user):
    for user_id, target in times_by_user.items():
        subs = subs_by_user.get(user_id)
        if not subs or not within_window(now, target):
            continue
        if already_logged(conn, "digest_log", "user_id = %s and date = %s", (user_id, today)):
            continue
        if tasks:
            titles = ", ".join(t["title"] for t in tasks[:6])
            more = f" (+{len(tasks) - 6} more)" if len(tasks) > 6 else ""
            body = f"{titles}{more}"
            for sub in subs:
                ok = send_push(
                    sub,
                    {"title": f"{len(tasks)} task(s) today", "body": body, "url": APP_URL, "tag": "digest"},
                )
                if not ok:
                    prune_subscription(conn, sub["endpoint"])
            log.info("Digest sent to %s: %d task(s)", user_id, len(tasks))
        log_sent(conn, "digest_log", "user_id, date", (user_id, today))


def run_task_reminders(conn, now: datetime, today: str, tasks, subs_by_user):
    all_subs = [sub for subs in subs_by_user.values() for sub in subs]
    if not all_subs:
        return
    for task in tasks:
        if not task["reminder_time"] or not within_window(now, task["reminder_time"]):
            continue
        if already_logged(conn, "task_reminder_log", "task_id = %s and date = %s", (task["id"], today)):
            continue
        for sub in all_subs:
            ok = send_push(
                sub,
                {"title": "Reminder", "body": task["title"], "url": APP_URL, "tag": f"task-{task['id']}"},
            )
            if not ok:
                prune_subscription(conn, sub["endpoint"])
        log.info("Task reminder sent: %r", task["title"])
        log_sent(conn, "task_reminder_log", "task_id, date", (task["id"], today))


def main():
    if not MICAH_TASKS_DATABASE_URL or not MICAH_TASKS_VAPID_PRIVATE_KEY:
        log.error("MICAH_TASKS_DATABASE_URL / MICAH_TASKS_VAPID_PRIVATE_KEY not set — skipping")
        return

    now = datetime.now(APP_TIMEZONE)
    today = now.strftime("%Y-%m-%d")

    conn = psycopg2.connect(MICAH_TASKS_DATABASE_URL)
    try:
        subs_by_user = subscriptions_by_user(conn)
        if not subs_by_user:
            return
        tasks = due_tasks(conn, today)
        run_digests(conn, now, today, tasks, subs_by_user, digest_times(conn))
        run_task_reminders(conn, now, today, tasks, subs_by_user)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
