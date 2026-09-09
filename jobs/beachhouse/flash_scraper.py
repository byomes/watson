"""jobs/beachhouse/flash_scraper.py — discover and store Flash-tab hotel
deals from Travelzoo, for a genuinely spur-of-the-moment single night away.

Two complementary discovery paths, both hitting real static price/discount
data (confirmed live 2026-09-09) -- unlike VRBO/Airbnb, so price_per_night
here is scraped, not manual (contrast schema.py's bh_listings.price_low/
price_high):

1. SITEMAP (primary): Travelzoo's main sitemap (sitemap-https.xml) lists
   individually-priced deal-article pages under /hotels/<bucket>/<name-with-
   price-and-discount>-<id>/ -- e.g. "/hotels/new-york/-232-Catskills-resort-
   w-indoor-water-park-tickets-30-off-3278807/". Filtered to the
   washington-dc and new-york buckets (the ones that actually cover
   FLASH_REGIONS' area -- Travelzoo's own bucketing is coarse, "washington-
   dc" covers Philly/Baltimore/Pittsburgh/Ocean City too). No Google search
   needed, no cost, and complete rather than subject to ranking noise. These
   pages carry NO address microdata, so city comes from matching a
   FLASH_REGIONS town name in the page text, falling back to a bucket-level
   label.

2. SEARCH (secondary): site:travelzoo.com + a FLASH_REGIONS town name via
   Serper/Google, same pattern as scraper.py. Finds Travelzoo's other deal
   page type, /hotel-booking/hotel/<id>/ (an evergreen member-rate page,
   not a dated flash deal) -- these DO carry schema.org address microdata.
   Lower yield than the sitemap (Google's ranking is noisy here), kept
   anyway since it's a real complementary source.

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

_SITEMAP_URL = "https://www.travelzoo.com/sitemap-https.xml"
_SITEMAP_BUCKETS = {
    # bucket slug -> fallback (town label, drive_hours) when no FLASH_REGIONS
    # town name can be matched in the deal's own text
    "washington-dc": ("Washington DC area", 1.5),
    "new-york": ("New York City area", 2.5),
}

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_SITEMAP_DEAL_RE = re.compile(
    r"^https://www\.travelzoo\.com/hotels/(" + "|".join(_SITEMAP_BUCKETS) + r")/[^/]+-(\d+)/$"
)
_HOTEL_BOOKING_ID_RE = re.compile(r"travelzoo\.com/(?:\w+/)?hotel-booking/hotel/(\d+)/")

_TITLE_RE = re.compile(r"<title>([^<]*)</title>")
_HEADLINE_RE = re.compile(r"^\$(\d+)\W*[–—-]\W*(.+)$")  # "$99—Downtown DC hotel"
_LOCALITY_RE = re.compile(r'itemprop="addressLocality">([^<]*)')
_REGION_RE = re.compile(r'itemprop="addressRegion">([^<]*)')
_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]*)"')
_DESC_RE = re.compile(r'<meta name="description" content="([^"]*)"')
_DISCOUNT_RE = re.compile(r"(\d{1,3}%(?:\s*[–-]\s*\d{1,3}%)?)\s*off", re.I)

# States plausibly within FLASH_REGIONS' ~2.5hr radius of Wilmington, DE.
# Guards against the SEARCH path's own failure mode: Google's ranking for
# "site:travelzoo.com hotel-booking <nearby town> hotel deal" can surface a
# same-site page that has nothing to do with that town (confirmed live
# 2026-09-09: an "Annapolis" search returned a Jekyll Island, GA hotel --
# ~11.5hrs away, tagged with Annapolis's ~2h drive_hours since the code
# trusted the search region blindly). A deal whose OWN extracted state
# isn't in this set gets dropped rather than mislabeled with a nearby
# drive time it doesn't deserve.
_PLAUSIBLE_STATES = {"DE", "PA", "NJ", "MD", "NY", "DC", "VA", "CT"}


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


def _sitemap_candidates() -> list[tuple[str, float, str, str]]:
    """Returns (fallback_town, fallback_drive_hours, source_id, url) tuples
    from the washington-dc/new-york sitemap buckets."""
    xml = _fetch(_SITEMAP_URL)
    if not xml:
        return []
    out = []
    for loc in _LOC_RE.findall(xml):
        m = _SITEMAP_DEAL_RE.match(loc)
        if not m:
            continue
        bucket, deal_id = m.group(1), m.group(2)
        town, drive_hours = _SITEMAP_BUCKETS[bucket]
        out.append((town, drive_hours, f"sm{deal_id}", loc))
    return out


def _search_candidates() -> list[tuple[str, float, str, str]]:
    """Returns (town, drive_hours, source_id, url) tuples from Serper
    site-scoped search per FLASH_REGIONS entry -- finds /hotel-booking/
    pages, a different Travelzoo content type than the sitemap covers."""
    seen: set[str] = set()
    out = []
    for region in FLASH_REGIONS:
        query = f"site:travelzoo.com hotel-booking {region['query']} deal"
        for r in search(query, max_results=10):
            url = r.get("url", "")
            m = _HOTEL_BOOKING_ID_RE.search(url)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            out.append((region["town"], region["drive_hours"], f"hb{m.group(1)}", url))
    return out


def discover_candidate_urls() -> list[tuple[str, float, str, str]]:
    """Combines both discovery paths. Returns deduped
    (fallback_town, fallback_drive_hours, source_id, url) tuples."""
    seen_ids: set[str] = set()
    candidates: list[tuple[str, float, str, str]] = []
    for town, drive_hours, sid, url in _sitemap_candidates() + _search_candidates():
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        candidates.append((town, drive_hours, sid, url))
    return candidates


def _detect_town(text: str) -> tuple[str, float] | None:
    """Best-effort: does any FLASH_REGIONS town name appear in this deal's
    own name/description text? Used when the page has no address microdata
    (the sitemap deal-article pages don't)."""
    for region in FLASH_REGIONS:
        if region["town"].lower() in text.lower():
            return region["town"], region["drive_hours"]
    return None


def parse_deal(page_html: str) -> dict | None:
    """Returns None if the page doesn't match Travelzoo's "$<price>—
    <headline>" title convention -- that's the one field everything else
    depends on, and its absence usually means this wasn't actually a priced
    hotel deal page (a stale/delisted URL still returning 200, or an index/
    category page that slipped through discovery)."""
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
    haystack = f"{name} {desc} {page_html[:150000]}"

    return {
        "name": name,
        "price_per_night": float(price),
        "discount_text": _first(_DISCOUNT_RE, haystack),
        "city": _first(_LOCALITY_RE, page_html),
        "state": _first(_REGION_RE, page_html),
        "description": desc,
        "primary_image_url": _first(_IMAGE_RE, page_html),
        "town_hint": _detect_town(f"{name} {desc}"),
    }


def _upsert(source_id: str, url: str, fallback_town: str, fallback_drive_hours: float, fields: dict) -> None:
    town, drive_hours = fields["town_hint"] or (fallback_town, fallback_drive_hours)
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

    kept, unparseable, out_of_area, failed = 0, 0, 0, 0
    for i, (town, drive_hours, sid, url) in enumerate(candidates, 1):
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
        if fields["state"] and fields["state"].strip().upper() not in _PLAUSIBLE_STATES:
            log.info("  out of area (state=%s) -- dropping", fields["state"])
            out_of_area += 1
            continue
        _upsert(sid, url, town, drive_hours, fields)
        kept += 1

    log.info(
        "[flash] done: %d kept, %d unparseable, %d out of area, %d failed",
        kept, unparseable, out_of_area, failed,
    )
    return {"kept": kept, "unparseable": unparseable, "out_of_area": out_of_area, "failed": failed}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
