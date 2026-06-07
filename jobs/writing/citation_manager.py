"""jobs/writing/citation_manager.py — fetch, format, and store citations."""
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]
CITATIONS_FILE = REPO / "data" / "citations.json"


def _load_citations() -> list:
    if CITATIONS_FILE.exists():
        try:
            return json.loads(CITATIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_citations(citations: list) -> None:
    CITATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CITATIONS_FILE.write_text(json.dumps(citations, indent=2), encoding="utf-8")


def _fetch_doi(doi: str) -> dict:
    from habanero import Crossref
    cr = Crossref()
    result = cr.works(ids=doi)
    item = result["message"]
    authors = []
    for a in item.get("author", []):
        name = a.get("family", "")
        if a.get("given"):
            name = f"{name}, {a['given']}"
        authors.append(name)
    date_parts = item.get("published", {}).get("date-parts", [[None]])[0]
    year = str(date_parts[0]) if date_parts and date_parts[0] else ""
    return {
        "doi": doi,
        "title": item.get("title", [""])[0],
        "authors": authors,
        "year": year,
        "journal": item.get("container-title", [""])[0],
        "volume": item.get("volume", ""),
        "issue": item.get("issue", ""),
        "pages": item.get("page", ""),
        "publisher": item.get("publisher", ""),
        "url": item.get("URL", ""),
        "type": "article",
    }


def _fetch_isbn(isbn: str) -> dict:
    import isbnlib
    clean = isbnlib.clean(isbn)
    meta = isbnlib.meta(clean)
    return {
        "isbn": clean,
        "title": meta.get("Title", ""),
        "authors": meta.get("Authors", []),
        "year": meta.get("Year", ""),
        "publisher": meta.get("Publisher", ""),
        "language": meta.get("Language", ""),
        "type": "book",
    }


def _format_chicago(ref: dict) -> str:
    if ref.get("type") == "book":
        authors = " and ".join(ref.get("authors", []))
        year = ref.get("year", "")
        title = ref.get("title", "")
        publisher = ref.get("publisher", "")
        return f'{authors}. {year}. *{title}*. {publisher}.'
    else:
        authors = ", ".join(ref.get("authors", []))
        year = ref.get("year", "")
        title = ref.get("title", "")
        journal = ref.get("journal", "")
        vol = ref.get("volume", "")
        issue = ref.get("issue", "")
        pages = ref.get("pages", "")
        doi = ref.get("doi", "")
        loc = f"{vol}({issue}):{pages}" if vol and issue else pages
        doi_str = f" https://doi.org/{doi}" if doi else ""
        return f'{authors}. {year}. "{title}." *{journal}* {loc}.{doi_str}'


def add_citation(identifier: str) -> str:
    citations = _load_citations()
    identifier = identifier.strip()
    doi_pattern = r'^10\.\d{4,}/'
    isbn_pattern = r'^(?:97[89])?\d{9}[\dX]$'

    try:
        if re.match(doi_pattern, identifier):
            ref = _fetch_doi(identifier)
        elif re.match(isbn_pattern, identifier.replace("-", "").replace(" ", "")):
            ref = _fetch_isbn(identifier)
        else:
            return f"Unrecognized identifier: {identifier}. Use a DOI (10.xxx/...) or ISBN."

        citations.append(ref)
        _save_citations(citations)
        formatted = _format_chicago(ref)
        return f"Added citation:\n{formatted}"
    except Exception as exc:
        return f"Failed to fetch citation for {identifier}: {exc}"


def list_citations() -> str:
    citations = _load_citations()
    if not citations:
        return "No citations saved."
    lines = [f"Saved citations ({len(citations)}):"]
    for i, ref in enumerate(citations, 1):
        lines.append(f"\n[{i}] {_format_chicago(ref)}")
    return "\n".join(lines)


def export_citations(fmt: str = "chicago") -> str:
    citations = _load_citations()
    if not citations:
        return "No citations to export."
    entries = [_format_chicago(ref) for ref in citations]
    return "\n\n".join(f"[{i+1}] {e}" for i, e in enumerate(entries))


def run(message: str = None) -> str:
    if not message:
        return list_citations()

    msg = message.strip()
    if msg.lower() in ("list", "show", "all"):
        return list_citations()
    if msg.lower().startswith("export"):
        return export_citations()

    # Check for DOI or ISBN
    doi_match = re.search(r'10\.\d{4,}/\S+', msg)
    isbn_match = re.search(r'(?:97[89])?\d[\d\-]{9,}[\dX]', msg.replace(" ", ""))

    if doi_match:
        return add_citation(doi_match.group())
    if isbn_match:
        return add_citation(isbn_match.group())

    return f"Usage: add citation <DOI or ISBN>, or 'list citations'"
