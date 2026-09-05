"""jobs/browser/browser_service.py — shared headless-browser context manager.

Mirrors the per-call pattern in jobs/gcal/gcal_service.py: no resident
browser process. get_page() launches a fresh Chromium instance, yields a
page, and tears the whole browser down again on exit — every call pays a
cold-launch cost, but nothing sits around holding memory or a lock between
calls.

CONCURRENCY GUARDRAIL — read before wiring in a new consumer:
Chromium render time sitting on this box carries the same kind of blocking
risk already flagged around OLLAMA_NUM_PARALLEL=1 (see the FMSPC/Hardware
section of WATSON_ARCHITECTURE.md) — a long-running call can starve
unrelated concurrent work. Browser jobs must run as one-off
subprocess.Popen invocations, the same non-blocking pattern Dev Loop uses
(jobs/dev_loop/trigger.py launches jobs/dev_loop/loop.py via Popen and
returns immediately). Never call get_page()/goto_safe() synchronously from
inside bot.py's Telegram handler path or any other request-serving code —
dispatch the job as its own process and notify on completion instead.

This module is the shared primitive only. It is not wired into any
consumer as of this pass — Curator's fetch logic and the Thesis Tracker
scraper are separate, later work.
"""
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from playwright.async_api import Page, async_playwright

from core.database import get_connection

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 15000

# Playwright's own default UA string self-identifies as headless (e.g.
# "HeadlessChrome/...") — some sites treat that differently (block it,
# serve a degraded/no-JS variant, etc.). This matches the UA convention
# Watson's other scraping code already uses (jobs/curator/research.py's
# _UA) rather than introducing a second style.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Cached per-origin so a job hitting many pages on the same site doesn't
# refetch robots.txt every navigation.
_robots_cache: dict[str, RobotFileParser] = {}


def _fetch_robots_parser(origin: str) -> RobotFileParser:
    """Fetches and parses `origin`/robots.txt ourselves via requests, rather
    than RobotFileParser.read()'s own urlopen — confirmed live 2026-07-22
    against httpbin.org (whose robots.txt returned a 503): stdlib
    RobotFileParser only fails open for HTTPError codes 401/403/4xx, so a
    5xx leaves `entries`/`default_entry` empty AND `last_checked` unset,
    and can_fetch() falls through to `if not self.last_checked: return
    False` — silently failing CLOSED on exactly the "can't be fetched at
    all" case. We take manual control of that decision instead of trusting
    stdlib's per-status-family default.

    Sends DEFAULT_USER_AGENT explicitly rather than requests' own default —
    confirmed live 2026-07-22: en.wikipedia.org/robots.txt 403s under
    requests' default UA (python-requests/...) but returns a real 200 under
    a desktop-browser UA. Using requests' default here would have made a
    genuinely-disallowed-looking response out of a site that's actually
    fine under a normal browser UA.

    Standing policy (bug_tracker: Privacy Guard scan fail-open robots.txt):
    disallow/ambiguous cases default to NOT scanning, not to proceeding. A
    404 is the one unambiguous "no restrictions" case — the Robots
    Exclusion Protocol (RFC 9309 §2.3.1.3) says a missing robots.txt means
    unrestricted access, so that alone still fails open. Every other
    non-200 (403, 401, 429, 5xx, ...) and any fetch exception (timeout,
    DNS failure, connection error, ...) is ambiguous — we can't tell
    whether the site actually disallows us or is just temporarily
    unreachable/misconfigured — so those fail CLOSED via disallow_all,
    which RobotFileParser.can_fetch() checks before allow_all or entries."""
    rp = RobotFileParser()
    try:
        resp = requests.get(
            f"{origin}/robots.txt", timeout=10, headers={"User-Agent": DEFAULT_USER_AGENT}
        )
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        elif resp.status_code == 404:
            log.info("robots.txt at %s returned 404 (no file) — failing open", origin)
            rp.allow_all = True
        else:
            log.warning(
                "robots.txt at %s returned %s — ambiguous, failing CLOSED (skip)",
                origin, resp.status_code,
            )
            rp.disallow_all = True
    except Exception as exc:
        log.warning(
            "robots.txt fetch failed for %s — ambiguous, failing CLOSED (skip): %s", origin, exc
        )
        rp.disallow_all = True
    return rp


def _robots_allowed(url: str, user_agent: str = DEFAULT_USER_AGENT) -> bool:
    """Checks the target's robots.txt before we ever navigate there.

    Fails OPEN only on the unambiguous case: a 404, meaning no robots.txt
    file exists at all. Fails CLOSED (returns False) on anything else that
    isn't a clean 200 — non-200/non-404 statuses and fetch exceptions are
    ambiguous, not a confirmed "no restrictions," so per standing policy we
    default to not scanning rather than proceeding (see _fetch_robots_parser
    for why this can't just delegate to RobotFileParser.read())."""
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    rp = _robots_cache.get(origin)
    if rp is None:
        rp = _fetch_robots_parser(origin)
        _robots_cache[origin] = rp
    return rp.can_fetch(user_agent, url)


def log_browser_failure(context: str, url: str, exc: Exception) -> None:
    """Every browser-job failure lands here instead of raising into a cron
    job or Telegram handler — same bug_tracker table/shape bot.py's `bug:`
    directive writes to (title/repo only; status/discovered_at default in
    the schema itself)."""
    log.error("%s failed for %s: %s", context, url, exc)
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO bug_tracker (title, description, repo) VALUES (?, ?, 'watson')",
            (f"jobs.browser: {context} failed for {url}", str(exc)),
        )
        conn.commit()
    except Exception as db_exc:
        log.error("Failed to log browser failure to bug_tracker: %s", db_exc)
    finally:
        # `with get_connection() as conn` only commits/rolls back on exit —
        # sqlite3.Connection's context manager never closes the connection,
        # so the previous version leaked one file handle per failure logged.
        if conn is not None:
            conn.close()


@asynccontextmanager
async def get_page(
    headless: bool = True,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    user_agent: str = DEFAULT_USER_AGENT,
    launch_args: list[str] | None = None,
):
    """Shared Chromium context manager for one-off browser jobs.

    headless must stay True on every production/automated path — non-headless
    is for local interactive debugging only and must never be wired into a
    cron job, Telegram handler, or dashboard route.

    launch_args is passed straight through to chromium.launch(args=...) --
    added for jobs/privacy/captcha_assist.py, which needs
    --remote-debugging-port/--remote-debugging-address to let a human mirror
    a *headless* page's live view over chrome://inspect. This does not
    change headless=True's default or the rule above; a caller opting into
    remote debugging is still responsible for binding it to a private
    interface only (see TAILSCALE_IP in jobs/dev/sandbox_session.py and
    jobs/privacy/captcha_assist.py for the convention), never 0.0.0.0."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, args=launch_args or [])
        page = await browser.new_page(user_agent=user_agent)
        page.set_default_timeout(timeout_ms)
        try:
            yield page
        finally:
            await browser.close()


async def goto_safe(page: Page, url: str, user_agent: str = DEFAULT_USER_AGENT, **goto_kwargs) -> bool:
    """Navigates `page` to `url`, respecting robots.txt, and never raises
    into the caller — a bad/blocked/timed-out fetch must not crash a cron
    job. Returns True on success, False on any failure (robots disallow,
    timeout, navigation error); failures are logged via
    log_browser_failure(), not raised."""
    if not _robots_allowed(url, user_agent):
        log.warning("robots.txt disallows fetching %s — skipping", url)
        return False
    try:
        await page.goto(url, **goto_kwargs)
        return True
    except Exception as exc:
        log_browser_failure("goto_safe", url, exc)
        return False
