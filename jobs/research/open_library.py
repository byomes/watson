"""jobs/research/open_library.py -- general keyword/topic search against Open
Library's public catalog (openlibrary.org/search.json), no key required.

Prototype only (2026-09-21, Bill's request) -- not wired into the skill
router, bot, or dashboard yet. Standalone module + CLI so it can be tested
directly before deciding whether/how to expose it. Distinct from
jobs/curator/research.py's fetch_open_library_details(), which looks up one
known title+author for spice/description/cover metadata; this is a broad
"what does Open Library have on this topic" search, same shape as
jobs/research/gutenberg.py's search().
"""
import requests

OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
OPEN_LIBRARY_COVER_URL = "https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"

# Open Library asks API consumers to identify themselves with a descriptive
# User-Agent rather than a generic/browser one -- no contact info embedded,
# just enough to identify the app if they ever need to reach out about usage.
_USER_AGENT = "Watson-PersonalAssistant/1.0 (+https://github.com/byomes/watson)"


def search(query: str, limit: int = 5) -> list[dict]:
    """Search Open Library for query, return up to `limit` hits with basic
    metadata. Raises on a failed request (network error, non-2xx status) --
    callers must be able to tell "no matches" (empty list) apart from "the
    request failed," same contract as jobs/research/gutenberg.py's search().
    """
    resp = requests.get(
        OPEN_LIBRARY_SEARCH_URL,
        params={"q": query, "limit": limit},
        headers={"User-Agent": _USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    docs = resp.json().get("docs", [])

    hits = []
    for doc in docs[:limit]:
        cover_i = doc.get("cover_i")
        hits.append({
            "title": doc.get("title", "Untitled"),
            "authors": ", ".join(doc.get("author_name") or []) or "Unknown",
            "first_publish_year": doc.get("first_publish_year"),
            "isbn": (doc.get("isbn") or [None])[0],
            "work_key": doc.get("key"),
            "cover_url": OPEN_LIBRARY_COVER_URL.format(cover_i=cover_i) if cover_i else None,
            # has_fulltext + ia together mean there's a readable/borrowable
            # copy on Internet Archive's own lending platform.
            "borrowable_on_archive": bool(doc.get("has_fulltext") and doc.get("ia")),
        })
    return hits


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "expository preaching"
    for i, hit in enumerate(search(q, limit=5), 1):
        year = hit["first_publish_year"] or "?"
        borrow = " [borrowable on Archive.org]" if hit["borrowable_on_archive"] else ""
        print(f"{i}. {hit['title']} -- {hit['authors']} ({year}){borrow}")
