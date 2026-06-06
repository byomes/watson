"""jobs/utilities/text_processor.py — Summarize text, extract keywords, convert HTML to markdown."""
import logging
import re

log = logging.getLogger(__name__)


def summarize(text: str, sentences: int = 3) -> str:
    try:
        import nltk
        from nltk.tokenize import sent_tokenize, word_tokenize
        from nltk.corpus import stopwords
        from collections import Counter

        sents = sent_tokenize(text)
        if len(sents) <= sentences:
            return text

        stop_words = set(stopwords.words("english"))
        words = [w.lower() for w in word_tokenize(text) if w.isalpha() and w.lower() not in stop_words]
        freq = Counter(words)

        def score(sent):
            return sum(freq.get(w.lower(), 0) for w in word_tokenize(sent) if w.isalpha())

        ranked = sorted(sents, key=score, reverse=True)[:sentences]
        ordered = [s for s in sents if s in ranked]
        return " ".join(ordered)
    except Exception as exc:
        log.warning("summarize failed: %s", exc)
        return text[:500]


def extract_keywords(text: str, count: int = 10) -> list:
    try:
        import nltk
        from nltk.tokenize import word_tokenize
        from nltk.corpus import stopwords
        from collections import Counter

        stop_words = set(stopwords.words("english"))
        words = [w.lower() for w in word_tokenize(text) if w.isalpha() and len(w) > 3 and w.lower() not in stop_words]
        return [w for w, _ in Counter(words).most_common(count)]
    except Exception as exc:
        log.warning("extract_keywords failed: %s", exc)
        return []


def html_to_markdown(html: str) -> str:
    try:
        import markdownify
        return markdownify.markdownify(html, heading_style="ATX").strip()
    except Exception as exc:
        log.warning("html_to_markdown failed: %s", exc)
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").get_text()


def run(message: str = None) -> str:
    if not message:
        return "Text processor ready."
    summary = summarize(message)
    keywords = extract_keywords(message)
    kw_line = f"\nKeywords: {', '.join(keywords)}" if keywords else ""
    return f"{summary}{kw_line}"
