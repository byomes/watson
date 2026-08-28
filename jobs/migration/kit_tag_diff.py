"""jobs/migration/kit_tag_diff.py — One-off: diff every Kit tag that is
ACTUALLY applied to at least one live subscriber against the approved Kit
tag -> Brevo mapping, so brevo_import.py's attribute set is built from
real data, not just from what the codebase's write paths are believed to
cover.

Part of the Kit -> Brevo migration (Phase 3) — see
~/watson/memory/kit_brevo_audit.md, "Phase 3 — tag diff findings".

Read-only against Kit. Never touches Brevo. No --live flag — there is
nothing to write, this only prints/returns a report.

Method: GET /v3/tags (api_key) for the full tag list (every tag that has
ever been created in the account, whether currently applied or not), then
GET /v3/tags/{id}/subscriptions?page=1 (api_secret) for each tag — reading
just `total_subscriptions` off page 1 is enough to know whether a tag is
actually applied to anyone; no need to page through every subscriber
email for a diff. A tag with total_subscriptions == 0 is real but unused,
reported separately from "mapped" and "unmapped-and-applied".
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.database import get_connection
from jobs.arc.api import _ARC_TAG_ID

load_dotenv(os.path.expanduser("~/watson/.env"))

_KIT_TAGS_URL = "https://api.convertkit.com/v3/tags"
_TIMEOUT = 15

_WRITING_ROOM_TAG_NAME = "writing-room-partner"
_DONOR_TAG_NAME = "donor"
_DONOR_SEGMENT_NAMES = ("first-time-donor", "major-donor", "lapsed-donor", "recurring-donor")


def _api_key() -> str:
    key = os.getenv("KIT_API_KEY", "")
    if not key:
        raise RuntimeError("KIT_API_KEY not set in .env")
    return key


def _api_secret() -> str:
    secret = os.getenv("KIT_API_SECRET", "")
    if not secret:
        raise RuntimeError("KIT_API_SECRET not set in .env")
    return secret


def fetch_all_tags() -> list[dict]:
    """[{id, name}, ...] for every tag Kit has ever created in this account."""
    resp = requests.get(_KIT_TAGS_URL, params={"api_key": _api_key()}, timeout=_TIMEOUT)
    resp.raise_for_status()
    return [{"id": t["id"], "name": t["name"]} for t in resp.json().get("tags", [])]


def fetch_tag_applied_count(tag_id: int) -> int:
    """total_subscriptions for a tag — 0 means the tag exists but nobody
    currently has it. One request, not a full pagination pull."""
    resp = requests.get(
        f"{_KIT_TAGS_URL}/{tag_id}/subscriptions",
        params={"api_secret": _api_secret(), "page": 1},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("total_subscriptions", 0)


def mapped_tag_ids() -> dict[int, str]:
    """{tag_id: mapping_target} for every tag the approved Phase 1/3
    mapping already accounts for, so the diff can tell mapped from
    unmapped. Resolved by ID where the codebase hardcodes one
    (ARC, lead magnets, companion guide), by name otherwise (the mapping
    table itself only has names for these)."""
    ids: dict[int, str] = {_ARC_TAG_ID: "ARC_READER"}

    companion_id = os.getenv("KIT_COMPANION_TAG_ID")
    if companion_id:
        ids[int(companion_id)] = "COMPANION_GUIDE_READER"

    conn = get_connection()
    try:
        for row in conn.execute(
            "SELECT slug, kit_tag_id FROM lead_magnets WHERE kit_tag_id IS NOT NULL"
        ).fetchall():
            ids[row["kit_tag_id"]] = f"LEAD_MAGNETS (slug={row['slug']})"
    finally:
        conn.close()

    return ids


def diff() -> dict:
    all_tags = fetch_all_tags()
    mapped_ids = mapped_tag_ids()
    mapped_names = {_WRITING_ROOM_TAG_NAME: "WRITING_ROOM_PARTNER", _DONOR_TAG_NAME: "DONOR"}
    mapped_names.update({name: "DONOR_SEGMENT" for name in _DONOR_SEGMENT_NAMES})

    mapped, unmapped_applied, unmapped_unused = [], [], []

    for tag in all_tags:
        target = mapped_ids.get(tag["id"]) or mapped_names.get(tag["name"])
        count = fetch_tag_applied_count(tag["id"])
        entry = {"id": tag["id"], "name": tag["name"], "applied_count": count}

        if target:
            entry["mapped_to"] = target
            mapped.append(entry)
        elif count > 0:
            unmapped_applied.append(entry)
        else:
            unmapped_unused.append(entry)

    return {
        "total_tags_in_account": len(all_tags),
        "mapped": mapped,
        "unmapped_and_applied": unmapped_applied,
        "unmapped_and_unused": unmapped_unused,
    }


if __name__ == "__main__":
    result = diff()
    print(f"Total tags in Kit account: {result['total_tags_in_account']}")
    print(f"\nMapped ({len(result['mapped'])}):")
    for t in result["mapped"]:
        print(f"  [{t['id']}] {t['name']} -> {t['mapped_to']} ({t['applied_count']} subscribers)")
    print(f"\nUNMAPPED and actually applied to subscribers ({len(result['unmapped_and_applied'])}) — needs a decision:")
    for t in result["unmapped_and_applied"]:
        print(f"  [{t['id']}] {t['name']} — {t['applied_count']} subscribers")
    print(f"\nUnmapped but unused (0 subscribers, informational only) ({len(result['unmapped_and_unused'])}):")
    for t in result["unmapped_and_unused"]:
        print(f"  [{t['id']}] {t['name']}")
