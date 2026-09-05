"""jobs/session_archives/classify.py — classify a conversation's title+summary
against Bill's known named Claude.ai projects, so imported conversations land
in the right project bucket instead of a flat catch-all.

Uses the same local sentence-transformers model already running for KB search
(jobs/skills/kb_search.py) — all-MiniLM-L6-v2, CPU, no network call — rather
than an LLM call per conversation. At ~1000 conversations per export this is
the difference between a few seconds and tens of minutes, and it's a
deterministic similarity score rather than an LLM's judgment call.

Conversations that don't clear CLASSIFY_THRESHOLD against any named project
stay in the catch-all project (see claude_export_import.FALLBACK_PROJECT) —
these are the "random chats" Bill expects to see, not misfiled guesses.
"""
import json
from pathlib import Path

import numpy as np
from chromadb.utils import embedding_functions

# Persisted copy of the last-seen project name+description list, written
# every time a projects-*.zip is actually processed (nightly import or
# backfill). Export download URLs are single-use — Bill has to regenerate a
# whole new account export to get another one — so re-tuning
# CLASSIFY_THRESHOLD and re-running against what's already archived
# shouldn't require him to burn another export just to get the project list
# back. See jobs/session_archives/reclassify_threshold.py.
#
# This file is fully overwritten every time build_project_refs() runs against
# a real export, so it only ever reflects Bill's actual named Claude.ai
# projects. Hand-defined buckets that don't correspond to a real Claude.ai
# project (e.g. "watson-development" — a huge, real, ongoing body of work
# that was never done inside a Claude.ai Project container) live in
# _MANUAL_PROJECT_REFS instead, which nothing auto-overwrites, and get merged
# in by load_project_refs_cache() every time classification actually runs.
_PROJECT_REFS_CACHE = Path(__file__).resolve().parents[2] / "data" / "session_archives" / "_project_refs.json"
_MANUAL_PROJECT_REFS = Path(__file__).resolve().parents[2] / "data" / "session_archives" / "_manual_project_refs.json"

# Started conservative at 0.40 (a conversation that doesn't clearly match
# stays in the catch-all rather than getting misfiled). Lowered to 0.30 after
# the first real backfill (2026-08-26): 0.40 left ~649 conversations
# unsorted, most of them genuinely classifiable — re-running at 0.30 moved
# 270 more with no visible false positives in a manual spot-check of the
# lowest-scoring matches (0.30-0.35 range). Kept as the permanent default,
# not just a one-off override, since it held up in practice.
CLASSIFY_THRESHOLD = 0.30

_ef = None


def _get_ef():
    global _ef
    if _ef is None:
        _ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2", device="cpu", local_files_only=True,
        )
    return _ef


def _cosine_sim_matrix(a, b):
    a = np.array(a)
    b = np.array(b)
    a_norm = a / np.linalg.norm(a, axis=1, keepdims=True)
    b_norm = b / np.linalg.norm(b, axis=1, keepdims=True)
    return a_norm @ b_norm.T


def build_project_refs(projects: list) -> list:
    """projects: raw project dicts from a Claude.ai export (projects-*.zip).
    Returns [{slug, name, text}] for every project with a real name — blank-
    named projects (Claude.ai's auto-grouped throwaway chats) aren't real
    writing projects and can't seed a meaningful reference vector, so they're
    excluded as classification targets."""
    from jobs.session_archives.storage import _slugify

    refs = []
    for p in projects:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        description = (p.get("description") or "").strip()
        refs.append({
            "slug": _slugify(name, max_len=60),
            "name": name,
            "text": f"{name}. {description}" if description else name,
        })
    if refs:
        save_project_refs_cache(refs)
    return _merge_manual(refs)


def _merge_manual(refs: list) -> list:
    """Manual refs win on a matching slug — lets a hand-written description
    (richer than a real Claude.ai project's often-blank description field)
    enrich classification for a real project without inventing a duplicate
    bucket. A manual ref with a slug that isn't a real project at all (e.g.
    a genuinely synthetic bucket) is just added alongside."""
    manual_by_slug = {m["slug"]: m for m in load_manual_project_refs()}
    merged = [manual_by_slug.pop(r["slug"], r) for r in refs]
    merged.extend(manual_by_slug.values())
    return merged


def save_project_refs_cache(refs: list) -> None:
    _PROJECT_REFS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _PROJECT_REFS_CACHE.write_text(json.dumps(refs, indent=2), encoding="utf-8")


def load_project_refs_cache() -> list:
    refs = []
    if _PROJECT_REFS_CACHE.is_file():
        try:
            refs = json.loads(_PROJECT_REFS_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _merge_manual(refs)


def load_manual_project_refs() -> list:
    if not _MANUAL_PROJECT_REFS.is_file():
        return []
    try:
        return json.loads(_MANUAL_PROJECT_REFS.read_text(encoding="utf-8"))
    except Exception:
        return []


def add_manual_project_ref(slug: str, name: str, text: str) -> None:
    """Register a hand-defined classification target that doesn't correspond
    to a real Claude.ai project (e.g. watson-development) — persists across
    future real-export cache refreshes, unlike _PROJECT_REFS_CACHE."""
    refs = [r for r in load_manual_project_refs() if r["slug"] != slug]
    refs.append({"slug": slug, "name": name, "text": text})
    _MANUAL_PROJECT_REFS.parent.mkdir(parents=True, exist_ok=True)
    _MANUAL_PROJECT_REFS.write_text(json.dumps(refs, indent=2), encoding="utf-8")


def classify(conversations: list, project_refs: list) -> list:
    """conversations: [{name, summary}, ...]. Returns a parallel list of
    (project_slug or None, score) tuples — None means no project cleared
    CLASSIFY_THRESHOLD, i.e. this stays in the catch-all."""
    if not project_refs or not conversations:
        return [(None, 0.0) for _ in conversations]

    ef = _get_ef()
    proj_vecs = ef([p["text"] for p in project_refs])
    conv_texts = [f"{(c.get('name') or '').strip()}. {(c.get('summary') or '').strip()}" for c in conversations]
    conv_vecs = ef(conv_texts)
    sims = _cosine_sim_matrix(conv_vecs, proj_vecs)

    results = []
    for i in range(len(conversations)):
        best_j = int(np.argmax(sims[i]))
        best_score = float(sims[i][best_j])
        if best_score >= CLASSIFY_THRESHOLD:
            results.append((project_refs[best_j]["slug"], best_score))
        else:
            results.append((None, best_score))
    return results
