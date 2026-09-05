"""jobs/migration/brevo_import.py — One-off: apply a kit_export.py snapshot
to Brevo, upserting each contact's approved attributes and list membership
(lead magnets + signup sources).

Part of the Kit -> Brevo migration (Phase 3) — see
~/watson/memory/kit_brevo_audit.md, "Phase 3 — final decisions".

Maps each contact's kit_export.py fields onto the APPROVED Brevo targets:

- ARC_READER, WRITING_ROOM_PARTNER, COMPANION_GUIDE_READER, DONOR — boolean
  attributes, set directly from the export's boolean fields.
- DONOR_SEGMENT — text attribute, set when the export has one.
- TWJ_LAUNCH_SIGNUP — boolean attribute, set only when true.
- LEAD_MAGNETS — NOT an attribute. Per-slug Brevo **list membership**
  (Bill's approved call — a single text attribute can't hold multiple
  magnet claims without data loss). One list per slug, named
  "Lead Magnet: {slug}".
- SIGNUP_SOURCES — also list membership, not attributes (audit doc
  "Phase 3 — final decisions", final 4-list set). Each export key maps to
  one Brevo list via _SIGNUP_SOURCE_LIST_NAMES below:
    ARC              -> "Signup Source: ARC"
    WRITING_ROOM     -> "Signup Source: Writing Room"
    TWJ_LAUNCH_PAGE  -> "Signup Source: TWJ Launch Page"
    LEGACY_FMS_WCKY  -> "Signup Source (legacy): FMS/WCKY"
  The first three duplicate a population already covered by a boolean
  attribute above (same Kit tag) — by design, not a bug: the attribute
  serves filtering/personalization, the list serves Brevo's native
  audience-picker UI in Comms Desk. LEGACY_FMS_WCKY has no attribute
  equivalent — it's a one-time historical carry-over, not ongoing
  tracking (see kit_export.py's docstring).

All list membership (lead-magnet and signup-source alike) is sent via the
`listIds` upsert param, which Brevo's own docs describe as ADDING the
contact to those lists ("Ids of the lists to add the contact to") —
confirmed additive, not replace, so this is safe to send without first
reading the contact's current list membership.

**`--folder-id` is REQUIRED for any `--live` run — no auto-pick of the
account's first folder.** This is a standing requirement (Bill,
2026-08-17), not specific to one run: picking a folder silently was
judged too risky to default. Every list (lead-magnet and signup-source)
is get-or-created under this one folder.

HARD REQUIREMENT (Phase 3, kit_brevo_audit.md "Phase 3 hard requirement"
section) — suppression-preserving upserts: Brevo does not document
whether an upsert that omits `smtpBlacklistSender`/`emailBlacklisted`
preserves or clears those fields on a contact that already has them set,
and Brevo's own community forum confirms REPLACE (not merge) semantics
for array-shaped contact fields in general. Per the standing rule for
this migration, undocumented is treated as "will clear," not assumed
safe. So: before building each contact's payload, this script checks the
contact's email against the most recent kit_suppression_*.json snapshot
(the same file brevo_suppression_import.py consumes for Phase 2). If the
email is suppressed, `smtpBlacklistSender: [DEFAULT_FROM_EMAIL]` and
`emailBlacklisted: true` are explicitly included in THIS script's upsert
payload too — every run, not just the first — so a Phase 3 run can never
silently undo Phase 2's suppression work.

Safe by default: dry run reads only the local kit_export_*.json and
kit_suppression_*.json snapshot files and prints a summary — it makes
ZERO Brevo API calls (same convention as brevo_suppression_import.py's
dry run). Only --live calls Brevo: resolves/creates lists, then upserts
every contact.
"""
import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.email_job.brevo_send import DEFAULT_FROM_EMAIL

load_dotenv(os.path.expanduser("~/watson/.env"))

_BASE = "https://api.brevo.com/v3"
_EXPORT_DIR = os.path.expanduser("~/watson/data/exports")
_TIMEOUT = 15
_DELAY_SECONDS = 0.2  # same politeness delay as brevo_suppression_import.py / dispatch.py
_LEAD_MAGNET_LIST_PREFIX = "Lead Magnet: "

# Final signup-source list set — audit doc "Phase 3 — final decisions" (2026-08-17).
_SIGNUP_SOURCE_LIST_NAMES = {
    "ARC": "Signup Source: ARC",
    "WRITING_ROOM": "Signup Source: Writing Room",
    "TWJ_LAUNCH_PAGE": "Signup Source: TWJ Launch Page",
    "LEGACY_FMS_WCKY": "Signup Source (legacy): FMS/WCKY",
}


def _headers() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": os.getenv("BREVO_API_KEY", ""),
    }


def _latest_snapshot(prefix: str) -> str | None:
    candidates = sorted(glob.glob(os.path.join(_EXPORT_DIR, f"{prefix}_*.json")))
    return candidates[-1] if candidates else None


def _lead_magnet_list_name(slug: str) -> str:
    return f"{_LEAD_MAGNET_LIST_PREFIX}{slug}"


def load_export(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f).get("contacts", [])


def load_suppressed_emails(path: str | None) -> set[str]:
    """Empty set (not an error) if no suppression snapshot exists yet —
    that's a legitimate state before Phase 2 has ever run --live, but it
    means nothing is protected, so the caller should surface this."""
    if not path:
        return set()
    with open(path) as f:
        data = json.load(f)
    return {s["email"] for s in data.get("suppressed", []) if s.get("email")}


def required_list_names(contacts: list[dict]) -> set[str]:
    """Every Brevo list name this import needs to exist — lead-magnet
    lists for every slug seen, plus whichever of the 4 signup-source
    lists any contact actually needs."""
    names = {_lead_magnet_list_name(slug) for c in contacts for slug in c.get("LEAD_MAGNETS", [])}
    names |= {
        _SIGNUP_SOURCE_LIST_NAMES[key]
        for c in contacts
        for key in c.get("SIGNUP_SOURCES", [])
        if key in _SIGNUP_SOURCE_LIST_NAMES
    }
    return names


def build_contact_payload(contact: dict, suppressed_emails: set[str], name_to_list_id: dict[str, int]) -> dict:
    attributes = {
        "ARC_READER": bool(contact.get("ARC_READER")),
        "WRITING_ROOM_PARTNER": bool(contact.get("WRITING_ROOM_PARTNER")),
        "COMPANION_GUIDE_READER": bool(contact.get("COMPANION_GUIDE_READER")),
        "DONOR": bool(contact.get("DONOR")),
    }
    if contact.get("first_name"):
        attributes["FIRSTNAME"] = contact["first_name"]
    if contact.get("DONOR_SEGMENT"):
        attributes["DONOR_SEGMENT"] = contact["DONOR_SEGMENT"]
    if contact.get("TWJ_LAUNCH_SIGNUP"):
        attributes["TWJ_LAUNCH_SIGNUP"] = True

    payload = {"email": contact["email"], "updateEnabled": True, "attributes": attributes}

    list_ids = []
    for slug in contact.get("LEAD_MAGNETS", []):
        list_id = name_to_list_id.get(_lead_magnet_list_name(slug))
        if list_id is not None:
            list_ids.append(list_id)
    for key in contact.get("SIGNUP_SOURCES", []):
        list_name = _SIGNUP_SOURCE_LIST_NAMES.get(key)
        list_id = name_to_list_id.get(list_name) if list_name else None
        if list_id is not None:
            list_ids.append(list_id)
    if list_ids:
        payload["listIds"] = list_ids

    if contact["email"] in suppressed_emails:
        payload["smtpBlacklistSender"] = [DEFAULT_FROM_EMAIL]
        payload["emailBlacklisted"] = True

    return payload


def fetch_existing_lists() -> dict[str, int]:
    """{list_name: list_id} for every Brevo list, paginated."""
    out: dict[str, int] = {}
    offset = 0
    while True:
        resp = requests.get(
            f"{_BASE}/contacts/lists", params={"limit": 50, "offset": offset},
            headers=_headers(), timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        lists = resp.json().get("lists", [])
        out.update({l["name"]: l["id"] for l in lists})
        if len(lists) < 50:
            break
        offset += 50
    return out


def get_or_create_lists(names: set[str], folder_id: int) -> dict[str, int]:
    """{list_name: list_id}, creating any list in `names` that doesn't
    already exist under folder_id. Only called under --live."""
    existing = fetch_existing_lists()
    result = {}
    for name in sorted(names):
        if name in existing:
            result[name] = existing[name]
            continue
        resp = requests.post(
            f"{_BASE}/contacts/lists", json={"name": name, "folderId": folder_id},
            headers=_headers(), timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        result[name] = resp.json()["id"]
    return result


def apply_contact(payload: dict) -> tuple[bool, str]:
    resp = requests.post(f"{_BASE}/contacts", json=payload, headers=_headers(), timeout=_TIMEOUT)
    if resp.status_code in (200, 201, 204):
        return True, str(resp.status_code)
    return False, f"{resp.status_code}: {resp.text[:200]}"


def run(export_path: str, suppression_path: str | None, live: bool = False, folder_id: int | None = None) -> dict:
    contacts = load_export(export_path)
    suppressed_emails = load_suppressed_emails(suppression_path)
    overlap = sum(1 for c in contacts if c["email"] in suppressed_emails)
    all_list_names = required_list_names(contacts)

    if not live:
        return {
            "live": False,
            "export": export_path,
            "suppression_snapshot": suppression_path,
            "would_import": len(contacts),
            "suppressed_overlap": overlap,
            "lists_needed": sorted(all_list_names),
            "sample": [c["email"] for c in contacts[:10]],
        }

    if not os.getenv("BREVO_API_KEY"):
        raise RuntimeError("BREVO_API_KEY not set in .env")
    if not folder_id:
        raise RuntimeError(
            "--folder-id is required for --live runs. No auto-pick of the account's "
            "first folder — this is a standing requirement, confirmed by Bill 2026-08-17, "
            "not just for this run."
        )

    list_ids = get_or_create_lists(all_list_names, folder_id) if all_list_names else {}

    applied, failed = [], []
    for contact in contacts:
        payload = build_contact_payload(contact, suppressed_emails, list_ids)
        ok, detail = apply_contact(payload)
        (applied if ok else failed).append({"email": contact["email"], "detail": detail})
        time.sleep(_DELAY_SECONDS)

    return {
        "live": True,
        "export": export_path,
        "folder_id": folder_id,
        "lists_created_or_reused": list_ids,
        "applied_count": len(applied),
        "failed_count": len(failed),
        "failed": failed,
        "suppressed_overlap": overlap,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Import a kit_export.py snapshot into Brevo — sets approved attributes and list membership (lead magnets + signup sources)."
    )
    parser.add_argument("--export", default=None, help="Path to a kit_export_*.json file. Defaults to the most recent one in data/exports/.")
    parser.add_argument("--suppression", default=None, help="Path to a kit_suppression_*.json file. Defaults to the most recent one in data/exports/.")
    parser.add_argument("--folder-id", type=int, default=None, help="Brevo folder ID for lead-magnet/signup-source lists. REQUIRED for --live — no auto-pick.")
    parser.add_argument("--live", action="store_true", help="Actually call Brevo's API. Without this flag, nothing is sent to Brevo.")
    args = parser.parse_args()

    export_path = args.export or _latest_snapshot("kit_export")
    if not export_path:
        raise SystemExit("No kit_export_*.json snapshot found in data/exports/ — run kit_export.py --live first.")
    suppression_path = args.suppression or _latest_snapshot("kit_suppression")

    result = run(export_path, suppression_path, live=args.live, folder_id=args.folder_id)

    if not result["live"]:
        print(f"[DRY RUN] Export: {result['export']}")
        print(f"[DRY RUN] Suppression snapshot: {result['suppression_snapshot'] or '(none found — nothing will be protected as suppressed)'}")
        print(f"[DRY RUN] Would import {result['would_import']} contacts.")
        print(f"[DRY RUN] {result['suppressed_overlap']} of those are in the suppression snapshot — "
              f"their upsert will force smtpBlacklistSender=[{DEFAULT_FROM_EMAIL}] + emailBlacklisted=true.")
        print(f"[DRY RUN] Lists needed: {result['lists_needed'] or '(none)'}")
        if result["sample"]:
            print(f"[DRY RUN] Sample: {', '.join(result['sample'])}")
        print("[DRY RUN] No Brevo API calls made. Re-run with --live (and --folder-id) to apply.")
    else:
        print(f"Folder id: {result['folder_id']}")
        print(f"Lists: {result['lists_created_or_reused']}")
        print(f"Applied {result['applied_count']} contacts ({result['suppressed_overlap']} suppression-protected).")
        if result["failed_count"]:
            print(f"Failed: {result['failed_count']}")
            for f in result["failed"][:20]:
                print(f"  {f['email']}: {f['detail']}")
