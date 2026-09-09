"""jobs/beachhouse/flash_scraper.py — discover and store Flash-tab hotel
deals from Travelzoo, for a genuinely spur-of-the-moment single night away.

Travelzoo's own search isn't targeted (site:travelzoo.com + a nearby town
name via Serper/Google, same discovery pattern as scraper.py), but its
individual hotel-deal pages ARE allowed by robots.txt and, unlike VRBO/
Airbnb, carry real static price/discount data — confirmed live 2026-09-09:
a deal page's <title>/og:title is reliably "$<price>—<headline>", and
schema.org microdata (itemprop="addressLocality"/"addressRegion") gives a
clean city/state. So price_per_night here is scraped, not manual (contrast
jobs/beachhouse/schema.py's bh_listings.price_low/price_high).

Groupon's getaway deal pages looked equally promising (robots.txt allows
them) but return HTTP 403 to a plain fetch — bot-blocked. Not pursued
further; Flash is Travelzoo-only for now.

Run standalone:
    PYTHONPATH=/home/billyomes/watson python3 -m jobs.beachhouse.flash_scraper
Safe to re-run — upserts by (source, source_id).
"""
import html
import logging
import re
import time

import requests

from jobs.beachhouse import FLASH_REGIONS
from jobs.beachhouse.schema import create_tables, get_connection
from jobs.research.web_search import search
from jobs.retreats.discover import robots_allowed

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT = 20
_POLITE_DELAY = 1.5

_TZ_ID_RE = re.compile(r"travelzoo\.com/(?:\w+/)?hotel-booking/hotel/(\d+)/")
_TITLE_RE = re.compile(r"<title>([^<]*)</title>")
_HEADLINE_RE = re.compile(r"^\$(\d+)\W*[–—-]\W*(.+)$")  # "$99—Downtown DC hotel"
_LOCALITY_RE = re.compile(r'itemprop="addressLocality">([^<]*)')
_REGION_RE = re.compile(r'itemprop="addressRegion">([^<]*)')
_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]*)"')
_DESC_RE = re.compile(r'<meta name="description" content="([^"]*)"')
_DISCOUNT_RE = re.compile(r"(\d{1,3}%(?:\s*[–-]\s*\d{1,3}%)?)\s*off", re.I)


def _source_id(url: str) -> str | None:
    m = _TZ_ID_RE.search(url)
    return m.group(1) if m else None


def discover_candidate_urls() -> list[tuple[str, float, str]]:
    """One Serper query per FLASH_REGIONS entry. Returns a deduped list of
    (town, drive_hours, url) tuples."""
    seen: set[str] = set()
    candidates: list[tuple[str, float, str]] = []
    for region in FLASH_REGIONS:
        query = f"site:travelzoo.com hotel-booking {region['query']} deal"
        for r in search(query, max_results=10):
            url = r.get("url", "")
            sid = _source_id(url)
            if not sid or sid in seen:
                continue
            seen.add(sid)
            candidates.append((region["town"], region["drive_hours"], url))
    return candidates


def _fetch(url: str) -> str | None:
    if not robots_allowed(url):
        log.warning("robots.txt disallows %s -- skipping", url)
        return None
    for attempt in range(2):
        try:
            resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
            if resp.status_code == 429 and attempt == 0:
                log.info("429 for %s -- backing off before one retry", url)
                time.sleep(10)
                continue
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            log.warning("fetch failed for %s: %s", url, exc)
            return None
    return None


def _first(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def parse_deal(page_html: str) -> dict | None:
    """Returns None if the page doesn't match Travelzoo's "$<price>—
    <headline>" title convention -- that's the one field everything else
    depends on, and its absence usually means this wasn't actually a priced
    hotel deal page (an index/category page slipped through discovery)."""
    title = _first(_TITLE_RE, page_html)
    if not title:
        return None
    title = html.unescape(title.split(" | ")[0].strip())
    m = _HEADLINE_RE.match(title)
    if not m:
        return None
    price = int(m.group(1))
    name = m.group(2).strip()

    desc = html.unescape(_first(_DESC_RE, page_html) or "")
    haystack = f"{desc} {page_html[:150000]}"

    return {
        "name": name,
        "price_per_night": float(price),
        "discount_text": _first(_DISCOUNT_RE, haystack),
        "city": _first(_LOCALITY_RE, page_html),
        "state": _first(_REGION_RE, page_html),
        "description": desc,
        "primary_image_url": _first(_IMAGE_RE, page_html),
    }


def _upsert(source_id: str, url: str, town: str, drive_hours: float, fields: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO bh_deals (
                source, source_id, source_url, name, city, state, town,
                drive_hours, price_per_night, discount_text, description,
                primary_image_url, discovered_at, last_seen_at
            ) VALUES ('travelzoo', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(source, source_id) DO UPDATE SET
                name=excluded.name, city=excluded.city, state=excluded.state,
                town=excluded.town, drive_hours=excluded.drive_hours,
                price_per_night=excluded.price_per_night,
                discount_text=excluded.discount_text,
                description=excluded.description,
                primary_image_url=excluded.primary_image_url,
                last_seen_at=datetime('now')
            """,
            (
                source_id, url, fields["name"], fields["city"] or town, fields["state"],
                town, drive_hours, fields["price_per_night"], fields["discount_text"],
                fields["description"], fields["primary_image_url"],
            ),
        )


def run() -> dict:
    create_tables()
    candidates = discover_candidate_urls()
    log.info("[flash] discovered %d unique candidate URLs", len(candidates))

    kept, unparseable, failed = 0, 0, 0
    for i, (town, drive_hours, url) in enumerate(candidates, 1):
        log.info("[flash][%d/%d] %s", i, len(candidates), url)
        page_html = _fetch(url)
        time.sleep(_POLITE_DELAY)
        if not page_html:
            failed += 1
            continue
        fields = parse_deal(page_html)
        if not fields:
            unparseable += 1
            continue
        sid = _source_id(url)
        _upsert(sid, url, town, drive_hours, fields)
        kept += 1

    log.info("[flash] done: %d kept, %d unparseable, %d failed", kept, unparseable, failed)
    return {"kept": kept, "unparseable": unparseable, "failed": failed}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
