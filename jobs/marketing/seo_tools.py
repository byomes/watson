"""jobs/marketing/seo_tools.py — SEO page analysis, sitemap generation, keyword suggestions."""
import logging
import re

log = logging.getLogger(__name__)
_URL_RE = re.compile(r'https?://[^\s]+')


def generate_sitemap(base_url: str, pages: list) -> str:
    base_url = base_url.rstrip("/")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for page in pages:
        url = f"{base_url}/{page.lstrip('/')}"
        lines.append(f"  <url><loc>{url}</loc></url>")
    lines.append("</urlset>")
    return "\n".join(lines)


def analyze_page_seo(url: str) -> dict:
    import requests
    from bs4 import BeautifulSoup
    result = {"url": url, "title": "", "meta_description": "", "h1_tags": [],
              "images_missing_alt": 0, "word_count": 0, "issues": []}
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "WatsonSEOBot/1.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title_tag = soup.find("title")
        result["title"] = title_tag.get_text().strip() if title_tag else ""
        if not result["title"]:
            result["issues"].append("Missing <title> tag")
        elif len(result["title"]) < 30:
            result["issues"].append(f"Title too short ({len(result['title'])} chars, aim for 50-60)")
        elif len(result["title"]) > 65:
            result["issues"].append(f"Title too long ({len(result['title'])} chars, aim for 50-60)")

        meta_desc = soup.find("meta", attrs={"name": "description"})
        result["meta_description"] = meta_desc.get("content", "").strip() if meta_desc else ""
        if not result["meta_description"]:
            result["issues"].append("Missing meta description")

        h1s = soup.find_all("h1")
        result["h1_tags"] = [h.get_text().strip() for h in h1s]
        if not h1s:
            result["issues"].append("No <h1> tag found")
        elif len(h1s) > 1:
            result["issues"].append(f"Multiple <h1> tags ({len(h1s)}) — use only one")

        imgs = soup.find_all("img")
        missing_alt = sum(1 for img in imgs if not img.get("alt"))
        result["images_missing_alt"] = missing_alt
        if missing_alt:
            result["issues"].append(f"{missing_alt} image(s) missing alt text")

        body_text = soup.get_text(separator=" ")
        result["word_count"] = len(body_text.split())
        if result["word_count"] < 300:
            result["issues"].append(f"Low word count ({result['word_count']}) — aim for 500+")

    except Exception as exc:
        log.error("analyze_page_seo failed: %s", exc)
        result["issues"].append(f"Fetch error: {exc}")
    return result


def suggest_keywords(content: str) -> list:
    try:
        from jobs.utilities.text_processor import extract_keywords
        return extract_keywords(content, count=15)
    except Exception as exc:
        log.warning("suggest_keywords failed: %s", exc)
        words = re.findall(r'\b[a-z]{4,}\b', content.lower())
        from collections import Counter
        return [w for w, _ in Counter(words).most_common(15)]


def run(message: str = None) -> str:
    if not message:
        return "SEO tools ready. Provide a URL to analyze."
    match = _URL_RE.search(message)
    if not match:
        return "No URL found in message."
    report = analyze_page_seo(match.group(0))
    lines = [
        f"SEO Report: {report['url']}",
        f"──────────────────────────",
        f"Title ({len(report['title'])} chars): {report['title'] or '(missing)'}",
        f"Meta desc: {report['meta_description'][:80] or '(missing)'}",
        f"H1 tags: {len(report['h1_tags'])}",
        f"Word count: {report['word_count']}",
        f"Images missing alt: {report['images_missing_alt']}",
    ]
    if report["issues"]:
        lines.append(f"\nIssues ({len(report['issues'])}):")
        for issue in report["issues"]:
            lines.append(f"  ⚠ {issue}")
    else:
        lines.append("\n✓ No SEO issues found")
    return "\n".join(lines)
