"""jobs/retreats/discover.py — enumerate candidate listing URLs per source.

Uses each site's XML sitemap rather than crawling links: simpler, and (confirmed
live against my-pastor.com and pastorgetaways.com 2026-08-16) sitemaps already
list every individual listing page directly, no pagination to walk. robots.txt
is still checked per domain before fetching — standing policy, not optional —
even though both confirmed sources currently allow it.
"""
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

log = logging.getLogger(__name__)

SEEN_PATH = Path.home() / "watson" / "data" / "retreats_seen.json"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_ROBOTS_CACHE: dict[str, RobotFileParser] = {}


def robots_allowed(url: str) -> bool:
    """Fetches robots.txt with our own browser-like UA rather than letting
    RobotFileParser.read() do it — read() uses urllib's bare default UA
    ("Python-urllib/x.y"), which some sites bot-block with a 403. RobotFileParser
    treats 401/403 as "disallow everything", which silently misreports a site
    as blocked when a normal browser UA would've been let through fine
    (confirmed live against ag.org 2026-08-16: 200 + empty body for a browser
    UA, but read()'s bare urllib request got treated as disallow-all)."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    rp = _ROBOTS_CACHE.get(origin)
    if rp is None:
        rp = RobotFileParser()
        rp.set_url(f"{origin}/robots.txt")
        try:
            resp = requests.get(f"{origin}/robots.txt", headers={"User-Agent": _UA}, timeout=15)
            if resp.status_code in (401, 403):
                rp.disallow_all = True
            elif resp.status_code >= 400:
                rp.allow_all = True  # missing robots.txt — spec default is allow
            else:
                rp.parse(resp.text.splitlines())
        except requests.RequestException:
            # Unreachable (DNS failure, timeout, etc.) — fail closed, don't fetch.
            rp = None
        _ROBOTS_CACHE[origin] = rp
    if rp is None:
        return False
    return rp.can_fetch(_UA, url)


def load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    try:
        return set(json.loads(SEEN_PATH.read_text()))
    except Exception:
        return set()


def save_seen(seen: set[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(sorted(seen)))


def _fetch_sitemap_urls(sitemap_url: str) -> list[str]:
    if not robots_allowed(sitemap_url):
        log.warning("robots.txt disallows %s — skipping source", sitemap_url)
        return []
    try:
        resp = requests.get(sitemap_url, headers={"User-Agent": _UA}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("sitemap fetch failed for %s: %s", sitemap_url, exc)
        return []

    urls = _LOC_RE.findall(resp.text)

    # A sitemap index (points at sub-sitemaps, e.g. WordPress's native
    # wp-sitemap.xml or Yoast's sitemap_index.xml) has every <loc> entry itself
    # ending in .xml. Recurse into ALL of them, not just ones whose name looks
    # like "post"/"page" — a real index always includes sub-sitemaps that don't
    # match that substring (e.g. wp-sitemap-taxonomies-category-1.xml), and
    # requiring every entry to match silently returned nothing whenever a
    # single non-matching sub-sitemap was present (confirmed live against
    # hopeforpastorswives.com 2026-08-16 — taxonomy sub-sitemaps broke the old
    # "all must match" check, returning zero candidates from a real 40+-post site).
    if urls and all(u.endswith(".xml") for u in urls):
        collected = []
        for sub in urls:
            collected.extend(_fetch_sitemap_urls(sub))
        return collected

    return [u for u in urls if not u.endswith(".xml")]


def discover_candidates(source: dict, seen: set[str]) -> list[str]:
    """Return new (not-yet-seen) candidate listing URLs for one source."""
    urls = _fetch_sitemap_urls(source["sitemap_url"])

    keyword_re = re.compile(source["keyword_filter"], re.IGNORECASE) if source.get("keyword_filter") else None
    exclude_re = re.compile(source["exclude"], re.IGNORECASE) if source.get("exclude") else None

    candidates = []
    for url in urls:
        if url in seen:
            continue
        if keyword_re and not keyword_re.search(url):
            continue
        if exclude_re and exclude_re.search(url):
            continue
        candidates.append(url)
    return candidates
