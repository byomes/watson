"""jobs/curator/research.py — spice rating / KU / page-count research pass.

Never guesses, and never blends. Spice content is *extracted*, not
synthesized: each trusted source (see _EXACT_DOMAINS / _categorize_source)
gets a full-page fetch. Common Sense Media writes prose, so its excerpt comes
from a keyword-window pull (_extract_relevant_excerpt). romance.io and
SpicyBooks publish their own numeric spice-scale rating directly, so those
try a structured-pattern match first (_extract_structured_rating) and only
fall back to the keyword-window approach if that pattern isn't found. The Fae
Shelf publishes both a structured chili-scale rating AND its own prose
Content Warnings section per book, so it gets both (_extract_faeshelf_excerpt)
rather than picking one pattern over the other. No LLM paraphrasing step
touches the wording the detail page shows either way. The
0-5 spice_rating number is still a judgment call, made by an Ollama pass that
weighs the extracted findings, and only fires when >=_MIN_FINDINGS real
findings were gathered and the model reports confidence — otherwise
needs_review with no rating.
"""
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlparse

import requests

from jobs.research.web_search import search as serper_search

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"  # accuracy-sensitive background job — see LLM Stack in WATSON_ARCHITECTURE.md


def _log_stage(job_id, stage_name: str, duration: float, stage_durations: dict | None = None) -> None:
    """One log line per pipeline stage — job_id, stage name, wall-clock duration. If
    stage_durations is given, accumulates into it (a stage can fire more than once per
    job, e.g. one fetch_full_text call per trusted source) so the caller (research_book_fast()
    or run_stage_b_enrichment()) can log a single per-job summary line at the end."""
    log.info("curator_timing job_id=%s stage=%s duration=%.2fs", job_id, stage_name, duration)
    if stage_durations is not None:
        stage_durations[stage_name] = stage_durations.get(stage_name, 0.0) + duration

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

# Tightened per-source timeout for Stage A's parallel waves (Commit 2,
# curator-spec.md). Serper queries and trusted-source/Amazon/Goodreads/Open
# Library fetches are all normally well under this (baseline measured
# 2026-07-22: 0.06-5.14s per call) — a source that doesn't respond in time is
# skipped gracefully rather than holding up the whole job. romance.io/
# FlareSolverr is excluded from Stage A entirely (Commit 4) and keeps its own
# separate, much longer timeout — never tightened by this constant.
_STAGE_A_TIMEOUT = 5

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
# romance.io was DORMANT 2026-07-21 through 2026-07-22 (Cloudflare JS
# challenge plain `requests` couldn't solve) — reactivated 2026-07-22 via the
# local FlareSolverr container (see _FLARESOLVERR_DOMAINS / fetch_full_text
# and _ROMANCE_IO_PATTERN comments for details). Included in the active
# search again below.
#
# The Fae Shelf (thefaeshelf.com) added 2026-07-22 — confirmed reachable
# (200, no blocking) via direct request the same night. Romantasy-specific
# book database with its own 0-5 chili-scale spice rating plus a per-book
# "Content Warnings" prose section — see _extract_faeshelf_excerpt for the
# confirmed page structure and real extracted examples.
#
# Also evaluated 2026-07-22 and NOT added:
#   - Story Snoops — domain does not resolve at all (DNS failure).
#   - Kids-in-Mind — 403 on every attempt.
#   - MoodReads — both plausible domain guesses failed (no working URL found).
#   - OwlCrate — returned 429 (rate-limited). This is NOT the same signal as
#     the three above (a genuine block/dead-domain) — 429 means the server is
#     alive and just throttling. Left out for now, but worth retesting with
#     slower request pacing rather than writing it off as confirmed-dead like
#     the others.
_EXACT_DOMAINS = [
    ("commonsensemedia.org", "Common Sense Media", "commonsensemedia", 1),
    ("romance.io", "romance.io", "romance_io", 2),
    ("spicybooks.org", "SpicyBooks", "spicybooks", 3),
    ("thefaeshelf.com", "The Fae Shelf", "faeshelf", 4),
]

# Domains search_top_content_sites() actually queries. romance.io reactivated
# 2026-07-22 now that FlareSolverr (see _FLARESOLVERR_DOMAINS, fetch_full_text)
# solves the Cloudflare challenge that made it dormant since 2026-07-21. Its
# SEO-heavy duplicate URLs (tracking-parameter variants, /similar pages,
# unrelated same-author results) previously crowded spicybooks.org out of a
# *combined* query's result cap — confirmed with "Beach Read": one combined
# query returned 4 romance.io URLs (3 near-duplicates) and zero spicybooks.org
# hits. That crowding risk doesn't apply here: search_top_content_sites()
# already runs one separate site-restricted query per domain (see below),
# so romance.io's result cap is isolated from every other domain's regardless.
_ACTIVE_SEARCH_DOMAINS = tuple(d for d, *_ in _EXACT_DOMAINS)

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
# romance.io: reactivated 2026-07-22 via FlareSolverr (see
# _FLARESOLVERR_DOMAINS / fetch_full_text) — was DORMANT 2026-07-21 through
# 2026-07-22 behind a site-wide Cloudflare JS challenge (`cf-mitigated:
# challenge` on every request) that plain `requests` couldn't solve regardless
# of User-Agent.
#
# Pattern corrected 2026-07-22 against real fetched pages (romance.io/books/
# .../the-thirteenth-child-erin-a-craig via FlareSolverr) — the original
# pattern was written from a described "Spice/Steam/Heat level: X/5 - Label"
# format that turned out to be wrong on the live page: actual text reads
# "Steam/Spice level: 1 of 5 Glimpses and kisses [?] · 14 ratings" — "of 5"
# not "/5", no " - " separator before the label, and the two category names
# appear slash-joined ("Steam/Spice") rather than as a single word.
_ROMANCE_IO_PATTERN = re.compile(
    r"(?:Steam|Spice|Heat)(?:/(?:Steam|Spice|Heat))?\s*level:?\s*(\d)\s*of\s*5\s+([^\[·\n]+)",
    re.IGNORECASE,
)
# Confirmed against real fetched pages (spicybooks.org/books/beach-read,
# /heated-rivalry, /a-court-of-thorns-and-roses) on 2026-07-21. SpicyBooks'
# FAQ-accordion body text reliably reads either "<Title> has a spice level of
# N/5 on our scale, which we rate as &quot;<Label>.&quot;" or "...is rated N
# out of 5 on the SpicyBooks spice scale. This means it's &quot;<Label>&quot;"
# — entity-escaped quotes survive _strip_html() as the literal string
# "&quot;", not real quote characters, since _strip_html() doesn't decode that
# entity (apostrophe variants and &nbsp;/&amp; are decoded, but not &quot;).
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
    """Returns (source_name, source_type, rank) if url matches one of the
    trusted domains in _EXACT_DOMAINS, else None. Lower rank = higher
    priority."""
    host = urlparse(url).netloc.lower()
    for domain, name, stype, rank in _EXACT_DOMAINS:
        if domain in host:
            return (name, stype, rank)
    return None


# Path substring that marks a URL as a specific book page (vs. a domain root,
# tropes/category listing, author page, etc.) for each active source_type —
# used by _discover_trusted_sources() to prefer the real book page when a
# search returns multiple URLs for the same domain.
_BOOK_PAGE_HINTS = {
    "commonsensemedia": "/book-reviews/",
    "spicybooks": "/books/",
    "faeshelf": "/book/",
    "romance_io": "/books/",
}


def _looks_like_book_page(stype: str, url: str) -> bool:
    hint = _BOOK_PAGE_HINTS.get(stype)
    return bool(hint) and hint in url


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"&#39;|&#x27;|&rsquo;|&#8217;", "'", text)
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


# The Fae Shelf's /book/<slug> pages (confirmed 2026-07-22 against 5 real
# pages: A Court of Thorns and Roses, Fourth Wing, The War of Two Queens,
# Sorcery of Thorns, Spinning Silver, Cinder) print the spice rating as N
# repeated 🌶️ spans plus a text label — no literal "N/5" digit anywhere in
# the page body (the only "/5" on the page is Goodreads' own star rating,
# e.g. "4.2/5 stars" — a different number entirely, not to be confused with
# spice). The rating+label is immediately followed by the page-count marker
# ("Warm 419 p · May 2015"), which anchors the regex below and rules out any
# accidental match elsewhere on the page. Confirmed ratings/labels seen live:
# 1/Mild, 2/Warm, 3/Spicy, 4/Very Spicy. 0/No Spice and 5/Scorching were not
# seen on a live page (the guide's own "0/5 No Spice" example list — Sorcery
# of Thorns, Spinning Silver, Cinder — actually shows 1/Mild on each book's
# live page now; the guide's static examples and the live per-book database
# have drifted out of sync on the site's own end). Counting chili spans
# directly, rather than mapping the label through a lookup table, means a
# 0-chili book still resolves correctly to "0/5 - No Spice" without ever
# needing one confirmed live.
_FAESHELF_SPICE_LABELS = ("No Spice", "Mild", "Warm", "Spicy", "Very Spicy", "Scorching")
_FAESHELF_SPICE_PATTERN = re.compile(
    r"((?:🌶️\s*)*)("
    + "|".join(re.escape(label) for label in sorted(_FAESHELF_SPICE_LABELS, key=len, reverse=True))
    + r")\s+\d+\s*p\s*·"
)

# The book page's own "Content Warnings" section — confirmed 2026-07-22 to
# appear exactly once per page, immediately followed by "Spoiler Discussion"
# (also exactly once) — a much simpler shape than CSM's multi-occurrence
# headers, so no positional disambiguation like _csm_header_positions() is
# needed here. Unlike CSM, this section is NOT sub-headed by category — it
# blends sexual-content, violence, and thematic warnings into one paragraph
# (confirmed live, e.g. The War of Two Queens: "Graphic violence and
# warfare... Explicit sexual content... Blood drinking and vampiric
# violence..." all in the same paragraph) — so extraction returns the whole
# section rather than trying to isolate a sex-specific subsection that
# doesn't exist as its own heading on this source.
_FAESHELF_CONTENT_WARNINGS_HEADER = "Content Warnings"
_FAESHELF_NEXT_SECTION_HEADER = "Spoiler Discussion"


def _extract_faeshelf_rating(text: str) -> str | None:
    """Pull The Fae Shelf's chili-rating + label directly out of the page
    (see module comment above for the confirmed page structure). Returns
    "N/5 - Label", or None if the pattern isn't found."""
    m = _FAESHELF_SPICE_PATTERN.search(text)
    if not m:
        return None
    rating = m.group(1).count("🌶️")
    label = m.group(2)
    return f"{rating}/5 - {label}"


def _extract_faeshelf_content_warnings(text: str) -> str | None:
    """The book page's own "Content Warnings" prose section, verbatim.
    Returns None if the header isn't found at all on the page."""
    start = text.find(_FAESHELF_CONTENT_WARNINGS_HEADER)
    if start == -1:
        return None
    end = text.find(_FAESHELF_NEXT_SECTION_HEADER, start)
    section = text[start:end if end != -1 else len(text)]
    return section.strip() or None


def _extract_faeshelf_excerpt(text: str) -> str | None:
    """Combines The Fae Shelf's structured chili-rating with its Content
    Warnings prose when both are present, joined by an em dash — gives the
    same at-a-glance number SpicyBooks/romance.io provide, plus the
    descriptive content CSM provides, from a single source. Returns just
    whichever piece was found if only one is present, or None if neither is
    (caller falls back to _extract_relevant_excerpt, same as the other
    structured sources)."""
    rating = _extract_faeshelf_rating(text)
    warnings = _extract_faeshelf_content_warnings(text)
    if rating and warnings:
        return f"{rating} — {warnings}"
    return rating or warnings


_FLARESOLVERR_URL = "http://localhost:8191/v1"
_FLARESOLVERR_TIMEOUT_MS = 60000

# Domains that sit behind a Cloudflare JS challenge plain `requests` can't
# solve — routed through the local FlareSolverr container (localhost:8191,
# docker run --name=flaresolverr, see project_backlog id=18) instead of a
# direct fetch. romance.io is the first and, as of 2026-07-22, only entry.
# commonsensemedia.org/spicybooks.org/thefaeshelf.com are unaffected — they
# stay on the plain requests.get() path below.
_FLARESOLVERR_DOMAINS = {"romance.io"}


def _flaresolverr_fetch_html(url: str) -> str | None:
    """Raw HTML via the local FlareSolverr container (solves a Cloudflare JS
    challenge the same way for romance.io as for Amazon's bot-block — see
    fetch_amazon_ku_status()). Returns None on any failure (container
    unreachable, non-ok status, missing solution)."""
    try:
        resp = requests.post(
            _FLARESOLVERR_URL,
            json={"cmd": "request.get", "url": url, "maxTimeout": _FLARESOLVERR_TIMEOUT_MS},
            timeout=(_FLARESOLVERR_TIMEOUT_MS / 1000) + 10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            log.warning("FlareSolverr returned non-ok status for %s: %s", url, data.get("message"))
            return None
        return (data.get("solution") or {}).get("response")
    except Exception as exc:
        log.warning("FlareSolverr fetch failed for %s: %s", url, exc)
        return None


def _fetch_via_flaresolverr(url: str) -> str | None:
    """Stripped-text wrapper around _flaresolverr_fetch_html(), used by
    fetch_full_text() for domains behind Cloudflare (romance.io). Confirmed
    2026-07-22 against real romance.io pages."""
    html = _flaresolverr_fetch_html(url)
    return _strip_html(html) if html else None


def fetch_full_text(url: str, timeout: int = 10) -> str | None:
    """timeout only applies to the plain-request branch — FlareSolverr-routed
    domains (romance.io) always use their own, much longer
    _FLARESOLVERR_TIMEOUT_MS regardless of what's passed here (Commit 2,
    curator-spec.md — that path isn't part of Stage A's tightened budget)."""
    host = urlparse(url).netloc.lower()
    if any(d in host for d in _FLARESOLVERR_DOMAINS):
        return _fetch_via_flaresolverr(url)
    try:
        resp = requests.get(url, headers=_UA, timeout=timeout)
        if not resp.text:
            return None
        return _strip_html(resp.text)
    except Exception as exc:
        log.warning("fetch_full_text failed for %s: %s", url, exc)
        return None


def search_top_content_sites(
    title: str, author: str | None, job_id=None, stage_durations: dict | None = None,
) -> list[dict]:
    """One site-restricted Serper query per active trusted domain (currently
    commonsensemedia.org, spicybooks.org, thefaeshelf.com — see
    _ACTIVE_SEARCH_DOMAINS), not one combined OR query. A combined query lets
    a domain with many SEO/duplicate URLs per book crowd another domain out
    of the shared result cap entirely —
    confirmed 2026-07-21 with "Beach Read": one combined query returned 4
    romance.io URLs (3 near-duplicates of the same book) and zero
    spicybooks.org hits, even though spicybooks.org has a real page for it. A
    separate query per domain guarantees each one gets its own results.

    Commit 2 (curator-spec.md): the per-domain queries now run concurrently
    (one thread per domain) on the tightened _STAGE_A_TIMEOUT instead of
    sequentially at the old 10s timeout — a slow/unresponsive domain no
    longer delays the others. Same query text, same domains, same
    dedup-by-URL merge; only the execution model changed. A domain whose
    query fails or times out just contributes zero results (graceful skip,
    same as search()'s existing internal try/except today)."""
    who = f"{title} {author}" if author else title

    def _query_domain(domain: str) -> list[dict]:
        _t = time.perf_counter()
        try:
            return serper_search(f"{who} site:{domain}", max_results=5, timeout=_STAGE_A_TIMEOUT)
        finally:
            _log_stage(job_id, f"site_search_{domain}", time.perf_counter() - _t, stage_durations)

    results: list[dict] = []
    seen_urls = set()
    with ThreadPoolExecutor(max_workers=len(_ACTIVE_SEARCH_DOMAINS)) as pool:
        futures = {pool.submit(_query_domain, d): d for d in _ACTIVE_SEARCH_DOMAINS}
        for future in as_completed(futures):
            domain = futures[future]
            try:
                domain_results = future.result()
            except Exception as exc:
                log.warning("search_top_content_sites: %s query failed: %s", domain, exc)
                domain_results = []
            for r in domain_results:
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
    return serper_search(f"{title} goodreads", max_results=5, timeout=_STAGE_A_TIMEOUT)


def _discover_trusted_sources(
    title: str, author: str | None, job_id=None, stage_durations: dict | None = None,
) -> tuple[dict[str, tuple[str, str, int]], list[str]]:
    """Site-search + categorize half of what used to be one atomic
    gather_spice_findings() call (split in Commit 2, curator-spec.md, so
    research_book() can run this as one Wave 1 task alongside its other
    Serper queries, then fetch/extract per source — see _fetch_finding() —
    only once every Wave 1 URL is known, as Wave 2).

    Returns (best_per_category, all_result_titles): best_per_category maps
    source_type -> (source_name, url, rank), one entry per trusted domain
    that turned up a book-page-shaped result; all_result_titles is every
    search-result title seen, for author extraction. Same categorization
    logic as before, unchanged — including romance.io, whose URL (if found)
    is discovered here but deliberately not fetched until Stage B (Commit 4)."""
    all_results = search_top_content_sites(title, author, job_id=job_id, stage_durations=stage_durations)

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

    all_titles = [r.get("title", "") for r in all_results if r.get("title")]
    return best_per_category, all_titles


def _fetch_finding(
    stype: str, name: str, url: str, rank: int, job_id=None, stage_durations: dict | None = None,
) -> dict | None:
    """Fetch + extract half of what used to be one atomic gather_spice_findings()
    call (split in Commit 2, curator-spec.md). Returns None on a failed/timed-out
    fetch or no extractable excerpt — graceful skip, exactly like today; the
    caller just won't have a finding for this source. Same per-source
    extraction dispatch as before, unchanged — including the romance_io
    branch, kept here so Stage B (Commit 4) can call this same function for
    romance.io without duplicating the extraction logic."""
    _t = time.perf_counter()
    text = fetch_full_text(url, timeout=_STAGE_A_TIMEOUT)
    _log_stage(job_id, f"fetch_{stype}", time.perf_counter() - _t, stage_durations)
    if not text:
        return None
    if stype == "romance_io":
        excerpt = _extract_structured_rating(text, _ROMANCE_IO_PATTERN) or _extract_relevant_excerpt(text)
    elif stype == "spicybooks":
        excerpt = _extract_structured_rating(text, _SPICYBOOKS_PATTERN) or _extract_relevant_excerpt(text)
    elif stype == "commonsensemedia":
        excerpt = _extract_commonsensemedia_excerpt(text)
    elif stype == "faeshelf":
        excerpt = _extract_faeshelf_excerpt(text) or _extract_relevant_excerpt(text)
    else:
        excerpt = _extract_relevant_excerpt(text)
    if not excerpt:
        return None
    return {"source_name": name, "source_type": stype, "rank": rank, "excerpt": excerpt, "url": url}


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
    for r in serper_search(f"{who} amazon kindle", max_results=5, timeout=_STAGE_A_TIMEOUT):
        url = r.get("url", "")
        if "amazon.com" in url:
            return url
    return None


def find_goodreads_book_page(title: str, author: str | None) -> str | None:
    """The canonical /book/show/<id>-<slug> page (real og:image/og:description/series
    info) — distinct from search_spice_content's results, which are often review or
    Q&A subpages that carry only generic site-branding og: tags."""
    who = f"{title} {author}" if author else title
    for r in serper_search(f"{who} goodreads", max_results=5, timeout=_STAGE_A_TIMEOUT):
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


def fetch_open_library_description(title: str, author: str | None, timeout: int = 10) -> str | None:
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
    description.

    author=None must omit the `author` param entirely rather than send an
    empty string — confirmed 2026-07-22: `author=` (empty) makes Open
    Library's search API 500, which this function swallows and returns as a
    plain "no record" None, silently dropping into the Goodreads-teaser
    fallback even when Open Library actually has a full synopsis under the
    title alone (confirmed for "The War of the Two Queens": empty-author
    request 500s, real request omitting the param returns a 1,151-char
    synopsis). See research_book_fast()'s retry-after-author-backfill for the
    other half of this fix."""
    try:
        params: dict = {"title": title, "limit": 1}
        if author:
            params["author"] = author
        resp = requests.get(
            "https://openlibrary.org/search.json",
            params=params,
            timeout=timeout,
        )
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
        if not docs:
            return None
        work_key = docs[0].get("key")
        if not work_key:
            return None

        work_resp = requests.get(f"https://openlibrary.org{work_key}.json", timeout=timeout)
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


# Kindle Unlimited check (Stage B, moved off Stage A's direct requests.get — see
# fetch_amazon_ku_status()). A bare "kindle unlimited" text search against the
# product page (fetch_page_details()'s approach, still used there for page_count/
# cover/description/series only) was validated 2026-07-23 against an
# independently-verified 8-book ground truth (Amazon's own KU-eligible filtered
# search, cross-checked via genuine data-asin result rows) and found to return
# True on every single page tested regardless of actual enrollment — the phrase
# always appears in Amazon's site nav and "customers also bought" carousel. The
# product-page buybox icon (a-icon-kindle-unlimited near the Kindle format's
# price) was tried next and scored 7/8 — better, but missed a confirmed-enrolled
# book whose buybox simply didn't render the badge on 3 separate fetches (session/
# region/cache variance, not a fluke — reproduced 3x). Searching with Amazon's own
# "Kindle Unlimited Eligible" filter and checking whether this exact ASIN appears
# as a genuine search-result row scored 8/8, including a second independent
# re-check on the two hardest cases, so that's the mechanism here instead of a
# product-page fetch.
_KU_ELIGIBLE_FILTER = "rh=n%3A133140011%2Cp_n_feature_nineteen_browse-bin%3A9045887011"


def _extract_asin(amazon_url: str) -> str | None:
    m = re.search(r"/dp/([A-Z0-9]{10})", amazon_url)
    return m.group(1) if m else None


def fetch_amazon_ku_status(amazon_url: str, title: str, author: str | None) -> dict:
    """Stage B-only Kindle Unlimited check, routed through the same local
    FlareSolverr container romance.io already uses (_flaresolverr_fetch_html) —
    Amazon bot-blocks a direct requests.get ~75% of the time regardless of
    User-Agent (confirmed 2026-07-20 through 2026-07-23), same block
    FlareSolverr already solves for romance.io's Cloudflare challenge.

    Searches Amazon's Kindle Store with the "Kindle Unlimited Eligible" filter
    applied for this exact title+author, then checks whether this book's own
    ASIN (parsed from the already-discovered amazon_url) shows up as a genuine
    search-result row — see the comment above _KU_ELIGIBLE_FILTER for why this
    replaced a product-page text/badge search.

    Returns {"kindle_unlimited": bool|None, "fetched": bool}. fetched=False
    means FlareSolverr itself failed, returned a block page, or amazon_url
    didn't contain a parseable ASIN — couldn't verify either way, never
    guessed. A successful, non-blocked search fetch always yields a
    definitive present/absent answer for this specific ASIN, so
    fetched=True never pairs with kindle_unlimited=None here."""
    asin = _extract_asin(amazon_url)
    if not asin:
        return {"kindle_unlimited": None, "fetched": False}

    query = quote(f"{title} {author}" if author else title)
    search_url = f"https://www.amazon.com/s?k={query}&i=digital-text&{_KU_ELIGIBLE_FILTER}"
    html = _flaresolverr_fetch_html(search_url)
    if not html or _is_amazon_block_page(html):
        return {"kindle_unlimited": None, "fetched": False}

    return {"kindle_unlimited": f'data-asin="{asin}"' in html, "fetched": True}


def fetch_page_details(url: str, timeout: int = 10) -> dict:
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
        resp = requests.get(url, headers=_UA, timeout=timeout)
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

def research_book_fast(title: str, author: str | None = None, job_id=None) -> dict:
    """Stage A (curator-spec.md Commit 3): the fast, parallel research pass —
    Commit 2's Wave 1 + Wave 2, plus author-extraction and the Open Library
    description retry (both cheap: pure regex, and a short-timeout network
    call respectively). Returns as soon as these finish, target ≤15s.

    Deliberately excludes:
    - romance.io (Commit 4 fetches it separately, in the background, after
      this function has already returned).
    - judge_spice_rating() — moved to run_stage_b_enrichment() below. This is
      the one piece of "Commit 5" that has to happen here too: a real Stage
      A/Stage B split is meaningless if the slowest call (measured 28-52s in
      the Commit 1 baseline) still blocks Stage A's return. Its logic,
      confidence gating, and informational-only status are all unchanged —
      only its position moved, exactly as Commit 5 specifies.
    - kindle_unlimited (2026-07-23) — moved to fetch_amazon_ku_status(), called
      from jobs/curator/ingest.py's enrich_submission_stage_b() alongside
      romance.io/judge_spice_rating. Amazon bot-blocks fetch_page_details()'s
      direct requests.get ~75% of the time; Stage B routes the check through
      FlareSolverr instead, same as romance.io's Cloudflare challenge.

    Returns:
    {
      "findings": [{"source_name","source_type","rank","excerpt","url"}],
      "author": str|None,          # extracted from search results, only if 2+ agree
      "page_count": int|None,
      "kindle_unlimited": None,  # always None here now — see fetch_amazon_ku_status() (Stage B)
      "cover_image_url": str|None,
      "description": str|None,
      "series_position": int|None,
      "series_total": int|None,
      "series_name": str|None,
      "sources": [{"type": str, "url": str}],
    }
    No "confident"/"reason"/"spice_rating" keys — those come from
    run_stage_b_enrichment() afterward and get merged into the already-
    persisted 'partial' book row in place (see jobs/curator/ingest.py).

    job_id: timing instrumentation (Commit 1, curator-spec.md) — every stage
    logs its own duration tagged with job_id, plus one curator_timing_summary
    line at the end with the total and a per-stage breakdown.

    Wave 1 (every independent Serper query — trusted-source site search,
    author-backfill, Amazon listing, Goodreads page) and Wave 2 (trusted-
    source page fetches minus romance.io, Amazon/Goodreads page-detail
    fetches, Open Library description) each run concurrently via
    ThreadPoolExecutor on the tightened _STAGE_A_TIMEOUT (Commit 2). Same
    extraction logic and same field-precedence rules throughout (Open
    Library description still wins over Amazon/Goodreads og:description,
    Amazon still authoritative for page_count).
    """
    _t_total = time.perf_counter()
    stage_durations: dict[str, float] = {}

    # ── Wave 1: every independent Serper query, concurrent ──────────────────
    with ThreadPoolExecutor(max_workers=4) as pool:
        sources_future = pool.submit(
            _discover_trusted_sources, title, author, job_id, stage_durations
        )

        author_future = None
        if author is None:
            # Author-backfill signal (Goodreads-style "Title by Author" titles) was
            # lost when the trusted-source search narrowed to CSM/SpicyBooks only
            # (2026-07-21) — those sites' own titles never name the author. This
            # dedicated, separate query restores it without touching that search.
            def _search_for_author():
                _t = time.perf_counter()
                try:
                    return search_for_author(title)
                finally:
                    _log_stage(job_id, "search_for_author", time.perf_counter() - _t, stage_durations)
            author_future = pool.submit(_search_for_author)

        def _find_amazon():
            _t = time.perf_counter()
            try:
                return find_amazon_listing(title, author)
            finally:
                _log_stage(job_id, "find_amazon_listing", time.perf_counter() - _t, stage_durations)
        amazon_url_future = pool.submit(_find_amazon)

        def _find_goodreads():
            _t = time.perf_counter()
            try:
                return find_goodreads_book_page(title, author)
            finally:
                _log_stage(job_id, "find_goodreads_book_page", time.perf_counter() - _t, stage_durations)
        goodreads_url_future = pool.submit(_find_goodreads)

        best_per_category, result_titles = sources_future.result()
        if author_future is not None:
            result_titles = result_titles + [
                r.get("title", "") for r in author_future.result() if r.get("title")
            ]
        amazon_url = amazon_url_future.result()
        goodreads_url = goodreads_url_future.result()

    # romance.io excluded from Stage A entirely (Commit 4 adds it back as a
    # Stage B-only fetch) — its URL, if discovered above, simply isn't fetched here.
    fetch_targets = {stype: v for stype, v in best_per_category.items() if stype != "romance_io"}

    # ── Wave 2: page fetches, concurrent — needs Wave 1's URLs first ────────
    findings: list[dict] = []
    page_count = None
    kindle_unlimited = None  # always None from Stage A now — see fetch_amazon_ku_status() (Stage B)
    cover_image_url = None
    description = None
    series_position = None
    series_total = None
    series_name = None

    with ThreadPoolExecutor(max_workers=6) as pool:
        finding_futures = {
            pool.submit(_fetch_finding, stype, name, url, rank, job_id, stage_durations): stype
            for stype, (name, url, rank) in fetch_targets.items()
        }

        # Open Library tried first (real API, no bot-blocking, often has a full
        # synopsis) - the page-detail results below only fill this in as a
        # fallback if Open Library had no record for this title (see
        # fetch_open_library_description's docstring).
        def _open_library():
            _t = time.perf_counter()
            try:
                return fetch_open_library_description(title, author, timeout=_STAGE_A_TIMEOUT)
            finally:
                _log_stage(job_id, "open_library_description", time.perf_counter() - _t, stage_durations)
        description_future = pool.submit(_open_library)

        # Amazon first (page count / KU authoritative there), Goodreads as a fallback for
        # whatever Amazon's og: tags didn't have — same sources already being fetched, no
        # new source category. In practice Amazon frequently bot-blocks plain requests
        # (confirmed 2026-07-20 — serves a "Continue shopping" interstitial, not the real
        # listing), so Goodreads ends up carrying most of this via the `or` fallback below.
        page_detail_futures = {}
        for source_type, url in (("amazon", amazon_url), ("goodreads", goodreads_url)):
            if not url:
                continue
            def _fetch_details(u=url, st=source_type):
                _t = time.perf_counter()
                try:
                    return fetch_page_details(u, timeout=_STAGE_A_TIMEOUT)
                finally:
                    _log_stage(job_id, f"fetch_page_details_{st}", time.perf_counter() - _t, stage_durations)
            page_detail_futures[source_type] = pool.submit(_fetch_details)

        for future in as_completed(finding_futures):
            stype = finding_futures[future]
            try:
                finding = future.result()
            except Exception as exc:
                log.warning("Wave 2 fetch for %s failed: %s", stype, exc)
                finding = None
            if finding:
                findings.append(finding)

        description = description_future.result()

        for source_type, future in page_detail_futures.items():
            try:
                details = future.result()
            except Exception as exc:
                log.warning("fetch_page_details(%s) failed: %s", source_type, exc)
                continue
            if source_type == "amazon":
                # kindle_unlimited intentionally not read from here anymore — moved to
                # Stage B (fetch_amazon_ku_status(), see jobs/curator/ingest.py's
                # enrich_submission_stage_b()), which routes through FlareSolverr instead
                # of this function's direct requests.get (Amazon bot-blocks that ~75% of
                # the time). page_count/cover/description/series still come from here,
                # unchanged.
                page_count = page_count or details["page_count"]
            cover_image_url = cover_image_url or details["cover_image_url"]
            description = description or details["description"]
            series_position = series_position or details["series_position"]
            series_total = series_total or details["series_total"]
            series_name = series_name or details.get("series_name")

    findings.sort(key=lambda f: f["rank"])
    findings = findings[:_MAX_DISPLAYED_FINDINGS]

    sources = [{"type": f["source_type"], "url": f["url"]} for f in findings]
    if goodreads_url and not any(s["url"] == goodreads_url for s in sources):
        sources.append({"type": "goodreads", "url": goodreads_url})
    if amazon_url:
        sources.append({"type": "amazon", "url": amazon_url})

    extracted_author = extract_author_from_titles(result_titles)

    # Retry the Open Library synopsis lookup now that author-backfill has run:
    # the call above only had whatever author the original caller supplied
    # (often None for a quick "Title" add), and a title-only Open Library
    # search is more likely to miss or match the wrong edition than one with
    # the author attached. A backfilled author found here beats whatever
    # shorter Goodreads-teaser description already filled in above.
    if author is None and extracted_author:
        _t = time.perf_counter()
        retried_description = fetch_open_library_description(title, extracted_author, timeout=_STAGE_A_TIMEOUT)
        _log_stage(job_id, "open_library_description_retry", time.perf_counter() - _t, stage_durations)
        if retried_description:
            description = retried_description

    total_duration = time.perf_counter() - _t_total
    stage_summary = " ".join(f"{name}={dur:.2f}s" for name, dur in stage_durations.items())
    log.info(
        "curator_timing_summary_stage_a job_id=%s total=%.2fs %s",
        job_id, total_duration, stage_summary,
    )

    return {
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


def _find_romance_io_finding(
    title: str, author: str | None, job_id=None, stage_durations: dict | None = None,
) -> dict | None:
    """Stage B-only romance.io discovery + fetch (Commit 4, curator-spec.md) —
    romance.io/FlareSolverr removed from the synchronous Stage A path
    entirely, unconditionally. No time budget here, so this does its own
    fresh site:romance.io Serper search rather than reusing whatever Stage
    A's _discover_trusted_sources() already found — simpler, fully decoupled
    from Stage A's internal state, and Serper is cheap regardless. No
    timeout tightening either: the plain Serper call uses the default
    timeout, and the FlareSolverr fetch (via _fetch_finding() ->
    fetch_full_text() -> _fetch_via_flaresolverr()) keeps its own
    long-standing _FLARESOLVERR_TIMEOUT_MS ceiling, exactly as before this
    refactor — only its position in the pipeline changed, not the call
    itself or the extraction regex (_ROMANCE_IO_PATTERN, unchanged).

    Returns a finding dict (same shape _fetch_finding() always returned) or
    None if no romance.io page was found or fetchable — same graceful-skip
    behavior as every other source, just running in the background now
    instead of blocking Stage A."""
    who = f"{title} {author}" if author else title

    _t = time.perf_counter()
    results = serper_search(f"{who} site:romance.io", max_results=5)
    _log_stage(job_id, "site_search_romance_io_stage_b", time.perf_counter() - _t, stage_durations)

    best: tuple[str, str, int] | None = None
    for r in results:
        url = r.get("url", "")
        if not url:
            continue
        cat = _categorize_source(url)
        if not cat or cat[1] != "romance_io":
            continue
        name, stype, rank = cat
        # Same book-page preference as _discover_trusted_sources() — a bare
        # romance.io homepage/category URL shouldn't beat a real book page.
        if best is None or (not _looks_like_book_page(stype, best[1]) and _looks_like_book_page(stype, url)):
            best = (name, url, rank)

    if best is None:
        return None

    name, url, rank = best
    return _fetch_finding("romance_io", name, url, rank, job_id, stage_durations)


def run_stage_b_enrichment(
    title: str, author: str | None, findings: list[dict], job_id=None,
) -> dict:
    """Stage B (curator-spec.md Commits 3-5): background enrichment that runs
    immediately after Stage A returns, in the same worker thread (see
    jobs/curator/worker.py), with no time budget — it's what upgrades the
    already-visible 'partial' book row to 'done' in place.

    Fetches romance.io (Commit 4, see _find_romance_io_finding()) and, if
    found, weighs it into the same findings judge_spice_rating() sees — same
    as pre-split behavior, where all 4 trusted sources were always available
    before judging. Then runs judge_spice_rating() (moved here in Commit 3;
    same logic, same confidence gating, same informational-only status per
    the 2026-07-22 gating decision — only its position changed).

    Returns {"confident": bool, "reason": str, "spice_rating": int|None,
    "romance_io_finding": dict|None} — the caller (jobs/curator/ingest.py's
    enrich_submission_stage_b()) persists romance_io_finding into
    spice_findings and recomputes spice_notes if it now outranks whatever
    Stage A's findings gave it.

    Never raises — judge_spice_rating() and _find_romance_io_finding() (via
    _fetch_finding()) already have their own internal try/except and degrade
    to "not found"/"not confident" rather than propagating; the caller
    (worker.py) additionally wraps this whole call so a Stage B miss is
    logged, never surfaced to Mel as an error (the Stage A result already
    stands on its own)."""
    stage_durations: dict[str, float] = {}

    romance_io_finding = _find_romance_io_finding(
        title, author, job_id=job_id, stage_durations=stage_durations
    )

    all_findings = list(findings)
    if romance_io_finding:
        all_findings.append(romance_io_finding)
        all_findings.sort(key=lambda f: f["rank"])
        all_findings = all_findings[:_MAX_DISPLAYED_FINDINGS]

    _t = time.perf_counter()
    rating_result = judge_spice_rating(title, author, all_findings)
    _log_stage(job_id, "judge_spice_rating", time.perf_counter() - _t, stage_durations)

    stage_summary = " ".join(f"{name}={dur:.2f}s" for name, dur in stage_durations.items())
    log.info("curator_timing_summary_stage_b job_id=%s %s", job_id, stage_summary)

    return {
        "confident": bool(rating_result.get("confident")),
        "reason": rating_result.get("reason", ""),
        "romance_io_finding": romance_io_finding,
        "spice_rating": rating_result.get("spice_rating"),
    }
