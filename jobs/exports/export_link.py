"""jobs/exports/export_link.py — general-purpose scoped, expiring,
single-use download link for any file on Watson's disk.

Generalizes jobs/kb/export_link.py's pattern (built 2026-08-24) beyond KB
zip exports. See the 2026-09-05 file-export-link proposal in watson-review
for the full design rationale and the decision record below.

MANDATORY sanitization: every file is scanned via jobs.exports.secret_scan
before a link is ever issued -- there is no caller-level way to skip this,
by design (Bill's explicit requirement, 2026-09-05: "no exceptions, no
trusted context carve-out"). If a text file contains a recognized secret
pattern, a redacted COPY is linked instead of the original -- never the
original with a secret still in it. Binary files cannot be safely
scanned/redacted with a text-based approach, so they are refused outright
rather than linked unscanned. If redaction somehow fails to fully clean
the content (checked via a re-scan of the redacted output), the whole
request is refused with the findings reported to the caller -- it never
silently falls back to linking the original.

The linked file is ALWAYS a fresh copy in _STAGING_DIR, whether or not
anything was redacted -- never the caller's original path. This means the
download route and the expiry-cleanup cron can always safely delete the
linked file after use/expiry without any risk of deleting a real,
still-needed file elsewhere on disk.

Reachability decision (2026-09-05, Option A): this route is registered on
the same Flask app/port as the MCP devdispatch connector and jobs.kb.api's
kb_bp, which sit behind Tailscale Funnel proxying ALL paths on port 5200
to the public internet -- Funnel has no path-level filtering, so this is
reachable from the open internet, not just the tailnet (confirmed
2026-09-05; jobs/kb/api.py's docstring claims Tailscale-only, which turned
out not to be true -- that claim predates this finding and hasn't been
corrected here). Chosen deliberately so Claude.ai itself (which has no
path onto the tailnet) can fetch a link directly, not just Bill by hand.
Security rests entirely on the token -- single-use, short expiry,
generated via secrets.token_urlsafe -- same trust model already accepted
for kb_export_link and the MCP connector itself, now backed by the
mandatory sanitization above so a leaked/guessed token can't expose a
credential even in the worst case.
"""
import json
import logging
import secrets
import tempfile
from pathlib import Path

from core.database import get_connection
from jobs.exports.schema import create_tables
from jobs.exports.secret_scan import redact

log = logging.getLogger(__name__)

_BASE_URL = "https://watson.tail0243ff.ts.net"
_LINK_TTL_SECONDS = 15 * 60
_MAX_BYTES = 100 * 1024 * 1024  # 100MB sanity cap, see proposal open question #2
_STAGING_DIR = Path(tempfile.gettempdir()) / "watson_export_links"


class ExportLinkError(Exception):
    """Raised when a file cannot be safely linked. `findings` is set when
    the refusal is because of detected (and unresolvable) secrets."""

    def __init__(self, message: str, findings: list[dict] | None = None):
        super().__init__(message)
        self.findings = findings or []


def _is_probably_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def create_export_link(file_path: str, expires_minutes: int = 15, caption: str | None = None) -> dict:
    """Scan+sanitize `file_path`, then issue a single-use download link.

    Returns {"download_url", "expires_at", "sanitized", "redactions"}.
    Raises ExportLinkError (never returns a partial/unsafe result) if the
    file is missing, over the size cap, binary, or -- extremely unlikely
    since redact() rewrites every match it finds -- still shows a hit on
    a re-scan of its own redacted output.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise ExportLinkError(f"not a file: {path}")

    size = path.stat().st_size
    if size > _MAX_BYTES:
        raise ExportLinkError(f"{path.name} is {size} bytes, over the {_MAX_BYTES}-byte export-link cap")

    raw = path.read_bytes()
    if not _is_probably_text(raw):
        raise ExportLinkError(
            f"{path.name} looks like binary content -- cannot safely scan/redact it for secrets, "
            "so it cannot be export-linked. No exceptions to this."
        )

    text = raw.decode("utf-8")
    redacted_text, findings = redact(text)

    if findings:
        # Re-scan the redacted output itself. If anything still matches --
        # e.g. a pattern only became visible after an earlier redaction
        # changed the surrounding text -- refuse rather than link a
        # partially-sanitized file.
        _, residual = redact(redacted_text)
        if residual:
            log.error("export_link: redaction left residual matches for %s: %s", path.name, residual)
            raise ExportLinkError(
                f"sanitization of {path.name} did not fully clean the content -- refusing to link it",
                findings=findings + residual,
            )
        content_to_stage = redacted_text
        sanitized = True
        log.warning(
            "export_link: %d secret pattern(s) redacted from %s before linking: %s",
            len(findings), path.name, findings,
        )
    else:
        content_to_stage = text
        sanitized = False

    _STAGING_DIR.mkdir(parents=True, exist_ok=True)
    staged_path = _STAGING_DIR / f"{secrets.token_hex(8)}_{path.name}"
    staged_path.write_text(content_to_stage, encoding="utf-8")

    create_tables()
    token = secrets.token_urlsafe(24)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO file_export_links "
            "(token, file_path, filename, caption, sanitized, redactions, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now', ?))",
            (
                token, str(staged_path), path.name, caption,
                1 if sanitized else 0, json.dumps(findings) if findings else None,
                f"+{expires_minutes * 60} seconds",
            ),
        )
        conn.commit()
        expires_at = conn.execute(
            "SELECT expires_at FROM file_export_links WHERE token = ?", (token,)
        ).fetchone()["expires_at"]
    finally:
        conn.close()

    download_url = f"{_BASE_URL}/export/download/{token}"
    log.info(
        "Export link issued for %s: %s (expires %s UTC, sanitized=%s)",
        path.name, download_url, expires_at, sanitized,
    )
    return {
        "download_url": download_url,
        "expires_at": expires_at,
        "sanitized": sanitized,
        "redactions": findings,
    }
