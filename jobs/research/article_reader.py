"""jobs/research/article_reader.py — Fetch full text of articles and web pages."""
import logging
import re

log = logging.getLogger(__name__)

_URL_RE = re.compile(r'https?://[^\s]+')


def fetch_article(url: str) -> dict:
    result = {"title": "", "text": "", "url": url}
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=True)
            meta = trafilatura.extract_metadata(downloaded)
            if text:
                result["text"] = text
                result["title"] = meta.title if meta and meta.title else ""
                return result
    except Exception as exc:
        log.warning("trafilatura failed: %s", exc)

    try:
        from newspaper import Article
        art = Article(url)
        art.download()
        art.parse()
        result["title"] = art.title or ""
        result["text"] = art.text or ""
    except Exception as exc:
        log.error("newspaper3k failed: %s", exc)

    return result


def run(message: str = None) -> str:
    if not message:
        return "Article reader ready. Provide a URL to fetch."
    match = _URL_RE.search(message)
    if not match:
        return "No URL found in message. Please include a full URL starting with http."
    article = fetch_article(match.group(0))
    if not article["text"]:
        return f"Could not extract text from {article['url']}."
    title = f"Title: {article['title']}\n\n" if article["title"] else ""
    preview = article["text"][:2000]
    suffix = f"\n\n[{len(article['text'])} chars total]" if len(article["text"]) > 2000 else ""
    return f"{title}{preview}{suffix}"
