import logging
import sys
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions
import requests

log = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
CHROMA_DIR = BASE_DIR / "data" / "chroma"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5-coder:7b"
TOP_K = 5

def search(question, k=TOP_K):
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    collection = client.get_collection(name="sermons", embedding_function=ef)
    results = collection.query(query_texts=[question], n_results=k)
    chunks = []
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        chunks.append({"title": meta["title"], "text": doc})
    return chunks

def synthesize(question, chunks, memory_context=""):
    context = ""
    for c in chunks:
        context += "--- From: " + c["title"] + " ---\n" + c["text"] + "\n\n"
    prompt = "You are a helpful assistant with access to sermon transcripts from Pastor Bill Yomes. Answer the following question using only the provided sermon excerpts. Be specific and reference which sermons your answer draws from.\n\n"
    if memory_context:
        prompt += memory_context + "\n\n## Current Message\n"
    prompt += "Question: " + question + "\n\nSermon excerpts:\n" + context + "\n\nAnswer:"
    resp = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}, timeout=240)
    resp.raise_for_status()
    return resp.json()["response"].strip()

def ask(question):
    from jobs.memory_manager import build_context, append_working_memory, detect_topic, append_project_memory
    log.info("Searching knowledge base for: %s", question)
    chunks = search(question)
    if not chunks:
        return "No relevant sermons found for that question."
    log.info("Found %d relevant chunks, synthesizing...", len(chunks))
    memory_context = build_context(question)
    answer = synthesize(question, chunks, memory_context)
    sources = list(dict.fromkeys(c["title"] for c in chunks))
    source_list = "\n".join("- " + s for s in sources)
    result = answer + "\n\nSources:\n" + source_list
    append_working_memory(question, answer)
    topic = detect_topic(question)
    if topic:
        append_project_memory(topic, f"Discussed: {question[:80]}")
    return result

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if len(sys.argv) < 2:
        print("Usage: python3 jobs/ask.py your question here")
        sys.exit(1)
    question = " ".join(sys.argv[1:])
    print(ask(question))

if __name__ == "__main__":
    main()
