"""kb_export.py — Zip matching KB source files and return zip path + caption."""
import io
import logging
import tempfile
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DOCUMENTS_DIR = BASE_DIR / "kb" / "documents"

_PREFIX = "kb export:"


def _extract_query(message: str) -> str:
    msg = message.strip()
    if msg.lower().startswith(_PREFIX):
        msg = msg[len(_PREFIX):].strip()
    return msg


def run(message: str = None) -> dict:
    """Search ChromaDB for query, zip matching source files.

    Returns dict with keys: ok, zip_path, caption, query, error.
    Caller is responsible for deleting zip_path after use.
    """
    if not message:
        return {"ok": False, "error": "No message provided."}

    query = _extract_query(message)
    if not query:
        return {"ok": False, "error": "What would you like to export from the knowledge base?"}

    try:
        from jobs.ask import search
        chunks = search(query)
    except Exception as exc:
        log.error("ChromaDB search failed: %s", exc)
        return {"ok": False, "error": f"Knowledge base search failed: {exc}"}

    if not chunks:
        return {"ok": False, "error": f"No results found in the knowledge base for '{query}'."}

    # Deduplicate source stems preserving order
    seen: set[str] = set()
    source_stems: list[str] = []
    for chunk in chunks:
        stem = chunk["title"]
        if stem not in seen:
            seen.add(stem)
            source_stems.append(stem)

    # Match stems to actual files in kb/documents/
    matched: list[Path] = []
    for stem in source_stems:
        for path in DOCUMENTS_DIR.iterdir():
            if path.is_file() and path.stem == stem:
                matched.append(path)
                break

    if not matched:
        stems_list = ", ".join(source_stems)
        return {"ok": False, "error": f"Source files not found in kb/documents/ for: {stems_list}"}

    # Build zip in a named temp file
    tmp = tempfile.NamedTemporaryFile(
        suffix=".zip", prefix="kb_export_", delete=False
    )
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in matched:
                zf.write(path, path.name)
        tmp.close()
    except Exception as exc:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        log.error("Zip creation failed: %s", exc)
        return {"ok": False, "error": f"Failed to create zip: {exc}"}

    caption = f"📦 KB Export: {query} — {len(matched)} file(s)"
    log.info("KB export: %d file(s) zipped for query '%s'", len(matched), query)
    return {"ok": True, "zip_path": tmp.name, "caption": caption, "query": query}
