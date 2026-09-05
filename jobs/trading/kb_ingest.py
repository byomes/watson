"""jobs/trading/kb_ingest.py — Ingest kb/trading-strategies/ into a new
"trading-strategies" ChromaDB collection, separate from the ministry "sermons"
collection. Reuses jobs/build_kb.py::ingest_dir() exactly as every other KB
source does — no new ingestion mechanism. Each subfolder maps to a
source_type, set at ingest time (the correct pattern per
jobs/kb/tag_source_types.py's own docstring, which describes itself as a
one-time backfill for the era before ingest_dir() took a source_type param).

Usage: PYTHONPATH=<repo> venv/bin/python -m jobs.trading.kb_ingest
"""
import logging
from pathlib import Path

from jobs.build_kb import ingest_dir

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
KB_DIR = BASE_DIR / "kb" / "trading-strategies"
COLLECTION_NAME = "trading-strategies"

FOLDER_TO_SOURCE_TYPE = {
    "articles": "article",
    "case-studies": "case-study",
}


def ingest_all() -> int:
    total = 0
    for folder, source_type in FOLDER_TO_SOURCE_TYPE.items():
        d = KB_DIR / folder
        if not d.exists():
            log.warning("Missing KB folder: %s", d)
            continue
        added = ingest_dir(d, COLLECTION_NAME, source_type=source_type)
        log.info("%s: %d new chunks (source_type=%s)", folder, added, source_type)
        total += added
    return total


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = ingest_all()
    log.info("trading-strategies KB ingest complete: %d new chunks", n)


if __name__ == "__main__":
    main()
