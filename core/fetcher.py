"""
Fetch all active sources — up to PER_SOURCE_CAP items each — and return a
candidate pool for the scorer. Every fetched URL is archived to
research_archive (INSERT OR IGNORE) as a permanent record regardless of
whether it makes the final briefing.
"""
import calendar
import html as _html
import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import feedparser
import requests
import urllib3
import yaml
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config.settings import BASE_DIR, FRESHNESS_DAYS
from core.database import get_connection
from core.summarizer import summarize

log = logging.getLogger(__name__)

SOURCES_PATH   = BASE_DIR / "config" / "sources.yaml"
PER_SOURCE_CAP = 2
TIMEOUT        = 15
_BROWSER_UA    = (
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
_DATE_ONLY   = re.compile(r"\d{4}-\d{2}-\d{2}")


# ── Date parsing ───────────────────────────────────────────────────────────

def _struct_to_iso(t) -> str | None:
    """Convert feedparser time.struct_time (assumed UTC) to ISO 8601."""
    if not t:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _parse_date_str(s: str) -> str | None:
    """Parse a date string (ISO, RFC 2822, or bare date) to an ISO string."""
    if not s:
        return None
    s = s.strip()
    # ISO 8601 (handles Z and ±HH:MM)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        pass
    # RFC 2822 — common in RSS <pubDate> fields
    try:
        dt = parsedate_to_datetime(s)
        return dt.isoformat()
    except Exception:
        pass
    # Bare date embedded somewhere (e.g. "January 15, 2025 — 2025-01-15")
    m = _DATE_ONLY.search(s)
    if m:
        try:
            dt = datetime.fromisoformat(m.group()).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass
    return None


def _date_from_link_context(a_tag) -> str | None:
    """Walk up to 3 parent levels looking for a <time> element near this link."""
    node = a_tag.parent
    for _ in range(3):
        if node is None:
            break
        time_el = node.find("time")
        if time_el:
            raw = time_el.get("datetime") or time_el.get_text(strip=True)
            parsed = _parse_date_str(raw)
            if parsed:
                return parsed
        node = node.parent
    return None


def _extract_page_date_signals(soup) -> str | None:
    """
    Extract a page-level publication date from JSON-LD or og:article:published_time.
    Used as a weak fallback for scraped article links when no per-link <time> exists.
    """
    # JSON-LD — look for datePublished on any top-level object
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for obj in items:
                if not isinstance(obj, dict):
                    continue
                for field in ("datePublished", "dateModified"):
                    parsed = _parse_date_str(obj.get(field, ""))
                    if parsed:
                        return parsed
        except Exception:
            pass
    # og:article:published_time
    og = soup.find("meta", property="article:published_time")
    if og:
        parsed = _parse_date_str(og.get("content", ""))
        if parsed:
            return parsed
    return None


# ── Generic helpers ────────────────────────────────────────────────────────

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


def _url_seen(conn, url: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM research_archive WHERE url = ?", (url,)
    ).fetchone() is not None


def _is_stale(published_at: str, date_unknown: bool) -> bool:
    """Return True if the item's date is unknown or older than FRESHNESS_DAYS."""
    if date_unknown:
        return True
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400 > FRESHNESS_DAYS
    except (ValueError, TypeError, AttributeError):
        return True


def _archive(conn, *, title, url, summary, source_name, source_type, priority,
             published_at, date_unknown):
    conn.execute(
        """
        INSERT OR IGNORE INTO research_archive
            (title, url, summary, source_name, source_type, priority,
             published_at, date_unknown)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (title, url, summary, source_name, source_type, priority,
         published_at, 1 if date_unknown else 0),
    )


def _candidate(title, url, summary, source_name, source_type, priority,
               published_at=None, date_unknown=False, has_content=False) -> dict:
    return {
        "title":        title,
        "url":          url,
        "summary":      summary,
        "source_name":  source_name,
        "source_type":  source_type,
        "priority":     priority,
        "published_at": published_at,
        "date_unknown": date_unknown,
        "has_content":  has_content,
    }


# ── Fetch strategies ───────────────────────────────────────────────────────

def _fetch_rss(source: dict) -> tuple[list[dict], int]:
    """Returns (candidates, skipped_seen_count)."""
    name        = source["name"]
    source_type = source.get("source_type", "article")
    priority    = int(source.get("priority", 2))
    feed_url    = source["rss"]
    results     = []
    skipped     = 0

    log.info("RSS  %s", name)
    try:
        resp = requests.get(feed_url, headers=_RSS_HEADERS,
                            timeout=TIMEOUT, verify=False)
        resp.raise_for_status()
    except requests.RequestException as exc:
        fallback_url = source.get("url")
        if fallback_url:
            log.warning("  RSS failed (%s) — falling back to scrape", exc)
            return _fetch_scrape({**source, "scrape": True})
        log.warning("  RSS failed (%s) — skipping", exc)
        return [], 0

    clean  = _sanitize_xml(resp.content)
    parsed = feedparser.parse(clean, sanitize_html=False)

    if parsed.get("bozo") and not parsed.entries:
        fallback_url = source.get("url")
        if fallback_url:
            log.warning("  RSS bozo (%s) — falling back to scrape",
                        parsed.get("bozo_exception"))
            return _fetch_scrape({**source, "scrape": True})
        log.warning("  RSS bozo, no fallback — skipping")
        return [], 0

    fetch_time = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        for entry in parsed.entries:
            if len(results) >= PER_SOURCE_CAP:
                break
            url = entry.get("link", "").strip()
            if not url:
                continue
            if _url_seen(conn, url):
                skipped += 1
                continue
            title = _strip_html(entry.get("title") or "").strip()
            if not title or len(title.split()) <= 3:
                continue

            content = _strip_html(
                entry.get("summary", "") or entry.get("description", "")
            )
            summary      = summarize(title, content, name)
            has_content  = bool(content)

            # Date: prefer feedparser's pre-parsed struct (UTC), fall back to strings
            pub_struct   = entry.get("published_parsed") or entry.get("updated_parsed")
            published_at = _struct_to_iso(pub_struct)
            if not published_at:
                raw_date     = entry.get("published") or entry.get("updated")
                published_at = _parse_date_str(raw_date) if raw_date else None
            date_unknown = published_at is None
            if date_unknown:
                published_at = fetch_time

            _archive(conn, title=title, url=url, summary=summary,
                     source_name=name, source_type=source_type, priority=priority,
                     published_at=published_at, date_unknown=date_unknown)
            if _is_stale(published_at, date_unknown):
                log.debug("  Date reject: %s", title[:60])
                continue
            results.append(_candidate(
                title, url, summary, name, source_type, priority,
                published_at=published_at, date_unknown=date_unknown,
                has_content=has_content,
            ))
            log.info("  + %s", title[:90])

    return results, skipped


def _fetch_scrape(source: dict) -> tuple[list[dict], int]:
    """Returns (candidates, skipped_seen_count)."""
    name        = source["name"]
    source_type = source.get("source_type", "article")
    priority    = int(source.get("priority", 2))
    url         = source["url"]
    results     = []
    skipped     = 0

    log.info("Scrape %s", name)
    try:
        resp = requests.get(url, headers=_SCRAPE_HEADERS,
                            timeout=TIMEOUT, verify=False)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("  Scrape failed (%s) — skipping", exc)
        return [], 0

    soup      = BeautifulSoup(resp.text, "html.parser")
    base      = resp.url
    seen      = set()
    fetch_time = datetime.now(timezone.utc).isoformat()

    # Page-level date signal — weak fallback if no per-link <time> found
    page_date = _extract_page_date_signals(soup)

    with get_connection() as conn:
        for a in soup.find_all("a", href=True):
            if len(results) >= PER_SOURCE_CAP:
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

            if article_url in seen:
                continue
            if _url_seen(conn, article_url):
                skipped += 1
                seen.add(article_url)
                continue
            seen.add(article_url)

            title = a.get_text(strip=True)
            if not title or not _is_article(title, article_url, name):
                continue

            summary = summarize(title, "", name)

            # Per-link date: look for <time> near this <a>; fall back to page date
            link_date    = _date_from_link_context(a)
            published_at = link_date or page_date
            date_unknown = published_at is None
            if date_unknown:
                published_at = fetch_time

            _archive(conn, title=title, url=article_url, summary=summary,
                     source_name=name, source_type=source_type, priority=priority,
                     published_at=published_at, date_unknown=date_unknown)
            if _is_stale(published_at, date_unknown):
                log.debug("  Date reject: %s", title[:60])
                continue
            results.append(_candidate(
                title, article_url, summary, name, source_type, priority,
                published_at=published_at, date_unknown=date_unknown,
                has_content=False,
            ))
            log.info("  + %s", title[:90])

    return results, skipped


# ── Source loader ──────────────────────────────────────────────────────────

def _load_sources() -> list[dict]:
    with open(SOURCES_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    sources = []
    for feed in data.get("briefing_feeds", []) or []:
        sources.append({**feed, "source_type": "article", "priority": 2})
    for category in ("authors", "organizations", "journals"):
        for entry in data.get(category, []) or []:
            entry.setdefault("source_type", "article")
            sources.append(entry)
    return sources


# ── Public API ─────────────────────────────────────────────────────────────

def fetch_all() -> dict:
    """
    Fetch all active sources (up to PER_SOURCE_CAP items each).
    Archives every new URL to research_archive immediately.

    Returns:
        pool             — candidate list for the scorer
        skipped_seen     — URLs already in research_archive (skipped)
        sources_active   — sources actually processed
        sources_inactive — sources skipped due to active: false
    """
    sources = _load_sources()
    if not sources:
        log.info("No sources configured in sources.yaml")
        return {"pool": [], "skipped_seen": 0, "sources_active": 0, "sources_inactive": 0}

    pool             = []
    total_skipped    = 0
    active_count     = 0
    inactive_count   = 0

    for source in sources:
        name = source.get("name", "unknown")
        if source.get("active") is False:
            inactive_count += 1
            log.debug("Skip inactive: %s", name)
            continue
        active_count += 1
        try:
            if source.get("rss"):
                candidates, seen = _fetch_rss(source)
            elif source.get("scrape") and source.get("url"):
                candidates, seen = _fetch_scrape(source)
            else:
                log.warning("No rss or scrape URL for '%s' — skipping", name)
                continue
        except Exception as exc:
            log.error("Unexpected error for '%s': %s", name, exc)
            continue
        pool.extend(candidates)
        total_skipped += seen

    log.info(
        "Fetch complete — %d candidates | %d already archived (skipped)",
        len(pool), total_skipped,
    )
    return {
        "pool":             pool,
        "skipped_seen":     total_skipped,
        "sources_active":   active_count,
        "sources_inactive": inactive_count,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    from core.database import init_db
    init_db()
    result = fetch_all()
    pool   = result["pool"]
    print(f"\nPool: {len(pool)} candidate(s)  ({result['skipped_seen']} already archived)")
    for item in pool:
        unk = " [date?]" if item["date_unknown"] else ""
        print(f"  [{item['priority']}] {item['source_name']} — {item['title'][:65]}{unk}")
