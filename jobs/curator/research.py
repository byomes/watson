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


def call_ollama(system: str, prompt: str, timeout: int = 90) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": MODEL, "system": system, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
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


def fetch_page_details(url: str) -> dict:
    """Best-effort scrape of an Amazon/Goodreads listing for page count, KU badge,
    cover image, description (near-verbatim editorial og:description, not
    Watson-written), and series position/total. Generic og:* tag scraping — works
    for both source types without a dedicated per-site parser."""
    out = {
        "page_count": None, "kindle_unlimited": False, "fetched": False,
        "cover_image_url": None, "description": None,
        "series_position": None, "series_total": None, "series_name": None,
    }
    try:
        resp = requests.get(url, headers=_UA, timeout=10)
        html = resp.text
        out["fetched"] = True

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

def judge_spice_rating(title: str, author: str | None, findings: list[dict]) -> dict:
    """Ollama weighs the extracted findings to produce a 0-5 spice_rating — the
    one place Watson's own judgment enters, and only the number, never wording
    shown to the user (that's always the findings' own excerpts). Refuses
    (confident=False) rather than guess if too few findings or they disagree."""
    if len(findings) < _MIN_FINDINGS:
        return {
            "confident": False,
            "reason": f"Only {len(findings)} trusted source(s) with usable content found (need >= {_MIN_FINDINGS})",
            "spice_rating": None,
        }

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
    prompt = f"""Book: {who}

Spice scale:
0 = Clean, no romance content
1 = Kissing Only
2 = Closed Door (sex implied, not shown)
3 = Fade to Black (scene starts, cuts away)
4 = Open Door (explicit content shown)
5 = Explicit (heavy/frequent explicit content)

Findings from trusted content-rating sources:
{findings_text}

Do these sources agree closely enough to be confident about one spice level? Note that
different sources may use different rating scales — weigh what they actually describe,
not just any number they cite.

Return JSON exactly in this shape:
{{
  "confident": true or false,
  "spice_rating": integer 0-5 or null,
  "reason": "if not confident, why (e.g. 'sources disagree', 'no specific content details found')"
}}"""

    try:
        raw = call_ollama(system, prompt)
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
      "kindle_unlimited": bool,
      "cover_image_url": str|None,
      "description": str|None,
      "series_position": int|None,
      "series_total": int|None,
      "series_name": str|None,
      "sources": [{"type": str, "url": str}],
    }
    """
    findings, result_titles = gather_spice_findings(title, author)
    amazon_url = find_amazon_listing(title, author)
    goodreads_url = find_goodreads_book_page(title, author)

    sources = [
        {"type": f["source_type"], "url": f["url"]} for f in findings
    ]
    if goodreads_url and not any(s["url"] == goodreads_url for s in sources):
        sources.append({"type": "goodreads", "url": goodreads_url})

    page_count = None
    kindle_unlimited = False
    cover_image_url = None
    description = None
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
            kindle_unlimited = kindle_unlimited or details["kindle_unlimited"]
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
