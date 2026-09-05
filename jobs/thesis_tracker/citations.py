"""jobs/thesis_tracker/citations.py — Check whether the thesis has been cited in
other published work, using free/keyless APIs, and alert on anything new.

Sources:
- OpenAlex (primary): the thesis is already indexed as work id OPENALEX_WORK_ID
  (confirmed by exact title match on 2026-08-22). `filter=cites:<id>` is a real
  reverse-citation lookup — the most reliable signal available here.
- Semantic Scholar (cross-check): resolves the thesis to a paperId by title
  search each run, then reads that paper's /citations endpoint. Free tier is
  globally rate-limited (shared pool, no key) — treated as best-effort; a 429
  or resolution failure just means this source contributes nothing this run,
  it never fails the whole job.
- Crossref (DOI watch, not a citation source): Crossref has no way to do
  reverse-citation lookup for a work without a DOI, and the thesis doesn't
  have one (DMin dissertations on Digital Commons typically aren't assigned
  one). A title/author search on Crossref would only ever find works whose
  own metadata resembles the thesis, which is not useful for "who cites this."
  What it CAN do is notice if Liberty/ProQuest ever mints a DOI for the thesis
  itself — worth knowing on its own, and would unlock stronger lookups later.

Cron: 40 8 * * 6 PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/thesis_tracker/citations.py
(staggered 30 min after the Digital Commons scrape.py pull, same day)
"""
import re
import time
from datetime import datetime, timezone

import requests

from jobs.thesis_tracker import send_telegram
from jobs.thesis_tracker.db import (
    init_db,
    get_known_citation_keys,
    insert_citations,
    get_known_doi,
    insert_doi_watch,
)

OPENALEX_WORK_ID = "W7162058137"
THESIS_TITLE = "Asynchronous Theologetics for A Digital Church"
THESIS_AUTHOR = "Yomes"

REQUEST_TIMEOUT = 20


def _norm_title(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _canonical_key(doi: str | None, title: str | None) -> str:
    if doi:
        return f"doi:{doi.lower().strip()}"
    return f"title:{_norm_title(title)}"


def fetch_openalex_citations() -> list[dict]:
    resp = requests.get(f"https://api.openalex.org/works/{OPENALEX_WORK_ID}", timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("cited_by_count"):
        return []

    resp = requests.get(
        "https://api.openalex.org/works",
        params={"filter": f"cites:{OPENALEX_WORK_ID}", "per-page": 200},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])

    citations = []
    for w in results:
        authors = ", ".join(
            a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])
        ).strip(", ")
        doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
        title = w.get("title") or w.get("display_name")
        citations.append({
            "source": "openalex",
            "external_id": w.get("id"),
            "title": title,
            "authors": authors,
            "venue": (w.get("primary_location") or {}).get("source", {}).get("display_name")
                     if (w.get("primary_location") or {}).get("source") else None,
            "year": w.get("publication_year"),
            "doi": doi,
            "url": (w.get("primary_location") or {}).get("landing_page_url") or w.get("id"),
            "confidence": "high",
        })
    return citations


def _resolve_semantic_scholar_paper_id() -> str | None:
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {"query": THESIS_TITLE, "fields": "title,paperId"}
    for attempt, delay in enumerate((0, 3, 8)):
        if delay:
            time.sleep(delay)
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            continue
        if resp.status_code == 429:
            continue
        if resp.status_code != 200:
            return None
        for candidate in resp.json().get("data", []):
            if _norm_title(candidate.get("title")) == _norm_title(THESIS_TITLE):
                return candidate.get("paperId")
        return None
    return None


def fetch_semantic_scholar_citations() -> list[dict]:
    paper_id = _resolve_semantic_scholar_paper_id()
    if not paper_id:
        return []

    url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}/citations"
    params = {"fields": "title,externalIds,year,authors,venue,url", "limit": 200}
    for attempt, delay in enumerate((0, 3, 8)):
        if delay:
            time.sleep(delay)
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            continue
        if resp.status_code == 429:
            continue
        if resp.status_code != 200:
            return []
        break
    else:
        return []

    citations = []
    for entry in resp.json().get("data", []):
        cp = entry.get("citingPaper") or {}
        authors = ", ".join(a.get("name", "") for a in cp.get("authors", [])).strip(", ")
        doi = (cp.get("externalIds") or {}).get("DOI")
        citations.append({
            "source": "semantic_scholar",
            "external_id": cp.get("paperId"),
            "title": cp.get("title"),
            "authors": authors,
            "venue": cp.get("venue"),
            "year": cp.get("year"),
            "doi": doi,
            "url": cp.get("url"),
            "confidence": "high",
        })
    return citations


def check_doi_watch() -> str | None:
    """Bonus check: has Crossref indexed a DOI for the thesis itself yet?
    Returns the newly found DOI, or None if already known / not found."""
    if get_known_doi():
        return None

    try:
        resp = requests.get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": THESIS_TITLE, "query.author": THESIS_AUTHOR, "rows": 5},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except requests.RequestException:
        return None

    for item in items:
        titles = item.get("title") or []
        if not titles or _norm_title(titles[0]) != _norm_title(THESIS_TITLE):
            continue
        authors = item.get("author") or []
        if not any(THESIS_AUTHOR.lower() in (a.get("family", "") or "").lower() for a in authors):
            continue
        doi = item.get("DOI")
        if doi:
            insert_doi_watch(doi, datetime.now(timezone.utc).isoformat())
            return doi
    return None


def check_citations() -> dict:
    """Pull citations from all sources, insert anything new, and alert.
    Never raises — per-source failures are logged and skipped, not fatal."""
    init_db()
    known_keys = get_known_citation_keys()

    found: dict[str, dict] = {}
    for fetch in (fetch_openalex_citations, fetch_semantic_scholar_citations):
        try:
            results = fetch()
        except Exception as exc:
            print(f"[thesis_tracker.citations] {fetch.__name__} failed: {exc}")
            continue
        for c in results:
            key = _canonical_key(c.get("doi"), c.get("title"))
            existing = found.get(key)
            if existing:
                existing["sources"].add(c["source"])
            else:
                found[key] = {**c, "sources": {c["source"]}}

    now = datetime.now(timezone.utc).isoformat()
    new_citations = []
    for key, c in found.items():
        if key in known_keys:
            continue
        new_citations.append({
            "canonical_key": key,
            "sources": ", ".join(sorted(c["sources"])),
            "confidence": c["confidence"],
            "title": c.get("title"),
            "authors": c.get("authors"),
            "venue": c.get("venue"),
            "year": c.get("year"),
            "doi": c.get("doi"),
            "url": c.get("url"),
            "first_seen_at": now,
        })

    if new_citations:
        insert_citations(new_citations)
        lines = [f"📚 New citation(s) found for your thesis ({len(new_citations)}):", ""]
        for c in new_citations:
            venue = f" — {c['venue']}" if c.get("venue") else ""
            year = f" ({c['year']})" if c.get("year") else ""
            lines.append(f"• {c['title']}{venue}{year}")
            if c.get("authors"):
                lines.append(f"  by {c['authors']}")
            if c.get("url"):
                lines.append(f"  {c['url']}")
        send_telegram("\n".join(lines))

    new_doi = check_doi_watch()
    if new_doi:
        send_telegram(
            f"📄 Your thesis now has a DOI on Crossref: {new_doi}\n"
            "This unlocks more reliable citation lookups going forward."
        )

    return {
        "success": True,
        "new_citations": len(new_citations),
        "new_doi": new_doi,
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    print(check_citations())
