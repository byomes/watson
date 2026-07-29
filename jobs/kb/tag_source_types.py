"""tag_source_types.py — One-time backfill: tag pre-existing 'sermons' chunks
with a source_type, matched against the actual files on disk in each source
folder (chunk metadata has no source_type field until jobs/build_kb.py's
source_type param, added alongside this script).

Dry-run by default — prints what would change and lists any chunk titles
that don't match a file in any known folder (mis-filed, deleted since
ingest, or a folder not yet listed in FOLDER_TO_TYPE). Pass --apply to
actually write the tags via collection.update() (metadata-only patch, no
re-embedding).

Usage:
  python jobs/kb/tag_source_types.py            # dry run
  python jobs/kb/tag_source_types.py --apply     # apply
"""
import argparse
import logging
from pathlib import Path

import chromadb

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CHROMA_DIR = BASE_DIR / "data" / "chroma"
KB_ROOT = BASE_DIR / "kb"
COLLECTION_NAME = "sermons"

# Folder name (under kb/) -> source_type tag. Add an entry here before
# ingesting any new off-pipeline folder into the sermons collection.
FOLDER_TO_TYPE = {
    "documents": "transcript",
    "bible-studies": "bible-study-note",
    "sermon-notes": "bible-study-note",
    "handouts": "handout",
}


def build_title_map() -> dict:
    title_map = {}
    for folder, stype in FOLDER_TO_TYPE.items():
        folder_path = KB_ROOT / folder
        if not folder_path.is_dir():
            continue
        for f in folder_path.glob("*"):
            if f.is_file():
                title_map[f.stem] = stype
    return title_map


def run(apply: bool = False) -> dict:
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_collection(COLLECTION_NAME)
    all_data = collection.get()

    title_map = build_title_map()

    update_ids, update_metas = [], []
    unmatched_titles = set()
    already_tagged = 0
    for cid, meta in zip(all_data["ids"], all_data["metadatas"]):
        if meta.get("source_type"):
            already_tagged += 1
            continue
        stype = title_map.get(meta["title"])
        if not stype:
            unmatched_titles.add(meta["title"])
            continue
        meta = dict(meta)
        meta["source_type"] = stype
        update_ids.append(cid)
        update_metas.append(meta)

    log.info(
        "%d chunks already tagged, %d chunks to tag, %d titles unmatched (dry run: %s)",
        already_tagged, len(update_ids), len(unmatched_titles), not apply,
    )
    if unmatched_titles:
        log.info("Unmatched titles:")
        for t in sorted(unmatched_titles):
            log.info("  - %s", t)

    if apply and update_ids:
        collection.update(ids=update_ids, metadatas=update_metas)
        log.info("Applied: tagged %d chunks.", len(update_ids))

    return {
        "already_tagged": already_tagged,
        "tagged": len(update_ids) if apply else 0,
        "would_tag": len(update_ids),
        "unmatched": sorted(unmatched_titles),
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
