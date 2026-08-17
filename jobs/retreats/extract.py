"""jobs/retreats/extract.py — turn a fetched listing page into structured fields.

Same never-guess contract as jobs/curator/ingest.py's _extract_book_from_text:
a field the page doesn't clearly state comes back null, never invented.
"""
import logging
import re

import requests
from bs4 import BeautifulSoup

from jobs.retreats import call_ollama, parse_json
from jobs.retreats.discover import robots_allowed, _UA

log = logging.getLogger(__name__)

_TIMEOUT = 20

_SYSTEM = (
    "You extract structured lodging-listing facts from a pastor-retreat directory "
    "page's text. You NEVER guess or infer beyond what the text states — a field "
    "the page doesn't clearly give you comes back null, not your best guess. "
    "Return only valid JSON, no other text."
)

_SCHEMA_INSTRUCTIONS = """Return JSON exactly in this shape:
{{
  "confident": true or false,
  "name": "string or null",
  "location": "string or null (city, state)",
  "price": "string or null (quote it as written, don't compute a number)",
  "capacity": "string or null (as written, e.g. 'sleeps 8')",
  "beds": "string or null",
  "baths": "string or null",
  "amenities": ["short phrase", ...] or [],
  "kitchen_status": "yes" or "no" or "unclear",
  "kitchen_detail": "string or null",
  "phone": "string or null",
  "website": "string or null",
  "email": "string or null",
  "free_or_paid": "free" or "paid" or null
}}

Set confident=false only if the page clearly isn't a specific bookable lodging
listing (e.g. it's a blog post, a staff bio, a generic ministry-info page with no
actual property described). A listing missing most details is still confident=true
with those fields null — thin documentation isn't the same as "not a listing".

"name" is the property/ministry's own name — it is almost always the page title
below (strip any trailing site tagline after a " - " or "|"), even when the body
text never repeats it verbatim afterward. Only leave name null if the page title
itself is generic (e.g. just the site's own name) and the body text never names
the specific property either.

Page title:
{title}

Page text:
{text}"""


def fetch_page_text(url: str) -> tuple[str | None, str | None]:
    """Returns (title, body_text), either None if the fetch/robots check failed."""
    if not robots_allowed(url):
        log.warning("robots.txt disallows %s — skipping", url)
        return None, None
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("fetch failed for %s: %s", url, exc)
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return title, " ".join(text.split())[:6000]


def extract_listing(url: str, title: str | None, page_text: str | None) -> dict | None:
    """Returns a listing dict (never-guess fields) or None if not a confident listing."""
    if not page_text:
        return None

    prompt = _SCHEMA_INSTRUCTIONS.format(title=title or "(none)", text=page_text)
    try:
        raw = call_ollama(_SYSTEM, prompt, timeout=90)
        parsed = parse_json(raw)
    except Exception as exc:
        log.error("extract_listing Ollama call failed for %s: %s", url, exc)
        return None

    if not parsed or not parsed.get("confident"):
        return None

    if not parsed.get("name"):
        # The model can correctly judge a page confident=true (real listing,
        # clearly named in the page title) but still fail to copy that name
        # into the JSON field — confirmed live 2026-08-16 against a real page
        # (Faith Mountain Ministries, Inc.) that had a clean, unambiguous
        # title and every other field extracted correctly, yet name kept
        # coming back null. Rather than keep tuning the prompt to hope the
        # model echoes it reliably, fall back to the page's own title —
        # stripped of a trailing " - tagline" / "| tagline" site suffix —
        # which is a name a human reading this page would recognize as
        # correct, not a guess at content the page didn't state.
        if not title:
            return None
        cleaned = re.split(r"\s+[-|]\s+", title, maxsplit=1)[0].strip()
        if not cleaned:
            return None
        parsed["name"] = cleaned

    parsed["source_url"] = url
    return parsed
