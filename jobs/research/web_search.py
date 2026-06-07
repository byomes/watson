"""jobs/research/web_search.py — Web search via Serper.dev (Google results)."""
import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

SERPER_URL = "https://google.serper.dev/search"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

log = logging.getLogger(__name__)


def search(query: str, max_results: int = 5) -> list[dict]:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        log.error("SERPER_API_KEY not set")
        return []

    try:
        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Serper request failed: %s", exc)
        return []

    results = []

    answer_box = data.get("answerBox")
    if answer_box:
        snippet = answer_box.get("answer") or answer_box.get("snippet", "")
        if snippet:
            results.append({
                "title": "Direct Answer",
                "snippet": snippet,
                "url": answer_box.get("link", ""),
            })

    for item in data.get("organic", [])[:max_results]:
        results.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "url": item.get("link", ""),
        })

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

    log.info("web_search invoked: message=%r", message[:120] if message else "")
    query = _extract_query(message)
    results = search(query)

    if not results:
        return f"No results found for: {query}"

    lines = [f"🔍 Search results for: {query}\n"]
    for r in results:
        snippet = r["snippet"][:200]
        url_line = f"\n  Source: {r['url']}" if r["url"] else ""
        lines.append(f"• {r['title']}\n  {snippet}{url_line}\n")

    return "\n".join(lines)
