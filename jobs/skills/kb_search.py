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


def _extract_query(message: str) -> str:
    msg = message.strip()
    msg_lower = msg.lower()
    for phrase in sorted(_TRIGGER_PHRASES, key=len, reverse=True):
        if msg_lower.startswith(phrase):
            return msg[len(phrase):].strip()
        idx = msg_lower.find(phrase)
        if idx != -1:
            return (msg[:idx] + msg[idx + len(phrase):]).strip()
    return msg


def _score_text(text: str, terms: list[str]) -> int:
    t = text.lower()
    return sum(1 for term in terms if term in t)


def run(message: str) -> str:
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

    lines = [f"Found {len(top_files)} relevant document(s) for '{query}':\n"]
    for _, filepath, excerpts in top_files:
        lines.append(f"📄 {filepath.stem}")
        for excerpt in excerpts:
            short = excerpt[:300]
            if len(excerpt) > 300:
                short += "..."
            lines.append(short)
        lines.append("---")

    return "\n".join(lines).rstrip("\n-").strip()
