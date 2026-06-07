"""jobs/research/semantic_search.py — semantic similarity search over Watson memory files."""
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
MODEL_NAME = "all-MiniLM-L6-v2"

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def encode_text(text: str) -> list:
    model = _get_model()
    return model.encode(text).tolist()


def find_similar(query: str, texts: list) -> list:
    if not texts:
        return []
    try:
        import numpy as np
        model = _get_model()
        q_vec = model.encode(query)
        t_vecs = model.encode(texts)
        q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-9)
        t_norms = t_vecs / (np.linalg.norm(t_vecs, axis=1, keepdims=True) + 1e-9)
        scores = t_norms @ q_norm
        ranked = sorted(zip(scores.tolist(), texts), reverse=True)
        return ranked
    except Exception as exc:
        log.error("find_similar failed: %s", exc)
        return []


def search_memory(query: str) -> str:
    md_files = list(MEMORY.rglob("*.md"))
    if not md_files:
        return "No memory files found."

    texts = []
    labels = []
    for path in md_files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore").strip()
            if content:
                # Use first 500 chars as the search chunk
                chunk = content[:500].replace("\n", " ")
                texts.append(chunk)
                labels.append(str(path.relative_to(REPO)))
        except Exception:
            continue

    if not texts:
        return "Memory files are empty."

    ranked = find_similar(query, texts)
    top = ranked[:3]

    lines = [f"Top memory matches for: {query}\n"]
    for score, chunk in top:
        label = labels[texts.index(chunk)]
        lines.append(f"• {label} (score: {score:.2f})")
        lines.append(f"  {chunk[:120]}...")
    return "\n".join(lines)


def run(message: str = None) -> str:
    if not message:
        return "Semantic search ready. Ask me what you know about a topic."

    query = re.sub(r"(?i)(search my memory|find related content|semantic search|what do i know about)\s*:?\s*", "", message).strip()
    if not query:
        return "Please provide a search query."

    return search_memory(query)
