"""jobs/retreats/fit.py — distance/drive-time from Newark, DE and the fit_rating heuristic.

Geocoding via Nominatim (OpenStreetMap), driving distance/time via OSRM's public
routing instance — both free, no API key, confirmed live 2026-08-16. Both are
public shared services: usage stays light (a handful of listings per run), one
request at a time with a short delay, per Nominatim's usage policy.
"""
import logging
import re
import time

import requests

from jobs.retreats import HOME_LOCATION, MAX_DRIVE_HOURS, TARGET_CAPACITY

log = logging.getLogger(__name__)

_UA = "watson-retreats/1.0 (personal, non-commercial; bill.yomes@gmail.com)"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OSRM_URL = "http://router.project-osrm.org/route/v1/driving"

_HOME_COORDS: tuple[float, float] | None = None


def _geocode(location: str) -> tuple[float, float] | None:
    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={"q": location, "format": "json", "limit": 1},
            headers={"User-Agent": _UA},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as exc:
        log.warning("geocode failed for %r: %s", location, exc)
        return None
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def _home_coords() -> tuple[float, float] | None:
    global _HOME_COORDS
    if _HOME_COORDS is None:
        _HOME_COORDS = _geocode(HOME_LOCATION)
    return _HOME_COORDS


def _format_drive_time(seconds: float) -> str:
    hours = seconds / 3600
    h = int(hours)
    m = round((hours - h) * 60)
    if m == 60:
        h, m = h + 1, 0
    return f"{h}h {m}m" if m else f"{h}h"


def distance_and_drive_time(location: str) -> tuple[float | None, str | None, float | None]:
    """Returns (distance_miles, drive_time, duration_seconds) from Newark, DE, or
    (None, None, None) if geocoding/routing failed — never a guessed distance."""
    home = _home_coords()
    if not home:
        return None, None, None

    time.sleep(1)  # Nominatim usage policy: max 1 request/second
    dest = _geocode(location)
    if not dest:
        return None, None, None

    try:
        resp = requests.get(
            f"{_OSRM_URL}/{home[1]},{home[0]};{dest[1]},{dest[0]}",
            params={"overview": "false"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        route = data["routes"][0]
    except Exception as exc:
        log.warning("routing failed for %r: %s", location, exc)
        return None, None, None

    miles = round(route["distance"] / 1609.34, 1)
    drive_time = _format_drive_time(route["duration"])
    return miles, drive_time, route["duration"]


_CAPACITY_NUM_RE = re.compile(r"\d+")


def compute_fit(kitchen_status: str | None, capacity: str | None) -> tuple[str, str]:
    """Rule-based, not a model call — matches the architecture doc's fit-rating spec:
    good when kitchen is confirmed and capacity covers the party, warn when either
    is unclear, bad when kitchen is explicitly absent or capacity is confirmed too
    small."""
    cap_num = None
    if capacity:
        nums = [int(n) for n in _CAPACITY_NUM_RE.findall(capacity)]
        if nums:
            cap_num = max(nums)

    kitchen_yes = kitchen_status == "yes"
    kitchen_no = kitchen_status == "no"
    capacity_ok = cap_num is not None and cap_num >= TARGET_CAPACITY
    capacity_too_small = cap_num is not None and cap_num < TARGET_CAPACITY

    if kitchen_no or capacity_too_small:
        return "bad", "Kitchen or capacity doesn't fit the family" if kitchen_no else \
            f"Capacity ({capacity}) is below the party of {TARGET_CAPACITY}"
    if kitchen_yes and capacity_ok:
        return "good", "Full kitchen and enough capacity for the family"
    return "warn", "Kitchen or capacity unclear — worth a call to confirm"


def within_range(duration_seconds: float | None) -> bool:
    if duration_seconds is None:
        return False
    return duration_seconds <= MAX_DRIVE_HOURS * 3600
