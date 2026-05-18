"""
ingest_drafts.py — Poll Upstash KV for pending blog drafts and load into watson.db.

Runs every 15 minutes on Stream (via cron). Picks up any draft:pending:* keys
written by the /draft page, parses title from frontmatter, inserts into
blog_drafts with status='pending'. The scheduler assigns publish dates and
handles publishing.

Cron entry:
  */15 * * * * cd /home/billyomes/watson && python3 jobs/ingest_drafts.py >> data/ingest_drafts.log 2>&1

Usage:
  python3 jobs/ingest_drafts.py           # normal run
  python3 jobs/ingest_drafts.py --dry-run # show what would be ingested without writing
"""

import argparse
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

KV_URL   = os.getenv("VERCEL_KV_REST_API_URL")
KV_TOKEN = os.getenv("VERCEL_KV_REST_API_TOKEN")

KEY_PREFIX = "draft:pending:"


# ---------------------------------------------------------------------------
# Upstash KV helpers
# ---------------------------------------------------------------------------

def _kv_headers() -> dict:
    return {"Authorization": f"Bearer {KV_TOKEN}"}


def _kv_list_pending() -> list[str]:
    """Return all KV keys matching draft:pending:*"""
    url = f"{KV_URL}/keys/{KEY_PREFIX}*"
    resp = requests.get(url, headers=_kv_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", [])


def _kv_get(key: str) -> str | None:
    """Fetch the value of a KV key."""
    url = f"{KV_URL}/get/{requests.utils.quote(key, safe='')}"
    resp = requests.get(url, headers=_kv_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    result = data.get("result")
    if isinstance(result, dict):
        return result.get("value")
    return result


def _kv_delete(key: str) -> None:
    """Delete a KV key."""
    url = f"{KV_URL}/del/{requests.utils.quote(key, safe='')}"
    resp = requests.get(url, headers=_kv_headers(), timeout=10)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_title(content: str) -> str | None:
    """Extract title from frontmatter title: field."""
    match = re.search(r"^title:\s*['\"]?(.+?)['\"]?\s*$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    # Fallback: first # heading
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def _strip_frontmatter(content: str) -> str:
    """Return body with frontmatter removed."""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:].lstrip()
    return content


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _get_connection():
    from core.database import get_connection
    return get_connection()


def _slug_exists(slug: str) -> bool:
    """Check if a draft with this slug already exists in the DB."""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM blog_drafts WHERE slug = ?", (slug,)
        ).fetchone()
    return row is not None


def _insert_draft(title: str, slug: str, content: str) -> int:
    """Insert a new draft into blog_drafts. Returns the new row id."""
    body = _strip_frontmatter(content)
    with _get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO blog_drafts (title, slug, body, status, created_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (title, slug, body, datetime.utcnow().isoformat()),
        )
        return cursor.lastrowid


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def ingest(dry_run: bool = False) -> None:
    if not KV_URL or not KV_TOKEN:
        log.error("VERCEL_KV_REST_API_URL or VERCEL_KV_REST_API_TOKEN not set")
        sys.exit(1)

    keys = _kv_list_pending()
    if not keys:
        log.info("No pending drafts in KV queue")
        return

    log.info("Found %d pending draft(s) in KV", len(keys))

    for key in keys:
        slug = key.removeprefix(KEY_PREFIX)
        log.info("Processing: %s", key)

        # Skip duplicates
        if _slug_exists(slug):
            log.warning("Slug already in DB, skipping: %s", slug)
            if not dry_run:
                _kv_delete(key)
            continue

        content = _kv_get(key)
        if not content:
            log.warning("Empty content for key: %s", key)
            continue

        title = _parse_title(content)
        if not title:
            log.warning("Could not parse title for slug: %s — skipping", slug)
            continue

        if dry_run:
            log.info("[DRY RUN] Would ingest: '%s' (%s)", title, slug)
            continue

        draft_id = _insert_draft(title, slug, content)
        _kv_delete(key)
        log.info("Ingested draft #%d: '%s' (%s)", draft_id, title, slug)

    log.info("Ingest complete")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Ingest pending drafts from KV into watson.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be ingested without writing to DB or deleting from KV")
    args = parser.parse_args()

    log.info("ingest_drafts running")
    ingest(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
