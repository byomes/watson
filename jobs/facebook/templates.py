import re


def _first_sentences(text, n=2):
    """Return the first n sentences from text."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return ' '.join(sentences[:n])


def draft_post(title, summary, url):
    """Draft a Facebook post: title, 2-sentence excerpt, URL, hashtags."""
    excerpt = _first_sentences(summary, 2)
    return f"{title}\n\n{excerpt}\n\n{url}\n\n#Apologetics #Theology #Faith"
