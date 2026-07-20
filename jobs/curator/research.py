"""jobs/curator/research.py — spice rating / KU / page-count research pass.

Never guesses, and never blends. Spice content is *extracted*, not
synthesized: each trusted source (see _EXACT_DOMAINS / _categorize_source)
gets a full-page fetch and a verbatim excerpt pulled from its own text via
keyword-proximity — no LLM paraphrasing step touches the wording the detail
page shows. The 0-5 spice_rating number is still a judgment call, made by an
Ollama pass that weighs the extracted excerpts, and only fires when >=2 real
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

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

_MIN_FINDINGS = 2
_MAX_DISPLAYED_FINDINGS = 4

# Ranked trusted sources for spice-content research, highest priority first.
# All get a full-page fetch + verbatim excerpt extraction, never an LLM-blended
# summary. Amazon reviews and generic blogs rank below all of these and are
# never fetched in full for spice content — supplementary "sources checked"
# context only, never the primary basis for a rating.
_EXACT_DOMAINS = [
    ("pluggedin.com", "Plugged In", "pluggedin", 1),
    ("booktriggerwarnings.com", "Book Trigger Warnings", "booktriggerwarnings", 2),
    ("commonsensemedia.org", "Common Sense Media", "commonsensemedia", 3),
]
# Best-effort domain-name heuristic for "dedicated clean-romance review blogs" —
# there's no fixed list of these, so this is opportunistic ("if found" per spec),
# unlike the exact-domain matches above.
_CLEAN_BLOG_HINTS = ("cleanread", "cleanromance", "cleanfiction", "spicemeter", "spicerating")

_SPICE_KEYWORDS = [
    "spice rating", "spice level", "sexual content", "sex scene", "content warning",
    "trigger warning", "explicit", "closed door", "open door", "fade to black",
    "making out", "make out", "kissing", "intimate", "intimacy", "bedroom",
    "mature content", "steamy", "romance content",
]


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
    """Returns (source_name, source_type, rank) if url matches a trusted
    category, else None. Lower rank = higher priority."""
    host = urlparse(url).netloc.lower()
    for domain, name, stype, rank in _EXACT_DOMAINS:
        if domain in host:
            return (name, stype, rank)
    if any(hint in host for hint in _CLEAN_BLOG_HINTS):
        return (host.replace("www.", ""), "clean_romance_blog", 4)
    if "goodreads.com" in host and ("/questions/" in url or "/review" in url):
        return ("Goodreads reader", "goodreads_reader", 5)
    if "reddit.com" in host:
        return ("Reddit", "reddit", 6)
    return None


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"&#39;|&rsquo;|&#8217;", "'", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_relevant_excerpt(text: str, window: int = 220) -> str | None:
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
    """One combined domain-restricted search for the top 3 exact-match trusted
    sources (pluggedin/booktriggerwarnings/commonsensemedia) — cheaper than a
    separate Serper call per domain."""
    who = f"{title} {author}" if author else title
    site_filter = " OR ".join(f"site:{d}" for d, *_ in _EXACT_DOMAINS)
    return serper_search(f"{who} ({site_filter})", max_results=6)


def search_spice_content(title: str, author: str | None) -> list[dict]:
    """General spice-content search — surfaces clean-romance blogs, Goodreads
    reader Q&A/reviews, and Reddit discussion for categorization."""
    who = f"{title} {author}" if author else title
    queries = [
        f"{who} spice rating content warnings",
        f"{who} goodreads sexual content review",
    ]
    results: list[dict] = []
    seen_urls = set()
    for q in queries:
        for r in serper_search(q, max_results=5):
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(r)
    return results


def gather_spice_findings(title: str, author: str | None) -> tuple[list[dict], list[str]]:
    """Returns (findings, all_result_titles).
    findings: ranked, deduped-by-category list of
    {"source_name","source_type","rank","excerpt","url"} — only entries where a
    real verbatim excerpt was actually found, capped at _MAX_DISPLAYED_FINDINGS.
    all_result_titles: every search-result title seen, for author extraction."""
    top_results = search_top_content_sites(title, author)
    generic_results = search_spice_content(title, author)
    all_results = top_results + generic_results

    best_per_category: dict[str, tuple[str, str, int]] = {}
    for r in all_results:
        url = r.get("url", "")
        if not url:
            continue
        cat = _categorize_source(url)
        if not cat:
            continue
        name, stype, rank = cat
        if stype not in best_per_category or rank < best_per_category[stype][2]:
            best_per_category[stype] = (name, url, rank)

    findings = []
    for stype, (name, url, rank) in best_per_category.items():
        text = fetch_full_text(url)
        if not text:
            continue
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
