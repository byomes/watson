"""jobs/beachhouse — Getaway Search: finds vacation-rental candidates for
Bill's family across four uses -- Beach (VA-FL Atlantic reunion houses),
Mountain (cabins within ~12hrs of Wilmington DE), Romance (secluded couples
getaways in the same driving radius), and Flash (curated same-week hotel
deal pages within ~2.5hrs, for a genuinely spur-of-the-moment single night)
-- surfaced at wtsn.me/p/beachhouse as tabs for Bill and Melanie to
browse/narrow. Kept the original module name/URL from the Beach-only
version rather than renaming, since the tool and its slug were already
live. Flash is structurally different (see FLASH_REGIONS/flash_scraper.py
below) -- a deal isn't a rental with bedrooms/bathrooms, so it isn't part
of CATEGORIES/AMENITY_FIELDS and has its own small schema/table/API.

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

Each region entry is {"query": <Serper search text>, "town": <display name
shown to Bill/Melanie>, "drive_hours": <approx one-way drive from
Wilmington, DE, rounded to the nearest 0.5h>} rather than a bare string --
`town` is what a listing's `city` falls back to when the page itself
doesn't say (see scraper.py's parse), and `drive_hours` gets stored
straight onto every listing discovered under that region. Estimates are
straight-line highway-time judgment calls, not routed -- close enough for
"is this a weekend trip or a full day's drive," not turn-by-turn accurate.
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


def _region(query: str, town: str, drive_hours: float) -> dict:
    return {"query": query, "town": town, "drive_hours": drive_hours}


CATEGORIES = {
    "beach": {
        "label": "Beach",
        "states": ["Virginia", "North Carolina", "South Carolina", "Georgia", "Florida"],
        # Representative regions per state -- not exhaustive of every small
        # town, but each is a real hub with substantial big-house-with-pool
        # inventory. Florida is Atlantic-coast towns only (Amelia Island
        # down through Vero Beach), not the Gulf coast.
        "regions": {
            "Virginia": [
                _region("Sandbridge Virginia Beach", "Sandbridge", 3.5),
                _region("Virginia Beach VA", "Virginia Beach", 3.5),
            ],
            "North Carolina": [
                _region("Outer Banks NC", "Outer Banks", 5.5),
                _region("Corolla NC", "Corolla", 5.5),
                _region("Duck NC", "Duck", 5.5),
                _region("Emerald Isle NC", "Emerald Isle", 7),
                _region("Topsail Island NC", "Topsail Island", 7),
                _region("Wrightsville Beach NC", "Wrightsville Beach", 7.5),
            ],
            "South Carolina": [
                _region("Myrtle Beach SC", "Myrtle Beach", 8),
                _region("North Myrtle Beach SC", "North Myrtle Beach", 7.5),
                _region("Hilton Head Island SC", "Hilton Head Island", 10.5),
                _region("Kiawah Island SC", "Kiawah Island", 10),
            ],
            "Georgia": [
                _region("Tybee Island GA", "Tybee Island", 11),
                _region("St. Simons Island GA", "St. Simons Island", 11.5),
                _region("Jekyll Island GA", "Jekyll Island", 11.5),
            ],
            "Florida": [
                _region("Amelia Island FL", "Amelia Island", 12),
                _region("St. Augustine FL", "St. Augustine", 12.5),
                _region("Daytona Beach FL", "Daytona Beach", 13),
                _region("Cocoa Beach FL", "Cocoa Beach", 14),
                _region("Vero Beach FL", "Vero Beach", 14.5),
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
            "Pennsylvania": [_region("Pocono Mountains PA", "Pocono Mountains", 2)],
            "New York": [
                _region("Catskills NY", "Catskills", 3.5),
                _region("Adirondacks NY", "Adirondacks", 6.5),
            ],
            "Massachusetts": [_region("Berkshires MA", "Berkshires", 4.5)],
            "Vermont": [_region("Green Mountains Vermont", "Green Mountains", 6.5)],
            "New Hampshire": [_region("White Mountains New Hampshire", "White Mountains", 7.5)],
            "Virginia": [
                _region("Blue Ridge Mountains Virginia", "Blue Ridge Mountains", 4.5),
                _region("Shenandoah Valley Virginia", "Shenandoah Valley", 3.5),
            ],
            "West Virginia": [
                _region("Snowshoe West Virginia", "Snowshoe", 5),
                _region("Canaan Valley West Virginia", "Canaan Valley", 4.5),
            ],
            "North Carolina": [
                _region("Asheville North Carolina", "Asheville", 8),
                _region("Blowing Rock North Carolina", "Blowing Rock", 7.5),
                _region("Great Smoky Mountains North Carolina", "Great Smoky Mountains", 9),
            ],
            "Tennessee": [
                _region("Gatlinburg Tennessee", "Gatlinburg", 9.5),
                _region("Great Smoky Mountains Tennessee", "Great Smoky Mountains", 9.5),
            ],
            "Georgia": [
                _region("Blue Ridge Georgia mountains", "Blue Ridge", 10),
                _region("Helen Georgia", "Helen", 10),
            ],
            "Ohio": [_region("Hocking Hills Ohio", "Hocking Hills", 6.5)],
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
            "Pennsylvania": [
                _region("Pocono Mountains PA", "Pocono Mountains", 2),
                _region("Brandywine Valley Pennsylvania", "Brandywine Valley", 0.5),
            ],
            "New York": [
                _region("Finger Lakes New York", "Finger Lakes", 5.5),
                _region("Catskills NY", "Catskills", 3.5),
            ],
            "Massachusetts": [_region("Berkshires MA", "Berkshires", 4.5)],
            "Vermont": [_region("Vermont countryside", "Vermont", 6.5)],
            "New Hampshire": [_region("White Mountains New Hampshire", "White Mountains", 7.5)],
            "Maryland": [_region("Chesapeake Bay Maryland", "Chesapeake Bay", 1.5)],
            "Virginia": [
                _region("Shenandoah Valley Virginia", "Shenandoah Valley", 3.5),
                _region("Blue Ridge Mountains Virginia", "Blue Ridge Mountains", 4.5),
            ],
            "West Virginia": [_region("West Virginia mountains", "West Virginia Mountains", 4.5)],
            "North Carolina": [
                _region("Asheville North Carolina", "Asheville", 8),
                _region("Blowing Rock North Carolina", "Blowing Rock", 7.5),
            ],
            "Tennessee": [_region("Gatlinburg Tennessee", "Gatlinburg", 9.5)],
            "Georgia": [
                _region("Blue Ridge Georgia mountains", "Blue Ridge", 10),
                _region("Helen Georgia", "Helen", 10),
            ],
            "South Carolina": [_region("Charleston South Carolina", "Charleston", 8.5)],
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

# Flash deals (jobs/beachhouse/flash_scraper.py, separate from the
# rental-shaped CATEGORIES above) -- Travelzoo's curated hotel deal pages,
# for a genuinely spur-of-the-moment single night out. Close by on purpose:
# a one-night trip isn't worth a multi-hour drive, so this is a short list
# of towns within roughly 2.5hrs of Wilmington DE, not the 12hr radius
# Mountain/Romance use. Unlike VRBO/Airbnb, Travelzoo's deal pages carry
# real static price/discount data (schema.org microdata + a predictable
# "$<price>-<headline>" page title) -- confirmed live 2026-09-09 -- so
# price here is scraped, not manual. Groupon's getaway deal pages looked
# promising too (robots.txt allows them) but return HTTP 403 to a plain
# fetch (bot-blocked) -- skipped rather than fought.
FLASH_REGIONS = [
    _region("Brandywine Valley PA hotel deal", "Brandywine Valley", 0.5),
    _region("Philadelphia PA hotel deal", "Philadelphia", 0.5),
    _region("Lancaster PA hotel deal", "Lancaster", 1.5),
    _region("New Hope Bucks County PA hotel deal", "New Hope", 1.5),
    _region("Rehoboth Beach DE hotel deal", "Rehoboth Beach", 1.5),
    _region("Baltimore MD hotel deal", "Baltimore", 1.5),
    _region("Annapolis MD hotel deal", "Annapolis", 2),
    _region("St Michaels Chesapeake Bay MD hotel deal", "St. Michaels", 2),
    _region("Cape May NJ hotel deal", "Cape May", 2),
    _region("Hershey PA hotel deal", "Hershey", 2),
    _region("New York City hotel deal", "New York City", 2.5),
]
