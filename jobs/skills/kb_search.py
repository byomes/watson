import chromadb
from chromadb.utils import embedding_functions
import requests
import os

CHROMA_PATH = "/home/billyomes/watson/data/chroma"
COLLECTION_NAME = "sermons"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"

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

def search_kb(query: str) -> dict:
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2",
        device="cpu",
        local_files_only=True
    )
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_collection(COLLECTION_NAME, embedding_function=ef)
    results = collection.query(query_texts=[query], n_results=3)

    chunks = [_trim_excerpt(c, query) for c in results["documents"][0]]
    sources = list(dict.fromkeys([
        m["title"] for m in results["metadatas"][0]
    ]))

    excerpts = "\n\n".join(chunks)
    response = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": SYNOPSIS_PROMPT.format(query=query, excerpts=excerpts),
        "stream": False
    }, timeout=120)
    response.raise_for_status()
    synopsis = response.json().get("response", "").strip()

    return {"synopsis": synopsis, "sources": sources, "query": query}

def format_result(result: dict) -> str:
    sources_list = "\n".join(f"• {s}" for s in result["sources"])
    return f"{result['synopsis']}\n\nSources:\n{sources_list}\n\nReply \"email that to me\" to send this to your inbox."
