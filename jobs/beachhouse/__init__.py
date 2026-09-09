"""jobs/beachhouse — Beach House Search: finds large group vacation-rental
candidates on the Atlantic coast from VA to FL for Bill's family (kids +
grandkids) reunion week, surfaced at wtsn.me/p/beachhouse for Bill and
Donna to browse/narrow.

No vacation-rental platform (VRBO, Airbnb, ...) offers a public search API,
and their own search UIs disallow scraping in robots.txt (VRBO: `Disallow:
/search?`; Airbnb: `Disallow: /s/*/*`). Individual LISTING DETAIL pages are
not disallowed on either site, though, and Google indexes them -- so
discovery goes through jobs.research.web_search (Serper/Google) with
site-scoped queries, and only the resulting listing URLs are fetched
directly (robots.txt-checked per URL regardless, same standing policy as
jobs/retreats and jobs/servantcare).

DEFAULT_MIN_BEDROOMS/DEFAULT_MIN_BATHROOMS are starting values for the
search form only, not a scrape-time filter -- every candidate with a
parseable bedroom count gets stored (see scraper.py), and Donna sets her
own real thresholds (bedrooms, bathrooms, pool, oceanfront) from the
wtsn.me search UI at query time.

No automated price-per-date lookup: VRBO puts a bot-detection challenge
(PerimeterX slide-to-verify) specifically on its "Check availability"
pricing step (confirmed live 2026-09-09), and Airbnb's pricing is behind
similar client-side gating. Solving that challenge to extract a price is
not something this job attempts -- see beachhouse_web.py's price_note
field for the manual-entry alternative instead.
"""
DEFAULT_MIN_BEDROOMS = 7
DEFAULT_MIN_BATHROOMS = 3

STATES = ["Virginia", "North Carolina", "South Carolina", "Georgia", "Florida"]

# Representative beach regions per state -- not exhaustive of every small
# town, but each is a real hub with substantial big-house-with-pool
# inventory. Add more regions here to widen the net; each region costs one
# Serper query per source per scraper run. Florida is Atlantic-coast towns
# only (Amelia Island down through Vero Beach), not the Gulf coast.
REGIONS = {
    "Virginia": ["Sandbridge Virginia Beach", "Virginia Beach VA"],
    "North Carolina": [
        "Outer Banks NC", "Corolla NC", "Duck NC", "Emerald Isle NC",
        "Topsail Island NC", "Wrightsville Beach NC",
    ],
    "South Carolina": [
        "Myrtle Beach SC", "North Myrtle Beach SC",
        "Hilton Head Island SC", "Kiawah Island SC",
    ],
    "Georgia": ["Tybee Island GA", "St. Simons Island GA", "Jekyll Island GA"],
    "Florida": [
        "Amelia Island FL", "St. Augustine FL", "Daytona Beach FL",
        "Cocoa Beach FL", "Vero Beach FL",
    ],
}

SOURCES = ("vrbo", "airbnb")
