"""jobs/session_archives/reclassify_threshold.py — re-run project
classification against everything still sitting in the catch-all project,
using a different confidence threshold, without needing a fresh Claude.ai
export.

Works entirely off what's already in Watson: the cached project reference
list (classify.load_project_refs_cache() — populated automatically the last
time a projects-*.zip was processed) and each archive's own stored `title`/
`summary` columns. This is what makes threshold tuning cheap to iterate on —
Anthropic's export download links are single-use, so re-running the actual
backfill/import against fresh export data means Bill regenerating a whole
new account export each time; this script sidesteps that for the common case
of "just try a different threshold."

If the cache is empty (no projects-*.zip has been processed yet), there's no
project reference list to classify against — fails clearly rather than
silently doing nothing.

Usage:
  PYTHONPATH=/home/billyomes/watson python3 -m jobs.session_archives.reclassify_threshold [threshold]
"""
import sys

from jobs.session_archives import classify
from jobs.session_archives import storage
from core.database import get_connection


def run(threshold: float = None, source_project: str = "claude-account-import") -> dict:
    threshold = classify.CLASSIFY_THRESHOLD if threshold is None else threshold

    project_refs = classify.load_project_refs_cache()
    if not project_refs:
        return {
            "error": "no cached project reference list yet — run the nightly import or "
                     "backfill_reclassify at least once against a real export first"
        }

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, summary FROM session_archives WHERE project = ?",
            (source_project,),
        ).fetchall()
    finally:
        conn.close()
    candidates = [dict(r) for r in rows]

    if not candidates:
        return {"error": f"nothing in '{source_project}' to reclassify"}

    # classify() decides None-vs-match against module-level CLASSIFY_THRESHOLD,
    # so swap it for the caller's value for this call only.
    orig_threshold = classify.CLASSIFY_THRESHOLD
    classify.CLASSIFY_THRESHOLD = threshold
    try:
        classifications = classify.classify(
            [{"name": c["title"], "summary": c["summary"] or ""} for c in candidates],
            project_refs,
        )
    finally:
        classify.CLASSIFY_THRESHOLD = orig_threshold

    moved = 0
    per_project_counts = {}
    for candidate, (project_slug, score) in zip(candidates, classifications):
        if not project_slug or project_slug == source_project:
            continue
        result = storage.reclassify_archive(candidate["id"], project_slug)
        if result.get("moved"):
            moved += 1
            per_project_counts[project_slug] = per_project_counts.get(project_slug, 0) + 1

    return {
        "threshold_used": threshold,
        "candidates_considered": len(candidates),
        "moved_to_real_project": moved,
        "remaining_in_catch_all": len(candidates) - moved,
        "per_project_counts": per_project_counts,
    }


if __name__ == "__main__":
    import json
    t = float(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps(run(threshold=t), indent=2))
