"""jobs/research/field_research.py -- on-demand field-engagement research
for Bill's writing projects (currently Guardrails).

Bill's standing rule (see the writing_digest master file this feeds into)
is that he personally verifies every field-engagement quote or statistic
before it goes in the book. This skill never blends or paraphrases a quote
-- it pulls the actual sentence(s) verbatim from a real fetched page and
hands back source + URL + exact quote, explicitly marked unverified. Same
"extract, don't blend" contract jobs/curator/research.py already uses for
spice-rating research; a fabricated-sounding quote that doesn't literally
appear in the fetched page text is dropped rather than trusted.

Trigger: on-demand only (Bill's choice, 2026-09-17) -- a Telegram/dashboard
skill (see memory/skills.json), not part of the nightly writing digest.
Runs through core.claude_tier.call_claude() first (budget-capped, same tier
several other Watson jobs already use), falling back to local Ollama
(qwen2.5:7b) if the tier is exhausted or unavailable -- never a direct
ANTHROPIC_API_KEY call.

Search sources (2026-09-18): general web (Serper) plus free scholarly
sources -- arXiv, Semantic Scholar (works keyless too, just rate-limited
harder without S2_API_KEY), OpenAlex, and CrossRef. Every candidate still
goes through the same fetch-a-real-page-and-pull-a-verbatim-quote gate
below, so a DOI landing page that turns out to be paywalled just yields no
quote rather than a fabricated one. Google Scholar is deliberately NOT a
source here -- it has no API, and scraping it violates its robots.txt and
ToS, risking Watson getting blocked.

Two additions (2026-09-18, after a side-by-side test against ChatGPT/
Gemini/Perplexity on a Christian-theology-and-AI topic surfaced two real
gaps):

1. A denominational/magisterial site search (_DENOMINATIONAL_SITES) --
   plain Serper web search rarely surfaces Vatican, SBC/ERLC, or similar
   institutional statement pages even when they're the single best source
   for this book's subject matter. All three external agents in that test
   found the Vatican's "Antiqua et nova" note; Watson's general web search
   alone did not.
2. An Unpaywall fallback (jobs/research/unpaywall.py) for any OpenAlex/
   CrossRef lead that carries a DOI: if the publisher landing page 403s
   (ResearchGate and Taylor & Francis both did in that test), look up a
   legal open-access mirror by DOI and retry the fetch there before giving
   up on the candidate.
"""
import logging
import re

import requests

from core.claude_tier import call_claude
from jobs.research.article_reader import fetch_article
from jobs.research.web_search import search as serper_search
from jobs.research.academic_search import search_arxiv, search_semantic_scholar
from jobs.research.openalex import search as openalex_search
from jobs.research.crossref import search as crossref_search
from jobs.research.unpaywall import find_oa_url
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"  # accuracy-sensitive extraction -- matches curator.research's choice

_MAX_SEARCH_RESULTS = 6
_MAX_PER_SCHOLARLY_SOURCE = 3
_MAX_QUOTES = 5
_MIN_PAGE_TEXT_LEN = 200

# High-signal Christian institutional/denominational sites that plain web
# search under-surfaces relative to how often they turn out to be the best
# source for this book's subject matter (Vatican, evangelical, Orthodox,
# mainline). Not exhaustive -- add to this list as new gaps show up.
_DENOMINATIONAL_SITES = [
    "vatican.va", "erlc.com", "sbc.net", "umc.org", "oca.org",
    "nae.org", "cslewisinstitute.org", "episcopalchurch.org",
]
_MAX_DENOMINATIONAL_RESULTS = 4

_TRIGGER_STRIP_RE = re.compile(
    r"^\s*(watson[,:]?\s*)?(run\s+)?field research(\s+skill)?\s*(on|for|about)?\s*:?\s*",
    re.IGNORECASE,
)


def _extract_topic(message: str) -> str:
    topic = _TRIGGER_STRIP_RE.sub("", message or "").strip()
    return topic or message.strip()


def _call_llm(system: str, prompt: str, job_name: str, trigger_message: str) -> str | None:
    claude_result = call_claude(
        system=system, user=prompt, job_name=job_name, message=trigger_message[:200],
    )
    if claude_result:
        return claude_result
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "system": system, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        return (resp.json().get("response") or "").strip()
    except Exception as exc:
        log.error("field_research: Ollama fallback failed: %s", exc)
        return None


def _pull_quote(topic: str, page_title: str, page_text: str, url: str) -> dict | None:
    if not page_text or len(page_text) < _MIN_PAGE_TEXT_LEN:
        return None

    system = (
        "You extract real, verbatim quotes from source text for a nonfiction "
        "book's field-engagement research. Never paraphrase or invent -- copy "
        "the exact sentence(s) as written. If nothing in the text is genuinely "
        "relevant to the topic, respond with exactly: NONE"
    )
    prompt = (
        f"Topic: {topic}\n\nSource text (from {page_title or url}):\n"
        f"{page_text[:6000]}\n\n"
        f"Find the single most relevant passage to the topic above. Respond in "
        f"exactly this format, no other text:\n"
        f"QUOTE: <the exact verbatim sentence(s), copied from the text above>\n"
        f"WHY: <one sentence on why this is relevant to the topic>"
    )
    raw = _call_llm(system, prompt, "research.field_research", topic)
    if not raw or raw.strip().upper().startswith("NONE"):
        return None

    quote_match = re.search(r"QUOTE:\s*(.+?)(?:\nWHY:|$)", raw, re.DOTALL)
    why_match = re.search(r"WHY:\s*(.+)", raw, re.DOTALL)
    quote = quote_match.group(1).strip().strip('"') if quote_match else None
    why = why_match.group(1).strip() if why_match else ""
    if not quote or quote.upper() == "NONE":
        return None

    # Guard against a hallucinated quote that doesn't actually appear in the
    # source -- check a leading slice rather than the whole quote, since
    # whitespace/quote-mark normalization can differ slightly.
    if quote[:40].lower() not in page_text.lower():
        log.warning("field_research: dropped a quote not found verbatim in source (%s)", url)
        return None

    return {"quote": quote, "why": why, "url": url, "title": page_title or url}


def _gather_candidate_urls(topic: str) -> list[dict]:
    """Pulls {title, url} leads from every wired-in free source, deduped by
    URL. Order matters -- general web first (most likely to be a readable
    page), scholarly sources after (more likely to be a paywalled landing
    page that simply yields no quote)."""
    leads: list[dict] = []
    seen_urls: set[str] = set()

    def _add(items):
        for item in items:
            url = item.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            leads.append({"title": item.get("title", ""), "url": url, "doi": item.get("doi", "")})

    _add(serper_search(topic, max_results=_MAX_SEARCH_RESULTS))

    try:
        sites = " OR ".join(f"site:{d}" for d in _DENOMINATIONAL_SITES)
        _add(serper_search(f"{topic} ({sites})", max_results=_MAX_DENOMINATIONAL_RESULTS))
    except Exception as exc:
        log.warning("field_research: denominational site search failed: %s", exc)

    try:
        _add(openalex_search(topic, max_results=_MAX_PER_SCHOLARLY_SOURCE))
    except Exception as exc:
        log.warning("field_research: OpenAlex source failed: %s", exc)

    try:
        _add(crossref_search(topic, max_results=_MAX_PER_SCHOLARLY_SOURCE))
    except Exception as exc:
        log.warning("field_research: CrossRef source failed: %s", exc)

    try:
        _add(search_semantic_scholar(topic, max_results=_MAX_PER_SCHOLARLY_SOURCE))
    except Exception as exc:
        log.warning("field_research: Semantic Scholar source failed: %s", exc)

    try:
        _add(search_arxiv(topic, max_results=_MAX_PER_SCHOLARLY_SOURCE))
    except Exception as exc:
        log.warning("field_research: arXiv source failed: %s", exc)

    return leads


def research(topic: str) -> list[dict]:
    """Search (web + free scholarly sources) -> fetch -> extract pipeline.
    Returns up to _MAX_QUOTES real, verbatim, source-linked candidates --
    never a synthesized summary."""
    leads = _gather_candidate_urls(topic)
    candidates = []
    for lead in leads:
        url = lead["url"]
        page = fetch_article(url)

        if len(page.get("text", "")) < _MIN_PAGE_TEXT_LEN and lead.get("doi"):
            oa_url = find_oa_url(lead["doi"])
            if oa_url and oa_url != url:
                oa_page = fetch_article(oa_url)
                if len(oa_page.get("text", "")) >= _MIN_PAGE_TEXT_LEN:
                    page, url = oa_page, oa_url

        found = _pull_quote(topic, page.get("title") or lead.get("title", ""), page.get("text", ""), url)
        if found:
            candidates.append(found)
        if len(candidates) >= _MAX_QUOTES:
            break
    return candidates


def run(message: str = None) -> str:
    if not message or not message.strip():
        return (
            "Field research ready. Tell me the topic or claim, e.g. "
            "\"field research on AI replacing pastoral counseling.\""
        )

    topic = _extract_topic(message)
    candidates = research(topic)

    if not candidates:
        return (
            f"No solid field-engagement material found for \"{topic}\" after "
            f"checking the top search results. Try rephrasing, or narrow it to "
            f"a more specific claim."
        )

    lines = [
        f"Field research: \"{topic}\" ({len(candidates)} candidate(s), "
        f"UNVERIFIED, confirm each before use):",
        "",
    ]
    for i, c in enumerate(candidates, 1):
        lines.append(f"{i}. {c['title']}")
        lines.append(f"   \"{c['quote']}\"")
        if c["why"]:
            lines.append(f"   Why: {c['why']}")
        lines.append(f"   {c['url']}")
        lines.append("")

    return "\n".join(lines).strip()
