"""jobs/beachhouse — Getaway Search: finds vacation-rental candidates for
Bill's family across three uses -- Beach (VA-FL Atlantic reunion houses),
Mountain (cabins within ~12hrs of Wilmington DE), and Romance (secluded
couples getaways in the same driving radius) -- surfaced at
wtsn.me/p/beachhouse as tabs for Bill and Melanie to browse/narrow. Kept
the original module name/URL from the Beach-only version rather than
renaming, since the tool and its slug were already live.

No vacation-rental platform (VRBO, Airbnb, ...) offers a public search API,
and their own search UIs disallow scraping in robots.txt (VRBO: `Disallow:
/search?`; Airbnb: `Disallow: /s/*/*`). Individual LISTING DETAIL pages are
not disallowed on either site, though, and Google indexes them -- so
discovery goes through jobs.research.web_search (Serper/Google) with
site-scoped queries per category/region, and only the resulting listing
URLs are fetched directly (robots.txt-checked per URL regardless, same
standing policy as jobs/retreats and jobs/servantcare).

Each category's `regions`/`query_terms` (what gets searched) are
code-defined here -- not self-service editable from the UI. What IS
self-service: every filter field (bedrooms, bathrooms, amenities, states)
on each tab is live-adjustable from the wtsn.me search form, pre-filled
with that category's defaults below but never a hard scrape-time cutoff --
every candidate with a parseable bedroom count gets stored (see
scraper.py), and the real filtering happens at query time.

No automated price-per-date lookup: VRBO puts a bot-detection challenge
(PerimeterX slide-to-verify) specifically on its "Check availability"
pricing step (confirmed live 2026-09-09), and Airbnb's pricing is behind
similar client-side gating. Solving that challenge to extract a price is
not something this job attempts -- see beachhouse_web.py's price_note
field for the manual-entry alternative instead.
"""
SOURCES = ("vrbo", "airbnb")

# Every amenity column that any category cares about. A category's
# "amenities" list (below) is which of these it exposes as a filter/badge --
# extraction always checks all of them regardless of category, since it's
# free signal and a beach house having a fireplace is still useful to know.
AMENITY_FIELDS = ["has_pool", "oceanfront", "hot_tub", "fireplace", "mountain_view", "secluded"]

# States within roughly a 12-hour drive of Wilmington, DE (~650-750 road
# miles at highway-averaged speed) -- the shared geographic scope for the
# Mountain and Romance categories. Excludes states at or past that radius
# (e.g. Wisconsin, Missouri, Alabama, peninsular Florida -- Beach already
# covers FL's Atlantic coast separately).
TWELVE_HOUR_STATES = [
    "Delaware", "Maryland", "New Jersey", "Pennsylvania", "Virginia",
    "New York", "Connecticut", "Rhode Island", "Massachusetts",
    "West Virginia", "North Carolina", "Vermont", "New Hampshire",
    "Ohio", "Kentucky", "South Carolina", "Maine", "Tennessee",
    "Michigan", "Indiana", "Georgia", "Illinois",
]

CATEGORIES = {
    "beach": {
        "label": "Beach",
        "states": ["Virginia", "North Carolina", "South Carolina", "Georgia", "Florida"],
        # Representative regions per state -- not exhaustive of every small
        # town, but each is a real hub with substantial big-house-with-pool
        # inventory. Florida is Atlantic-coast towns only (Amelia Island
        # down through Vero Beach), not the Gulf coast.
        "regions": {
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
        },
        "query_terms": "large group vacation rental oceanfront pool 7+ bedrooms",
        "amenities": [
            {"key": "has_pool", "label": "Pool"},
            {"key": "oceanfront", "label": "Oceanfront"},
        ],
        "default_min_bedrooms": 7,
        "default_min_bathrooms": 3,
    },
    "mountain": {
        "label": "Mountain",
        "states": TWELVE_HOUR_STATES,
        # Curated real mountain destinations within the 12hr radius -- not
        # every one of TWELVE_HOUR_STATES has one, so not every selectable
        # state will return results; that's expected (the state list is the
        # driving-distance scope, not a promise every state has a match).
        "regions": {
            "Pennsylvania": ["Pocono Mountains PA"],
            "New York": ["Catskills NY", "Adirondacks NY"],
            "Massachusetts": ["Berkshires MA"],
            "Vermont": ["Green Mountains Vermont"],
            "New Hampshire": ["White Mountains New Hampshire"],
            "Virginia": ["Blue Ridge Mountains Virginia", "Shenandoah Valley Virginia"],
            "West Virginia": ["Snowshoe West Virginia", "Canaan Valley West Virginia"],
            "North Carolina": [
                "Asheville North Carolina", "Blowing Rock North Carolina",
                "Great Smoky Mountains North Carolina",
            ],
            "Tennessee": ["Gatlinburg Tennessee", "Great Smoky Mountains Tennessee"],
            "Georgia": ["Blue Ridge Georgia mountains", "Helen Georgia"],
            "Ohio": ["Hocking Hills Ohio"],
        },
        "query_terms": "cozy cabin mountain vacation rental hot tub fireplace mountain view",
        "amenities": [
            {"key": "hot_tub", "label": "Hot tub"},
            {"key": "fireplace", "label": "Fireplace"},
            {"key": "mountain_view", "label": "Mountain view"},
        ],
        "default_min_bedrooms": 4,
        "default_min_bathrooms": 2,
    },
    "romance": {
        "label": "Romance",
        "states": TWELVE_HOUR_STATES,
        # Overlaps Mountain's destinations (a secluded cabin often serves
        # both) plus a few non-mountain romantic/historic towns.
        "regions": {
            "Pennsylvania": ["Pocono Mountains PA", "Brandywine Valley Pennsylvania"],
            "New York": ["Finger Lakes New York", "Catskills NY"],
            "Massachusetts": ["Berkshires MA"],
            "Vermont": ["Vermont countryside"],
            "New Hampshire": ["White Mountains New Hampshire"],
            "Maryland": ["Chesapeake Bay Maryland"],
            "Virginia": ["Shenandoah Valley Virginia", "Blue Ridge Mountains Virginia"],
            "West Virginia": ["West Virginia mountains"],
            "North Carolina": ["Asheville North Carolina", "Blowing Rock North Carolina"],
            "Tennessee": ["Gatlinburg Tennessee"],
            "Georgia": ["Blue Ridge Georgia mountains", "Helen Georgia"],
            "South Carolina": ["Charleston South Carolina"],
        },
        "query_terms": "romantic secluded cabin getaway hot tub fireplace couples",
        "amenities": [
            {"key": "hot_tub", "label": "Hot tub"},
            {"key": "fireplace", "label": "Fireplace"},
            {"key": "secluded", "label": "Secluded"},
        ],
        "default_min_bedrooms": 1,
        "default_max_bedrooms": 2,
        "default_min_bathrooms": 1,
    },
}
