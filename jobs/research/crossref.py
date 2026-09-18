"""jobs/research/crossref.py — CrossRef scholarly metadata search (free, no API key).

CrossRef indexes DOI-registered works (journal articles, books, book chapters)
across virtually every publisher. Landing pages are often paywalled, but
titles/authors/DOIs are useful as leads and the URL is worth a fetch attempt
since many resolve to an open abstract or full text page.
"""
import logging

import requests

log = logging.getLogger(__name__)

CROSSREF_URL = "https://api.crossref.org/works"


def search(query: str, max_results: int = 5, timeout: int = 10) -> list[dict]:
    try:
        resp = requests.get(
            CROSSREF_URL,
            params={"query": query, "rows": max_results},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("CrossRef search failed: %s", exc)
        return []

    results = []
    for item in data.get("message", {}).get("items", [])[:max_results]:
        title = (item.get("title") or [""])[0]
        authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in item.get("author", [])[:3]
        ]
        year = ""
        date_parts = (item.get("published") or item.get("published-print") or item.get("published-online") or {}).get("date-parts")
        if date_parts and date_parts[0]:
            year = date_parts[0][0]
        results.append({
            "title": title,
            "authors": [a for a in authors if a],
            "year": year,
            "url": item.get("URL", ""),
            "doi": item.get("DOI", ""),
            "type": item.get("type", ""),
        })
    return results


def run(message: str = None) -> str:
    if not message:
        return "CrossRef search ready. Ask me to search CrossRef for a topic."

    query = message.strip()
    results = search(query)
    if not results:
        return f"No CrossRef results for: {query}"

    lines = [f"CrossRef: {query}\n"]
    for r in results:
        authors = ", ".join(r["authors"][:2])
        lines.append(f"• {r['title']} ({authors}, {r['year']})\n  {r['url']}")
    return "\n".join(lines)
