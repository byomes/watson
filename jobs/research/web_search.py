"""jobs/research/web_search.py — Web search via DuckDuckGo Instant Answer API (no key required)."""
import logging
import urllib.parse

import requests

DDGO_URL = "https://api.duckduckgo.com/"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

log = logging.getLogger(__name__)


def search(query: str, max_results: int = 5) -> list[dict]:
    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
    }
    try:
        resp = requests.get(DDGO_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("DuckDuckGo request failed: %s", exc)
        return []

    results = []

    answer = data.get("Answer", "").strip()
    if answer:
        results.append({"title": "Direct Answer", "snippet": answer, "url": ""})

    abstract = data.get("AbstractText", "").strip()
    abstract_url = data.get("AbstractURL", "").strip()
    if abstract:
        results.append({"title": "Summary", "snippet": abstract, "url": abstract_url})

    for topic in data.get("RelatedTopics", []):
        if len(results) >= max_results + 2:
            break
        # RelatedTopics can contain nested groups — handle both forms
        if "Topics" in topic:
            for sub in topic["Topics"]:
                text = sub.get("Text", "").strip()
                url = sub.get("FirstURL", "").strip()
                if text:
                    results.append({"title": text[:60], "snippet": text, "url": url})
        else:
            text = topic.get("Text", "").strip()
            url = topic.get("FirstURL", "").strip()
            if text:
                results.append({"title": text[:60], "snippet": text, "url": url})

    return results


def _extract_query(message: str) -> str:
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": f"Extract only the search query from this message. Return just the search terms, nothing else.\n\nMessage: {message}",
                "stream": False,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip() or message.strip()
    except Exception as exc:
        log.warning("Query extraction failed, using raw message: %s", exc)
        return message.strip()


def run(message: str = None) -> str:
    if message is None:
        return "Web search ready. Ask me to search for anything."

    query = _extract_query(message)
    results = search(query)

    if not results:
        return f"No results found for: {query}"

    lines = [f"🔍 Search results for: {query}\n"]
    for r in results:
        snippet = r["snippet"][:200]
        url_line = f"  {r['url']}" if r["url"] else ""
        lines.append(f"• {r['title']}\n  {snippet}{url_line}\n")

    return "\n".join(lines)
