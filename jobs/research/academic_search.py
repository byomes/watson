"""jobs/research/academic_search.py — search arXiv and Semantic Scholar.

Google Scholar is deliberately not a source here -- it has no API, and
scraping it violates its robots.txt and ToS, risking Watson's IP getting
blocked."""
import logging
import os
import re

import requests

log = logging.getLogger(__name__)

SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


def search_arxiv(query: str, max_results: int = 5) -> list:
    try:
        import arxiv
        client = arxiv.Client()
        search = arxiv.Search(query=query, max_results=max_results, sort_by=arxiv.SortCriterion.Relevance)
        results = []
        for paper in client.results(search):
            results.append({
                "title": paper.title,
                "authors": [a.name for a in paper.authors],
                "summary": paper.summary[:300].replace("\n", " "),
                "url": paper.entry_id,
                "published": paper.published.strftime("%Y-%m-%d") if paper.published else "",
            })
        return results
    except Exception as exc:
        log.error("arXiv search failed: %s", exc)
        return []


def search_semantic_scholar(query: str, max_results: int = 5, timeout: int = 10) -> list:
    headers = {}
    api_key = os.getenv("S2_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key

    try:
        resp = requests.get(
            SEMANTIC_SCHOLAR_URL,
            params={
                "query": query,
                "limit": max_results,
                "fields": "title,abstract,authors,year,url,citationCount",
            },
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Semantic Scholar search failed: %s", exc)
        return []

    results = []
    for paper in data.get("data", [])[:max_results]:
        results.append({
            "title": paper.get("title", ""),
            "authors": [a.get("name", "") for a in paper.get("authors", [])],
            "abstract": (paper.get("abstract") or "")[:300],
            "url": paper.get("url", ""),
            "year": paper.get("year", ""),
            "citations": paper.get("citationCount", 0),
        })
    return results


def run(message: str = None) -> str:
    if not message:
        return "Academic search ready. Ask me to search arXiv or Semantic Scholar."

    query = re.sub(r"(?i)(search arxiv|find academic papers|scholarly search|research papers on|search scholar)\s*:?\s*", "", message).strip()
    if not query:
        return "Please provide a search query."

    lines = [f"Academic search: {query}\n"]

    arxiv_results = search_arxiv(query)
    if arxiv_results:
        lines.append("arXiv papers:")
        for p in arxiv_results:
            authors = ", ".join(p["authors"][:2])
            lines.append(f"  • {p['title']} ({authors}, {p['published']})\n    {p['url']}")
    else:
        lines.append("arXiv: no results.")

    s2_results = search_semantic_scholar(query)
    if s2_results:
        lines.append("\nSemantic Scholar:")
        for p in s2_results:
            authors = ", ".join(p["authors"][:2])
            lines.append(f"  • {p['title']} ({authors}, {p['year']}, {p['citations']} citations)\n    {p['url']}")
    else:
        lines.append("\nSemantic Scholar: no results.")

    return "\n".join(lines)
