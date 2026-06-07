"""jobs/research/news_search.py — search news via GNews and trending via pytrends."""
import logging
import re

log = logging.getLogger(__name__)


def search_gnews(query: str, max_results: int = 5) -> list:
    try:
        from gnews import GNews
        g = GNews(language="en", country="US", max_results=max_results)
        articles = g.get_news(query)
        results = []
        for a in articles:
            results.append({
                "title": a.get("title", ""),
                "description": a.get("description", ""),
                "url": a.get("url", ""),
                "published": a.get("published date", ""),
            })
        return results
    except Exception as exc:
        log.error("GNews search failed: %s", exc)
        return []


def get_trending(topic: str = "theology") -> list:
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=360)
        pt.build_payload([topic], timeframe="now 7-d")
        related = pt.related_queries()
        rising = related.get(topic, {}).get("rising")
        if rising is not None and not rising.empty:
            return rising["query"].tolist()[:10]
        top = related.get(topic, {}).get("top")
        if top is not None and not top.empty:
            return top["query"].tolist()[:10]
        return []
    except Exception as exc:
        log.error("pytrends failed: %s", exc)
        return []


def run(message: str = None) -> str:
    if not message:
        return "News search ready. Ask me to search for news or trending topics."

    query = re.sub(r"(?i)(search news|latest news on|what is trending|current events about|news about)\s*:?\s*", "", message).strip()
    if not query:
        return "Please provide a search topic."

    results = search_gnews(query)
    if not results:
        return f"No news found for: {query}"

    lines = [f"News: {query}\n"]
    for a in results:
        lines.append(f"• {a['title']}")
        if a.get("description"):
            lines.append(f"  {a['description'][:120]}")
        if a.get("url"):
            lines.append(f"  {a['url']}")
    return "\n".join(lines)
