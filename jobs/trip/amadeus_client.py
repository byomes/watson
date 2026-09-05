"""jobs/trip/amadeus_client.py — thin wrapper over the Amadeus Self-Service
test/sandbox API (test.api.amadeus.com). Deliberately pinned to the test
host — see WATSON_ARCHITECTURE.md trip-finder note: search endpoints
(Flight Cheapest Date Search, Hotel Search v3) are documented as real-time
even on the free test-tier key, and moving to the production host requires
a billing profile, which is a separate decision, not a default.

Endpoint parameter shapes below follow Amadeus's published docs but are
UNTESTED against live responses — no credentials have been issued yet.
Verify field names (esp. hotel `chainCode`, `rating`, offer price fields)
against a real response on first live run and adjust `propose.py`'s
filters if they differ.
"""
import time

import requests

from config.settings import AMADEUS_API_KEY, AMADEUS_API_SECRET

BASE_URL = "https://test.api.amadeus.com"

_token_cache = {"access_token": None, "expires_at": 0}


class AmadeusNotConfigured(RuntimeError):
    pass


def get_access_token() -> str:
    if not AMADEUS_API_KEY or not AMADEUS_API_SECRET:
        raise AmadeusNotConfigured(
            "AMADEUS_API_KEY / AMADEUS_API_SECRET not set in .env — "
            "sign up at developers.amadeus.com (free self-service, test "
            "environment) and add both to ~/watson/.env."
        )

    # 60s safety margin so a token doesn't expire mid-request.
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    resp = requests.post(
        f"{BASE_URL}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": AMADEUS_API_KEY,
            "client_secret": AMADEUS_API_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    _token_cache["access_token"] = payload["access_token"]
    _token_cache["expires_at"] = time.time() + payload.get("expires_in", 1799)
    return _token_cache["access_token"]


def _get(path: str, params: dict) -> dict:
    resp = requests.get(
        f"{BASE_URL}{path}",
        params=params,
        headers={"Authorization": f"Bearer {get_access_token()}"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def cheapest_dates(origin: str, destination: str, duration: str = "2,3") -> list:
    """Flight Cheapest Date Search — cached, refreshes daily per Amadeus docs.
    `duration` is a nights range, e.g. "2,3" for a long-weekend trip.
    Returns the raw `data` list: [{departureDate, returnDate, price: {total}}...]
    """
    payload = _get(
        "/v2/shopping/flight-dates",
        {
            "origin": origin,
            "destination": destination,
            "duration": duration,
            "nonStop": "false",
            "viewBy": "DURATION",
        },
    )
    return payload.get("data", [])


def hotels_by_city(city_code: str, radius_km: int = 20) -> list:
    """Hotel List by city — returns hotelIds to feed into hotel_offers()."""
    payload = _get(
        "/v1/reference-data/locations/hotels/by-city",
        {"cityCode": city_code, "radius": radius_km, "radiusUnit": "KM"},
    )
    return payload.get("data", [])


def hotel_offers(hotel_ids: list, check_in: str, check_out: str, adults: int = 2) -> list:
    """Hotel Search v3 — real-time offers for a batch of hotelIds."""
    if not hotel_ids:
        return []
    payload = _get(
        "/v3/shopping/hotel-offers",
        {
            "hotelIds": ",".join(hotel_ids),
            "checkInDate": check_in,
            "checkOutDate": check_out,
            "adults": adults,
            "roomQuantity": 1,
            "bestRateOnly": "true",
        },
    )
    return payload.get("data", [])
