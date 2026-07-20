"""jobs/curator/ingest.py — turn a text/image/link submission into a `pending` book row.

Never guesses. Ambiguous or under-evidenced submissions become `needs_review`
with no spice_rating set, per CURATOR spec.
"""
import base64
import json
import logging
import re
from urllib.parse import urlparse

import requests

from jobs.curator import get_db, send_telegram
from jobs.curator.research import OLLAMA_URL, call_ollama, parse_json, research_book

log = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_VISION_MODEL = "moondream"

_DOMAIN_TYPES = (
    ("tiktok.com", "tiktok"),
    ("instagram.com", "instagram"),
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("goodreads.com", "goodreads"),
    ("amazon.com", "amazon"),
)

_MINOR_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "off", "on", "onto", "or", "out", "over", "per", "so", "the",
    "to", "up", "via", "vs", "vs.", "with", "yet",
}


def title_case(text: str) -> str:
    """Real title-case: capitalizes major words, lowercases articles/
    prepositions/short conjunctions unless they're the first/last word or
    follow a colon. Leaves words that already look intentionally cased alone
    (e.g. "McDonald", "iPhone", all-caps acronyms)."""
    if not text:
        return text
    words = text.split(" ")
    n = len(words)
    result = []
    for i, word in enumerate(words):
        if not word:
            result.append(word)
            continue
        is_edge = i == 0 or i == n - 1
        prev_ends_colon = i > 0 and words[i - 1].endswith(":")
        stripped = word.strip(".,!?;:'\"")
        if not is_edge and not prev_ends_colon and stripped.lower() in _MINOR_WORDS:
            result.append(word.lower())
        else:
            result.append(_capitalize_word(word))
    return " ".join(result)


def _capitalize_word(word: str) -> str:
    if len(word) > 1 and any(c.isupper() for c in word[1:]) and not word.isupper():
        return word  # already intentionally cased — McDonald, iPhone, etc.
    parts = re.split(r"(-)", word)
    return "".join(p if p == "-" else (p[:1].upper() + p[1:].lower()) for p in parts)


def _classify_link(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for domain, source_type in _DOMAIN_TYPES:
        if domain in host:
            return source_type
    return "other"


def _ocr_cover(image_bytes: bytes) -> dict:
    """Read title/author off a cover photo via a local vision model (moondream)."""
    b64 = base64.b64encode(image_bytes).decode()
    prompt = (
        "Look at this book cover. Respond on one line exactly as: "
        "Title: <title> | Author: <author>\n"
        "If you cannot clearly read a title, respond exactly: Title: UNKNOWN | Author: UNKNOWN"
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": _VISION_MODEL, "prompt": prompt, "images": [b64], "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = (resp.json().get("response") or "").strip()
    except Exception as exc:
        log.error("cover OCR failed: %s", exc)
        return {"title": None, "author": None, "raw_text": f"OCR error: {exc}"}

    match = re.search(r"Title:\s*(.+?)\s*\|\s*Author:\s*(.+)", raw, re.IGNORECASE)
    if not match:
        return {"title": None, "author": None, "raw_text": raw}

    title = match.group(1).strip()
    author = match.group(2).strip()
    if title.upper() == "UNKNOWN":
        title = None
    if author.upper() == "UNKNOWN":
        author = None
    return {"title": title, "author": author, "raw_text": raw}


def fetch_og_metadata(url: str) -> dict:
    """og:title / og:description scrape, or YouTube oEmbed where a full fetch works better."""
    if "youtube.com" in url or "youtu.be" in url:
        try:
            resp = requests.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "title": data.get("title", ""),
                "raw_text": f"{data.get('title', '')} — {data.get('author_name', '')}",
            }
        except Exception as exc:
            log.warning("YouTube oEmbed failed for %s: %s", url, exc)

    try:
        resp = requests.get(url, headers=_UA, timeout=10)
        html = resp.text
        og_title = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', html
        )
        og_desc = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']', html
        )
        title = og_title.group(1).strip() if og_title else ""
        desc = og_desc.group(1).strip() if og_desc else ""
        return {"title": title, "raw_text": f"{title}\n{desc}".strip()}
    except Exception as exc:
        log.warning("og-metadata fetch failed for %s: %s", url, exc)
        return {"title": "", "raw_text": f"fetch failed: {exc}"}


def _extract_book_from_text(raw_text: str) -> dict:
    """LLM pass: pull a candidate book title/author out of a caption/description.
    Returns {"title": str|None, "author": str|None, "confident": bool}."""
    if not raw_text or not raw_text.strip():
        return {"title": None, "author": None, "confident": False}

    system = (
        "You extract a book title and author from a social media caption or page title. "
        "You NEVER guess — if the text doesn't clearly name one specific book, you say so. "
        "Return only valid JSON, no other text."
    )
    prompt = f"""Text:
{raw_text[:1500]}

Return JSON exactly in this shape:
{{"confident": true or false, "title": "string or null", "author": "string or null"}}

Set confident=false if the text is ambiguous, names multiple books, or doesn't clearly
identify one specific book."""

    try:
        raw = call_ollama(system, prompt)
        parsed = parse_json(raw)
    except Exception as exc:
        log.error("_extract_book_from_text Ollama call failed: %s", exc)
        parsed = None

    if not parsed or not parsed.get("confident") or not parsed.get("title"):
        return {"title": None, "author": None, "confident": False}

    return {"title": parsed["title"], "author": parsed.get("author"), "confident": True}


def extract_multiple_books_from_text(raw_text: str) -> dict:
    """LLM pass for a 'book haul'/wrap-up post that may mention several books. Never
    guesses — a title only makes it into confident_titles if it's clearly and
    specifically named; anything else is described in uncertain_note for a human to
    search manually (see jobs.curator.worker's reel_link handling).
    Returns {"confident_titles": [{"title","author"}], "uncertain_note": str}."""
    if not raw_text or not raw_text.strip():
        return {"confident_titles": [], "uncertain_note": "No text could be extracted from the link."}

    system = (
        "You extract book titles and authors mentioned in a social media caption or post "
        "about multiple books (a book haul, wrap-up, or recommendation reel). You NEVER "
        "guess — only list a title if it is clearly and specifically named, not merely "
        "implied or ambiguous. Return only valid JSON, no other text."
    )
    prompt = f"""Text:
{raw_text[:2000]}

Return JSON exactly in this shape:
{{
  "confident_titles": [{{"title": "string", "author": "string or null"}}, ...],
  "uncertain_note": "plain description of anything you saw but couldn't confidently identify as a specific book, or empty string if everything was clear"
}}"""

    try:
        raw = call_ollama(system, prompt)
        parsed = parse_json(raw)
    except Exception as exc:
        log.error("extract_multiple_books_from_text Ollama call failed: %s", exc)
        parsed = None

    if not parsed:
        return {"confident_titles": [], "uncertain_note": raw_text[:1000]}

    titles = parsed.get("confident_titles") or []
    valid_titles = [t for t in titles if isinstance(t, dict) and t.get("title")]
    return {
        "confident_titles": valid_titles,
        "uncertain_note": parsed.get("uncertain_note", ""),
    }


def _create_book(
    *, title: str, author: str, status: str, added_by, series=None, spice_rating=None,
    spice_notes="", page_count=None, kindle_unlimited=False,
    cover_image_url=None, description=None, series_number=None, series_total=None,
) -> int:
    # series_number is Phase 1's existing column; research's "series_position" (from
    # Amazon/Goodreads) fills the same slot — no separate column for the same concept.
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO books (title, author, series, series_number, series_total, "
            "spice_rating, spice_notes, page_count, kindle_unlimited, "
            "kindle_unlimited_checked_at, cover_image_url, description, status, added_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?)",
            (title, author, series, series_number, series_total, spice_rating,
             spice_notes, page_count, int(bool(kindle_unlimited)),
             cover_image_url, description, status, added_by),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _add_source(book_id: int, source_type: str, url: str | None, raw_text: str | None) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO book_sources (book_id, type, url, raw_extracted_text) VALUES (?, ?, ?, ?)",
            (book_id, source_type, url, raw_text),
        )
        conn.commit()
    finally:
        conn.close()


def _add_spice_findings(book_id: int, findings: list[dict]) -> None:
    """Persist the attributed, verbatim findings gathered in research.py —
    these are what the detail page quotes directly, never re-worded here."""
    if not findings:
        return
    conn = get_db()
    try:
        conn.executemany(
            "INSERT INTO spice_findings (book_id, source_name, source_type, rank, excerpt, url) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (book_id, f["source_name"], f["source_type"], f["rank"], f["excerpt"], f["url"])
                for f in findings
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _derive_spice_notes(findings: list[dict]) -> str:
    """Short quick-glance note for the pending-queue card — still the source's
    own words (attributed, truncated), not a Watson-authored paragraph."""
    if not findings:
        return ""
    top = findings[0]
    excerpt = top["excerpt"]
    if len(excerpt) > 140:
        excerpt = excerpt[:140].rsplit(" ", 1)[0] + "…"
    return f"{top['source_name']}: {excerpt}"


def _notify(book_id: int, title: str, author: str, status: str, spice_rating, source_urls: list[str]) -> None:
    from jobs.telegram.pending import store_pending_action

    if status == "needs_review":
        text = (
            f"📚 Curator — needs review\n\n{title}"
            + (f" by {author}" if author else "")
            + "\n\nCouldn't confidently identify this book or its content rating. "
            "Open the Curator app's Pending queue to fill in details."
        )
        send_telegram(text)
        return

    scale = ["Clean", "Kissing Only", "Closed Door", "Fade to Black", "Open Door", "Explicit"]
    rating_label = scale[spice_rating] if spice_rating is not None and 0 <= spice_rating <= 5 else "unrated"
    sources_text = "\n".join(source_urls) if source_urls else "(no source links)"
    text = (
        f"📚 Curator — found a book\n\n{title} by {author}\n"
        f"Proposed rating: {spice_rating} ({rating_label})\n\n"
        f"Sources:\n{sources_text}\n\nApprove / Edit / Reject?"
    )
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"cur_approve:{book_id}"},
            {"text": "✏️ Edit", "callback_data": f"cur_edit:{book_id}"},
            {"text": "🚫 Reject", "callback_data": f"cur_reject:{book_id}"},
        ]]
    }
    message_id = send_telegram(text, reply_markup=keyboard)
    if message_id:
        store_pending_action("curator_edit", message_id, {"source_db": "curator", "book_id": book_id})


def ingest_submission(
    *,
    submitted_by=None,
    title: str | None = None,
    author: str | None = None,
    series: str | None = None,
    link: str | None = None,
    image_bytes: bytes | None = None,
    image_mimetype: str | None = None,
    notify_telegram: bool = True,
) -> dict:
    """Entry point. Exactly one of (title given), link, or image_bytes drives identification.

    notify_telegram=False suppresses the per-book Approve/Edit/Reject message — used for
    batch items, where the batch-completion SMS is the "done" signal instead (avoids
    spamming one Telegram message per book in a multi-book batch)."""
    source_type = "other"
    source_url = None
    raw_text = None

    if image_bytes:
        source_type = "screenshot"
        if not title:
            ocr = _ocr_cover(image_bytes)
            title = title or ocr["title"]
            author = author or ocr["author"]
            raw_text = ocr["raw_text"]
    elif link:
        source_type = _classify_link(link)
        source_url = link
        meta = fetch_og_metadata(link)
        raw_text = meta["raw_text"]
        if not title:
            extracted = _extract_book_from_text(raw_text)
            if extracted["confident"]:
                title = extracted["title"]
                author = author or extracted["author"]
    elif not title:
        raise ValueError("must provide title, link, or image_bytes")

    if not title:
        book_id = _create_book(
            title="Unknown", author=author or "Unknown", series=series,
            status="needs_review", added_by=submitted_by,
        )
        _add_source(book_id, source_type, source_url, raw_text)
        if notify_telegram:
            _notify(book_id, "Unknown", author or "", "needs_review", None, [])
        return {"status": "needs_review", "book_id": book_id, "reason": "could not identify a book title"}

    title = title_case(title)
    author = author or "Unknown"

    research = research_book(title, author if author != "Unknown" else None)
    series = series or research.get("series_name")
    # Backfill author from search results the same way series_name already does —
    # only when the user didn't supply one, and only if 2+ independent sources agree
    # (extract_author_from_titles never guesses off a single mention).
    if author == "Unknown" and research.get("author"):
        author = research["author"]

    findings = research.get("findings", [])

    if not research["confident"]:
        book_id = _create_book(
            title=title, author=author, series=series, status="needs_review", added_by=submitted_by,
            spice_notes=_derive_spice_notes(findings),
            page_count=research.get("page_count"), kindle_unlimited=research.get("kindle_unlimited", False),
            cover_image_url=research.get("cover_image_url"), description=research.get("description"),
            series_number=research.get("series_position"), series_total=research.get("series_total"),
        )
        if source_type or source_url or raw_text:
            _add_source(book_id, source_type, source_url, raw_text)
        for s in research.get("sources", []):
            _add_source(book_id, s["type"], s["url"], None)
        _add_spice_findings(book_id, findings)
        if notify_telegram:
            _notify(book_id, title, author, "needs_review", None, [])
        return {
            "status": "needs_review", "book_id": book_id,
            "reason": research.get("reason", "insufficient evidence for a confident rating"),
        }

    book_id = _create_book(
        title=title, author=author, series=series, status="pending", added_by=submitted_by,
        spice_rating=research["spice_rating"], spice_notes=_derive_spice_notes(findings),
        page_count=research.get("page_count"), kindle_unlimited=research.get("kindle_unlimited", False),
        cover_image_url=research.get("cover_image_url"), description=research.get("description"),
        series_number=research.get("series_position"), series_total=research.get("series_total"),
    )
    if source_type or source_url or raw_text:
        _add_source(book_id, source_type, source_url, raw_text)
    source_urls = []
    for s in research.get("sources", []):
        _add_source(book_id, s["type"], s["url"], None)
        source_urls.append(s["url"])
    _add_spice_findings(book_id, findings)

    if notify_telegram:
        _notify(book_id, title, author, "pending", research["spice_rating"], source_urls)

    return {"status": "pending", "book_id": book_id, "spice_rating": research["spice_rating"]}
