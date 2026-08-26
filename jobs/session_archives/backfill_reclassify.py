"""jobs/session_archives/backfill_reclassify.py — one-time tool to sort the
conversations already archived under FALLBACK_PROJECT (from the initial bulk
import, before this classification system existed) into their real Claude.ai
projects. Not a cron job — run manually, once, after dropping a fresh
conversations-*.zip + projects-*.zip pair in the usual drop folder.

Why this is separate from claude_export_import.py: those archives predate
the source_conversation_uuid column, so they can't be matched back to a
conversation by uuid — this instead matches by exact title, since
build_title() is deterministic (same conversation -> same title every time).
Only 1:1 title matches are touched; any title shared by more than one archive
or more than one conversation is left alone and reported, rather than guessed
at — a wrong reclassification is worse than an unsorted one.

Usage: PYTHONPATH=/home/billyomes/watson python3 -m jobs.session_archives.backfill_reclassify
"""
import json
from collections import defaultdict
from pathlib import Path

from jobs.session_archives import classify
from jobs.session_archives import claude_export_render as render
from jobs.session_archives import storage
from jobs.session_archives.claude_export_import import DROP_DIR, FALLBACK_PROJECT, _extract_all, _load_conversations, _load_projects

WATSON_DIR = Path(__file__).resolve().parents[2]
WORK_DIR = DROP_DIR / "_backfill_work"


def run(source_project: str = FALLBACK_PROJECT) -> dict:
    conv_zips = sorted(DROP_DIR.glob("conversations-*.zip"))
    if not conv_zips:
        return {"error": f"no conversations-*.zip found in {DROP_DIR} — drop a fresh export there first"}
    proj_zips = sorted(DROP_DIR.glob("projects-*.zip"))

    if WORK_DIR.exists():
        import shutil
        shutil.rmtree(WORK_DIR)
    _extract_all(conv_zips + proj_zips, WORK_DIR)
    conversations = _load_conversations(WORK_DIR)
    projects = _load_projects(WORK_DIR)

    if not conversations:
        return {"error": "export unpacked but conversations.json had zero entries"}

    # Build title -> [conversation] from the fresh export
    conv_by_title = defaultdict(list)
    for c in conversations:
        conv_by_title[render.build_title(c)].append(c)

    # Build title -> [archive row] from what's still unsorted
    candidates = storage.archives_missing_source_uuid(source_project)
    archives_by_title = defaultdict(list)
    for row in candidates:
        archives_by_title[row["title"]].append(row)

    project_refs = classify.build_project_refs(projects)

    matched = []
    ambiguous_titles = []
    unmatched_titles = []

    for title, archive_rows in archives_by_title.items():
        conv_matches = conv_by_title.get(title, [])
        if len(archive_rows) == 1 and len(conv_matches) == 1:
            matched.append((archive_rows[0], conv_matches[0]))
        elif not conv_matches:
            unmatched_titles.append(title)
        else:
            ambiguous_titles.append(title)

    moved = 0
    stayed_in_fallback = 0
    per_project_counts = {}
    uuid_backfilled_only = 0

    if matched:
        classifications = classify.classify(
            [{"name": c.get("name"), "summary": c.get("summary")} for _, c in matched],
            project_refs,
        )
    else:
        classifications = []

    for (archive_row, conversation), (project_slug, score) in zip(matched, classifications):
        uuid = conversation.get("uuid")
        if project_slug and project_slug != source_project:
            result = storage.reclassify_archive(archive_row["id"], project_slug, source_conversation_uuid=uuid)
            if result.get("moved"):
                moved += 1
                per_project_counts[project_slug] = per_project_counts.get(project_slug, 0) + 1
            else:
                stayed_in_fallback += 1
        else:
            # No confident project match -- still worth backfilling the uuid
            # (in case a later export re-includes this conversation and would
            # otherwise treat it as new) without moving anything.
            storage.reclassify_archive(archive_row["id"], source_project, source_conversation_uuid=uuid)
            stayed_in_fallback += 1
            uuid_backfilled_only += 1

    import shutil
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    for zp in conv_zips + proj_zips:
        zp.unlink(missing_ok=True)

    return {
        "candidates_considered": len(candidates),
        "matched_1_to_1": len(matched),
        "moved_to_real_project": moved,
        "stayed_in_fallback_uuid_backfilled_only": uuid_backfilled_only,
        "stayed_in_fallback_no_confident_project": stayed_in_fallback - uuid_backfilled_only,
        "ambiguous_titles_skipped": len(ambiguous_titles),
        "ambiguous_title_examples": ambiguous_titles[:10],
        "unmatched_titles": len(unmatched_titles),
        "per_project_counts": per_project_counts,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
