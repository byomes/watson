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

get_project_summary_for() is rebuilt live from session_archives rows on every
call (title/created_at/summary, newest first, superseded archives excluded)
rather than reading an accumulated file — there is deliberately no
independent summary store to write into or drift from. This replaced an
earlier design (a per-project _summary.md file, appended to on every
archive_session/reclassify_archive call) after that file was found to
contain an entry with no corresponding archive row: something had written
to it directly — most plausibly a one-off manual test of the "explicit
project bypasses auto-classification" behavior — and because the file was
pure accumulated text with no row backing it, there was no way to tell a
legitimate entry from a stray one, nor any path for a deleted/reclassified
archive's entry to ever be removed. Retired 2026-08-26.

data/ is covered by both of Watson's nightly backup legs (OneDrive + local
restic) with no changes needed there — see WATSON_ARCHITECTURE.md, "Session
Archives (Claude.ai)" for the verification test.

Reserved project-slug convention: any project slug starting with "_" (e.g.
"_test") is for internal/dev testing only. classify()'s auto-classifier
never routes real "general"-fallback content into one (see the filter in
archive_session below), so ad-hoc test archives created during tool
development stay out of real project history as long as the caller passes
project="_test" explicitly rather than "general". Added 2026-08-26 after a
prior upgrade pass risked leaving test archives mixed into real projects.

File staging: archive_session's files can be passed as either inline
{filename, content_base64} or {filename, file_ref} — the latter references
a file already pushed via stage_file(), avoiding a second full base64
round-trip when a file needs to be built, verified, then archived. Staged
files live under data/session_archives/_staging/<token>/, are single-use
(deleted once archive_session consumes them), and expire after
STAGING_TTL_SECONDS if never consumed.
"""
import base64
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

from core.database import get_connection

WATSON_DIR = Path(__file__).resolve().parents[2]
ARCHIVES_ROOT = WATSON_DIR / "data" / "session_archives"
STAGING_ROOT = ARCHIVES_ROOT / "_staging"
STAGING_TTL_SECONDS = 24 * 60 * 60      # unconsumed staged files are purged after this long
RESERVED_PROJECT_PREFIX = "_"           # "_test", "_dev", etc. — never an auto-classify target

MAX_FILE_BYTES = 8 * 1024 * 1024        # per-file cap, decoded size
MAX_TOTAL_BYTES = 20 * 1024 * 1024      # transcript + accepted files combined, decoded
MAX_TRANSCRIPT_BYTES = 5 * 1024 * 1024  # generous — a hard error, not a skip, since the
                                         # transcript is the core content, not optional
MAX_SUMMARY_RETURN_BYTES = 20_000       # get_project_summary read cap (newest-first, so
                                         # truncation only ever drops the oldest entries)
SMALL_CONTENT_WARN_BYTES = 300          # below this, a transcript or file is flagged as
                                         # suspiciously small rather than silently accepted —
                                         # this is what would have caught archive #1666's
                                         # 22-byte placeholder file immediately (2026-08-26)

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
    # A leading RESERVED_PROJECT_PREFIX ("_") is preserved deliberately — the
    # generic strip("-") below would otherwise eat it (an underscore isn't
    # [a-z0-9], so it becomes a "-" that strip("-") then removes), silently
    # turning "_test" into "test" and defeating the reserved-slug convention
    # entirely. Caught by testing stage_file/archive_session together
    # (2026-08-26): an archive passed project="_test" landed in a real-
    # looking "test" project instead of staying clearly marked as internal.
    reserved = (text or "").strip().startswith(RESERVED_PROJECT_PREFIX)
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    slug = (slug or "session")[:max_len].strip("-") or "session"
    return RESERVED_PROJECT_PREFIX + slug if reserved else slug


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


def _purge_expired_staging() -> None:
    if not STAGING_ROOT.is_dir():
        return
    cutoff = time.time() - STAGING_TTL_SECONDS
    for entry in STAGING_ROOT.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass  # best-effort cleanup — a race with a concurrent consume is fine to skip


def stage_file(filename: str, content_base64: str) -> dict:
    """Push one file's bytes to Watson once, ahead of an archive_session call
    — returns a file_ref token that archive_session's files list can carry
    instead of content_base64, so a large file doesn't have to be
    base64-encoded and re-sent if the caller needs to verify/retry before
    the archive_session call actually commits. Single-use: consumed and
    deleted by archive_session, or purged after STAGING_TTL_SECONDS if
    never consumed."""
    _purge_expired_staging()
    if not filename or not filename.strip():
        return {"error": "filename is required", "error_code": "bad_request"}
    try:
        raw = base64.b64decode(content_base64 or "", validate=False)
    except Exception as exc:
        return {"error": f"invalid base64: {exc}", "error_code": "bad_request"}
    if len(raw) > MAX_FILE_BYTES:
        return {
            "error": f"file too large ({len(raw)} bytes > {MAX_FILE_BYTES} cap)",
            "error_code": "bad_request",
        }

    token = secrets.token_hex(16)
    staged_dir = STAGING_ROOT / token
    staged_dir.mkdir(parents=True)
    safe_name = _sanitize_filename(filename)
    (staged_dir / safe_name).write_bytes(raw)
    return {
        "file_ref": token,
        "filename": safe_name,
        "size_bytes": len(raw),
        "expires_at": datetime.fromtimestamp(time.time() + STAGING_TTL_SECONDS).isoformat(timespec="seconds"),
    }


def _read_staged_file(file_ref: str) -> tuple:
    """Returns (raw_bytes, filename) for a staged file, or (None, error_dict)
    if the ref is missing/expired/invalid. Deletes the staged directory on
    successful read — refs are single-use."""
    staged_dir = STAGING_ROOT / re.sub(r"[^A-Za-z0-9]", "", file_ref or "")
    if not staged_dir.is_dir():
        return None, {"error": f"file_ref '{file_ref}' not found or already consumed/expired", "error_code": "not_found"}
    on_disk = [p for p in staged_dir.iterdir() if p.is_file()]
    if not on_disk:
        shutil.rmtree(staged_dir, ignore_errors=True)
        return None, {"error": f"file_ref '{file_ref}' is empty", "error_code": "not_found"}
    staged_path = on_disk[0]
    raw = staged_path.read_bytes()
    filename = staged_path.name
    shutil.rmtree(staged_dir, ignore_errors=True)
    return (raw, filename), None


def _find_cross_project_duplicate(transcript: str, exclude_project: str):
    """Cheap near-duplicate check: does another (non-superseded) archive in a
    *different* project already start with roughly the same text? Used only
    on the 'general'-fallback auto-classify path, since that's already the
    expensive-classification code path — not run on every archive_session
    call. A LIKE substring match on a normalized opening fingerprint catches
    the common case (same conversation re-archived or re-imported under a
    different project) without a full-transcript similarity pass."""
    fingerprint = re.sub(r"\s+", " ", (transcript or "").strip())[:200]
    if len(fingerprint) < 40:
        return None
    escaped = fingerprint.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, project, title FROM session_archives "
            "WHERE project != ? AND superseded_by IS NULL AND transcript LIKE ? ESCAPE '\\' LIMIT 1",
            (exclude_project, f"%{escaped}%"),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# ── Write path ───────────────────────────────────────────────────────────

def archive_session(transcript, files, project, title, summary, source_conversation_uuid=None) -> dict:
    if not transcript or not transcript.strip():
        return {"error": "transcript is required and cannot be empty", "error_code": "bad_request"}
    if not project or not project.strip():
        return {
            "error": "project is required — pass 'general' explicitly if this session isn't tied to a specific project",
            "error_code": "bad_request",
        }
    if not title or not title.strip():
        return {"error": "title is required", "error_code": "bad_request"}
    if not summary or not summary.strip():
        return {"error": "summary is required", "error_code": "bad_request"}
    if files is None:
        files = []
    if not isinstance(files, list):
        return {
            "error": "files must be a list of {filename, content_base64} or {filename, file_ref} objects",
            "error_code": "bad_request",
        }

    transcript_bytes = transcript.encode("utf-8")
    if len(transcript_bytes) > MAX_TRANSCRIPT_BYTES:
        return {
            "error": f"transcript too large ({len(transcript_bytes)} bytes > "
                     f"{MAX_TRANSCRIPT_BYTES} cap) — split the session into multiple archives",
            "error_code": "bad_request",
        }

    was_general_fallback = _slugify(project, max_len=60) == "general"
    project_slug = _slugify(project, max_len=60)
    auto_classified = False
    if project_slug == "general":
        # Claude.ai explicitly said it couldn't confidently name a project —
        # rather than actually filing it under the literal "general" bucket,
        # try the same classifier the nightly export importer uses (cached
        # project refs, title+summary similarity) before falling back to
        # "general" for real. Neither Claude.ai nor Watson can reliably guess
        # a project blind; a calibrated similarity score can do better than
        # Claude.ai's own guess for chats outside a formal Claude.ai Project.
        from jobs.session_archives import classify
        refs = [r for r in classify.load_project_refs_cache() if not r["slug"].startswith(RESERVED_PROJECT_PREFIX)]
        if refs:
            [(classified_slug, _score)] = classify.classify([{"name": title, "summary": summary}], refs)
            if classified_slug:
                project_slug = classified_slug
                auto_classified = True

    possible_duplicate = None
    if was_general_fallback:
        possible_duplicate = _find_cross_project_duplicate(transcript, project_slug)

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
    warnings = []
    if len(transcript_bytes) < SMALL_CONTENT_WARN_BYTES:
        warnings.append(
            f"transcript is only {len(transcript_bytes)} bytes — unusually short for a full "
            "verbatim transcript; verify this isn't a placeholder before treating the archive as complete."
        )

    for f in files:
        raw_filename = (f or {}).get("filename", "?")
        file_ref = (f or {}).get("file_ref")
        if file_ref:
            staged, staged_err = _read_staged_file(file_ref)
            if staged_err:
                skipped_files.append({"filename": raw_filename, "reason": staged_err["error"]})
                continue
            raw, staged_name = staged
            filename = _sanitize_filename(raw_filename if raw_filename and raw_filename != "?" else staged_name)
        else:
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
        accepted_files.append({"filename": dest.name, "size_bytes": len(raw)})
        if len(raw) < SMALL_CONTENT_WARN_BYTES:
            warnings.append(
                f"file '{dest.name}' is only {len(raw)} bytes — unusually small; verify it's not a "
                "truncated/placeholder write before treating the archive as complete."
            )

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
        "auto_classified": auto_classified,
    }
    if warnings:
        result["warnings"] = warnings
    if secrets_flagged:
        result["secrets_flagged_patterns"] = sorted(secret_hits)
    if possible_duplicate:
        result["possible_duplicate"] = {
            **possible_duplicate,
            "note": (
                f"Archive #{possible_duplicate['id']} in project '{possible_duplicate['project']}' "
                "opens with nearly the same text as this transcript — this may be the same "
                "conversation already archived under a different project. Consider reclassify_archive "
                "or mark_archive_superseded instead of leaving both as separate copies."
            ),
        }
    return result


# ── Read path ────────────────────────────────────────────────────────────
# Two entry points per operation: a message-based one (kept for the
# run_watson_skill/_SKILL_PRE_CHECKS route used by Telegram/dashboard, where
# free text is all there is) and a typed one taking real parameters (used
# directly by the get_archive/list_archives/etc. MCP tools in
# jobs/devdispatch/api.py, skipping trigger-phrase parsing entirely). The
# message-based functions are thin wrappers around the typed ones.

def list_archives(message: str = "") -> dict:
    project = _strip_trigger(message, ("list archives:", "list archives"))
    return list_archives_by_project(project or None)


def list_archives_by_project(project: str = None, include_superseded: bool = False) -> dict:
    conn = get_connection()
    try:
        where, params = [], []
        if project:
            where.append("project = ?")
            params.append(_slugify(project, max_len=60))
        if not include_superseded:
            where.append("superseded_by IS NULL")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            "SELECT id, project, title, created_at, file_count, superseded_by FROM session_archives "
            f"{clause} ORDER BY created_at DESC LIMIT 20",
            params,
        ).fetchall()
    finally:
        conn.close()
    return {"archives": [dict(r) for r in rows]}


def search_archives(message: str = "") -> dict:
    query = _strip_trigger(message, ("search archives:", "search archives"))
    return search_archives_by_query(query)


def search_archives_by_query(query: str, include_superseded: bool = False) -> dict:
    if not query or not query.strip():
        return {"error": "provide a search query, e.g. 'retreat budget decision'", "error_code": "bad_request"}

    conn = get_connection()
    try:
        fts_clause = "" if include_superseded else "AND sa.superseded_by IS NULL"
        like_clause = "" if include_superseded else "AND superseded_by IS NULL"
        try:
            rows = conn.execute(
                "SELECT sa.id AS id, sa.project AS project, sa.title AS title, sa.created_at AS created_at, "
                "snippet(session_archives_fts, 1, '[', ']', '...', 12) AS snippet "
                "FROM session_archives_fts JOIN session_archives sa ON sa.id = session_archives_fts.rowid "
                f"WHERE session_archives_fts MATCH ? {fts_clause} ORDER BY rank LIMIT 20",
                (_fts_match_expr(query),),
            ).fetchall()
            results = [dict(r) for r in rows]
        except Exception:
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT id, project, title, created_at, substr(transcript, 1, 300) AS snippet "
                f"FROM session_archives WHERE (transcript LIKE ? OR title LIKE ?) {like_clause} "
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
        return {
            "error": "provide an archive id, e.g. 'get archive: 12' or 'get archive: 12 notes.md'",
            "error_code": "bad_request",
        }
    try:
        archive_id = int(parts[0])
    except ValueError:
        return {"error": f"'{parts[0]}' is not a valid archive id", "error_code": "bad_request"}
    filename = parts[1].strip() if len(parts) > 1 else None
    return get_archive_by_id(archive_id, filename)


def get_archive_by_id(archive_id: int, filename: str = None) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, project, title, dir_path, transcript, file_count, created_at, "
            "secrets_flagged, secrets_patterns, superseded_by FROM session_archives WHERE id = ?",
            (archive_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"error": f"no archive with id {archive_id}", "error_code": "not_found"}

    archive_dir = WATSON_DIR / row["dir_path"]
    on_disk_files = sorted(
        p.name for p in archive_dir.iterdir() if p.is_file() and p.name != "transcript.md"
    ) if archive_dir.is_dir() else []

    if filename:
        target_name = _sanitize_filename(filename)
        if target_name not in on_disk_files:
            return {
                "error": f"file '{filename}' not found in archive {archive_id}",
                "error_code": "not_found",
                "available_files": on_disk_files,
            }
        raw = (archive_dir / target_name).read_bytes()
        return {
            "id": archive_id,
            "filename": target_name,
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "size_bytes": len(raw),
        }

    result = {
        "id": row["id"],
        "project": row["project"],
        "title": row["title"],
        "created_at": row["created_at"],
        "transcript": row["transcript"],
        "files": on_disk_files,
        "secrets_flagged": bool(row["secrets_flagged"]),
        "secrets_flagged_patterns": row["secrets_patterns"].split(",") if row["secrets_patterns"] else [],
    }
    if row["superseded_by"]:
        result["superseded_by"] = row["superseded_by"]
        result["note"] = (
            f"This archive was marked superseded by archive #{row['superseded_by']} — "
            "that one is the corrected/authoritative version."
        )
    return result


def list_projects(message: str = "") -> dict:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT project, COUNT(*) AS archive_count, MAX(created_at) AS most_recent "
            "FROM session_archives WHERE superseded_by IS NULL GROUP BY project ORDER BY most_recent DESC"
        ).fetchall()
    finally:
        conn.close()
    return {"projects": [dict(r) for r in rows]}


def get_project_summary(message: str = "") -> dict:
    project = _strip_trigger(
        message,
        ("get project summary:", "project summary:", "get project summary", "project summary"),
    )
    return get_project_summary_for(project)


def get_project_summary_for(project: str) -> dict:
    """Rebuilt live from session_archives every call — title/created_at/
    summary, newest first, superseded archives excluded — rather than read
    from an accumulated file. There is no separate summary store to write to
    (see module docstring): this is what closes off the drift a stray direct
    write previously caused, since every line here traces back to a real,
    still-live archive row."""
    if not project or not project.strip():
        return {"error": "provide a project slug, e.g. 'curator'", "error_code": "bad_request"}
    project_slug = _slugify(project, max_len=60)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT title, created_at, summary FROM session_archives "
            "WHERE project = ? AND superseded_by IS NULL ORDER BY created_at DESC",
            (project_slug,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {
            "error": f"no summary found for project '{project_slug}' — check 'list projects' for known slugs",
            "error_code": "not_found",
        }

    blocks = []
    for r in rows:
        recap = (r["summary"] or "").strip() or "(no summary recorded for this archive — see its full transcript via get_archive)"
        blocks.append(f"## {r['created_at']} — {r['title']}\n\n{recap}\n\n---\n")
    text = "\n".join(blocks)
    truncated = len(text.encode("utf-8")) > MAX_SUMMARY_RETURN_BYTES
    if truncated:
        text = text[:MAX_SUMMARY_RETURN_BYTES]
    return {"project": project_slug, "summary": text, "truncated": truncated}


def mark_superseded(archive_id: int, superseded_by: int) -> dict:
    """Marks archive_id as superseded by another archive — hides it from
    list_archives/search_archives/list_projects by default without deleting
    it or touching its files on disk, per the immutable/append-only design
    (see module docstring). Pass superseded_by=None to un-mark."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM session_archives WHERE id = ?", (archive_id,)).fetchone()
        if row is None:
            return {"error": f"no archive with id {archive_id}", "error_code": "not_found"}
        if superseded_by is not None:
            if superseded_by == archive_id:
                return {"error": "an archive cannot supersede itself", "error_code": "bad_request"}
            replacement = conn.execute(
                "SELECT id FROM session_archives WHERE id = ?", (superseded_by,)
            ).fetchone()
            if replacement is None:
                return {"error": f"no archive with id {superseded_by} to supersede with", "error_code": "not_found"}
        conn.execute(
            "UPDATE session_archives SET superseded_by = ? WHERE id = ?",
            (superseded_by, archive_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": archive_id, "superseded_by": superseded_by, "hidden_from_listings": bool(superseded_by)}


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
    directory on disk and updates its DB row (project, dir_path, and — only
    if it had none — a fallback summary). get_project_summary_for is derived
    live from these rows, so nothing further needs updating for the new
    project's summary to pick this archive up correctly."""
    new_project_slug = _slugify(new_project, max_len=60)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, project, title, dir_path, created_at, summary FROM session_archives WHERE id = ?",
            (archive_id,),
        ).fetchone()
        if row is None:
            return {"error": f"no archive with id {archive_id}", "error_code": "not_found"}

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
        if not row["summary"]:
            set_clause += ", summary = ?"
            update_args.append("(reclassified from an earlier bulk import — see full transcript for content)")
        update_args.append(archive_id)
        conn.execute(f"UPDATE session_archives SET {set_clause} WHERE id = ?", update_args)
        conn.commit()
    finally:
        conn.close()

    return {"id": archive_id, "project": new_project_slug, "dir_path": new_rel, "moved": True}
