"""jobs/research/openalex.py — OpenAlex scholarly works search (free, no API key).

OpenAlex (openalex.org) is an open catalog of scholarly works covering
science, humanities, and theology far more broadly than arXiv. Used here as
a free complement/fallback to Semantic Scholar for field_research.py.
"""
import logging

import requests

log = logging.getLogger(__name__)

OPENALEX_URL = "https://api.openalex.org/works"


def search(query: str, max_results: int = 5, timeout: int = 10) -> list[dict]:
    try:
        resp = requests.get(
            OPENALEX_URL,
            params={"search": query, "per-page": max_results},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("OpenAlex search failed: %s", exc)
        return []

    results = []
    for w in data.get("results", [])[:max_results]:
        oa = w.get("open_access") or {}
        primary = w.get("primary_location") or {}
        url = oa.get("oa_url") or primary.get("landing_page_url") or w.get("id", "")
        authors = [
            (a.get("author") or {}).get("display_name", "")
            for a in (w.get("authorships") or [])[:3]
        ]
        results.append({
            "title": w.get("title") or w.get("display_name") or "",
            "authors": [a for a in authors if a],
            "year": w.get("publication_year", ""),
            "url": url,
            "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "is_oa": bool(oa.get("is_oa")),
        })
    return results


def run(message: str = None) -> str:
    if not message:
        return "OpenAlex search ready. Ask me to search OpenAlex for a topic."

    query = message.strip()
    results = search(query)
    if not results:
        return f"No OpenAlex results for: {query}"

    lines = [f"OpenAlex: {query}\n"]
    for r in results:
        authors = ", ".join(r["authors"][:2])
        oa_tag = " [open access]" if r["is_oa"] else ""
        lines.append(f"• {r['title']} ({authors}, {r['year']}){oa_tag}\n  {r['url']}")
    return "\n".join(lines)
