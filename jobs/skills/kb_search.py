"""KB search skill — searches ~/watson/kb/documents/ (or KB_LOCAL_DIR) for query terms."""
import os
import re
from pathlib import Path

_TRIGGER_PHRASES = (
    "search kb",
    "search my notes",
    "search my sermons",
    "what have i said about",
    "what did i preach on",
    "find in my notes",
    "look in my sermons",
    "kb search",
)

_STOP_WORDS = frozenset({
    "the", "a", "an", "in", "on", "of", "to", "for", "and", "or",
    "is", "it", "that", "this", "with", "from", "as", "at", "be",
    "was", "are", "by",
})

_KB_DEFAULT = Path.home() / "watson" / "kb" / "documents"
_MAX_FILE_BYTES = 500 * 1024  # 500 KB


_STRIP_LEAD_WORDS = frozenset({
    "and", "summarize", "my", "teaching", "on", "the", "about",
    "position", "please", "can", "you", "tell", "me", "what", "is",
})


def _extract_query(message: str) -> str:
    msg = message.strip()
    msg_lower = msg.lower()
    for phrase in sorted(_TRIGGER_PHRASES, key=len, reverse=True):
        if msg_lower.startswith(phrase):
            msg = msg[len(phrase):].strip()
            break
        idx = msg_lower.find(phrase)
        if idx != -1:
            msg = (msg[:idx] + msg[idx + len(phrase):]).strip()
            break
    # Strip filler words from the start word by word
    words = msg.split()
    while words and words[0].lower() in _STRIP_LEAD_WORDS:
        words = words[1:]
    return " ".join(words)


def _score_text(text: str, terms: list[str]) -> int:
    t = text.lower()
    return sum(1 for term in terms if term in t)


def run(message: str = None) -> str:
    if not message:
        return "Please provide a search term."
    kb_dir = Path(os.getenv("KB_LOCAL_DIR", str(_KB_DEFAULT)))
    query = _extract_query(message)
    if not query:
        return "What should I search for in your notes?"

    raw_terms = re.sub(r"[^a-z0-9\s]", "", query.lower()).split()
    terms = [t for t in raw_terms if t and t not in _STOP_WORDS]
    if not terms:
        return f"No meaningful search terms found in '{query}'."

    if not kb_dir.exists():
        return f"Knowledge base directory not found: {kb_dir}"

    file_results = []
    for filepath in kb_dir.rglob("*"):
        if not filepath.is_file():
            continue
        if filepath.stat().st_size > _MAX_FILE_BYTES:
            continue
        try:
            text = filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        file_score = _score_text(text, terms)
        if file_score == 0:
            continue
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        scored_paras = sorted(
            ((p, _score_text(p, terms)) for p in paragraphs if _score_text(p, terms) > 0),
            key=lambda x: x[1],
            reverse=True,
        )
        top_paras = [p for p, _ in scored_paras[:3]]
        file_results.append((file_score, filepath, top_paras))

    file_results.sort(key=lambda x: x[0], reverse=True)
    top_files = file_results[:5]

    if not top_files:
        return f"No matches found in your notes for '{query}'."

    all_excerpts = []
    filenames = []
    for _, filepath, excerpts in top_files:
        filenames.append(filepath.stem)
        all_excerpts.extend(excerpts)

    context = "\n\n---\n\n".join(all_excerpts)[:4000]

    try:
        import requests as _req
        resp = _req.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": (
                    "You are reviewing the personal sermons, Bible studies, and ministry notes of "
                    "Dr. Bill Yomes, pastor of Catalyst Community Church in Wilmington, DE. "
                    "Based only on the following excerpts from his work, answer this question or "
                    f"summarize what his materials say about this topic: '{query}'\n\n"
                    f"Excerpts:\n{context}\n\n"
                    "Provide a clear, concise summary in 3-5 sentences. "
                    "Speak in third person about Dr. Bill's teaching."
                ),
                "stream": False,
            },
            timeout=45,
        )
        resp.raise_for_status()
        summary = resp.json().get("response", "").strip()
        sources = ", ".join(filenames)
        return f"📚 From your notes on '{query}':\n\n{summary}\n\n---\nSources: {sources}"
    except Exception:
        lines = [
            f"Found {len(top_files)} relevant document(s) for '{query}':\n",
            "(Summary unavailable — showing raw excerpts)",
        ]
        for _, filepath, excerpts in top_files:
            lines.append(f"📄 {filepath.stem}")
            for excerpt in excerpts:
                short = excerpt[:500]
                if len(excerpt) > 500:
                    short += "..."
                lines.append(short)
            lines.append("---")
        return "\n".join(lines).rstrip("\n-").strip()
