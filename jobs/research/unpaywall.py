"""jobs/research/unpaywall.py -- Unpaywall lookup for open-access mirrors.

Given a DOI, finds a legal open-access location for a paper whose publisher
landing page 403s/404s (e.g. Taylor & Francis, ResearchGate, SAGE) -- lets
field_research.py retry a fetch on a readable mirror instead of just
dropping the candidate. Free API, no key, just a contact email per
Unpaywall's usage policy (not auth, just identification).
"""
import logging

import requests

from config.settings import RESEARCH_CONTACT_EMAIL

log = logging.getLogger(__name__)

UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}"


def find_oa_url(doi: str, timeout: int = 10) -> str | None:
    if not doi:
        return None
    try:
        resp = requests.get(
            UNPAYWALL_URL.format(doi=doi.strip()),
            params={"email": RESEARCH_CONTACT_EMAIL},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("Unpaywall lookup failed for doi=%s: %s", doi, exc)
        return None

    best = data.get("best_oa_location") or {}
    return best.get("url_for_pdf") or best.get("url") or None
