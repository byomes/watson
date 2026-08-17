"""jobs/retreats/push.py — POST discovered listings to the wcky ingest API."""
import logging

import requests

from jobs.retreats import RETREATS_API_KEY, WCKY_BASE

log = logging.getLogger(__name__)


def push_listings(listings: list[dict]) -> dict:
    """listings already in RetreatInput shape. Returns {"inserted", "skipped_duplicates"}
    or {"error": ...} if the request itself failed."""
    if not listings:
        return {"inserted": 0, "skipped_duplicates": 0}
    if not RETREATS_API_KEY:
        log.error("RETREATS_API_KEY not set — cannot push")
        return {"error": "RETREATS_API_KEY not set"}

    try:
        resp = requests.post(
            f"{WCKY_BASE}/api/retreats/ingest",
            json={"listings": listings},
            headers={"X-Retreats-Key": RETREATS_API_KEY, "Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        log.error("push_listings failed: %s", exc)
        return {"error": str(exc)}
