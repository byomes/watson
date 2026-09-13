"""tag_sermon_years.py — One-time backfill: tag pre-existing 'sermons'
chunks (source_type == "transcript") with the year they were preached,
parsed from their title's leading date (see jobs/build_kb.py's
year_from_title(), added alongside this script — every ingest from now
on tags itself automatically; this script only covers chunks indexed
before that existed).

Used by jobs/ask.py to weight kb_search results toward sermons preached
2022 or later, which Bill trusts more theologically than his earlier
material.

Dry-run by default — prints what would change. Pass --apply to actually
write the tags via collection.update() (metadata-only patch, no
re-embedding).

Usage:
  python jobs/kb/tag_sermon_years.py            # dry run
  python jobs/kb/tag_sermon_years.py --apply     # apply
"""
import argparse
import logging
from pathlib import Path

import chromadb

from jobs.build_kb import year_from_title

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CHROMA_DIR = BASE_DIR / "data" / "chroma"
COLLECTION_NAME = "sermons"


def run(apply: bool = False) -> dict:
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_collection(COLLECTION_NAME)
    all_data = collection.get(where={"source_type": "transcript"})

    update_ids, update_metas = [], []
    unparseable_titles = set()
    already_tagged = 0
    for cid, meta in zip(all_data["ids"], all_data["metadatas"]):
        if meta.get("year") is not None:
            already_tagged += 1
            continue
        year = year_from_title(meta["title"])
        if year is None:
            unparseable_titles.add(meta["title"])
            continue
        meta = dict(meta)
        meta["year"] = year
        update_ids.append(cid)
        update_metas.append(meta)

    log.info(
        "%d chunks already tagged, %d chunks to tag, %d titles unparseable (dry run: %s)",
        already_tagged, len(update_ids), len(unparseable_titles), not apply,
    )
    if unparseable_titles:
        log.info("Unparseable titles (no leading date):")
        for t in sorted(unparseable_titles):
            log.info("  - %s", t)

    if apply and update_ids:
        collection.update(ids=update_ids, metadatas=update_metas)
        log.info("Applied: tagged %d chunks.", len(update_ids))

    return {
        "already_tagged": already_tagged,
        "tagged": len(update_ids) if apply else 0,
        "would_tag": len(update_ids),
        "unparseable": sorted(unparseable_titles),
        "applied": apply,
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually write the tags (default: dry run)")
    args = parser.parse_args()
    run(apply=args.apply)


if __name__ == "__main__":
    main()
