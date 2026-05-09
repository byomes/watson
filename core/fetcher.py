"""
Fetch RSS feeds and scrape URLs → summarize → score → store to briefing_items.
This is the single source-of-truth for populating the briefing queue.
"""
import html as _html
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import feedparser
import requests
import urllib3
import yaml
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config.settings import BASE_DIR
from core.database import get_connection
from core.summarizer import summarize

log = logging.getLogger(__name__)

SOURCES_PATH = BASE_DIR / "config" / "sources.yaml"
MAX_ITEMS    = 20
TIMEOUT      = 15
_BROWSER_UA  = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_RSS_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}
_SCRAPE_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,*/*",
}

_HTML_TAGS   = re.compile(r"<[^>]+>")
_DATE_IN_URL = re.compile(r"/\d{4}/\d{2}|/\d{4}/")
_XML_INVALID = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

_PRIORITY_BASE = {1: 100, 2: 60, 3: 30}
_TYPE_BONUS    = {"podcast": 20, "publication": 15, "journal": 10, "article": 5}


# ── Helpers ────────────────────────────────────────────────────────────────

def _strip_html(text):
    if not text:
        return ""
    stripped  = _HTML_TAGS.sub(" ", text)
    unescaped = _html.unescape(stripped)
    return " ".join(unescaped.split())


def _is_article(title, url, source_name):
    """Return False for nav links, menu items, and section headers."""
    if title.strip() == source_name.strip():
        return False
    if len(title.split()) <= 3:
        return False
    if _DATE_IN_URL.search(url):
        return True
    path     = url.split("?")[0].split("#")[0].rstrip("/")
    last_seg = path.rsplit("/", 1)[-1] if "/" in path else path
    return len(last_seg.split("-")) >= 3


def _sanitize_xml(raw: bytes) -> str:
    return _XML_INVALID.sub("", raw.decode("utf-8", errors="replace"))


def _score(priority: int, source_type: str) -> int:
    return _PRIORITY_BASE.get(priority, 30) + _TYPE_BONUS.get(source_type, 5)


def _url_exists(conn, url: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM briefing_items WHERE url = ?", (url,)
    ).fetchone() is not None


def _insert(conn, *, title, url, summary, source_name, source_type, priority):
    conn.execute(
        """
        INSERT INTO briefing_items
            (title, url, summary, source_name, source_type, priority, score)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (title, url, summary, source_name, source_type, priority,
         _score(priority, source_type)),
    )


# ── Source loaders ─────────────────────────────────────────────────────────

def _load_sources():
    with open(SOURCES_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    sources = []
    # briefing_feeds have no priority/source_type fields — use sensible defaults
    for feed in data.get("briefing_feeds", []) or []:
        sources.append({**feed, "source_type": "article", "priority": 2})
    for category in ("authors", "organizations", "journals"):
        for entry in data.get(category, []) or []:
            entry.setdefault("source_type", "article")
            sources.append(entry)
    return sources


# ── Fetch strategies ───────────────────────────────────────────────────────

def _fetch_rss(source: dict, budget: int) -> int:
    name        = source["name"]
    source_type = source.get("source_type", "article")
    priority    = int(source.get("priority", 2))
    feed_url    = source["rss"]
    added       = 0

    log.info("RSS  %s", name)
    try:
        resp = requests.get(feed_url, headers=_RSS_HEADERS,
                            timeout=TIMEOUT, verify=False)
        resp.raise_for_status()
    except requests.RequestException as exc:
        # Graceful fallback: if the source also has a URL, try scraping it
        fallback_url = source.get("url")
        if fallback_url:
            log.warning("  RSS failed (%s) — falling back to scrape", exc)
            return _fetch_scrape({**source, "scrape": True}, budget)
        log.warning("  RSS failed (%s) — skipping", exc)
        return 0

    clean = _sanitize_xml(resp.content)
    parsed = feedparser.parse(clean, sanitize_html=False)

    if parsed.get("bozo") and not parsed.entries:
        fallback_url = source.get("url")
        if fallback_url:
            log.warning("  RSS bozo (%s) — falling back to scrape",
                        parsed.get("bozo_exception"))
            return _fetch_scrape({**source, "scrape": True}, budget)
        log.warning("  RSS bozo, no fallback — skipping")
        return 0

    with get_connection() as conn:
        for entry in parsed.entries:
            if added >= budget:
                break
            url = entry.get("link", "").strip()
            if not url or _url_exists(conn, url):
                continue
            title = _strip_html(entry.get("title") or "").strip()
            if not title or len(title.split()) <= 3:
                continue
            content = _strip_html(
                entry.get("summary", "") or entry.get("description", "")
            )
            summary = summarize(title, content, name)
            _insert(conn, title=title, url=url, summary=summary,
                    source_name=name, source_type=source_type, priority=priority)
            log.info("  + %s", title[:90])
            added += 1

    return added


def _fetch_scrape(source: dict, budget: int) -> int:
    name        = source["name"]
    source_type = source.get("source_type", "article")
    priority    = int(source.get("priority", 2))
    url         = source["url"]
    added       = 0

    log.info("Scrape %s", name)
    try:
        resp = requests.get(url, headers=_SCRAPE_HEADERS,
                            timeout=TIMEOUT, verify=False)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("  Scrape failed (%s) — skipping", exc)
        return 0

    soup = BeautifulSoup(resp.text, "html.parser")
    base = resp.url
    seen = set()

    with get_connection() as conn:
        for a in soup.find_all("a", href=True):
            if added >= budget:
                break
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            if href.startswith("http"):
                article_url = href
            elif href.startswith("//"):
                article_url = "https:" + href
            else:
                article_url = urljoin(base, href)

            if article_url in seen or _url_exists(conn, article_url):
                continue
            seen.add(article_url)

            title = a.get_text(strip=True)
            if not title or not _is_article(title, article_url, name):
                continue

            summary = summarize(title, "", name)
            _insert(conn, title=title, url=article_url, summary=summary,
                    source_name=name, source_type=source_type, priority=priority)
            log.info("  + %s", title[:90])
            added += 1

    return added


# ── Public API ─────────────────────────────────────────────────────────────

def fetch_all() -> int:
    """Fetch all active sources, store new items to briefing_items. Returns count added."""
    sources = _load_sources()
    if not sources:
        log.info("No sources configured in sources.yaml")
        return 0

    total  = 0
    budget = MAX_ITEMS

    for source in sources:
        if budget <= 0:
            log.info("Item cap (%d) reached — stopping fetch", MAX_ITEMS)
            break
        name = source.get("name", "unknown")
        if source.get("active") is False:
            log.debug("Skip inactive: %s", name)
            continue
        try:
            if source.get("rss"):
                added = _fetch_rss(source, budget)
            elif source.get("scrape") and source.get("url"):
                added = _fetch_scrape(source, budget)
            else:
                log.warning("No rss or scrape URL for '%s' — skipping", name)
                continue
        except Exception as exc:
            log.error("Unexpected error for '%s': %s", name, exc)
            continue
        total  += added
        budget -= added

    log.info("Fetch complete — %d new item(s) added to briefing_items", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    from core.database import init_db
    init_db()
    count = fetch_all()
    print(f"\nDone. {count} new item(s) added.")
