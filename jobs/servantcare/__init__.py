"""jobs/servantcare — dedicated search tool over ServantCARE's Hospitality
Homes directory (servantcare.com/hospitality-homes), for Bill's family to
browse/search vacation rentals at wtsn.me/p/servantcare.

Distinct from jobs/retreats/ (a separate pastor-retreat discovery pipeline
that pushes candidates to williamckyomes.com/retreats, no local DB, no
photos) -- this module keeps its own DB and downloaded photos so the search
UI can filter and render results without re-hitting servantcare.com per
request.

DB: ~/watson/data/servantcare.db (own file, same one-db-per-domain pattern
as congregation.db/donors.db/curator.db -- not core.database's watson.db).
Images: ~/watson/data/servantcare_images/<pID>/<n>.<ext>, served publicly
(unauthenticated, filename-regex-gated) by jobs/servantcare/servantcare_web.py
so <img src> tags in the browser can hotlink them directly, same pattern as
jobs/comms/api.py's serve_asset().
"""
import os

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

BASE_URL = "https://servantcare.com"
LISTINGS_PATH = "/hospitality-homes"

# Eastern-US scope per Bill's request (2026-09-02): standard "east of the
# Mississippi" state list (Wikipedia convention) -- Louisiana, Arkansas,
# Missouri, Iowa, and Minnesota are excluded despite the river forming part
# of their border, matching how the term is normally used.
EASTERN_US_STATES = {
    "Alabama", "Connecticut", "Delaware", "Florida", "Georgia", "Illinois",
    "Indiana", "Kentucky", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Mississippi", "New Hampshire", "New Jersey", "New York",
    "North Carolina", "Ohio", "Pennsylvania", "Rhode Island",
    "South Carolina", "Tennessee", "Vermont", "Virginia", "West Virginia",
    "Wisconsin", "District of Columbia",
}

AMENITY_LABELS = {
    "1": "Internet/WiFi",
    "3": "Cable/Streaming TV",
    "4": "Washer/Dryer Available",
    "5": "Pool",
    "6": "BBQ Grill",
    "7": "RV Hook-up",
    "8": "Hot Tub",
    "9": "Exercise room/equipment",
    "11": "Private (Unhosted or separate accommodations)",
    "12": "Lake/Water Access",
    "13": "Long Term Stay (1 month - 1 year)",
    "19": "Soul CARE Available (coaching, debriefing, personal care, marriage coaching, etc)",
}
