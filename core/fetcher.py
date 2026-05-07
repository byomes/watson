import logging
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

from config.settings import BASE_DIR
from core.database import get_connection

log = logging.getLogger(__name__)

SOURCES_PATH = BASE_DIR / "config" / "sources.yaml"
SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
SCRAPE_TIMEOUT = 15


def _load_sources():
    with open(SOURCES_PATH) as f:
        data = yaml.safe_load(f) or {}
    sources = []
    for category in ("authors", "organizations", "journals"):
        for entry in data.get(category, []) or []:
            entry.setdefault("source_type", "article")
            sources.append(entry)
    return sources


def _url_exists(conn, url):
    row = conn.execute("SELECT 1 FROM items WHERE url = ?", (url,)).fetchone()
    return row is not None


def _insert_item(conn, source_name, source_type, title, url, summary, published_date):
    conn.execute(
        """
        INSERT INTO items (source_name, source_type, title, url, summary, published_date, status)
        VALUES (?, ?, ?, ?, ?, ?, 'new')
        """,
        (source_name, source_type, title, url, summary, published_date),
    )


def _fetch_rss(source):
    name = source["name"]
    source_type = source["source_type"]
    feed_url = source["rss"]
    new_count = 0

    log.info("Fetching RSS: %s (%s)", name, feed_url)
    # Pass browser headers so feeds behind CDN checks don't block us
    parsed = feedparser.parse(
        feed_url,
        request_headers=SCRAPE_HEADERS,
        sanitize_html=False,
    )

    if parsed.get("bozo") and not parsed.entries:
        bozo_exc = parsed.get("bozo_exception")
        # If the feed is truly empty/unparseable, fall back to scraping the URL
        url_field = source.get("url")
        if url_field:
            log.warning("RSS bozo for '%s' (%s) — falling back to scrape", name, bozo_exc)
            return _fetch_scrape({**source, "scrape": True})
        raise ValueError(f"Feed parse error for {name}: {bozo_exc}")

    with get_connection() as conn:
        for entry in parsed.entries:
            url = entry.get("link", "").strip()
            if not url:
                continue

            if _url_exists(conn, url):
                log.debug("  skip (exists): %s", url)
                continue

            title = entry.get("title", "Untitled").strip()
            summary = entry.get("summary", "") or entry.get("description", "")
            # feedparser gives time_struct tuples; convert to ISO string
            pub_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            published_date = None
            if pub_struct:
                published_date = datetime(*pub_struct[:6], tzinfo=timezone.utc).isoformat()

            _insert_item(conn, name, source_type, title, url, summary, published_date)
            log.info("  + %s", title)
            new_count += 1

    return new_count


def _fetch_scrape(source):
    name = source["name"]
    source_type = source["source_type"]
    url = source["url"]
    new_count = 0

    log.info("Scraping: %s (%s)", name, url)
    resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=SCRAPE_TIMEOUT)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    base = resp.url  # resolved URL after any redirects

    seen_urls = set()
    with get_connection() as conn:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue

            # Resolve relative URLs
            if href.startswith("http"):
                article_url = href
            elif href.startswith("//"):
                article_url = "https:" + href
            else:
                from urllib.parse import urljoin
                article_url = urljoin(base, href)

            if article_url in seen_urls:
                continue
            seen_urls.add(article_url)

            if _url_exists(conn, article_url):
                log.debug("  skip (exists): %s", article_url)
                continue

            title = a.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            _insert_item(conn, name, source_type, title, article_url, None, None)
            log.info("  + %s", title)
            new_count += 1

    return new_count


def fetch_all():
    sources = _load_sources()
    if not sources:
        log.info("No sources configured in sources.yaml")
        return 0

    total_new = 0

    for source in sources:
        name = source.get("name", "unknown")
        if source.get("active") is False:
            log.info("Skipping inactive source: %s", name)
            continue
        try:
            if source.get("rss"):
                total_new += _fetch_rss(source)
            elif source.get("scrape") and source.get("url"):
                total_new += _fetch_scrape(source)
            else:
                log.warning("Source '%s' has no rss or scrape URL — skipping", name)
        except requests.RequestException as e:
            log.error("Network error for '%s': %s", name, e)
        except Exception as e:
            log.error("Failed to fetch '%s': %s", name, e)

    log.info("Fetch complete — %d new item(s) added", total_new)
    return total_new


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    count = fetch_all()
    print(f"\nDone. {count} new item(s) added.")
