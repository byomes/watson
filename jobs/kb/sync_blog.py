# Cron: 0 6 * * 1 (Monday 6am -- after the week's blog posts are published)
"""jobs/kb/sync_blog.py — Sync wcky blog posts (content/blog/*.md) into the
KB's ai-ghostwritten archive.

Bill's blog posts are AI-drafted from his sermon transcripts (the raw
transcript URL is fed to claude.ai weekly to draft a post -- see
jobs/generate.py) -- his own ideas, expanded by AI, not his original words.
Kept in the "sermons" collection tagged source_type="ai-ghostwritten" so
they never blend into normal/expanded search (jobs/build_kb.py's
GHOSTWRITTEN_SOURCE_TYPE; jobs/ask.py and jobs/skills/kb_search.py both
exclude it unless explicitly requested).

Copies new posts from ~/wcky/content/blog/ into kb/ghostwritten/ with the
YAML frontmatter stripped -- just the article body, so search chunks aren't
polluted with title/date/category metadata. Idempotent: only copies files
not already present (by filename) and only re-ingests when something new
was copied, safe to run repeatedly.

Usage:
  PYTHONPATH=/home/billyomes/watson python3 jobs/kb/sync_blog.py
"""
import logging
import re
from pathlib import Path

from jobs.build_kb import ingest_dir, GHOSTWRITTEN_SOURCE_TYPE

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BLOG_SOURCE_DIR = Path.home() / "wcky" / "content" / "blog"
GHOSTWRITTEN_DIR = BASE_DIR / "kb" / "ghostwritten"

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1).strip()


def sync_blog() -> dict:
    GHOSTWRITTEN_DIR.mkdir(parents=True, exist_ok=True)
    source_files = sorted(BLOG_SOURCE_DIR.glob("*.md"))
    if not source_files:
        log.warning("No blog posts found in %s", BLOG_SOURCE_DIR)
        return {"copied": 0, "chunks_added": 0}

    copied = 0
    for src in source_files:
        dest = GHOSTWRITTEN_DIR / src.name
        if dest.exists():
            continue
        body = _strip_frontmatter(src.read_text(encoding="utf-8"))
        if not body:
            log.warning("No body content after stripping frontmatter: %s", src.name)
            continue
        dest.write_text(body, encoding="utf-8")
        copied += 1
        log.info("Copied new blog post: %s", src.name)

    chunks_added = 0
    if copied:
        chunks_added = ingest_dir(GHOSTWRITTEN_DIR, "sermons", source_type=GHOSTWRITTEN_SOURCE_TYPE)

    log.info("Blog sync complete: %d new post(s) copied, %d chunk(s) added", copied, chunks_added)
    return {"copied": copied, "chunks_added": chunks_added}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    sync_blog()


if __name__ == "__main__":
    main()
