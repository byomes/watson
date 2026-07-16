"""jobs/research/gutenberg.py — Project Gutenberg research via Gutendex, approval-gated
ingestion into a separate 'gutenberg' ChromaDB collection (kept isolated from 'sermons').

Gutendex is used for metadata/URLs only — self-hosted locally by default (see
GUTENDEX_BASE_URL in .env), since the public gutendex.com API is blocked by a Cloudflare
challenge (bug_tracker #11). The actual book text is always downloaded directly from the
URL Gutendex supplies (typically gutenberg.org), never proxied through Gutendex.
"""
import logging
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.database import get_connection
from jobs.build_kb import ingest_dir

load_dotenv()

log = logging.getLogger(__name__)

GUTENDEX_BASE_URL = os.getenv("GUTENDEX_BASE_URL", "http://127.0.0.1:8010")
GUTENDEX_BOOKS_URL = f"{GUTENDEX_BASE_URL}/books"
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = BASE_DIR / "kb" / "documents" / "gutenberg"
COLLECTION_NAME = "gutenberg"

_PG_START_RE = re.compile(
    r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
    re.IGNORECASE | re.DOTALL,
)
_PG_END_RE = re.compile(
    r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*",
    re.IGNORECASE | re.DOTALL,
)


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gutenberg_books (
                id             INTEGER PRIMARY KEY,
                title          TEXT NOT NULL,
                author         TEXT,
                year           INTEGER,
                download_count INTEGER,
                ingested_at    TEXT NOT NULL DEFAULT (datetime('now')),
                file_path      TEXT NOT NULL
            )
        """)


_bootstrap()


def _already_ingested(book_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, author, year, download_count, file_path "
            "FROM gutenberg_books WHERE id = ?",
            (book_id,),
        ).fetchone()
    return dict(row) if row else None


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "untitled"


def _plain_text_url(formats: dict) -> str | None:
    if "text/plain; charset=utf-8" in formats:
        return formats["text/plain; charset=utf-8"]
    for mime, url in formats.items():
        if mime.startswith("text/plain"):
            return url
    return None


def _parse_book(book: dict) -> dict | None:
    text_url = _plain_text_url(book.get("formats", {}))
    if not text_url:
        return None
    authors = ", ".join(a.get("name", "") for a in book.get("authors", [])) or "Unknown"
    return {
        "id": book["id"],
        "title": book.get("title", "Untitled"),
        "authors": authors,
        # Gutendex exposes author birth/death years, not the book's first-publish year —
        # there is no reliable publish-year field in this API, so leave it unset rather
        # than fabricate one from author metadata.
        "year": None,
        "download_count": book.get("download_count", 0),
        "text_url": text_url,
    }


def search(query: str, limit: int = 5) -> list[dict]:
    """Search Gutendex for query, return up to `limit` hits with metadata + plain-text URL.

    Raises on a failed request (network error, non-2xx status) rather than swallowing it —
    callers must be able to tell "no matches" (empty list) apart from "the request failed."
    """
    resp = requests.get(GUTENDEX_BOOKS_URL, params={"search": query}, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    hits = []
    for book in data.get("results", []):
        parsed = _parse_book(book)
        if parsed:
            hits.append(parsed)
        if len(hits) >= limit:
            break
    return hits


def _fetch_book(book_id: int) -> dict | None:
    """Look up a single book's metadata/URL by id via Gutendex's /books/{id} endpoint.

    Raises on a failed request. Returns None only for the non-error case where Gutendex
    has the book but offers no usable plain-text edition.
    """
    resp = requests.get(f"{GUTENDEX_BOOKS_URL}/{book_id}", timeout=15)
    resp.raise_for_status()
    return _parse_book(resp.json())


def _strip_license_boilerplate(text: str) -> str:
    start_match = _PG_START_RE.search(text)
    if start_match:
        text = text[start_match.end():]
    end_match = _PG_END_RE.search(text)
    if end_match:
        text = text[:end_match.start()]
    return text.strip()


def download_and_ingest(book_id: int) -> dict:
    """Download the plain-text edition for book_id, strip PG boilerplate, save to
    kb/documents/gutenberg/, and ingest into the 'gutenberg' ChromaDB collection.

    Never re-downloads or re-ingests an id already present in gutenberg_books.
    Returns {"ok": bool, "title": str, "chunks_added": int, "already_ingested": bool, "error": str|None}.
    """
    existing = _already_ingested(book_id)
    if existing:
        return {
            "ok": True,
            "title": existing["title"],
            "chunks_added": 0,
            "already_ingested": True,
            "error": None,
        }

    try:
        book = _fetch_book(book_id)
    except Exception as exc:
        log.error("Gutendex lookup failed for id=%s: %s", book_id, exc)
        return {"ok": False, "title": None, "chunks_added": 0, "already_ingested": False,
                 "error": f"Could not fetch book metadata from Gutendex: {exc}"}
    if not book:
        return {"ok": False, "title": None, "chunks_added": 0, "already_ingested": False,
                 "error": "Gutendex has no usable plain-text edition for this book."}

    try:
        text_resp = requests.get(book["text_url"], timeout=60)
        text_resp.raise_for_status()
        text_resp.encoding = text_resp.encoding or "utf-8"
        raw_text = text_resp.text
    except Exception as exc:
        log.error("Text download failed for id=%s: %s", book_id, exc)
        return {"ok": False, "title": book["title"], "chunks_added": 0, "already_ingested": False,
                 "error": f"Text download failed: {exc}"}

    cleaned = _strip_license_boilerplate(raw_text)
    if not cleaned:
        return {"ok": False, "title": book["title"], "chunks_added": 0, "already_ingested": False,
                 "error": "Downloaded text was empty after stripping boilerplate."}

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = DOCS_DIR / f"{book_id}-{_slugify(book['title'])}.txt"
    file_path.write_text(cleaned, encoding="utf-8")

    chunks_added = ingest_dir(DOCS_DIR, COLLECTION_NAME)

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO gutenberg_books (id, title, author, year, download_count, file_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (book_id, book["title"], book["authors"], book["year"], book["download_count"], str(file_path)),
        )

    return {
        "ok": True,
        "title": book["title"],
        "chunks_added": chunks_added,
        "already_ingested": False,
        "error": None,
    }
