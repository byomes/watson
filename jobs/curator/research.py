"""jobs/curator/research.py — spice rating / KU / page-count research pass.

Never guesses. If fewer than 2 sources are found, sources disagree, or the
synthesis model isn't confident, the caller gets confident=False and must
route the book to needs_review with no spice_rating set.
"""
import json
import logging
import re

import requests

from jobs.research.web_search import search as serper_search

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"  # accuracy-sensitive background job — see LLM Stack in WATSON_ARCHITECTURE.md

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

_MIN_SOURCES = 2


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


def search_spice_content(title: str, author: str | None) -> list[dict]:
    """Search Goodreads/StoryGraph/clean-romance review sites for content-rating info."""
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


def find_amazon_listing(title: str, author: str | None) -> str | None:
    who = f"{title} {author}" if author else title
    for r in serper_search(f"{who} amazon kindle", max_results=5):
        url = r.get("url", "")
        if "amazon.com" in url:
            return url
    return None


def fetch_amazon_details(url: str) -> dict:
    """Best-effort scrape of an Amazon listing for page count + Kindle Unlimited badge."""
    out = {"page_count": None, "kindle_unlimited": False, "fetched": False}
    try:
        resp = requests.get(url, headers=_UA, timeout=10)
        html = resp.text
        out["fetched"] = True

        out["kindle_unlimited"] = bool(
            re.search(r"kindle unlimited", html, re.IGNORECASE)
        )

        page_match = re.search(
            r'([\d,]+)\s*pages?\b', html, re.IGNORECASE
        )
        if page_match:
            try:
                out["page_count"] = int(page_match.group(1).replace(",", ""))
            except ValueError:
                pass
    except Exception as exc:
        log.warning("fetch_amazon_details failed for %s: %s", url, exc)
    return out


def synthesize_rating(title: str, author: str | None, snippets: list[dict]) -> dict:
    """Ask the model to read collected snippets and propose a 0-5 spice rating.
    Model is instructed to refuse (confident=False) rather than guess."""
    if len(snippets) < _MIN_SOURCES:
        return {
            "confident": False,
            "reason": f"Only {len(snippets)} source(s) found (need >= {_MIN_SOURCES})",
            "spice_rating": None,
            "spice_notes": "",
        }

    snippet_text = "\n\n".join(
        f"[{i+1}] {s.get('title', '')}\nURL: {s.get('url', '')}\n{s.get('snippet', '')}"
        for i, s in enumerate(snippets)
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

Search result snippets:
{snippet_text}

Based ONLY on this evidence, do the snippets agree on a spice level, and is the evidence
specific enough to be confident (not just "clean romance" marketing copy with no detail)?

Return JSON exactly in this shape:
{{
  "confident": true or false,
  "spice_rating": integer 0-5 or null,
  "spice_notes": "short note citing what was found, e.g. 'one closed-door scene, ch. 14' or empty string",
  "reason": "if not confident, why (e.g. 'sources disagree', 'no specific content details found')"
}}"""

    try:
        raw = call_ollama(system, prompt)
        parsed = parse_json(raw)
    except Exception as exc:
        log.error("synthesize_rating Ollama call failed: %s", exc)
        parsed = None

    if not parsed or "confident" not in parsed:
        return {
            "confident": False,
            "reason": "synthesis model call failed or returned unparseable output",
            "spice_rating": None,
            "spice_notes": "",
        }

    if not parsed.get("confident"):
        parsed.setdefault("spice_rating", None)
        parsed.setdefault("spice_notes", "")
        parsed.setdefault("reason", "model reported low confidence")
        return parsed

    rating = parsed.get("spice_rating")
    if not isinstance(rating, int) or not (0 <= rating <= 5):
        return {
            "confident": False,
            "reason": f"model returned invalid spice_rating: {rating!r}",
            "spice_rating": None,
            "spice_notes": "",
        }

    return parsed


def research_book(title: str, author: str | None = None) -> dict:
    """Full research pass. Returns:
    {
      "confident": bool,
      "reason": str,               # populated when not confident
      "spice_rating": int|None,
      "spice_notes": str,
      "page_count": int|None,
      "kindle_unlimited": bool,
      "sources": [{"type": str, "url": str}],
    }
    """
    spice_results = search_spice_content(title, author)
    amazon_url = find_amazon_listing(title, author)

    sources = [
        {"type": "goodreads" if "goodreads" in r.get("url", "") else "other", "url": r["url"]}
        for r in spice_results if r.get("url")
    ]

    page_count = None
    kindle_unlimited = False
    if amazon_url:
        details = fetch_amazon_details(amazon_url)
        page_count = details["page_count"]
        kindle_unlimited = details["kindle_unlimited"]
        sources.append({"type": "amazon", "url": amazon_url})

    rating_result = synthesize_rating(title, author, spice_results)

    return {
        "confident": bool(rating_result.get("confident")),
        "reason": rating_result.get("reason", ""),
        "spice_rating": rating_result.get("spice_rating"),
        "spice_notes": rating_result.get("spice_notes", ""),
        "page_count": page_count,
        "kindle_unlimited": kindle_unlimited,
        "sources": sources,
    }
