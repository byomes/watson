"""jobs/church_calendar/subsplash_monitor.py -- polls the church's public
Subsplash "Special Events" calendar embed for newly-added events and asks
Kaci Gravatt (the leader who manages event creation, see
jobs/events/schema.py and jobs/events/signup_detect.py) whether Watson
should track signups for each one.

There is no API access to this Subsplash account -- Playwright renders the
public embed page and the event cards are read straight out of the DOM.
Every card links to a stable Subsplash permalink (".../lb/ev/+<id>"); that
id, not the title/date text, is the dedup key for "have we seen this
before".

Recurring series (e.g. a weekly Bible study) post one calendar card per
occurrence, each with its own permalink id but the same title -- Kaci is
only asked once per title, not once per week (see _already_prompted_title
in run()). An event whose name already exists in church_events (e.g. Kaci
told Watson about it directly via the existing Telegram new-event-notice
path, jobs/events/schema.py) is also skipped -- this job only fills the
gap of events nobody told Watson about yet.

Answering "yes" inserts the same kind of church_events row
jobs/events/signup_detect.py already knows how to auto-attach email
registrations to (tracking_active=1, created_by='Kaci Gravatt') -- from
there everything else (signup-email matching) works unchanged. Answering
"no" (or not answering) just leaves the event untracked.

If the scrape itself fails 3 runs in a row (page unreachable, or the
embed's markup changed enough that no event cards parse) Bill gets a
one-time Telegram alert -- see _record_scrape_result. It doesn't fire
again on every failed run after that (would just spam him hourly), only
when a fresh streak first crosses the threshold; a single success resets
the streak.

Cron: hourly, offset from the top of the hour -- see the cron file.
"""
import logging
import re
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from core.vacation import vacation_gate
from jobs.events.schema import create_tables as create_event_tables
from jobs.telegram.pending import store_pending_action

log = logging.getLogger(__name__)

# Catalyst Community Church's public "Special Events" calendar embed.
CALENDAR_URL = "https://subsplash.com/+9tjq/lb/ca/+b7md4wp?embed&branding"
KACI_PERSON_NAME = "Kaci Gravatt"

_EVENT_LINK_RE = re.compile(r"/lb/ev/(\+[a-z0-9]+)")

_FAIL_STREAK_KEY = "subsplash_monitor_fail_streak"
_FAIL_ALERT_THRESHOLD = 3  # alert once failures reach this many in a row ("more than twice")


class ScrapeError(Exception):
    pass


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subsplash_calendar_seen (
                subsplash_id  TEXT PRIMARY KEY,
                title         TEXT NOT NULL,
                date_line     TEXT,
                first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                prompted      INTEGER NOT NULL DEFAULT 0
            )
        """)


_bootstrap()


def _scrape_events() -> list[dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(CALENDAR_URL, wait_until="networkidle", timeout=30000)
        except Exception as exc:
            browser.close()
            raise ScrapeError(f"could not load calendar embed: {exc}")

        cards = page.eval_on_selector_all(
            'a[href*="/lb/ev/"]',
            """
            els => els.map(e => {
              const card = e.closest('div.app-link-to') || e;
              return { href: e.href, text: card.innerText || '' };
            })
            """,
        )
        browser.close()

    events = []
    seen_ids = set()
    for card in cards:
        m = _EVENT_LINK_RE.search(card["href"])
        if not m:
            continue
        subsplash_id = m.group(1)
        if subsplash_id in seen_ids:
            continue
        seen_ids.add(subsplash_id)

        lines = [l for l in card["text"].split("\n") if l.strip()]
        if len(lines) < 4:
            log.warning("card for %s had too few lines, skipping", subsplash_id)
            continue
        events.append({
            "subsplash_id": subsplash_id,
            "title": lines[2].strip(),
            "date_line": lines[3].strip(),
        })

    if not events:
        raise ScrapeError("no event cards parsed -- calendar embed markup may have changed")
    return events


def _parse_date_line(date_line: str) -> tuple[str | None, str | None]:
    """'September 20, 2026 from 10:00am - 2:00pm' -> ('2026-09-20', '10:00am - 2:00pm').

    Falls back to (None, date_line) for any shape this doesn't recognize --
    start_date/event_time are both optional on church_events (see
    jobs/events/schema.py), Kaci or Bill can fill them in from the dashboard.
    """
    m = re.match(r"^(.*\d{4})\s+from\s+(.+)$", date_line.strip())
    if not m:
        return None, date_line.strip() or None
    date_part, time_part = m.group(1).strip(), m.group(2).strip()
    try:
        dt = datetime.strptime(date_part, "%B %d, %Y")
        return dt.strftime("%Y-%m-%d"), time_part
    except ValueError:
        return None, date_line.strip()


def _tg_send_to_kaci(text: str, keyboard: dict) -> int | None:
    if vacation_gate("normal", "jobs.church_calendar.subsplash_monitor", text):
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT telegram_chat_id FROM people WHERE name = ?", (KACI_PERSON_NAME,)
        ).fetchone()
    chat_id = row["telegram_chat_id"] if row else None
    if not chat_id:
        log.error("%s has no telegram_chat_id -- not onboarded, cannot prompt", KACI_PERSON_NAME)
        return None
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "reply_markup": keyboard},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("result", {}).get("message_id")
    except Exception as exc:
        log.error("Telegram send to Kaci failed: %s", exc)
        return None


def _get_fail_streak() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = ?", (_FAIL_STREAK_KEY,)
        ).fetchone()
    return int(row["value"]) if row else 0


def _set_fail_streak(n: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO system_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (_FAIL_STREAK_KEY, str(n)),
        )


def _alert_bill_fail_streak(streak: int, error: str) -> None:
    # "system_failure" priority always sends, even during vacation mode --
    # same convention as jobs/dev/ollama_monitor.py and the other
    # infra-health alerts in this codebase (see core/vacation.py).
    if vacation_gate("system_failure", "jobs.church_calendar.subsplash_monitor", "fail streak"):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("no Telegram credentials -- cannot send fail-streak alert")
        return
    text = (
        f"⚠️ Subsplash calendar monitor has failed {streak} runs in a row.\n"
        f"Latest error: {error}\n\n"
        f"Check logs/subsplash_monitor.log -- the embed's page markup may have changed. - Watson"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        ).raise_for_status()
    except Exception as exc:
        log.error("failed to send fail-streak alert: %s", exc)


def run() -> dict:
    """Called by cron. Never raises -- scrape failures are logged and
    returned as {success: False, error}, same convention as
    jobs/thesis_tracker/scrape.py. A run that fails 3+ times in a row
    (_FAIL_ALERT_THRESHOLD) alerts Bill once per streak; any successful
    run resets the streak."""
    create_event_tables()
    try:
        events = _scrape_events()
    except Exception as exc:
        log.error("scrape failed: %s", exc)
        streak = _get_fail_streak() + 1
        _set_fail_streak(streak)
        if streak == _FAIL_ALERT_THRESHOLD:
            _alert_bill_fail_streak(streak, str(exc))
        return {"success": False, "error": str(exc)}

    _set_fail_streak(0)

    with get_connection() as conn:
        known_ids = {
            r["subsplash_id"]
            for r in conn.execute("SELECT subsplash_id FROM subsplash_calendar_seen")
        }
    new_events = [e for e in events if e["subsplash_id"] not in known_ids]
    prompted = 0

    for e in new_events:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO subsplash_calendar_seen (subsplash_id, title, date_line) VALUES (?, ?, ?)",
                (e["subsplash_id"], e["title"], e["date_line"]),
            )
            already_prompted_title = conn.execute(
                "SELECT 1 FROM subsplash_calendar_seen "
                "WHERE title = ? AND prompted = 1 AND subsplash_id != ? LIMIT 1",
                (e["title"], e["subsplash_id"]),
            ).fetchone()

        with get_connection() as conn:
            already_tracked = conn.execute(
                "SELECT 1 FROM church_events WHERE LOWER(event_name) = LOWER(?) LIMIT 1",
                (e["title"],),
            ).fetchone()

        if already_prompted_title or already_tracked:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE subsplash_calendar_seen SET prompted = 1 WHERE subsplash_id = ?",
                    (e["subsplash_id"],),
                )
            reason = "already tracked in church_events" if already_tracked else "already asked about under a different date"
            log.info("%r %s, skipping", e["title"], reason)
            continue

        start_date, event_time = _parse_date_line(e["date_line"])
        payload = {
            "subsplash_id": e["subsplash_id"],
            "title": e["title"],
            "date_line": e["date_line"],
            "start_date": start_date,
            "event_time": event_time,
        }
        # placeholder telegram_message_id=0, same as
        # jobs/events/signup_detect.py's identical chicken-and-egg (need the
        # pending row's id to build the keyboard, but need the keyboard to
        # send the message that produces a real telegram_message_id) --
        # patched below once the send returns one.
        pending_id = store_pending_action("subsplash_new_event", 0, payload)

        text = (
            f"\U0001F4C5 New event on the church calendar: \"{e['title']}\"\n"
            f"{e['date_line']}\n\n"
            f"Should Watson track signups for this one? - Watson"
        )
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Yes, track it", "callback_data": f"subs_yes:{pending_id}"},
                {"text": "❌ No", "callback_data": f"subs_no:{pending_id}"},
            ]]
        }
        tg_msg_id = _tg_send_to_kaci(text, keyboard)
        if not tg_msg_id:
            continue

        with get_connection() as conn:
            conn.execute(
                "UPDATE tg_pending_actions SET telegram_message_id=? WHERE id=?",
                (tg_msg_id, pending_id),
            )
            conn.execute(
                "UPDATE subsplash_calendar_seen SET prompted = 1 WHERE subsplash_id = ?",
                (e["subsplash_id"],),
            )
        prompted += 1
        log.info("prompted Kaci re %r (pending_id=%d)", e["title"], pending_id)

    return {"success": True, "new_events": len(new_events), "prompted": prompted}


# -- Action handlers (called from bot.py callbacks) -------------------------

def handle_subsplash_new_event_yes(payload: dict) -> str:
    title = payload.get("title", "New Event")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO church_events (event_name, start_date, event_time, tracking_active, created_by) "
            "VALUES (?, ?, ?, 1, ?)",
            (title, payload.get("start_date"), payload.get("event_time"), KACI_PERSON_NAME),
        )
    return f"✅ Now tracking \"{title}\" -- signups will auto-attach as they come in. - Watson"


def handle_subsplash_new_event_no(payload: dict) -> str:
    title = payload.get("title", "this event")
    return f"\U0001F44D Got it, not tracking \"{title}\". - Watson"


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from dotenv import load_dotenv

    load_dotenv(Path.home() / "watson" / ".env")
    logging.basicConfig(level=logging.INFO)
    print(run())
