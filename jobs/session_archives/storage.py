"""jobs/session_archives/storage.py — Claude.ai session archive read/write logic.

Write path: archive_session() — called from the dedicated archive_session MCP
tool (jobs/devdispatch/api.py), not through the skill router (it needs
structured params + a file array, which run_watson_skill's single `message`
string can't carry).

Read path: list_archives / search_archives / get_archive / list_projects /
get_project_summary — plain Watson skills, reached the same way kb_search is:
via run_watson_skill's single free-text `message`, matched by a
_SKILL_PRE_CHECKS trigger phrase in jobs/skillbuilder/router.py. Each
function's first parameter is named `message` so the router forwards the raw
trigger-prefixed text directly (see router._run_skill).

Storage layout: data/session_archives/<project>/<timestamp>-<slug>/
  transcript.md   — frontmatter (project, title, created_at, secrets_flagged)
                    + the full verbatim transcript
  <files...>       — sanitized filenames, as submitted
  ../._summary.md  — rolling per-project catch-up file, newest entry on top

data/ is covered by both of Watson's nightly backup legs (OneDrive + local
restic) with no changes needed there — see WATSON_ARCHITECTURE.md, "Session
Archives (Claude.ai)" for the verification test.
"""
import base64
import re
import shutil
from datetime import datetime
from pathlib import Path

from core.database import get_connection

WATSON_DIR = Path(__file__).resolve().parents[2]
ARCHIVES_ROOT = WATSON_DIR / "data" / "session_archives"

MAX_FILE_BYTES = 8 * 1024 * 1024        # per-file cap, decoded size
MAX_TOTAL_BYTES = 20 * 1024 * 1024      # transcript + accepted files combined, decoded
MAX_TRANSCRIPT_BYTES = 5 * 1024 * 1024  # generous — a hard error, not a skip, since the
                                         # transcript is the core content, not optional
MAX_SUMMARY_RETURN_BYTES = 20_000       # get_project_summary read cap (newest-first, so
                                         # truncation only ever drops the oldest entries)

# Flagged, never silently stripped — the match just gets surfaced (pattern
# names + a frontmatter flag) so a human or a future Claude.ai session knows
# to look before treating the archive as shareable.
_SECRET_PATTERNS = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("stripe_key", re.compile(r"sk_live_[A-Za-z0-9]{16,}")),
    ("private_key_block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9\-_.]{20,}")),
    ("generic_secret_assignment", re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{8,}"
    )),
)


def _scan_secrets(text: str) -> set:
    return {name for name, pattern in _SECRET_PATTERNS if pattern.search(text)}


def _slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug or "session")[:max_len].strip("-") or "session"


def _sanitize_filename(name: str) -> str:
    # Collapse any path component down to a bare filename before it ever
    # touches the filesystem — an incoming filename is untrusted input, and
    # "../../etc/passwd"-shaped values must not be able to write outside the
    # archive directory.
    name = (name or "file").replace("\\", "/").split("/")[-1]
    name = name.lstrip(".") or "file"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:150] or "file"


def _strip_trigger(message: str, triggers: tuple) -> str:
    msg = (message or "").strip()
    low = msg.lower()
    for t in sorted(triggers, key=len, reverse=True):
        if low.startswith(t):
            return msg[len(t):].strip()
    return msg


def _fts_match_expr(query: str) -> str:
    # AND-of-terms, each quoted individually, so arbitrary natural-language
    # punctuation in `query` can't be misread as FTS5 query-syntax operators.
    words = re.findall(r"[A-Za-z0-9_]+", query)
    return " ".join(f'"{w}"' for w in words) if words else '""'


def _append_project_summary(project_slug: str, title: str, created_at: str, summary: str) -> None:
    project_dir = ARCHIVES_ROOT / project_slug
    project_dir.mkdir(parents=True, exist_ok=True)
    summary_path = project_dir / "_summary.md"
    existing = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    entry = f"## {created_at} — {title}\n\n{summary.strip()}\n\n---\n\n"
    summary_path.write_text(entry + existing, encoding="utf-8")


# ── Write path ───────────────────────────────────────────────────────────

def archive_session(transcript, files, project, title, summary, source_conversation_uuid=None) -> dict:
    if not transcript or not transcript.strip():
        return {"error": "transcript is required and cannot be empty"}
    if not project or not project.strip():
        return {"error": "project is required — pass 'general' explicitly if this session isn't tied to a specific project"}
    if not title or not title.strip():
        return {"error": "title is required"}
    if not summary or not summary.strip():
        return {"error": "summary is required"}
    if files is None:
        files = []
    if not isinstance(files, list):
        return {"error": "files must be a list of {filename, content_base64} objects"}

    transcript_bytes = transcript.encode("utf-8")
    if len(transcript_bytes) > MAX_TRANSCRIPT_BYTES:
        return {
            "error": f"transcript too large ({len(transcript_bytes)} bytes > "
                     f"{MAX_TRANSCRIPT_BYTES} cap) — split the session into multiple archives"
        }

    project_slug = _slugify(project, max_len=60)
    title_slug = _slugify(title)
    created_dt = datetime.now()
    timestamp = created_dt.strftime("%Y%m%d-%H%M%S")
    created_at = created_dt.isoformat(timespec="seconds")

    # Guard against two archives landing in the same second with the same
    # title slug (e.g. a fast bulk import) silently colliding into one
    # directory and overwriting each other's transcript.md.
    base_name = f"{timestamp}-{title_slug}"
    archive_dir = ARCHIVES_ROOT / project_slug / base_name
    n = 1
    while archive_dir.exists():
        archive_dir = ARCHIVES_ROOT / project_slug / f"{base_name}-{n}"
        n += 1
    archive_dir.mkdir(parents=True)

    accepted_files = []
    skipped_files = []
    total_bytes = len(transcript_bytes)
    secret_hits = _scan_secrets(transcript)

    for f in files:
        raw_filename = (f or {}).get("filename", "?")
        try:
            filename = _sanitize_filename(raw_filename)
            raw = base64.b64decode((f or {}).get("content_base64") or "", validate=False)
        except Exception as exc:
            skipped_files.append({"filename": raw_filename, "reason": f"invalid base64: {exc}"})
            continue
        if len(raw) > MAX_FILE_BYTES:
            skipped_files.append({"filename": filename, "reason": f"file too large ({len(raw)} bytes > {MAX_FILE_BYTES} cap)"})
            continue
        if total_bytes + len(raw) > MAX_TOTAL_BYTES:
            skipped_files.append({"filename": filename, "reason": "archive total size cap exceeded"})
            continue

        dest = archive_dir / filename
        n = 1
        while dest.exists():
            stem, dot, ext = filename.rpartition(".")
            dest = archive_dir / (f"{stem or filename}-{n}{dot}{ext}")
            n += 1
        dest.write_bytes(raw)
        total_bytes += len(raw)
        accepted_files.append(dest.name)

        try:
            secret_hits |= _scan_secrets(raw.decode("utf-8"))
        except UnicodeDecodeError:
            pass  # binary file — not scanned

    secrets_flagged = bool(secret_hits)
    frontmatter = (
        "---\n"
        f"project: {project_slug}\n"
        f"title: {title}\n"
        f"created_at: {created_at}\n"
        f"secrets_flagged: {'true' if secrets_flagged else 'false'}\n"
        + (f"secrets_patterns: {', '.join(sorted(secret_hits))}\n" if secrets_flagged else "")
        + "---\n\n"
    )
    (archive_dir / "transcript.md").write_text(frontmatter + transcript, encoding="utf-8")

    rel_dir = str(archive_dir.relative_to(WATSON_DIR))

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO session_archives "
            "(project, title, dir_path, transcript, file_count, secrets_flagged, secrets_patterns, "
            "created_at, summary, source_conversation_uuid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_slug, title, rel_dir, transcript, len(accepted_files),
             1 if secrets_flagged else 0,
             ",".join(sorted(secret_hits)) if secrets_flagged else None, created_at,
             summary, source_conversation_uuid),
        )
        archive_id = cur.lastrowid
        try:
            conn.execute(
                "INSERT INTO session_archives_fts(rowid, title, transcript) VALUES (?, ?, ?)",
                (archive_id, title, transcript),
            )
        except Exception:
            pass  # FTS5 table doesn't exist on this build — search_archives falls back to LIKE
        conn.commit()
    finally:
        conn.close()

    _append_project_summary(project_slug, title, created_at, summary)

    result = {
        "id": archive_id,
        "project": project_slug,
        "title": title,
        "dir_path": rel_dir,
        "created_at": created_at,
        "file_count": len(accepted_files),
        "files": accepted_files,
        "skipped_files": skipped_files,
        "secrets_flagged": secrets_flagged,
    }
    if secrets_flagged:
        result["secrets_flagged_patterns"] = sorted(secret_hits)
    return result


# ── Read path (via run_watson_skill) ────────────────────────────────────

def list_archives(message: str = "") -> dict:
    project = _strip_trigger(message, ("list archives:", "list archives"))
    conn = get_connection()
    try:
        if project:
            rows = conn.execute(
                "SELECT id, project, title, created_at, file_count FROM session_archives "
                "WHERE project = ? ORDER BY created_at DESC LIMIT 20",
                (_slugify(project, max_len=60),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, project, title, created_at, file_count FROM session_archives "
                "ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
    finally:
        conn.close()
    return {"archives": [dict(r) for r in rows]}


def search_archives(message: str = "") -> dict:
    query = _strip_trigger(message, ("search archives:", "search archives"))
    if not query:
        return {"error": "provide a search query, e.g. 'search archives: retreat budget decision'"}

    conn = get_connection()
    try:
        try:
            rows = conn.execute(
                "SELECT sa.id AS id, sa.project AS project, sa.title AS title, sa.created_at AS created_at, "
                "snippet(session_archives_fts, 1, '[', ']', '...', 12) AS snippet "
                "FROM session_archives_fts JOIN session_archives sa ON sa.id = session_archives_fts.rowid "
                "WHERE session_archives_fts MATCH ? ORDER BY rank LIMIT 20",
                (_fts_match_expr(query),),
            ).fetchall()
            results = [dict(r) for r in rows]
        except Exception:
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT id, project, title, created_at, substr(transcript, 1, 300) AS snippet "
                "FROM session_archives WHERE transcript LIKE ? OR title LIKE ? "
                "ORDER BY created_at DESC LIMIT 20",
                (like, like),
            ).fetchall()
            results = [dict(r) for r in rows]
    finally:
        conn.close()
    return {"results": results, "query": query}


def get_archive(message: str = "") -> dict:
    body = _strip_trigger(message, ("get archive:", "get archive"))
    parts = body.split(None, 1)
    if not parts:
        return {"error": "provide an archive id, e.g. 'get archive: 12' or 'get archive: 12 notes.md'"}
    try:
        archive_id = int(parts[0])
    except ValueError:
        return {"error": f"'{parts[0]}' is not a valid archive id"}
    filename = parts[1].strip() if len(parts) > 1 else None

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, project, title, dir_path, transcript, file_count, created_at, "
            "secrets_flagged, secrets_patterns FROM session_archives WHERE id = ?",
            (archive_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"error": f"no archive with id {archive_id}"}

    archive_dir = WATSON_DIR / row["dir_path"]
    on_disk_files = sorted(
        p.name for p in archive_dir.iterdir() if p.is_file() and p.name != "transcript.md"
    ) if archive_dir.is_dir() else []

    if filename:
        target_name = _sanitize_filename(filename)
        if target_name not in on_disk_files:
            return {"error": f"file '{filename}' not found in archive {archive_id}", "available_files": on_disk_files}
        raw = (archive_dir / target_name).read_bytes()
        return {
            "id": archive_id,
            "filename": target_name,
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "size_bytes": len(raw),
        }

    return {
        "id": row["id"],
        "project": row["project"],
        "title": row["title"],
        "created_at": row["created_at"],
        "transcript": row["transcript"],
        "files": on_disk_files,
        "secrets_flagged": bool(row["secrets_flagged"]),
        "secrets_flagged_patterns": row["secrets_patterns"].split(",") if row["secrets_patterns"] else [],
    }


def list_projects(message: str = "") -> dict:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT project, COUNT(*) AS archive_count, MAX(created_at) AS most_recent "
            "FROM session_archives GROUP BY project ORDER BY most_recent DESC"
        ).fetchall()
    finally:
        conn.close()
    return {"projects": [dict(r) for r in rows]}


def get_project_summary(message: str = "") -> dict:
    project = _strip_trigger(
        message,
        ("get project summary:", "project summary:", "get project summary", "project summary"),
    )
    if not project:
        return {"error": "provide a project slug, e.g. 'project summary: curator'"}
    project_slug = _slugify(project, max_len=60)
    summary_path = ARCHIVES_ROOT / project_slug / "_summary.md"
    if not summary_path.is_file():
        return {"error": f"no summary found for project '{project_slug}' — check 'list projects' for known slugs"}

    text = summary_path.read_text(encoding="utf-8")
    truncated = len(text.encode("utf-8")) > MAX_SUMMARY_RETURN_BYTES
    if truncated:
        text = text[:MAX_SUMMARY_RETURN_BYTES]
    return {"project": project_slug, "summary": text, "truncated": truncated}


# ── Claude.ai export import support (jobs/session_archives/claude_export_import.py,
# jobs/session_archives/backfill_reclassify.py) ─────────────────────────────

def known_source_uuids() -> set:
    """All source_conversation_uuid values already archived — lets a repeat
    nightly export skip conversations it has already imported."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT source_conversation_uuid FROM session_archives WHERE source_conversation_uuid IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def archives_missing_source_uuid(project: str) -> list:
    """Archives in `project` with no source_conversation_uuid recorded yet —
    the backfill candidates for a one-time reclassification pass."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, created_at, summary FROM session_archives "
            "WHERE project = ? AND source_conversation_uuid IS NULL",
            (_slugify(project, max_len=60),),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def reclassify_archive(archive_id: int, new_project: str, source_conversation_uuid: str = None) -> dict:
    """Move an existing archive into a different project: relocates its
    directory on disk, updates its DB row, and appends a recap to the new
    project's _summary.md. Does not touch the old project's _summary.md —
    that entry is left in place as harmless history."""
    new_project_slug = _slugify(new_project, max_len=60)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, project, title, dir_path, created_at, summary FROM session_archives WHERE id = ?",
            (archive_id,),
        ).fetchone()
        if row is None:
            return {"error": f"no archive with id {archive_id}"}

        already_in_project = row["project"] == new_project_slug
        if already_in_project and not source_conversation_uuid:
            return {"id": archive_id, "project": new_project_slug, "moved": False, "reason": "already in this project"}
        if already_in_project:
            # No physical move needed, but a uuid backfill was requested —
            # applying it is the whole point of this call. Skipping it here
            # (as an earlier version of this function did) silently drops
            # the uuid, which makes the next export re-import this
            # conversation as if it were new — confirmed the hard way: a
            # backfill run that only ever called this branch left 649
            # conversations without a uuid, and the next nightly-import test
            # duplicated all of them (2026-08-26, cleaned up).
            conn.execute(
                "UPDATE session_archives SET source_conversation_uuid = ? WHERE id = ?",
                (source_conversation_uuid, archive_id),
            )
            conn.commit()
            return {"id": archive_id, "project": new_project_slug, "moved": False, "reason": "uuid backfilled, already in this project"}

        old_dir = WATSON_DIR / row["dir_path"]
        new_project_dir = ARCHIVES_ROOT / new_project_slug
        new_project_dir.mkdir(parents=True, exist_ok=True)
        new_dir = new_project_dir / old_dir.name
        n = 1
        while new_dir.exists():
            new_dir = new_project_dir / f"{old_dir.name}-{n}"
            n += 1
        shutil.move(str(old_dir), str(new_dir))
        new_rel = str(new_dir.relative_to(WATSON_DIR))

        update_args = [new_project_slug, new_rel]
        set_clause = "project = ?, dir_path = ?"
        if source_conversation_uuid:
            set_clause += ", source_conversation_uuid = ?"
            update_args.append(source_conversation_uuid)
        update_args.append(archive_id)
        conn.execute(f"UPDATE session_archives SET {set_clause} WHERE id = ?", update_args)
        conn.commit()
    finally:
        conn.close()

    recap = row["summary"] or f"(reclassified from an earlier bulk import — see full transcript for content)"
    _append_project_summary(new_project_slug, row["title"], row["created_at"], recap)

    return {"id": archive_id, "project": new_project_slug, "dir_path": new_rel, "moved": True}
