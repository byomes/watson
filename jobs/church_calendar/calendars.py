"""jobs/church_calendar/calendars.py -- unified, read-only copy of all four
Catalyst Subsplash calendars (Special Events, Small Groups, Kids, Services).

Each calendar is a public Subsplash embed (no API), read with the same
Playwright card scrape subsplash_monitor.py uses. Every event is upserted
into church_calendar_events (watson.db) keyed by its Subsplash id, with its
event-page details (description, register link) fetched once and cached.
Recurring events are one row per occurrence; `series` (calendar + title)
groups them.

This is deliberately separate from subsplash_monitor.py: that job only
asks Kaci about NEW Special Events. Nothing here messages anyone.

Cron: hourly (see the crontab entry next to the subsplash monitor).
Usage: python -m jobs.church_calendar.calendars   (sync all calendars)
"""
import logging
import re

from playwright.sync_api import sync_playwright

from core.database import get_connection
from jobs.church_calendar.subsplash_monitor import (
    _BROWSER_USER_AGENT, ScrapeError, _parse_date_line, _scrape_events,
)

log = logging.getLogger(__name__)

SITE = "https://subsplash.com/+9tjq"
# name -> embed id (the "+xxxx" in .../lb/ca/+xxxx)
CALENDARS = {
    "Special Events": "+b7md4wp",
    "Small Groups": "+5cxzm6j",
    "Kids": "+c3tcnxz",
    "Services": "+bwmdrdz",
}


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS church_calendar_events (
                subsplash_id  TEXT PRIMARY KEY,
                calendar      TEXT NOT NULL,
                title         TEXT NOT NULL,
                series        TEXT NOT NULL,
                start_date    TEXT,
                time_text     TEXT,
                date_line     TEXT,
                event_url     TEXT NOT NULL,
                register_url  TEXT,
                description   TEXT,
                location      TEXT,
                details_at    TEXT,
                active        INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cce_date ON church_calendar_events(start_date, active)")


_bootstrap()


def event_url(subsplash_id: str) -> str:
    return f"{SITE}/lb/ev/{subsplash_id}"


def _fetch_details(context, subsplash_id: str) -> dict:
    """Open one event page: description, register link, address."""
    page = context.new_page()
    try:
        page.goto(event_url(subsplash_id), wait_until="networkidle", timeout=30000)
        body = page.inner_text("body")
        reg = page.eval_on_selector_all(
            "a", "els=>els.filter(e=>/register/i.test(e.innerText)).map(e=>e.href)")
        # Page text runs: title, date, time, [Add to calendar, Share, Register now,]
        # description, ..., address, email, copyright.
        m = re.search(r"(?:Register now|Share)\n(.*?)(?:\n©|\Z)", body, re.S)
        desc = re.sub(r"^\s*Register now\s*", "", m.group(1)).strip() if m else ""
        desc = re.sub(r"\n{2,}", "\n", desc) or None
        return {"register_url": reg[0] if reg else None, "description": desc}
    finally:
        page.close()


def sync_all() -> dict:
    """Scrape every calendar, upsert events, fetch details for new ones.
    Returns {"added": n, "removed": n, "failed": [calendar names]}."""
    added, removed, failed = 0, 0, []
    for name, cid in CALENDARS.items():
        try:
            events = _scrape_events(f"{SITE}/lb/ca/{cid}?embed&branding")
        except ScrapeError as exc:
            log.error("calendar %s failed: %s", name, exc)
            failed.append(name)
            continue
        seen = {e["subsplash_id"] for e in events}
        with get_connection() as conn:
            known = {r["subsplash_id"] for r in conn.execute(
                "SELECT subsplash_id FROM church_calendar_events WHERE calendar = ?", (name,))}
            for e in events:
                start_date, time_text = _parse_date_line(e["date_line"])
                if e["subsplash_id"] not in known:
                    added += 1
                    log.info("new event in %s: %s %s", name, e["title"], e["date_line"])
                conn.execute("""
                    INSERT INTO church_calendar_events
                        (subsplash_id, calendar, title, series, start_date, time_text, date_line, event_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(subsplash_id) DO UPDATE SET
                        title=excluded.title, series=excluded.series, start_date=excluded.start_date,
                        time_text=excluded.time_text, date_line=excluded.date_line,
                        active=1, updated_at=datetime('now')
                """, (e["subsplash_id"], name, e["title"], f"{name}|{e['title']}",
                      start_date, time_text, e["date_line"], event_url(e["subsplash_id"])))
            # Future events that vanished from the calendar were removed or moved.
            for sid in known - seen:
                cur = conn.execute(
                    "UPDATE church_calendar_events SET active=0, updated_at=datetime('now') "
                    "WHERE subsplash_id=? AND active=1 AND (start_date IS NULL OR start_date >= date('now'))",
                    (sid,))
                if cur.rowcount:
                    removed += 1
                    log.info("event removed from %s: %s", name, sid)
    _fill_details()
    return {"added": added, "removed": removed, "failed": failed}


def _fill_details(limit: int = 80) -> None:
    with get_connection() as conn:
        todo = [r["subsplash_id"] for r in conn.execute(
            "SELECT subsplash_id FROM church_calendar_events WHERE details_at IS NULL AND active=1 "
            "ORDER BY start_date LIMIT ?", (limit,))]
    if not todo:
        return
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=_BROWSER_USER_AGENT)
        for sid in todo:
            try:
                d = _fetch_details(context, sid)
            except Exception as exc:
                log.warning("details for %s failed: %s", sid, exc)
                continue
            with get_connection() as conn:
                conn.execute(
                    "UPDATE church_calendar_events SET register_url=?, description=?, "
                    "details_at=datetime('now') WHERE subsplash_id=?",
                    (d["register_url"], d["description"], sid))
        browser.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(sync_all())
