"""jobs/migration/kit_export.py — One-off: full Kit contact export with
tag-derived attributes, for import into Brevo via brevo_import.py.

Part of the Kit -> Brevo migration (Phase 3) — see
~/watson/memory/kit_brevo_audit.md.

Pulls every Kit subscriber (same full-pagination pattern as
kit_suppression_export.py), plus subscriber membership in each tag from
the APPROVED Kit tag -> Brevo mapping (see audit doc's "Kit tag -> Brevo
attribute mapping" table), and combines the two into one row per
subscriber:

- email, first_name, kit_state (raw Kit subscriber state — carried for
  reference only; Phase 2's kit_suppression_*.json snapshot remains the
  one source of truth for which contacts are suppressed)
- ARC_READER (bool) — _ARC_TAG_ID, imported from jobs.arc.api (single
  source of truth for that tag ID; same tag covers full ARC signup and
  ARC waitlist per the audit doc)
- WRITING_ROOM_PARTNER (bool) — the "writing-room-partner" tag, resolved
  by name via GET /v3/tags (no hardcoded ID exists for it anywhere else
  in this codebase either)
- COMPANION_GUIDE_READER (bool) — KIT_COMPANION_TAG_ID env var
- LEAD_MAGNETS (list of magnet slugs) — per-magnet kit_tag_id, read from
  the lead_magnets table in watson.db (one row per magnet)
- DONOR (bool) + DONOR_SEGMENT (str | None) — the "donor" tag plus
  whichever one of the four segment tags Kit has
  (first-time-donor/major-donor/lapsed-donor/recurring-donor — the exact
  set jobs/givebutter/sync.py's _compute_segment produces), resolved by
  name
- TWJ_LAUNCH_SIGNUP (bool) — tag ID 20828390. Watson's own .env has no
  KIT_TWJ_TAG_ID (per the audit doc's env var table, that tag lives only
  on wcky's separate Vercel Kit credential), but tags are account-wide in
  Kit regardless of which API key created them — this ID was found
  empirically via jobs/migration/kit_tag_diff.py (2026-08-17) and
  confirmed against a real subscriber count, not guessed. Set as this
  script's default for --twj-tag-id; still overridable.
- SIGNUP_SOURCES (list of internal keys: "ARC", "WRITING_ROOM",
  "TWJ_LAUNCH_PAGE", "LEGACY_FMS_WCKY") — the approved signup-source
  list set (audit doc "Phase 3 — final decisions"). Each of the first
  three duplicates a population already covered by a boolean attribute
  above (same underlying Kit tag) — by design, not a bug: the attribute
  serves filtering/personalization logic, the list serves Brevo's native
  audience-picker UI. LEGACY_FMS_WCKY has no attribute equivalent — it's
  a one-time historical carry-over of the FMS (19057804) and WCKY
  (19057806) tags, which fed a now-dead /api/kit/subscribe route
  (confirmed dead in Phase 1, not migrated) but still have 7 real
  contacts on them. Nothing in Watson's code will ever add anyone to
  this list going forward. brevo_import.py maps each key to its Brevo
  list name via listIds, same as LEAD_MAGNETS.

Uses GET /v3/tags (api_key, one call) to resolve tag IDs by name, then
GET /v3/tags/{id}/subscriptions (api_secret, paginated 50/page) once per
resolved tag — far cheaper than a per-subscriber tag lookup, which would
be one API call per subscriber (thousands of calls).

Safe by default: --live is required to write anything. Without --live,
this still reads the real data from Kit (read-only calls, same
convention as kit_suppression_export.py's dry run) and prints a summary
only — it does NOT write data/exports/kit_export_<timestamp>.json. Only
--live writes that file. This script never talks to Brevo at all.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.database import get_connection
from jobs.arc.api import _ARC_TAG_ID

load_dotenv(os.path.expanduser("~/watson/.env"))

_KIT_SUBSCRIBERS_URL = "https://api.convertkit.com/v3/subscribers"
_KIT_TAGS_URL = "https://api.convertkit.com/v3/tags"
_OUTPUT_DIR = os.path.expanduser("~/watson/data/exports")
_TIMEOUT = 15

_WRITING_ROOM_TAG_NAME = "writing-room-partner"
_DONOR_TAG_NAME = "donor"
# Exact set jobs/givebutter/sync.py's _compute_segment() can produce —
# kept in sync with that function, not re-derived here.
_DONOR_SEGMENT_NAMES = ("first-time-donor", "major-donor", "lapsed-donor", "recurring-donor")

# Confirmed via jobs/migration/kit_tag_diff.py (2026-08-17) against a real
# subscriber count — see module docstring. Not in Watson's .env; wcky's
# separate Vercel Kit credential created this tag, but it's account-wide.
_TWJ_TAG_ID_DEFAULT = 20828390

# Legacy FMS/WCKY signup-source list — see module docstring. Same env
# vars the now-dead /api/kit/subscribe route used (jobs/dashboard/app.py),
# read here only to preserve the 7 existing contacts' history, not to
# revive that route.
_FMS_TAG_ID = os.getenv("KIT_FMS_TAG_ID")
_WCKY_TAG_ID = os.getenv("KIT_WCKY_TAG_ID")


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


def fetch_all_subscribers() -> dict[str, dict]:
    """Page through every Kit subscriber. Returns {email: {first_name,
    kit_subscriber_id, kit_state, kit_created_at}} for every subscriber
    regardless of state — suppression filtering is Phase 2's job, not
    this script's."""
    subscribers: dict[str, dict] = {}
    page, total_pages = 1, 1

    while page <= total_pages:
        resp = requests.get(
            _KIT_SUBSCRIBERS_URL,
            params={"api_secret": _api_secret(), "page": page},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        total_pages = data.get("total_pages", 1)

        for sub in data.get("subscribers", []):
            email = sub.get("email_address")
            if not email:
                continue
            subscribers[email] = {
                "first_name": sub.get("first_name"),
                "kit_subscriber_id": sub.get("id"),
                "kit_state": sub.get("state"),
                "kit_created_at": sub.get("created_at"),
            }
        page += 1

    return subscribers


def fetch_tag_map() -> dict[str, int]:
    """{tag_name: tag_id} for every tag in the Kit account."""
    resp = requests.get(_KIT_TAGS_URL, params={"api_key": _api_key()}, timeout=_TIMEOUT)
    resp.raise_for_status()
    return {t["name"]: t["id"] for t in resp.json().get("tags", [])}


def fetch_tag_subscriber_emails(tag_id: int) -> set[str]:
    """Every subscriber email currently subscribed to tag_id (any state —
    active and cancelled both included, since a cancelled subscriber's
    past tag membership is still real signal for the export)."""
    emails: set[str] = set()
    page, total_pages = 1, 1

    while page <= total_pages:
        resp = requests.get(
            f"{_KIT_TAGS_URL}/{tag_id}/subscriptions",
            params={"api_secret": _api_secret(), "page": page},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        total_pages = data.get("total_pages", 1)

        for entry in data.get("subscriptions", []):
            email = (entry.get("subscriber") or {}).get("email_address")
            if email:
                emails.add(email)
        page += 1

    return emails


def fetch_lead_magnet_tags() -> dict[str, int]:
    """{slug: kit_tag_id} for every active lead magnet with a tag ID set."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT slug, kit_tag_id FROM lead_magnets WHERE kit_tag_id IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return {row["slug"]: row["kit_tag_id"] for row in rows}


def build_export(twj_tag_id: int | None = _TWJ_TAG_ID_DEFAULT) -> tuple[list[dict], dict]:
    """Combine subscriber list + tag memberships into one record per
    subscriber. Returns (contacts, stats)."""
    subscribers = fetch_all_subscribers()
    tag_map = fetch_tag_map()

    companion_tag_id = os.getenv("KIT_COMPANION_TAG_ID")
    companion_tag_id = int(companion_tag_id) if companion_tag_id else None

    writing_room_tag_id = tag_map.get(_WRITING_ROOM_TAG_NAME)
    donor_tag_id = tag_map.get(_DONOR_TAG_NAME)
    segment_tag_ids = {name: tag_map[name] for name in _DONOR_SEGMENT_NAMES if name in tag_map}
    lead_magnet_tag_ids = fetch_lead_magnet_tags()
    fms_tag_id = int(_FMS_TAG_ID) if _FMS_TAG_ID else None
    wcky_tag_id = int(_WCKY_TAG_ID) if _WCKY_TAG_ID else None

    unresolved = []
    if writing_room_tag_id is None:
        unresolved.append(_WRITING_ROOM_TAG_NAME)
    if companion_tag_id is None:
        unresolved.append("KIT_COMPANION_TAG_ID (env not set)")
    if donor_tag_id is None:
        unresolved.append(_DONOR_TAG_NAME)
    if not twj_tag_id:
        unresolved.append("TWJ_LAUNCH_SIGNUP (no --twj-tag-id — skipped)")
    if fms_tag_id is None:
        unresolved.append("LEGACY_FMS_WCKY: KIT_FMS_TAG_ID not set")
    if wcky_tag_id is None:
        unresolved.append("LEGACY_FMS_WCKY: KIT_WCKY_TAG_ID not set")

    arc_emails = fetch_tag_subscriber_emails(_ARC_TAG_ID)
    writing_room_emails = fetch_tag_subscriber_emails(writing_room_tag_id) if writing_room_tag_id else set()
    companion_emails = fetch_tag_subscriber_emails(companion_tag_id) if companion_tag_id else set()
    donor_emails = fetch_tag_subscriber_emails(donor_tag_id) if donor_tag_id else set()
    segment_emails = {name: fetch_tag_subscriber_emails(tid) for name, tid in segment_tag_ids.items()}
    lead_magnet_emails = {slug: fetch_tag_subscriber_emails(tid) for slug, tid in lead_magnet_tag_ids.items()}
    twj_emails = fetch_tag_subscriber_emails(twj_tag_id) if twj_tag_id else set()
    legacy_fms_wcky_emails = (fetch_tag_subscriber_emails(fms_tag_id) if fms_tag_id else set()) | (
        fetch_tag_subscriber_emails(wcky_tag_id) if wcky_tag_id else set()
    )

    contacts = []
    for email, info in subscribers.items():
        segment = next((name for name, emails in segment_emails.items() if email in emails), None)
        magnets = sorted(slug for slug, emails in lead_magnet_emails.items() if email in emails)

        signup_sources = []
        if email in arc_emails:
            signup_sources.append("ARC")
        if email in writing_room_emails:
            signup_sources.append("WRITING_ROOM")
        if email in twj_emails:
            signup_sources.append("TWJ_LAUNCH_PAGE")
        if email in legacy_fms_wcky_emails:
            signup_sources.append("LEGACY_FMS_WCKY")

        contacts.append({
            "email": email,
            "first_name": info["first_name"],
            "kit_subscriber_id": info["kit_subscriber_id"],
            "kit_state": info["kit_state"],
            "kit_created_at": info["kit_created_at"],
            "ARC_READER": email in arc_emails,
            "WRITING_ROOM_PARTNER": email in writing_room_emails,
            "COMPANION_GUIDE_READER": email in companion_emails,
            "LEAD_MAGNETS": magnets,
            "DONOR": email in donor_emails,
            "DONOR_SEGMENT": segment,
            "TWJ_LAUNCH_SIGNUP": email in twj_emails,
            "SIGNUP_SOURCES": signup_sources,
        })

    stats = {
        "total_subscribers": len(subscribers),
        "arc_reader_count": len(arc_emails),
        "writing_room_partner_count": len(writing_room_emails),
        "companion_guide_reader_count": len(companion_emails),
        "lead_magnet_counts": {slug: len(emails) for slug, emails in lead_magnet_emails.items()},
        "donor_count": len(donor_emails),
        "donor_segment_counts": {name: len(emails) for name, emails in segment_emails.items()},
        "twj_launch_signup_count": len(twj_emails),
        "legacy_fms_wcky_count": len(legacy_fms_wcky_emails),
        "unresolved_tags": unresolved,
    }
    return contacts, stats


def write_snapshot(contacts: list[dict], stats: dict) -> str:
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(_OUTPUT_DIR, f"kit_export_{timestamp}.json")
    with open(path, "w") as f:
        json.dump({
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": "kit_v3_subscribers_and_tags",
            "stats": stats,
            "contacts": contacts,
        }, f, indent=2)
    return path


def run(live: bool = False, twj_tag_id: int | None = _TWJ_TAG_ID_DEFAULT) -> dict:
    contacts, stats = build_export(twj_tag_id=twj_tag_id)

    if not live:
        return {"live": False, "would_export": len(contacts), "stats": stats}

    path = write_snapshot(contacts, stats)
    return {"live": True, "exported": len(contacts), "path": path, "stats": stats}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export every Kit subscriber plus their tag-derived attributes to a local JSON snapshot."
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Write the snapshot file to data/exports/. Without this flag, only a summary is printed and nothing is written."
    )
    parser.add_argument(
        "--twj-tag-id", type=int, default=_TWJ_TAG_ID_DEFAULT,
        help=f"Kit tag ID for TWJ launch signups. Defaults to {_TWJ_TAG_ID_DEFAULT} (confirmed via kit_tag_diff.py, not in Watson's .env — see module docstring). Pass 0 to skip TWJ_LAUNCH_SIGNUP entirely."
    )
    args = parser.parse_args()

    result = run(live=args.live, twj_tag_id=args.twj_tag_id)
    stats = result["stats"]

    print(f"Kit subscribers seen: {stats['total_subscribers']}")
    print(f"ARC_READER: {stats['arc_reader_count']}")
    print(f"WRITING_ROOM_PARTNER: {stats['writing_room_partner_count']}")
    print(f"COMPANION_GUIDE_READER: {stats['companion_guide_reader_count']}")
    print(f"Lead magnets: {stats['lead_magnet_counts']}")
    print(f"DONOR: {stats['donor_count']}")
    print(f"Donor segments: {stats['donor_segment_counts']}")
    print(f"TWJ_LAUNCH_SIGNUP: {stats['twj_launch_signup_count']}")
    print(f"Signup Source (legacy): FMS/WCKY: {stats['legacy_fms_wcky_count']}")
    if stats["unresolved_tags"]:
        print(f"Unresolved (skipped, not guessed): {stats['unresolved_tags']}")

    if result["live"]:
        print(f"Exported {result['exported']} contacts to {result['path']}")
    else:
        print(
            f"[DRY RUN] Would export {result['would_export']} contacts. "
            "Re-run with --live to write the snapshot file. No files written."
        )
