import logging

import chromadb
from chromadb.utils import embedding_functions
import requests
import os
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py
from jobs.build_kb import boosted_distance, GHOSTWRITTEN_SOURCE_TYPE
from jobs.research.open_library import search as search_open_library

log = logging.getLogger(__name__)

CHROMA_PATH = "/home/billyomes/watson/data/chroma"
COLLECTION_NAME = "sermons"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"
RESULT_COUNT = 3
# Cosine distance above which the closest KB match is too weak to call a real
# answer (ChromaDB always returns its k-nearest neighbors regardless of
# relevance, so an empty result set never actually happens with a populated
# collection -- this is what decides "no good match" instead). Chosen
# empirically 2026-09-21 against the live sermons collection: genuinely
# on-topic queries ("forgiveness and reconciliation" 0.28, "prayer and faith"
# 0.39, "grief and loss" 0.48) all land well under this; clearly unrelated
# ones ("xyzzy nonexistent quantum bagpipe therapy topic" 0.79, "python
# programming syntax error" 0.77, "delaware public library card renewal"
# 0.69) land at or above it. Not a hard science -- retune if the library
# fallback fires too often or too rarely in practice.
MAX_RELEVANT_DISTANCE = 0.7
# Over-fetch so the year-based re-rank (jobs.build_kb.boosted_distance) has
# room to pull 2022+ sermon chunks above equally-relevant older ones before
# cutting to RESULT_COUNT. Only meaningful for the "sermons" collection —
# other collections (e.g. gutenberg/classics) have no `year` field.
FETCH_MULTIPLIER = 3

SYNOPSIS_PROMPT = """You are a research assistant summarizing content from a pastor's personal knowledge base of sermons and theological documents.

Based on the following excerpts, write a neutral 3-5 sentence synopsis answering the query. Be factual and concise. Do not add information not present in the excerpts.

Query: {query}

Excerpts:
{excerpts}

Return only the synopsis. No preamble, no commentary."""

EXCERPT_WINDOW = 500

def _trim_excerpt(text: str, query: str, window: int = EXCERPT_WINDOW) -> str:
    """Return a window of `text` centered on the first query-term hit, or the head of `text` if no hit is found."""
    lower_text = text.lower()
    pos = -1
    for term in query.lower().split():
        pos = lower_text.find(term)
        if pos != -1:
            break
    if pos == -1:
        return text[:window]
    half = window // 2
    start = max(0, pos - half)
    end = min(len(text), start + window)
    return text[start:end]

def search_kb(query: str, collection_name: str = COLLECTION_NAME, sermons_only: bool = False,
              ghostwritten_only: bool = False) -> dict:
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2",
        device="cpu",
        local_files_only=True
    )
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_collection(collection_name, embedding_function=ef)
    # source_type tiering only applies to the sermons collection (bible-study-note,
    # devotional, handout, transcript, ai-ghostwritten) -- gutenberg/classics chunks
    # have no source_type field.
    is_sermons = collection_name == COLLECTION_NAME
    if not is_sermons:
        where = None
    elif ghostwritten_only:
        where = {"source_type": GHOSTWRITTEN_SOURCE_TYPE}
    elif sermons_only:
        where = {"source_type": "transcript"}
    else:
        # Default and "expanded search" both exclude ai-ghostwritten -- it must
        # be explicitly requested, never blended in automatically.
        where = {"source_type": {"$ne": GHOSTWRITTEN_SOURCE_TYPE}}
    fetch_n = RESULT_COUNT * FETCH_MULTIPLIER if is_sermons else RESULT_COUNT
    results = collection.query(
        query_texts=[query], n_results=fetch_n, where=where,
        include=["documents", "metadatas", "distances"],
    )

    docs, metas, dists = results["documents"][0], results["metadatas"][0], results["distances"][0]
    if is_sermons:
        scored = [(boosted_distance(d, m.get("year")), m, doc) for doc, m, d in zip(docs, metas, dists)]
        scored.sort(key=lambda row: row[0])
        top = scored[:RESULT_COUNT]
        top_distance = top[0][0] if top else None
        docs = [doc for _, _, doc in top]
        metas = [m for _, m, _ in top]
    else:
        top_distance = dists[0] if dists else None
        docs, metas = docs[:RESULT_COUNT], metas[:RESULT_COUNT]

    if not docs or top_distance is None or top_distance > MAX_RELEVANT_DISTANCE:
        # Watson's own KB has nothing relevant on this -- fall back to the
        # public library catalog (Open Library) so the reply is still useful
        # instead of forcing a synopsis out of an unrelated nearest-neighbor
        # match. Never lets a fallback failure (network, etc.) break the KB
        # search itself.
        try:
            library_hits = search_open_library(query, limit=5)
        except Exception as exc:
            log.warning("Open Library fallback search failed for %r: %s", query, exc)
            library_hits = []
        return {"synopsis": f"No results found for '{query}' in Watson's KB.", "sources": [],
                "query": query, "collection": collection_name, "sermons_only": sermons_only,
                "ghostwritten_only": ghostwritten_only, "library_fallback": library_hits}

    chunks = [_trim_excerpt(c, query) for c in docs]
    sources = list(dict.fromkeys([m["title"] for m in metas]))

    excerpts = "\n\n".join(chunks)
    response = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": SYNOPSIS_PROMPT.format(query=query, excerpts=excerpts),
        "stream": False
    }, timeout=120)
    response.raise_for_status()
    synopsis = response.json().get("response", "").strip()

    return {"synopsis": synopsis, "sources": sources, "query": query,
            "collection": collection_name, "sermons_only": sermons_only,
            "ghostwritten_only": ghostwritten_only}

def format_result(result: dict) -> str:
    library_hits = result.get("library_fallback")
    if library_hits is not None:
        if not library_hits:
            return result["synopsis"]
        lines = [result["synopsis"], "", "The public library catalog has:"]
        for hit in library_hits:
            year = hit["first_publish_year"] or "?"
            borrow = " (borrowable on Archive.org)" if hit["borrowable_on_archive"] else ""
            lines.append(f"• {hit['title']} — {hit['authors']} ({year}){borrow}")
        return "\n".join(lines)

    sources_list = "\n".join(f"• {s}" for s in result["sources"])
    out = f"{result['synopsis']}\n\nSources:\n{sources_list}"
    if sources_list:
        out += "\n\nReply \"email that to me\" to send this to your inbox."
    if result.get("collection", COLLECTION_NAME) == COLLECTION_NAME:
        if result.get("ghostwritten_only", False):
            out += "\n\nSearched AI-ghostwritten archive only."
        elif result.get("sermons_only", False):
            out += "\n\nSearched sermon transcripts only. Reply \"expanded search\" to include devotionals, bible study notes, and other KB content."
    return out
