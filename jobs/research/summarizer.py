"""jobs/research/summarizer.py — summarize text using LSA and extract topics via LDA."""
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]


def _lsa_summarize(text: str, sentence_count: int = 5) -> str:
    from sumy.parsers.plaintext import PlaintextParser
    from sumy.nlp.tokenizers import Tokenizer
    from sumy.summarizers.lsa import LsaSummarizer
    from sumy.nlp.stemmers import Stemmer
    from sumy.utils import get_stop_words

    parser = PlaintextParser.from_string(text, Tokenizer("english"))
    stemmer = Stemmer("english")
    summarizer = LsaSummarizer(stemmer)
    summarizer.stop_words = get_stop_words("english")
    sentences = summarizer(parser.document, sentence_count)
    return " ".join(str(s) for s in sentences)


def _lda_topics(text: str, num_topics: int = 5, num_words: int = 5) -> list:
    from gensim import corpora, models

    words = [w.lower() for w in re.findall(r'\b[a-z]{4,}\b', text)]
    stopwords = {
        "this", "that", "with", "from", "they", "have", "been", "were",
        "their", "which", "will", "also", "more", "some", "what", "when",
        "there", "then", "than", "these", "those", "about", "into", "such",
    }
    words = [w for w in words if w not in stopwords]

    if len(words) < 20:
        return []

    # Build corpus in chunks of 10 words (simulated "documents")
    chunk_size = 50
    docs = [words[i:i+chunk_size] for i in range(0, len(words), chunk_size)]
    docs = [d for d in docs if len(d) >= 5]
    if len(docs) < 2:
        docs = [words]

    dictionary = corpora.Dictionary(docs)
    corpus = [dictionary.doc2bow(d) for d in docs]

    if len(dictionary) < num_words:
        return []

    lda = models.LdaModel(
        corpus, num_topics=min(num_topics, len(docs)), id2word=dictionary,
        passes=5, random_state=42,
    )
    topics = []
    for idx, topic in lda.show_topics(num_topics=num_topics, num_words=num_words, formatted=False):
        topics.append([word for word, _ in topic])
    return topics


def summarize(text: str, sentence_count: int = 5, include_topics: bool = True) -> dict:
    if len(text.split()) < 30:
        return {"summary": text, "topics": [], "word_count": len(text.split())}

    try:
        summary = _lsa_summarize(text, sentence_count=sentence_count)
    except Exception as exc:
        log.warning("LSA summarize failed: %s", exc)
        sentences = re.split(r'(?<=[.!?])\s+', text)
        summary = " ".join(sentences[:sentence_count])

    topics = []
    if include_topics:
        try:
            topics = _lda_topics(text)
        except Exception as exc:
            log.warning("LDA topics failed: %s", exc)

    return {
        "summary": summary,
        "topics": topics,
        "word_count": len(text.split()),
        "summary_word_count": len(summary.split()),
    }


def summarize_file(file_path: str, sentence_count: int = 5) -> dict:
    p = Path(file_path).expanduser()
    if not p.exists():
        return {"summary": "", "topics": [], "error": f"File not found: {file_path}"}
    text = p.read_text(encoding="utf-8", errors="ignore")
    # Strip markdown formatting
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,2}(.+?)\*{1,2}', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    return summarize(text, sentence_count=sentence_count)


def run(message: str = None) -> str:
    if not message:
        return "Usage: summarize <text or file path> [in N sentences]"

    sentence_match = re.search(r'in\s+(\d+)\s+sentence', message, re.IGNORECASE)
    sentence_count = int(sentence_match.group(1)) if sentence_match else 5

    # Remove the sentence count clause for text extraction
    text_part = re.sub(r'\s+in\s+\d+\s+sentences?', '', message, flags=re.IGNORECASE).strip()

    # File path or inline text?
    if re.search(r'\.(txt|md|rst|pdf)$', text_part, re.IGNORECASE):
        result = summarize_file(text_part, sentence_count=sentence_count)
    else:
        result = summarize(text_part, sentence_count=sentence_count)

    if result.get("error"):
        return f"Summarize error: {result['error']}"

    lines = [
        f"Summary ({result['summary_word_count']} words from {result['word_count']}):",
        result["summary"],
    ]
    if result["topics"]:
        lines.append("\nTopics:")
        for i, topic in enumerate(result["topics"], 1):
            lines.append(f"  {i}. {', '.join(topic)}")
    return "\n".join(lines)
