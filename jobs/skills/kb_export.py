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


def extract_query(message: str, prefix: str = _PREFIX) -> str:
    """Strip a leading trigger prefix (e.g. "kb export:") off a raw skill
    message, if present. Also used by jobs.kb.export_link with its own
    "kb export link:" prefix, since a message reaching a skill via the
    router's keyword pre-check still has the trigger phrase attached."""
    msg = message.strip()
    if msg.lower().startswith(prefix):
        msg = msg[len(prefix):].strip()
    return msg


def search_and_zip(query: str) -> dict:
    """Search ChromaDB for query, zip matching source files.

    Shared by kb_export.run() (Telegram, sends the zip as a raw attachment)
    and jobs.kb.export_link.run() (dashboard/MCP, wraps the zip in an
    expiring download link) — both need the exact same search-and-match
    behavior, so it lives here once.

    Returns dict with keys: ok, zip_path, caption, query, error.
    Caller is responsible for deleting zip_path after use.
    """
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


def run(message: str = None) -> dict:
    """Telegram-facing entry point: extract the query from the raw message,
    then search_and_zip().

    Returns dict with keys: ok, zip_path, caption, query, error.
    Caller is responsible for deleting zip_path after use.
    """
    if not message:
        return {"ok": False, "error": "No message provided."}

    query = extract_query(message)
    if not query:
        return {"ok": False, "error": "What would you like to export from the knowledge base?"}

    return search_and_zip(query)
