"""jobs/research/isbn_lookup.py — look up book metadata by ISBN or title."""
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]


def lookup_isbn(isbn: str) -> dict:
    import isbnlib
    clean = isbnlib.clean(isbn)
    if not (isbnlib.is_isbn10(clean) or isbnlib.is_isbn13(clean)):
        return {"success": False, "error": f"Invalid ISBN: {isbn}"}
    try:
        meta = isbnlib.meta(clean)
        cover = isbnlib.cover(clean) or {}
        return {
            "success": True,
            "isbn": clean,
            "isbn13": isbnlib.to_isbn13(clean),
            "title": meta.get("Title", ""),
            "authors": meta.get("Authors", []),
            "year": meta.get("Year", ""),
            "publisher": meta.get("Publisher", ""),
            "language": meta.get("Language", ""),
            "cover_url": cover.get("thumbnail", ""),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def search_by_title(query: str, limit: int = 5) -> list:
    import isbnlib
    try:
        results = isbnlib.goom(query)
        books = []
        for item in results[:limit]:
            clean = isbnlib.clean(item.get("isbn13", "") or item.get("isbn10", ""))
            books.append({
                "isbn": clean,
                "title": item.get("title", ""),
                "authors": item.get("authors", []),
                "year": item.get("year", ""),
                "publisher": item.get("publisher", ""),
            })
        return books
    except Exception as exc:
        log.warning("ISBN title search failed: %s", exc)
        return []


def run(message: str = None) -> str:
    if not message:
        return "Usage: isbn lookup <ISBN> or isbn search <title>"

    msg = message.strip()

    # Detect ISBN pattern
    isbn_match = re.search(r'(?:97[89])?\d[\d\-]{8,}[\dX]', msg.replace(" ", ""))
    if isbn_match:
        result = lookup_isbn(isbn_match.group())
        if not result["success"]:
            return f"ISBN lookup failed: {result['error']}"
        authors = ", ".join(result["authors"])
        lines = [
            f"Title: {result['title']}",
            f"Authors: {authors}",
            f"Year: {result['year']}",
            f"Publisher: {result['publisher']}",
            f"ISBN-13: {result['isbn13']}",
            f"Language: {result['language']}",
        ]
        if result.get("cover_url"):
            lines.append(f"Cover: {result['cover_url']}")
        return "\n".join(lines)

    # Search by title
    query = re.sub(r'^(isbn\s+)?(search|find|lookup)\s+', '', msg, flags=re.IGNORECASE).strip()
    if not query:
        return "Usage: isbn lookup <ISBN> or isbn search <title>"

    books = search_by_title(query)
    if not books:
        return f"No books found for: {query}"

    lines = [f"Books matching '{query}':"]
    for b in books:
        authors = ", ".join(b["authors"])
        lines.append(f"\n  {b['title']} ({b['year']})")
        lines.append(f"    By: {authors}")
        lines.append(f"    Publisher: {b['publisher']}")
        lines.append(f"    ISBN: {b['isbn']}")
    return "\n".join(lines)
