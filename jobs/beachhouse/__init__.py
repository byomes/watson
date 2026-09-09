"""jobs/beachhouse — Beach House Search: finds large group vacation-rental
candidates across VA/NC/SC for Bill's family (kids + grandkids) reunion
week, surfaced at wtsn.me/p/beachhouse for Bill and Donna to browse/narrow.

No vacation-rental platform (VRBO, Airbnb, ...) offers a public search API,
and their own search UIs disallow scraping in robots.txt (VRBO: `Disallow:
/search?`; Airbnb: `Disallow: /s/*/*`). Individual LISTING DETAIL pages are
not disallowed on either site, though, and Google indexes them -- so
discovery goes through jobs.research.web_search (Serper/Google) with
site-scoped queries, and only the resulting listing URLs are fetched
directly (robots.txt-checked per URL regardless, same standing policy as
jobs/retreats and jobs/servantcare).

Hard filters applied after fetching each candidate (never trust the search
snippet alone): bedrooms >= MIN_BEDROOMS, bathrooms >= MIN_BATHROOMS, pool
present, and oceanfront/direct beach access present.
"""
MIN_BEDROOMS = 7
MIN_BATHROOMS = 3

STATES = ["Virginia", "North Carolina", "South Carolina"]

# Representative beach regions per state -- not exhaustive of every small
# town, but each is a real hub with substantial big-house-with-pool
# inventory. Add more regions here to widen the net; each region costs one
# Serper query per source per scraper run.
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
}

SOURCES = ("vrbo", "airbnb")
