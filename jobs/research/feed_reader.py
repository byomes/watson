"""jobs/research/feed_reader.py — Parse RSS/Atom feeds and return latest entries."""
import logging
import re

log = logging.getLogger(__name__)

_URL_RE = re.compile(r'https?://[^\s]+')


def parse_feed(url: str) -> list:
    import feedparser
    feed = feedparser.parse(url)
    entries = []
    for entry in feed.entries[:10]:
        entries.append({
            "title": entry.get("title", ""),
            "summary": entry.get("summary", ""),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
        })
    return entries


def run(message: str = None) -> str:
    if not message:
        return "Feed reader ready. Provide an RSS/Atom feed URL."
    match = _URL_RE.search(message)
    if not match:
        return "No URL found in message. Please include a full feed URL."
    url = match.group(0)
    try:
        entries = parse_feed(url)
    except Exception as exc:
        log.error("feed_reader failed: %s", exc)
        return f"Failed to parse feed: {exc}"
    if not entries:
        return f"No entries found in feed: {url}"
    lines = [f"Feed: {url}\n"]
    for i, e in enumerate(entries, 1):
        pub = f" ({e['published']})" if e["published"] else ""
        summary = e["summary"][:200].replace("\n", " ") if e["summary"] else ""
        lines.append(f"{i}. {e['title']}{pub}")
        if summary:
            lines.append(f"   {summary}")
        if e["link"]:
            lines.append(f"   {e['link']}")
        lines.append("")
    return "\n".join(lines).strip()
