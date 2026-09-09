"""jobs/beachhouse/scraper.py — discover, fetch, and store candidate
listings from VRBO and Airbnb, for every category in jobs.beachhouse.CATEGORIES.

Neither platform offers a search API, and both disallow scraping their own
search UI in robots.txt (VRBO: `Disallow: /search?`; Airbnb: `Disallow:
/s/*/*`) — but neither disallows individual listing DETAIL pages, and
Google indexes those. So discovery goes through jobs.research.web_search
(Serper/Google) with site-scoped queries per category region, and only the
resulting listing URLs get fetched directly — robots.txt-checked per URL
regardless (jobs.retreats.discover.robots_allowed), same standing policy
as jobs/retreats and jobs/servantcare.

Every candidate with a parseable bedroom count gets stored, with its real
bedrooms/bathrooms/amenity values from the actual page content — the
search snippet alone is never trusted for those values, since Google's
title/snippet text is user-facing marketing copy, not verified structured
data. There is no hard accept/reject filter here on purpose: Bill/Melanie
set their own real thresholds from the wtsn.me search UI at query time,
against whatever this module has stored. A listing found under more than
one category's queries (e.g. a secluded cabin with a hot tub matching both
Mountain and Romance) gets one row per category — see schema.py.

Run standalone (all categories):
    PYTHONPATH=/home/billyomes/watson python3 -m jobs.beachhouse.scraper
Or one category at a time:
    PYTHONPATH=/home/billyomes/watson python3 -m jobs.beachhouse.scraper mountain
Safe to re-run — upserts by (source, source_id, category).
"""
import html
import logging
import re
import sys
import time

import requests

from jobs.beachhouse import AMENITY_FIELDS, CATEGORIES, SOURCES
from jobs.beachhouse.schema import create_tables, get_connection
from jobs.research.web_search import search
from jobs.retreats.discover import robots_allowed

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT = 20
_POLITE_DELAY = 1.5  # seconds between detail-page fetches, any source

# VRBO listing URLs are vrbo.com/<id> where id starts with a digit (e.g.
# "3659882", "9892588ha") -- anchored so we don't also match VRBO's own
# SEO category/region pages, e.g. vrbo.com/vacation-rentals/pool/usa/... ,
# whose first path segment ("vacation-rentals") would otherwise satisfy a
# bare \w+ capture.
_VRBO_ID_RE = re.compile(r"vrbo\.com/(\d[\w-]*)(?:[/?]|$)")
_AIRBNB_ID_RE = re.compile(r"airbnb\.com/rooms/(\d+)")

_BEDROOM_RE = re.compile(r"(\d+)\s*[- ]?bedrooms?\b", re.I)
_BATHROOM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?bathrooms?\b", re.I)
_SLEEPS_RE = re.compile(r"\bsleeps?\s+(\d+)\b", re.I)
_TITLE_RE = re.compile(r"<title>([^<]*)</title>")
_DESC_RE = re.compile(r'<meta name="description" content="([^"]*)"')
_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]*)"')
_CITY_RE = re.compile(r"\bin ([A-Z][A-Za-z .'-]{2,40}?)(?:[!,]| -)")

# One regex per AMENITY_FIELDS entry -- order must match.
_AMENITY_RES = {
    "has_pool": re.compile(r"\b(private pool|swimming pool|outdoor pool|heated pool)\b", re.I),
    "oceanfront": re.compile(r"\b(oceanfront|beachfront|direct beach access|ocean front)\b", re.I),
    "hot_tub": re.compile(r"\b(hot tub|jacuzzi|whirlpool spa)\b", re.I),
    "fireplace": re.compile(r"\b(fireplace|wood[- ]burning stove)\b", re.I),
    "mountain_view": re.compile(r"\b(mountain view|ski[- ]in|ski[- ]out|slope[- ]side)\b", re.I),
    "secluded": re.compile(r"\b(secluded|private retreat|tucked away|off the grid|remote getaway)\b", re.I),
}


def _source_id(source: str, url: str) -> str | None:
    m = (_VRBO_ID_RE if source == "vrbo" else _AIRBNB_ID_RE).search(url)
    return m.group(1) if m else None


def discover_candidate_urls(category: str) -> list[tuple[str, str, float, str, str]]:
    """Runs one Serper query per (state region, source) for the given
    category. Returns a deduped list of (state, town, drive_hours, source,
    url) tuples -- state/town/drive_hours all come from which region query
    surfaced the URL (see __init__.py's CATEGORIES), not parsed from the
    page, since that's known and reliable up front."""
    cfg = CATEGORIES[category]
    seen: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str, float, str, str]] = []
    for state, regions in cfg["regions"].items():
        for region in regions:
            for src in SOURCES:
                query = f"site:{src}.com {region['query']} {cfg['query_terms']}"
                for r in search(query, max_results=10):
                    url = r.get("url", "")
                    sid = _source_id(src, url)
                    if not sid or (src, sid) in seen:
                        continue
                    seen.add((src, sid))
                    candidates.append((state, region["town"], region["drive_hours"], src, url))
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


def parse_listing(page_html: str) -> dict | None:
    """Extracts fields from a fetched detail page's title/meta/body text.
    Returns None if bedroom count (the one field every hard filter depends
    on) can't be found at all -- everything else degrades gracefully to
    None/False rather than dropping the candidate."""
    title = _first(_TITLE_RE, page_html)
    if not title:
        return None
    title = html.unescape(title)
    desc = html.unescape(_first(_DESC_RE, page_html) or "")
    # bounded scan -- these platforms ship 500KB-1.5MB pages; the summary
    # facts (bed/bath/sleeps/amenities) always appear well within the first
    # slice of rendered body text in practice (confirmed live against both
    # vrbo.com and airbnb.com detail pages, 2026-09-09)
    haystack = f"{title} {desc} {page_html[:300000]}"

    bedrooms = _first(_BEDROOM_RE, haystack)
    if bedrooms is None:
        return None
    bathrooms = _first(_BATHROOM_RE, haystack)
    sleeps = _first(_SLEEPS_RE, haystack)
    city_raw = _first(_CITY_RE, title)
    city = city_raw.split(",")[0].strip() if city_raw else None

    fields = {
        "name": title.split(" - ")[0].strip() or title,
        "description": desc,
        "city": city,
        "bedrooms": int(bedrooms),
        "bathrooms": float(bathrooms) if bathrooms else None,
        "max_sleeps": int(sleeps) if sleeps else None,
        "primary_image_url": _first(_IMAGE_RE, page_html),
    }
    for amenity in AMENITY_FIELDS:
        fields[amenity] = bool(_AMENITY_RES[amenity].search(haystack))
    return fields


def _upsert(
    category: str, source: str, source_id: str, url: str, state: str,
    drive_hours: float, fields: dict,
) -> None:
    amenity_cols = ", ".join(AMENITY_FIELDS)
    amenity_qs = ", ".join("?" * len(AMENITY_FIELDS))
    amenity_updates = ", ".join(f"{a}=excluded.{a}" for a in AMENITY_FIELDS)
    amenity_values = [int(fields[a]) for a in AMENITY_FIELDS]

    with get_connection() as conn:
        conn.execute(
            f"""INSERT INTO bh_listings (
                category, source, source_id, source_url, name, city, state,
                drive_hours, bedrooms, bathrooms, max_sleeps, {amenity_cols}, description,
                primary_image_url, discovered_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {amenity_qs}, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(source, source_id, category) DO UPDATE SET
                name=excluded.name, city=excluded.city, state=excluded.state,
                drive_hours=excluded.drive_hours,
                bedrooms=excluded.bedrooms, bathrooms=excluded.bathrooms,
                max_sleeps=excluded.max_sleeps, {amenity_updates},
                description=excluded.description,
                primary_image_url=excluded.primary_image_url,
                last_seen_at=datetime('now')
            """,
            [
                category, source, source_id, url, fields["name"], fields["city"], state,
                drive_hours, fields["bedrooms"], fields["bathrooms"], fields["max_sleeps"],
                *amenity_values, fields["description"], fields["primary_image_url"],
            ],
        )


def run_category(category: str) -> dict:
    candidates = discover_candidate_urls(category)
    log.info("[%s] discovered %d unique candidate URLs", category, len(candidates))

    kept, unparseable, failed = 0, 0, 0
    for i, (state, town, drive_hours, source, url) in enumerate(candidates, 1):
        log.info("[%s][%d/%d] %s %s", category, i, len(candidates), source, url)
        page_html = _fetch(url)
        time.sleep(_POLITE_DELAY)
        if not page_html:
            failed += 1
            continue
        fields = parse_listing(page_html)
        if not fields:
            unparseable += 1
            continue
        # prefer the town actually named on the listing page; fall back to
        # the region's own town label when the page didn't say (see
        # __init__.py's CATEGORIES) so `city` is basically never blank
        fields["city"] = fields["city"] or town
        sid = _source_id(source, url)
        _upsert(category, source, sid, url, state, drive_hours, fields)
        kept += 1

    log.info("[%s] done: %d kept, %d unparseable, %d failed", category, kept, unparseable, failed)
    return {"kept": kept, "unparseable": unparseable, "failed": failed}


def run(categories: list[str] | None = None) -> dict:
    create_tables()
    categories = categories or list(CATEGORIES.keys())
    results = {}
    for category in categories:
        results[category] = run_category(category)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    requested = sys.argv[1:] or None
    run(requested)
