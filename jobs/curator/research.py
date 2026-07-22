"""jobs/curator/research.py — spice rating / KU / page-count research pass.

Never guesses, and never blends. Spice content is *extracted*, not
synthesized: each trusted source (see _EXACT_DOMAINS / _categorize_source)
gets a full-page fetch. Common Sense Media writes prose, so its excerpt comes
from a keyword-window pull (_extract_relevant_excerpt). romance.io and
SpicyBooks publish their own numeric spice-scale rating directly, so those
try a structured-pattern match first (_extract_structured_rating) and only
fall back to the keyword-window approach if that pattern isn't found. No LLM
paraphrasing step touches the wording the detail page shows either way. The
0-5 spice_rating number is still a judgment call, made by an Ollama pass that
weighs the extracted findings, and only fires when >=_MIN_FINDINGS real
findings were gathered and the model reports confidence — otherwise
needs_review with no rating.
"""
import json
import logging
import re
from urllib.parse import urlparse

import requests

from jobs.research.web_search import search as serper_search

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"  # accuracy-sensitive background job — see LLM Stack in WATSON_ARCHITECTURE.md

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_MIN_FINDINGS = 1
_MAX_DISPLAYED_FINDINGS = 4

# Trusted sources for spice-content research, highest priority first. Narrowed
# 2026-07-21 to exactly these three after live-refetch testing showed Plugged
# In / Book Trigger Warnings / Goodreads Q&A / Reddit / clean-romance-blog
# results were unreliable or noisy (Book Trigger Warnings 403'd on every
# attempt; Reddit blocked every attempt; several Goodreads Q&A "findings" were
# just the bare question restated, no answer; two Plugged In hits anchored on
# site nav text instead of real content). Common Sense Media writes prose and
# gets the keyword-window extraction; romance.io and SpicyBooks publish their
# own numeric spice-scale rating directly and get a structured-pattern match
# first (see _extract_structured_rating), falling back to the keyword-window
# approach only if that pattern isn't found.
#
# romance.io stays in this list so _categorize_source() can still recognize it
# if a URL for it ever surfaces, but it's DORMANT as of 2026-07-21 — see
# _ACTIVE_SEARCH_DOMAINS below and the _ROMANCE_IO_PATTERN comment. It is
# deliberately excluded from the active search.
_EXACT_DOMAINS = [
    ("commonsensemedia.org", "Common Sense Media", "commonsensemedia", 1),
    ("romance.io", "romance.io", "romance_io", 2),
    ("spicybooks.org", "SpicyBooks", "spicybooks", 3),
]

# Domains search_top_content_sites() actually queries. romance.io is left out —
# it sits behind a site-wide Cloudflare JS challenge (confirmed 2026-07-21,
# see _ROMANCE_IO_PATTERN below) so every fetch of it fails anyway, and testing
# showed its SEO-heavy duplicate URLs (tracking-parameter variants, /similar
# pages, unrelated same-author results) were crowding spicybooks.org out of a
# combined query's result cap entirely, even for titles with a real, working
# SpicyBooks page (confirmed with "Beach Read": a combined query returned 4
# romance.io URLs, 3 of them near-duplicates for the same book, and zero
# spicybooks.org hits).
_ACTIVE_SEARCH_DOMAINS = tuple(d for d, *_ in _EXACT_DOMAINS if d != "romance.io")

_SPICE_KEYWORDS = [
    "spice rating", "spice level", "sexual content", "sex scene", "content warning",
    "trigger warning", "explicit", "closed door", "open door", "fade to black",
    "making out", "make out", "kissing", "intimate", "intimacy", "bedroom",
    "mature content", "steamy", "romance content",
]

# Structured-rating patterns for sources that publish their own numeric spice
# scale directly rather than prose — tried first for those sources, with
# _extract_relevant_excerpt() as a fallback if the pattern isn't found.
#
# romance.io: DORMANT as of 2026-07-21 — the site sits behind a site-wide
# Cloudflare JS challenge (`cf-mitigated: challenge` on every request, confirmed
# 2026-07-21 including with full browser headers), which plain `requests`
# cannot solve regardless of User-Agent — the same dead-source problem Book
# Trigger Warnings had, just from a different blocking mechanism. Left in place
# unused (not wired into the active search — see _ACTIVE_SEARCH_DOMAINS) so
# it's easy to re-enable if/when a headless-browser fetch path exists (backlog:
# FlareSolverr). This pattern is also UNVERIFIED against a real fetched page —
# written from the described "Spice/Steam/Heat level: X/5 - Label" format only,
# since no page was ever successfully fetched to confirm it.
_ROMANCE_IO_PATTERN = re.compile(
    r"(?:Spice|Steam|Heat)\s*level:?\s*(\d)/5\s*-\s*([^.\n]+)",
    re.IGNORECASE,
)
# Confirmed against real fetched pages (spicybooks.org/books/beach-read,
# /heated-rivalry, /a-court-of-thorns-and-roses) on 2026-07-21. SpicyBooks'
# FAQ-accordion body text reliably reads either "<Title> has a spice level of
# N/5 on our scale, which we rate as &quot;<Label>.&quot;" or "...is rated N
# out of 5 on the SpicyBooks spice scale. This means it's &quot;<Label>&quot;"
# — entity-escaped quotes survive _strip_html() as the literal string
# "&quot;", not real quote characters, since _strip_html() doesn't decode that
# entity (only &nbsp;/&amp;/&#39; are decoded).
_SPICYBOOKS_PATTERN = re.compile(
    r'(?:has a spice level of|is rated)\s+(\d)(?:/5|\s+out of 5)[^"&]*?'
    r"(?:rate as|this means it.s)\s*&quot;([^&\".]+)",
    re.IGNORECASE,
)


def call_ollama(system: str, prompt: str, timeout: int = 90, options: dict | None = None) -> str:
    payload = {"model": MODEL, "system": system, "prompt": prompt, "stream": False}
    if options:
        payload["options"] = options
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return (resp.json().get("response") or "").strip()


def parse_json(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ── Trusted-source discovery + extraction ───────────────────────────────────

def _categorize_source(url: str) -> tuple[str, str, int] | None:
    """Returns (source_name, source_type, rank) if url matches one of the three
    trusted domains, else None. Lower rank = higher priority."""
    host = urlparse(url).netloc.lower()
    for domain, name, stype, rank in _EXACT_DOMAINS:
        if domain in host:
            return (name, stype, rank)
    return None


# Path substring that marks a URL as a specific book page (vs. a domain root,
# tropes/category listing, author page, etc.) for each active source_type —
# used by gather_spice_findings() to prefer the real book page when a search
# returns multiple URLs for the same domain.
_BOOK_PAGE_HINTS = {
    "commonsensemedia": "/book-reviews/",
    "spicybooks": "/books/",
}


def _looks_like_book_page(stype: str, url: str) -> bool:
    hint = _BOOK_PAGE_HINTS.get(stype)
    return bool(hint) and hint in url


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"&#39;|&rsquo;|&#8217;", "'", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_relevant_excerpt(text: str, window: int = 450) -> str | None:
    """Verbatim window of text around the first spice-relevant keyword hit — no
    LLM, no paraphrasing. The source's own words, not Watson's."""
    lower = text.lower()
    best_idx = None
    for kw in _SPICE_KEYWORDS:
        idx = lower.find(kw)
        if idx != -1 and (best_idx is None or idx < best_idx):
            best_idx = idx
    if best_idx is None:
        return None
    start = max(0, best_idx - 80)
    end = min(len(text), best_idx + window)
    excerpt = text[start:end].strip()
    if start > 0:
        sp = excerpt.find(" ")
        if sp != -1:
            excerpt = excerpt[sp + 1:]
    if end < len(text):
        sp = excerpt.rfind(" ")
        if sp != -1:
            excerpt = excerpt[:sp]
    excerpt = excerpt.strip()
    return excerpt or None


def _extract_structured_rating(text: str, pattern: re.Pattern) -> str | None:
    """Pull a numeric spice-scale rating directly out of a structured-rating
    source's page text (romance.io, SpicyBooks) — no keyword-window guessing
    needed when the source already states its own number. Returns
    "N/5 - Label", or None if the pattern isn't found (caller falls back to
    _extract_relevant_excerpt)."""
    m = pattern.search(text)
    if not m:
        return None
    level, label = m.group(1), m.group(2).strip()
    return f"{level}/5 - {label}"


# Common Sense Media publishes fixed category headers (confirmed against real
# fetched pages 2026-07-22: /book-reviews/the-thirteenth-child, /beach-read,
# /a-court-of-thorns-and-roses-book-1). Order varies per book — CSM ranks
# categories by severity for that specific title, not a fixed template order —
# so headers can't be assumed adjacent to each other; extraction locates ALL
# header occurrences and slices between whichever one comes next positionally,
# whatever it is. Each header also appears 2-3 times per page (a short "at a
# glance" teaser near the top, the full detailed writeup further down,
# sometimes a "customize filters" CTA that repeats the category name) — the
# detailed writeup is reliably the longest of these, so picking max(len)
# selects it without needing to guess which occurrence index it lands at.
_CSM_SECTION_HEADERS = (
    "Parents Need to Know", "Violence & Scariness", "Sex, Romance & Nudity", "Language",
    "Drinking, Drugs & Smoking", "Products & Purchases", "Positive Messages",
    "Positive Role Models", "Diverse Representations", "Educational Value",
)
# Fixed CTA CSM inserts right after every detailed category writeup ("Did you
# know you can flag iffy content? Adjust limits for <Category> in your kid's
# entertainment guide...") — confirmed identical wording after 4 different
# categories on the same page (2026-07-22). Trimmed off a captured section if
# present, since it's UI chrome, not part of the review.
_CSM_CTA_MARKER = "Did you know you can flag iffy content?"


def _csm_header_positions(text: str) -> list[tuple[int, str]]:
    positions = []
    for header in _CSM_SECTION_HEADERS:
        positions.extend((m.start(), header) for m in re.finditer(re.escape(header), text))
    positions.sort()
    return positions


def _extract_csm_section(text: str, header: str, positions: list[tuple[int, str]] | None = None) -> str | None:
    """Returns the longest occurrence of `header`'s section — from the header
    text up to wherever the next known CSM header occurs, whichever header
    that is — trimmed of CSM's flag-iffy-content CTA if present. Returns None
    if `header` doesn't appear on the page at all."""
    if positions is None:
        positions = _csm_header_positions(text)
    candidates = []
    for i, (pos, h) in enumerate(positions):
        if h != header:
            continue
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        section = text[pos:end]
        cta_idx = section.find(_CSM_CTA_MARKER)
        if cta_idx != -1:
            section = section[:cta_idx]
        candidates.append(section.strip())
    if not candidates:
        return None
    return max(candidates, key=len) or None


def _extract_commonsensemedia_excerpt(text: str) -> str | None:
    """Common Sense Media writes prose organized under fixed category headers
    (see _CSM_SECTION_HEADERS) rather than one continuous review — a
    fixed-window keyword search can straddle multiple unrelated categories
    (confirmed 2026-07-22: a 450-char window from "The Thirteenth Child" ran
    Drinking/Sex/Language/Products/Positive-Role-Models/Positive-Messages/
    Educational-Value together into one incoherent blob, cut off mid-word at
    both ends). Extracts the "Sex, Romance & Nudity" section specifically by
    its own header boundaries instead. Falls back to _extract_relevant_excerpt
    only if that header isn't found at all on the page (expected to be rare)."""
    section = _extract_csm_section(text, "Sex, Romance & Nudity")
    return section or _extract_relevant_excerpt(text)


def extract_csm_parents_summary(text: str) -> str | None:
    """The "Parents Need to Know" section — CSM's own single-paragraph
    quick-glance summary, often the most useful sentence for an at-a-glance
    rating. Not currently wired into any stored field (spice_notes is still
    derived from the top finding's excerpt in jobs.curator.ingest); exposed
    here so it's available if that's wanted."""
    return _extract_csm_section(text, "Parents Need to Know")


def fetch_full_text(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=_UA, timeout=10)
        if not resp.text:
            return None
        return _strip_html(resp.text)
    except Exception as exc:
        log.warning("fetch_full_text failed for %s: %s", url, exc)
        return None


def search_top_content_sites(title: str, author: str | None) -> list[dict]:
    """One site-restricted Serper query per active trusted domain (currently
    commonsensemedia.org, spicybooks.org — see _ACTIVE_SEARCH_DOMAINS), not one
    combined OR query. A combined query lets a domain with many SEO/duplicate
    URLs per book crowd another domain out of the shared result cap entirely —
    confirmed 2026-07-21 with "Beach Read": one combined query returned 4
    romance.io URLs (3 near-duplicates of the same book) and zero
    spicybooks.org hits, even though spicybooks.org has a real page for it. A
    separate query per domain guarantees each one gets its own results."""
    who = f"{title} {author}" if author else title
    results: list[dict] = []
    seen_urls = set()
    for domain in _ACTIVE_SEARCH_DOMAINS:
        for r in serper_search(f"{who} site:{domain}", max_results=5):
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(r)
    return results


def search_for_author(title: str) -> list[dict]:
    """Dedicated, separate Serper query restricted to goodreads.com, used only
    to backfill titles for extract_author_from_titles() when the author is
    unknown. Deliberately NOT merged into search_top_content_sites()'s
    CSM/SpicyBooks queries — confirmed 2026-07-21 that combining multiple
    domains into one shared-result-cap query lets one crowd another out, which
    is exactly what removed Goodreads-style "Title by Author" results (and
    with them, author-backfill signal) when the trusted-source search was
    narrowed to CSM/SpicyBooks only. Kept fully independent so it can never
    affect those results."""
    return serper_search(f"{title} goodreads", max_results=5)


def gather_spice_findings(title: str, author: str | None) -> tuple[list[dict], list[str]]:
    """Returns (findings, all_result_titles).
    findings: ranked, deduped-by-category list of
    {"source_name","source_type","rank","excerpt","url"} — only entries where a
    real rating (structured or prose-extracted) was actually found, capped at
    _MAX_DISPLAYED_FINDINGS.
    all_result_titles: every search-result title seen, for author extraction."""
    all_results = search_top_content_sites(title, author)

    best_per_category: dict[str, tuple[str, str, int]] = {}
    for r in all_results:
        url = r.get("url", "")
        if not url:
            continue
        cat = _categorize_source(url)
        if not cat:
            continue
        name, stype, rank = cat
        current = best_per_category.get(stype)
        # Serper doesn't reliably rank a domain's book-specific page above its
        # homepage/category pages within one query — confirmed 2026-07-21: a
        # spicybooks.org query for "Beach Read" ranked the bare homepage
        # (spicybooks.org/) above the real spicybooks.org/books/beach-read
        # page, and first-seen-wins alone would have picked the homepage.
        # Once a book-page-shaped URL is found for a category, don't let a
        # later non-book-page URL for the same category displace it.
        if (
            current is None
            or rank < current[2]
            or (not _looks_like_book_page(stype, current[1]) and _looks_like_book_page(stype, url))
        ):
            best_per_category[stype] = (name, url, rank)

    findings = []
    for stype, (name, url, rank) in best_per_category.items():
        text = fetch_full_text(url)
        if not text:
            continue
        if stype == "romance_io":
            excerpt = _extract_structured_rating(text, _ROMANCE_IO_PATTERN) or _extract_relevant_excerpt(text)
        elif stype == "spicybooks":
            excerpt = _extract_structured_rating(text, _SPICYBOOKS_PATTERN) or _extract_relevant_excerpt(text)
        elif stype == "commonsensemedia":
            excerpt = _extract_commonsensemedia_excerpt(text)
        else:
            excerpt = _extract_relevant_excerpt(text)
        if not excerpt:
            continue
        findings.append({
            "source_name": name, "source_type": stype, "rank": rank,
            "excerpt": excerpt, "url": url,
        })

    findings.sort(key=lambda f: f["rank"])
    findings = findings[:_MAX_DISPLAYED_FINDINGS]

    all_titles = [r.get("title", "") for r in all_results if r.get("title")]
    return findings, all_titles


def extract_author_from_titles(titles: list[str]) -> str | None:
    """Never guesses off a single mention — requires the same author name to
    appear (via a " by <Name>" pattern) in 2+ independent result titles."""
    pattern = re.compile(r"\bby\s+([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3})")
    candidates: dict[str, int] = {}
    for t in titles:
        m = pattern.search(t)
        if m:
            name = m.group(1).strip().rstrip(".,")
            candidates[name] = candidates.get(name, 0) + 1
    if not candidates:
        return None
    best_name, count = max(candidates.items(), key=lambda kv: kv[1])
    return best_name if count >= 2 else None


# ── Amazon / Goodreads: page count, KU, cover, description, series ─────────

def find_amazon_listing(title: str, author: str | None) -> str | None:
    who = f"{title} {author}" if author else title
    for r in serper_search(f"{who} amazon kindle", max_results=5):
        url = r.get("url", "")
        if "amazon.com" in url:
            return url
    return None


def find_goodreads_book_page(title: str, author: str | None) -> str | None:
    """The canonical /book/show/<id>-<slug> page (real og:image/og:description/series
    info) — distinct from search_spice_content's results, which are often review or
    Q&A subpages that carry only generic site-branding og: tags."""
    who = f"{title} {author}" if author else title
    for r in serper_search(f"{who} goodreads", max_results=5):
        url = r.get("url", "")
        if re.search(r"goodreads\.com/(en/)?book/show/\d+", url) and "/reviews" not in url:
            return url
    return None


_OPEN_LIBRARY_DESCRIPTION_CAP = 2000

# Open Library descriptions are community-editable wiki content and can carry
# injected spam/promotional links — confirmed 2026-07-22: "Beach Read"'s
# description ended with a markdown link to an unrelated third-party
# PDF-download site (`[**Beach Read pdf**](https://.../beach-read-pdf/)`),
# baked into Open Library's own API response, not something we introduced.
# Strip markdown-style links and any remaining bare URLs before this ever
# reaches the app.
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(https?://[^)]+\)")
_BARE_URL_RE = re.compile(r"https?://\S+")


def _strip_injected_links(text: str) -> str:
    text = _MARKDOWN_LINK_RE.sub("", text)
    text = _BARE_URL_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_open_library_description(title: str, author: str | None) -> str | None:
    """Open Library's public API (openlibrary.org, no key required, no
    bot-blocking risk — it's a real documented API, not a scrape) tried first
    for the plot synopsis. Goodreads' og:description meta tag is frequently
    pre-truncated to a short teaser by Goodreads itself (confirmed 2026-07-22
    against real pages: 56 chars for multiple books, well under even our own
    600-char cap, so the cap was never the cause). Confirmed live 2026-07-22:
    "A Court of Thorns and Roses" returned a real 731-char synopsis this way.
    Returns None if Open Library has no record — common for newer releases
    (confirmed for "The Thirteenth Child", a 2024 book: zero results) — in
    which case the caller falls back to the existing Goodreads-derived
    description."""
    try:
        resp = requests.get(
            "https://openlibrary.org/search.json",
            params={"title": title, "author": author or "", "limit": 1},
            timeout=10,
        )
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
        if not docs:
            return None
        work_key = docs[0].get("key")
        if not work_key:
            return None

        work_resp = requests.get(f"https://openlibrary.org{work_key}.json", timeout=10)
        work_resp.raise_for_status()
        desc = work_resp.json().get("description")
        if isinstance(desc, dict):
            desc = desc.get("value")
        if not desc or not isinstance(desc, str):
            return None
        desc = _strip_injected_links(desc)
        return desc[:_OPEN_LIBRARY_DESCRIPTION_CAP] or None
    except Exception as exc:
        log.warning("Open Library description lookup failed for %r: %s", title, exc)
        return None


# Amazon frequently returns a bot-block/"automated access" interstitial instead
# of the real listing (confirmed 2026-07-20 and repeatedly since, regardless of
# User-Agent — HTTP 200, but a small boilerplate page, not the actual product
# page). Detecting this lets kindle_unlimited distinguish "confirmed not on
# KU" (real page fetched, no badge) from "couldn't check at all" (blocked),
# rather than silently collapsing both into False.
_AMAZON_BLOCK_MARKER = "api-services-support@amazon.com"
_AMAZON_BLOCK_MIN_LENGTH = 10000  # real Amazon product pages run 200KB+


def _is_amazon_block_page(html: str) -> bool:
    return _AMAZON_BLOCK_MARKER in html or len(html) < _AMAZON_BLOCK_MIN_LENGTH


def fetch_page_details(url: str) -> dict:
    """Best-effort scrape of an Amazon/Goodreads listing for page count, KU badge,
    cover image, description (near-verbatim editorial og:description, not
    Watson-written), and series position/total. Generic og:* tag scraping — works
    for both source types without a dedicated per-site parser.

    kindle_unlimited is three-state: True (badge found), False (real page
    fetched, no badge), or None (couldn't verify — e.g. Amazon's block page)."""
    out = {
        "page_count": None, "kindle_unlimited": None, "fetched": False,
        "cover_image_url": None, "description": None,
        "series_position": None, "series_total": None, "series_name": None,
    }
    try:
        resp = requests.get(url, headers=_UA, timeout=10)
        html = resp.text
        out["fetched"] = True

        if _is_amazon_block_page(html):
            out["kindle_unlimited"] = None
        else:
            out["kindle_unlimited"] = bool(
                re.search(r"kindle unlimited", html, re.IGNORECASE)
            )

        page_match = re.search(r'([\d,]+)\s*pages?\b', html, re.IGNORECASE)
        if page_match:
            try:
                out["page_count"] = int(page_match.group(1).replace(",", ""))
            except ValueError:
                pass

        og_image = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']', html
        )
        if og_image:
            out["cover_image_url"] = og_image.group(1).strip() or None

        og_desc = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']', html
        )
        if og_desc:
            desc = og_desc.group(1).strip()
            out["description"] = desc[:600] if desc else None

        # Amazon-style "Book 2 of 4" gives both position and total directly.
        series_match = re.search(r'Book\s+(\d+)\s+of\s+(\d+)', html, re.IGNORECASE)
        if series_match:
            out["series_position"] = int(series_match.group(1))
            out["series_total"] = int(series_match.group(2))
        else:
            # Goodreads' og:title is "Book Title (Series Name, #N)" — gives name +
            # position but not total (Goodreads often doesn't know it either).
            gr_series = re.search(
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'][^"\']*\(([^,()]+),\s*#(\d+)\)["\']',
                html,
            )
            if gr_series:
                out["series_name"] = gr_series.group(1).strip() or None
                out["series_position"] = int(gr_series.group(2))
    except Exception as exc:
        log.warning("fetch_page_details failed for %s: %s", url, exc)
    return out


# ── Rating judgment ──────────────────────────────────────────────────────────

# CSM's own severity word for the "Sex, Romance & Nudity" section, mapped to a
# rough point-estimate on OUR 0-5 scale. Confirmed 2026-07-22 against real
# pages: CSM either states an explicit word ("a lot"/"a little") or, for "no
# content" cases, omits any word entirely (e.g. "Sex, Romance & Nudity No
# sexual activities are described...").
_CSM_WORD_ESTIMATE = {"a lot": 4.5, "a little": 1.5}
_CSM_NO_WORD_ESTIMATE = 0.0

# SpicyBooks' own label text (not its raw 1-5 number, which doesn't align with
# ours - their "1 = Mild (Fade to Black)" is our "3 = Fade to Black") mapped to
# a rough point-estimate on OUR scale. Confirmed 2026-07-22 against SpicyBooks'
# own canonical scale pages (spicybooks.org/spice/0 through /5). "Spicy
# (Moderate Scenes)" is the one interpolated entry — it doesn't literally name
# one of our own categories, so it's estimated at 4 (scenes are shown, per
# "Moderate Scenes", but not yet at SpicyBooks' own "Explicit" tier).
_SPICYBOOKS_LABEL_ESTIMATE = [
    (re.compile(r"no spice", re.IGNORECASE), 0.0),
    (re.compile(r"mild.*fade to black", re.IGNORECASE), 3.0),
    (re.compile(r"warm.*closed door", re.IGNORECASE), 2.0),
    (re.compile(r"spicy.*moderate scenes", re.IGNORECASE), 4.0),
    (re.compile(r"very spicy.*explicit", re.IGNORECASE), 5.0),
    (re.compile(r"scorching.*very explicit", re.IGNORECASE), 5.0),
]

# Above this gap (in our-scale point-estimate terms), CSM and SpicyBooks are
# treated as a genuine disagreement, not just different granularity. Confirmed
# 2026-07-22: at 1.0, this resolves ACOTAR/It Ends with Us/Icebreaker (gap 0.5,
# CSM "a lot" vs SpicyBooks "Moderate Scenes") while still correctly holding
# back on Beach Read (gap 1.5, CSM describes graphic intercourse/oral sex in
# detail vs SpicyBooks calling it merely "Fade to Black" — a real contradiction,
# not just calibration noise).
_RECONCILE_GAP_THRESHOLD = 1.0


def _csm_scale_estimate(excerpt: str) -> float | None:
    m = re.match(r"Sex, Romance & Nudity (a lot|a little)\b", excerpt)
    if m:
        return _CSM_WORD_ESTIMATE[m.group(1)]
    if excerpt.startswith("Sex, Romance & Nudity"):
        return _CSM_NO_WORD_ESTIMATE
    return None


def _spicybooks_scale_estimate(excerpt: str) -> float | None:
    m = re.match(r"\d/5 - (.+)", excerpt)
    if not m:
        return None
    label = m.group(1)
    for pattern, estimate in _SPICYBOOKS_LABEL_ESTIMATE:
        if pattern.search(label):
            return estimate
    return None


def _try_reconcile_csm_spicybooks(findings: list[dict]) -> dict | None:
    """If findings are exactly one CSM + one SpicyBooks finding, and both map
    to a rough point-estimate on our scale, check whether they're in the same
    ballpark (gap <= _RECONCILE_GAP_THRESHOLD) despite reading as "different
    severity levels" to a naive comparison. The two sources use genuinely
    different underlying scales (CSM: prose + severity word; SpicyBooks: a 0-5
    scale that doesn't numerically align with ours), so a small gap after
    converting both to a common scale usually means they actually agree, just
    at different granularity — not a real disagreement. Returns a confident
    result dict if reconciled, else None (caller falls back to the existing
    Ollama-based judgment, which still runs for genuine disagreements)."""
    if len(findings) != 2:
        return None
    by_type = {f["source_type"]: f for f in findings}
    if set(by_type) != {"commonsensemedia", "spicybooks"}:
        return None

    csm_est = _csm_scale_estimate(by_type["commonsensemedia"]["excerpt"])
    sb_est = _spicybooks_scale_estimate(by_type["spicybooks"]["excerpt"])
    if csm_est is None or sb_est is None:
        return None

    if abs(csm_est - sb_est) > _RECONCILE_GAP_THRESHOLD:
        return None

    return {"confident": True, "spice_rating": round(sb_est), "reason": ""}


def judge_spice_rating(title: str, author: str | None, findings: list[dict]) -> dict:
    """Ollama weighs the extracted findings to produce a 0-5 spice_rating — the
    one place Watson's own judgment enters, and only the number, never wording
    shown to the user (that's always the findings' own excerpts). Refuses
    (confident=False) rather than guess if too few findings or they disagree.

    NOT CURRENTLY USED TO GATE VISIBILITY as of 2026-07-22 — jobs.curator.ingest
    no longer branches on this function's confident/spice_rating output to
    decide needs_review vs. pending (that's now gated on whether any findings
    exist at all, full stop). This function still runs on every book and its
    output is still stored, kept in place for potential future use — same
    treatment as the dormant romance.io code above."""
    if len(findings) < _MIN_FINDINGS:
        return {
            "confident": False,
            "reason": f"Only {len(findings)} trusted source(s) with usable content found (need >= {_MIN_FINDINGS})",
            "spice_rating": None,
        }

    reconciled = _try_reconcile_csm_spicybooks(findings)
    if reconciled:
        return reconciled

    findings_text = "\n\n".join(
        f"[{f['source_name']}] {f['excerpt']}" for f in findings
    )
    who = f"'{title}' by {author}" if author else f"'{title}'"
    system = (
        "You are a careful book content researcher. You rate romance/sexual content on a "
        "0-5 scale using ONLY the evidence given. You NEVER guess. If the evidence is thin, "
        "contradictory, or doesn't clearly describe content level, you set confident=false "
        "and do not propose a rating. Return only valid JSON, no other text."
    )

    # Question and reason-example wording scale to len(findings) — a fixed
    # plural "do these sources agree" question plus a seeded "sources disagree"
    # example reason caused the model to hallucinate source disagreement even
    # with exactly one finding (confirmed reproducibly 2026-07-22: 3/3 runs on
    # single-source input returned a "sources disagree" reason regardless of
    # what the one source actually said). The agreement-check framing is only
    # meaningful with 2+ findings, so it's kept there; single-source prompts
    # ask a plain confidence question instead and never see "disagree" as a
    # suggested phrase.
    if len(findings) == 1:
        question = (
            "Based on this source, can you confidently assign one spice level? Weigh "
            "what it actually describes, not just any number it cites — its own scale "
            "may differ from ours."
        )
        reason_hint = (
            "if not confident, state specifically why (e.g. 'no specific content "
            "details given', 'the description is too vague to place on the scale')"
        )
    else:
        question = (
            "Do these sources agree closely enough to be confident about one spice "
            "level? Note that different sources may use different rating scales — "
            "weigh what they actually describe, not just any number they cite."
        )
        reason_hint = (
            "if not confident, state specifically why (e.g. 'the sources describe "
            "meaningfully different severity levels', 'no specific content details "
            "given')"
        )

    # Confirmed 2026-07-22: without this, the model sometimes invents a numeric
    # rating for a source that never gave one (e.g. attributing "4/5" to Common
    # Sense Media, which only ever writes a severity word like "a lot", never a
    # number) — up to ~1/3 of runs at default temperature. Reduces but doesn't
    # fully eliminate it alone; paired with temperature=0 below for reliability.
    number_constraint = (
        'Only cite a numeric rating for a source if that source\'s own excerpt '
        'explicitly states one as a number (e.g. "X/5"). If a source only uses '
        "descriptive words or severity labels without a number, describe it in "
        "words — do not convert it to a number yourself."
    )

    # Confirmed 2026-07-22: without this, the model treated any non-graphic
    # description as "too vague to rate" rather than recognizing it as Closed
    # Door itself — e.g. "Divine Rivals" (CSM: "a little... wedding night in
    # which sex is new to both of them... no graphic detail") deterministically
    # returned confident=false at temperature=0, when a human would place this
    # at 2 without hesitation. Adding this sentence flipped it to confident,
    # rating=2, 3/3 runs, without changing the clearly-graphic ("a lot") cases
    # or the multi-source disagreement cases.
    closed_door_clarification = (
        'A source describing sex or intimacy narratively without graphic/anatomical '
        'detail is not automatically "too vague" — that is exactly what "Closed Door" '
        "(2) describes."
    )

    prompt = f"""Book: {who}

Spice scale:
0 = Clean, no romance content
1 = Kissing Only
2 = Closed Door (sex implied, not shown)
3 = Fade to Black (scene starts, cuts away)
4 = Open Door (explicit content shown)
5 = Explicit (heavy/frequent explicit content)

{closed_door_clarification}

Findings from trusted content-rating sources:
{findings_text}

{question}

{number_constraint}

Return JSON exactly in this shape:
{{
  "confident": true or false,
  "spice_rating": integer 0-5 or null,
  "reason": "{reason_hint}"
}}"""

    try:
        # temperature=0: this is deterministic evidence-weighing, not creative
        # generation. Confirmed 2026-07-22 that Ollama's default (0.8, no
        # override previously set here) caused real run-to-run instability on
        # identical input — e.g. "Icebreaker" flipped between confident=true
        # and confident=false across repeated identical calls. temperature=0
        # eliminated that: 3/3 identical results across 4 re-tested books.
        raw = call_ollama(system, prompt, options={"temperature": 0})
        parsed = parse_json(raw)
    except Exception as exc:
        log.error("judge_spice_rating Ollama call failed: %s", exc)
        parsed = None

    if not parsed or "confident" not in parsed:
        return {
            "confident": False,
            "reason": "rating-judgment model call failed or returned unparseable output",
            "spice_rating": None,
        }

    if not parsed.get("confident"):
        parsed.setdefault("spice_rating", None)
        parsed.setdefault("reason", "model reported low confidence")
        return parsed

    rating = parsed.get("spice_rating")
    if not isinstance(rating, int) or not (0 <= rating <= 5):
        return {
            "confident": False,
            "reason": f"model returned invalid spice_rating: {rating!r}",
            "spice_rating": None,
        }

    return parsed


# ── Orchestration ────────────────────────────────────────────────────────────

def research_book(title: str, author: str | None = None) -> dict:
    """Full research pass. Returns:
    {
      "confident": bool,
      "reason": str,               # populated when not confident
      "spice_rating": int|None,
      "findings": [{"source_name","source_type","rank","excerpt","url"}],
      "author": str|None,          # extracted from search results, only if 2+ agree
      "page_count": int|None,
      "kindle_unlimited": bool|None,  # None = couldn't verify (e.g. Amazon blocked)
      "cover_image_url": str|None,
      "description": str|None,
      "series_position": int|None,
      "series_total": int|None,
      "series_name": str|None,
      "sources": [{"type": str, "url": str}],
    }
    """
    findings, result_titles = gather_spice_findings(title, author)
    if author is None:
        # Author-backfill signal (Goodreads-style "Title by Author" titles) was
        # lost when the trusted-source search narrowed to CSM/SpicyBooks only
        # (2026-07-21) — those sites' own titles never name the author. This
        # dedicated, separate query restores it without touching that search.
        result_titles = result_titles + [
            r.get("title", "") for r in search_for_author(title) if r.get("title")
        ]
    amazon_url = find_amazon_listing(title, author)
    goodreads_url = find_goodreads_book_page(title, author)

    sources = [
        {"type": f["source_type"], "url": f["url"]} for f in findings
    ]
    if goodreads_url and not any(s["url"] == goodreads_url for s in sources):
        sources.append({"type": "goodreads", "url": goodreads_url})

    page_count = None
    kindle_unlimited = None  # unknown until an Amazon fetch actually succeeds
    cover_image_url = None
    # Open Library tried first (real API, no bot-blocking, often has a full
    # synopsis) - the per-source loop below only fills this in as a fallback
    # if Open Library had no record for this title (see
    # fetch_open_library_description's docstring).
    description = fetch_open_library_description(title, author)
    series_position = None
    series_total = None
    series_name = None

    # Amazon first (page count / KU authoritative there), Goodreads as a fallback for
    # whatever Amazon's og: tags didn't have — same sources already being fetched, no
    # new source category. In practice Amazon frequently bot-blocks plain requests
    # (confirmed 2026-07-20 — serves a "Continue shopping" interstitial, not the real
    # listing), so Goodreads ends up carrying most of this via the `or` fallback below.
    for source_type, url in (("amazon", amazon_url), ("goodreads", goodreads_url)):
        if not url:
            continue
        details = fetch_page_details(url)
        if source_type == "amazon":
            page_count = page_count or details["page_count"]
            if details["kindle_unlimited"] is not None:
                kindle_unlimited = details["kindle_unlimited"]
        cover_image_url = cover_image_url or details["cover_image_url"]
        description = description or details["description"]
        series_position = series_position or details["series_position"]
        series_total = series_total or details["series_total"]
        series_name = series_name or details.get("series_name")

    if amazon_url:
        sources.append({"type": "amazon", "url": amazon_url})

    rating_result = judge_spice_rating(title, author, findings)
    extracted_author = extract_author_from_titles(result_titles)

    return {
        "confident": bool(rating_result.get("confident")),
        "reason": rating_result.get("reason", ""),
        "spice_rating": rating_result.get("spice_rating"),
        "findings": findings,
        "author": extracted_author,
        "page_count": page_count,
        "kindle_unlimited": kindle_unlimited,
        "cover_image_url": cover_image_url,
        "description": description,
        "series_position": series_position,
        "series_total": series_total,
        "series_name": series_name,
        "sources": sources,
    }
