import logging
import re
import string
from collections import Counter

from core.database import get_connection

log = logging.getLogger(__name__)

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "this", "that", "these", "those", "it", "its",
    "not", "from", "as", "he", "she", "they", "we", "you", "i", "his", "her",
    "their", "our", "my", "your", "also", "about", "up", "out", "into",
    "than", "so", "if", "what", "which", "who", "when", "where", "how",
    "all", "each", "more", "other", "can", "just", "then", "no", "very",
    "there", "here", "been", "some", "such", "even", "after", "before",
    "while", "through", "over", "under", "between", "against", "during",
}

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _tokenize(text):
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return [w for w in text.split() if w not in _STOPWORDS and len(w) > 2]


def _split_sentences(text):
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


def summarize(title, content, source_name=""):
    """Return a 2-3 sentence extractive summary. Falls back to title if content is empty."""
    content = (content or "").strip()
    if not content:
        return title

    sentences = _split_sentences(content)
    if not sentences:
        return title
    if len(sentences) <= 2:
        return " ".join(sentences)

    all_words = _tokenize(content)
    if not all_words:
        return sentences[0] if sentences else title

    word_freq = Counter(all_words)
    max_freq = max(word_freq.values())

    scored = []
    for i, sent in enumerate(sentences):
        words = _tokenize(sent)
        if not words:
            continue

        # keyword score: sum of relative frequencies, normalized by sentence length
        kw = sum(word_freq[w] / max_freq for w in words) / len(words)

        # position bonus: first sentence matters most
        pos = 1.0 / (i + 1) * 0.4

        # length score: prefer 10–40 word sentences
        word_count = len(sent.split())
        if 10 <= word_count <= 40:
            ln = 1.0
        elif word_count < 10:
            ln = word_count / 10.0
        else:
            ln = 40.0 / word_count

        scored.append((kw + pos + ln * 0.2, i, sent))

    if not scored:
        return title

    n = 3 if len(sentences) >= 6 else 2
    top = sorted(scored, reverse=True)[:n]
    top.sort(key=lambda x: x[1])  # restore original order
    return " ".join(s for _, _, s in top)


def summarize_items():
    """Re-summarize all new items in the legacy `items` table using extractive method."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, url, summary FROM items WHERE status = 'new'"
        ).fetchall()

    if not rows:
        log.info("No new items to summarize")
        return 0

    updated = 0
    for row in rows:
        content = row["summary"] or ""
        new_summary = summarize(row["title"], content)
        with get_connection() as conn:
            conn.execute(
                "UPDATE items SET summary = ? WHERE id = ?",
                (new_summary, row["id"]),
            )
        updated += 1

    log.info("Summarized %d item(s)", updated)
    return updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    count = summarize_items()
    print(f"\nDone. {count} item(s) summarized.")
