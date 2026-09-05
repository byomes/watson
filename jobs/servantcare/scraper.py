"""jobs/servantcare/scraper.py — pull ServantCARE Hospitality Homes listings
(Eastern-US scope) into servantcare.db, with photos downloaded locally.

Run standalone: `python -m jobs.servantcare.scraper` (from ~/watson, with
PYTHONPATH=/home/billyomes/watson). Safe to re-run — upserts by pid and
re-downloads only images not already on disk.
"""
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from jobs.retreats.discover import robots_allowed
from jobs.servantcare import BASE_URL, EASTERN_US_STATES, LISTINGS_PATH
from jobs.servantcare.schema import create_tables, get_connection

log = logging.getLogger(__name__)

_UA = "watson-servantcare/1.0 (personal, non-commercial; bill.yomes@gmail.com)"
_TIMEOUT = 20
_POLITE_DELAY = 1.0  # seconds between requests to servantcare.com

IMAGES_DIR = Path.home() / "watson" / "data" / "servantcare_images"

_HH_RE = re.compile(r"var HospitatlityHomes\s*=\s*(\[.*?\]);", re.S)
_INT_RE = re.compile(r"(\d+)")


def _get(url: str) -> str | None:
    if not robots_allowed(url):
        log.warning("robots.txt disallows %s -- skipping", url)
        return None
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        log.warning("fetch failed for %s: %s", url, exc)
        return None


def discover_eastern_us_listings() -> list[dict]:
    """Fetch the unfiltered listings page once and pull every property out of
    its embedded `HospitatlityHomes` JS array (the same data the site's own
    client-side filter/pagination JS uses) -- avoids paging through the UI's
    10-per-page results or issuing one request per state."""
    html = _get(f"{BASE_URL}{LISTINGS_PATH}")
    if not html:
        return []
    match = _HH_RE.search(html)
    if not match:
        log.error("HospitatlityHomes JSON block not found on listings page")
        return []
    all_listings = json.loads(match.group(1))
    return [
        item for item in all_listings
        if item.get("CountryID") == "United States"
        and item.get("StateProvID") in EASTERN_US_STATES
    ]


def _first_int(text: str | None) -> int | None:
    if not text:
        return None
    m = _INT_RE.search(text)
    return int(m.group(1)) if m else None


def _parse_detail(html: str, source_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {}

    overview = soup.select_one("#listing-overview")
    if overview:
        heading = overview.find("h4")
        paragraphs = [p.get_text(" ", strip=True) for p in overview.find_all("p")]
        desc_parts = []
        if heading:
            desc_parts.append(heading.get_text(" ", strip=True))
        desc_parts.extend(p for p in paragraphs if p)
        out["description"] = "\n\n".join(desc_parts) if desc_parts else None

    allergy_header = soup.find(lambda tag: tag.name == "h3" and "Allergy Alert" in tag.get_text())
    if allergy_header:
        p = allergy_header.find_next("p")
        out["allergy_alert"] = p.get_text(" ", strip=True) if p else None
    else:
        out["allergy_alert"] = None

    details_header = soup.find(lambda tag: tag.name == "h3" and tag.get_text(strip=True) == "Details")
    if details_header:
        ul = details_header.find_next("ul")
        items = [li.get_text(" ", strip=True) for li in ul.find_all("li")] if ul else []
        for item in items:
            low = item.lower()
            if "bedroom" in low:
                out["bedrooms"] = _first_int(item)
            elif "bed(s)" in low:
                out["beds"] = _first_int(item)
            elif "bathroom" in low:
                out["bathrooms"] = _first_int(item)
            elif "sleeps" in low:
                out["max_sleeps"] = _first_int(item)
            elif "max stay" in low:
                out["max_stay_nights"] = _first_int(item)

    amenities_header = soup.find(lambda tag: tag.name == "h3" and tag.get_text(strip=True) == "Amenities")
    if amenities_header:
        ul = amenities_header.find_next("ul")
        out["amenities"] = [li.get_text(" ", strip=True) for li in ul.find_all("li")] if ul else []
    else:
        out["amenities"] = []

    pricing_container = soup.select_one("#listing-pricing-list")
    pricing = []
    if pricing_container:
        for li in pricing_container.select("ul > li"):
            label = li.find("h5")
            detail = li.find("p")
            amount = li.find("span")
            pricing.append({
                "label": label.get_text(strip=True) if label else None,
                "detail": detail.get_text(strip=True) if detail else None,
                "amount": amount.get_text(strip=True) if amount else None,
            })
    out["pricing"] = pricing
    out["price_summary"] = pricing[0]["amount"] if pricing and pricing[0]["amount"] else None

    map_div = soup.select_one("#singleListingMap")
    if map_div:
        lat = map_div.get("data-latitude")
        lng = map_div.get("data-longitude")
        out["latitude"] = float(lat) if lat else None
        out["longitude"] = float(lng) if lng else None

    images = []
    for slide in soup.select(".swiper-slide[data-src]"):
        src = slide.get("data-src")
        if src:
            images.append(urljoin(BASE_URL, src))
    out["images"] = images

    return out


def _download_image(url: str, pid: int, seq: int) -> str | None:
    ext = os.path.splitext(url)[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    dest_dir = IMAGES_DIR / str(pid)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{seq}{ext}"
    if dest.exists():
        return str(dest.relative_to(IMAGES_DIR))
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return str(dest.relative_to(IMAGES_DIR))
    except requests.RequestException as exc:
        log.warning("image download failed for %s: %s", url, exc)
        return None


def scrape_listing(summary: dict) -> bool:
    pid = summary["pID"]
    chandle = summary["cHandle"]
    source_url = f"{BASE_URL}{LISTINGS_PATH}/{chandle}"
    html = _get(source_url)
    time.sleep(_POLITE_DELAY)
    if not html:
        return False

    detail = _parse_detail(html, source_url)

    local_images = []
    for seq, img_url in enumerate(detail.get("images") or []):
        rel_path = _download_image(img_url, pid, seq)
        if rel_path:
            local_images.append((seq, rel_path, img_url))
        time.sleep(_POLITE_DELAY)

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO sc_listings (
                pid, chandle, name, city, state, country, bedrooms, beds,
                bathrooms, max_sleeps, max_stay_nights, description,
                allergy_alert, amenities_json, pricing_json, price_summary,
                latitude, longitude, source_url, primary_image_path, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(pid) DO UPDATE SET
                name=excluded.name, city=excluded.city, state=excluded.state,
                country=excluded.country, bedrooms=excluded.bedrooms,
                beds=excluded.beds, bathrooms=excluded.bathrooms,
                max_sleeps=excluded.max_sleeps,
                max_stay_nights=excluded.max_stay_nights,
                description=excluded.description,
                allergy_alert=excluded.allergy_alert,
                amenities_json=excluded.amenities_json,
                pricing_json=excluded.pricing_json,
                price_summary=excluded.price_summary,
                latitude=excluded.latitude, longitude=excluded.longitude,
                source_url=excluded.source_url,
                primary_image_path=excluded.primary_image_path,
                scraped_at=datetime('now')
            """,
            (
                pid, chandle, summary.get("Name"), summary.get("City"),
                summary.get("StateProvID"), summary.get("CountryID"),
                detail.get("bedrooms") or _first_int(summary.get("Bedrooms")),
                detail.get("beds"),
                detail.get("bathrooms") or _first_int(summary.get("Bathrooms")),
                detail.get("max_sleeps") or _first_int(summary.get("MaxSleeps")),
                detail.get("max_stay_nights"),
                detail.get("description"), detail.get("allergy_alert"),
                json.dumps(detail.get("amenities") or []),
                json.dumps(detail.get("pricing") or []),
                detail.get("price_summary"),
                detail.get("latitude") or (float(summary["Latitude"]) if summary.get("Latitude") else None),
                detail.get("longitude") or (float(summary["Longitude"]) if summary.get("Longitude") else None),
                source_url,
                local_images[0][1] if local_images else None,
            ),
        )
        conn.execute("DELETE FROM sc_listing_images WHERE pid = ?", (pid,))
        for seq, rel_path, img_url in local_images:
            conn.execute(
                "INSERT INTO sc_listing_images (pid, seq, local_path, source_url) VALUES (?, ?, ?, ?)",
                (pid, seq, rel_path, img_url),
            )
    return True


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    create_tables()
    listings = discover_eastern_us_listings()
    log.info("found %d eastern-US listings to scrape", len(listings))
    ok, failed = 0, 0
    for i, summary in enumerate(listings, 1):
        log.info("[%d/%d] %s (pid=%s)", i, len(listings), summary.get("Name"), summary["pID"])
        if scrape_listing(summary):
            ok += 1
        else:
            failed += 1
    log.info("done: %d ok, %d failed", ok, failed)


if __name__ == "__main__":
    run()
