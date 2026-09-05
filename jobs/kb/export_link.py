"""jobs/kb/export_link.py — Build a KB export zip and return a scoped,
expiring, single-use download link instead of a raw local file path.

Reuses jobs.skills.kb_export.search_and_zip for the actual search-and-zip
work — this module only adds token issuance/storage on top, so Telegram's
kb_export skill (which still sends the raw zip as a Telegram attachment,
interfaces=["telegram"] in skills.json) and this one (dashboard/MCP-facing,
interfaces=["dashboard"]) share one implementation of "search KB, zip
matching source files" without duplicating it.

The returned link points at GET /kb/download/<token> (jobs/kb/api.py),
which is only reachable over Tailscale — no public Funnel route is added
for it. That route deletes the zip and marks the token used once the
response has fully streamed (see after_this_request there); a token that's
never clicked is swept up by jobs/kb/export_link_cleanup.py.
"""
import logging
import secrets

from core.database import get_connection
from jobs.kb.schema import create_tables
from jobs.skills.kb_export import extract_query, search_and_zip

log = logging.getLogger(__name__)

_BASE_URL = "https://watson.tail0243ff.ts.net"
_LINK_TTL_SECONDS = 15 * 60
_PREFIX = "kb export link:"


def run(message: str = None) -> dict:
    """Search the KB, zip matching source files, issue a download link.

    Returns dict with keys: ok, download_url, expires_at, caption, query, error.
    """
    if not message:
        return {"ok": False, "error": "No message provided."}

    query = extract_query(message, prefix=_PREFIX)
    if not query:
        return {"ok": False, "error": "What would you like to export from the knowledge base?"}

    result = search_and_zip(query)
    if not result.get("ok"):
        return result

    create_tables()
    token = secrets.token_urlsafe(24)

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO kb_export_links (token, zip_path, query, caption, expires_at) "
            "VALUES (?, ?, ?, ?, datetime('now', ?))",
            (token, result["zip_path"], result["query"], result["caption"], f"+{_LINK_TTL_SECONDS} seconds"),
        )
        conn.commit()
        expires_at = conn.execute(
            "SELECT expires_at FROM kb_export_links WHERE token = ?", (token,)
        ).fetchone()["expires_at"]
    finally:
        conn.close()

    download_url = f"{_BASE_URL}/kb/download/{token}"
    log.info("KB export link issued for query '%s': %s (expires %s UTC)", query, download_url, expires_at)
    return {
        "ok": True,
        "download_url": download_url,
        "expires_at": expires_at,
        "caption": result["caption"],
        "query": query,
    }
