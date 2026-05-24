"""
scheduler.py — Publish scheduled blog drafts to byomes/wcky.

Runs daily on the Stream (via cron). Assigns pending drafts to the next
available Tuesday/Thursday/Saturday 10am slot and pushes to GitHub at
publish time.

Cron entry (runs every day at 10am):
  0 10 * * * cd /home/billyomes/watson && python3 jobs/scheduler.py >> data/scheduler.log 2>&1

Usage:
  python3 jobs/scheduler.py           # normal run
  python3 jobs/scheduler.py --dry-run # show what would publish without pushing
"""

import argparse
import base64
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

WCKY_GITHUB_REPO  = os.getenv("WCKY_GITHUB_REPO",  "byomes/wcky")
WCKY_GITHUB_TOKEN = os.getenv("WCKY_GITHUB_TOKEN")
WATSON_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN")
WATSON_CHAT_ID   = os.getenv("WATSON_CHAT_ID")
VERCEL_DEPLOY_HOOK = os.getenv("VERCEL_DEPLOY_HOOK")

# Publish days: 1=Tuesday, 3=Thursday, 5=Saturday
PUBLISH_DAYS = {1, 3, 5}
PUBLISH_HOUR = 10


def _get_connection():
    from core.database import get_connection
    return get_connection()


def _next_publish_slots(count: int, after: date = None) -> list[date]:
    """Return the next `count` publish dates (Tue/Thu/Sat) after `after`."""
    if after is None:
        after = date.today()
    slots = []
    d = after + timedelta(days=1)
    while len(slots) < count:
        if d.weekday() in PUBLISH_DAYS:
            slots.append(d)
        d += timedelta(days=1)
    return slots


def _last_scheduled_date() -> date | None:
    """Return the latest scheduled_date already assigned in the DB."""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(scheduled_date) as last FROM blog_drafts WHERE scheduled_date IS NOT NULL"
        ).fetchone()
    if row and row["last"]:
        return date.fromisoformat(row["last"])
    return None


def _get_pending_drafts() -> list:
    """Return all drafts with status='pending' and no scheduled_date, oldest first."""
    with _get_connection() as conn:
        return conn.execute(
            """SELECT id, title, slug, body, created_at
               FROM blog_drafts
               WHERE status = 'pending' AND scheduled_date IS NULL
               ORDER BY id ASC"""
        ).fetchall()


def _get_due_drafts(target_date: str = None) -> list:
    """Return drafts scheduled for today."""
    today = target_date or date.today().isoformat()
    with _get_connection() as conn:
        return conn.execute(
            """SELECT id, title, slug, body, scheduled_date
               FROM blog_drafts
               WHERE status = 'pending' AND scheduled_date = ?
               ORDER BY id ASC""",
            (today,),
        ).fetchall()


def _assign_schedule(draft_id: int, publish_date: date) -> None:
    with _get_connection() as conn:
        conn.execute(
            "UPDATE blog_drafts SET scheduled_date = ? WHERE id = ?",
            (publish_date.isoformat(), draft_id),
        )
    log.info("Draft #%d scheduled for %s", draft_id, publish_date)


def _build_md(draft: dict) -> str:
    """Build final frontmatter + body for publication."""
    title = draft["title"]
    slug  = draft["slug"]
    pub_date = draft["scheduled_date"]
    body  = draft["body"]

    # Generate excerpt from first 160 chars of body
    plain = body.replace("#", "").replace("*", "").replace("\n", " ").strip()
    plain = plain.replace('"', "'")  # prevent YAML breakage
    excerpt = plain[:157] + "..." if len(plain) > 160 else plain

    return (
        f"---\n"
        f"title: \"{title}\"\n"
        f"date: \"{pub_date}\"\n"
        f"slug: \"{slug}\"\n"
        f"category: \"Teaching\"\n"
        f"categories: [\"Teaching\"]\n"
        f"excerpt: \"{excerpt}\"\n"
        f"---\n\n"
        f"{body}\n"
    )


def _push_to_github(filename: str, content: str, title: str) -> str:
    """Push file to byomes/wcky via GitHub API. Returns html_url."""
    if not WCKY_GITHUB_TOKEN:
        raise RuntimeError("WCKY_GITHUB_TOKEN not set")

    api_url = f"https://api.github.com/repos/{WCKY_GITHUB_REPO}/contents/content/blog/{filename}"
    headers = {
        "Authorization": f"token {WCKY_GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    existing = requests.get(api_url, headers=headers, timeout=10)
    sha = existing.json().get("sha") if existing.status_code == 200 else None

    payload = {
        "message": f"publish: {title}",
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()["content"]["html_url"]


def _trigger_vercel() -> None:
    if not VERCEL_DEPLOY_HOOK:
        log.warning("VERCEL_DEPLOY_HOOK not set — skipping deploy trigger")
        return
    resp = requests.post(VERCEL_DEPLOY_HOOK, timeout=10)
    resp.raise_for_status()
    log.info("Vercel deploy triggered")


def _mark_published(draft_id: int) -> None:
    with _get_connection() as conn:
        conn.execute(
            "UPDATE blog_drafts SET status = 'published', published_at = date('now') WHERE id = ?",
            (draft_id,),
        )


def assign_schedules() -> None:
    """Assign publish dates to any unscheduled pending drafts."""
    pending = _get_pending_drafts()
    if not pending:
        log.info("No unscheduled drafts to assign")
        return

    # Start slots after the last already-scheduled date
    last = _last_scheduled_date()
    slots = _next_publish_slots(len(pending), after=last)

    for draft, slot in zip(pending, slots):
        _assign_schedule(draft["id"], slot)

    log.info("Assigned %d drafts to publish slots", len(pending))


def publish_due(dry_run: bool = False, target_date: str = None) -> None:
    """Push any drafts scheduled for today to GitHub."""
    due = _get_due_drafts(target_date=target_date)
    if not due:
        log.info("No drafts due today (%s)", date.today().isoformat())
        return

    log.info("%d draft(s) due today", len(due))
    deployed = False

    for draft in due:
        title    = draft["title"]
        slug     = draft["slug"]
        pub_date = draft["scheduled_date"]
        filename = f"{pub_date}-{slug}.md"
        md       = _build_md(dict(draft))

        if dry_run:
            log.info("[DRY RUN] Would publish: %s", filename)
            continue

        try:
            url = _push_to_github(filename, md, title)
            _mark_published(draft["id"])
            log.info("Published: %s → %s", filename, url)
            deployed = True
        except Exception as e:
            log.error("Failed to publish draft #%d: %s", draft["id"], e)
            try:
                if WATSON_BOT_TOKEN and WATSON_CHAT_ID:
                    requests.post(
                        f"https://api.telegram.org/bot{WATSON_BOT_TOKEN}/sendMessage",
                        json={"chat_id": WATSON_CHAT_ID, "text": f"⚠️ Watson scheduler failed to publish: {filename}\n\nError: {e}", "parse_mode": "HTML"},
                        timeout=10,
                    )
            except Exception:
                pass

    if deployed:
        try:
            _trigger_vercel()
        except Exception as e:
            log.error("Vercel deploy failed: %s", e)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Blog publish scheduler")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would publish without pushing")
    parser.add_argument("--date", default=None,
                        help="Publish drafts due on this date (YYYY-MM-DD), default today")
    args = parser.parse_args()

    log.info("Scheduler running — %s", args.date or date.today().isoformat())

    # Step 1: assign slots to any new unscheduled drafts
    assign_schedules()

    # Step 2: publish anything due today
    publish_due(dry_run=args.dry_run, target_date=args.date)

    log.info("Scheduler done")


if __name__ == "__main__":
    main()
