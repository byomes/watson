"""jobs/migration/kit_suppression_export.py — One-off: pull Kit's full
subscriber list and split out genuine opt-outs (cancelled/bounced/
complained) into a local JSON snapshot for later import into Brevo's
suppression list via brevo_suppression_import.py.

Part of the Kit -> Brevo migration (Phase 2) — see
~/watson/memory/kit_brevo_audit.md.

Kit v3's subscriber `state` enum has exactly five values: active,
cancelled, bounced, complained, inactive. Only cancelled/bounced/complained
are genuine suppression signals (unsubscribed, hard-bounced, or marked
spam) — `inactive` is Kit's engagement-based state (no opens/clicks in
~90 days) and does NOT mean opted out, so it is deliberately excluded from
`_SUPPRESSED_STATES` below and bucketed separately in stats for visibility
instead of being silently dropped or silently miscounted as a suppression.

Uses the same v3 full-pagination + client-side state bucketing pattern as
jobs/campaigns/kit_import.py (api_secret query param), rather than relying
on Kit's subscriber_state=cancelled filter alone — that filter only
guarantees 'cancelled', and this needs bounced/complained too, so every
subscriber is paged and bucketed by its actual state instead.

Safe by default: --live is required to write anything. Without --live,
this still reads the real data from Kit (a read-only call, same convention
as kit_import.py's dry run) and prints a summary only — it does NOT write
data/exports/kit_suppression_<timestamp>.json. Only --live writes that
file. This script never talks to Brevo at all.
"""
import argparse
import json
import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

_KIT_SUBSCRIBERS_URL = "https://api.convertkit.com/v3/subscribers"
_ACTIVE_STATE = "active"
# Genuine opt-out/suppression signals only. Deliberately excludes 'inactive'
# (engagement-based, not an opt-out — see module docstring) and anything
# outside Kit's documented 5-value enum, which is bucketed separately
# below as "unrecognized" rather than silently treated as suppressed.
_SUPPRESSED_STATES = {"cancelled", "bounced", "complained"}
_OUTPUT_DIR = os.path.expanduser("~/watson/data/exports")


def fetch_all_subscribers() -> tuple[list[dict], dict]:
    """Page through every Kit subscriber and split into suppressed
    (cancelled/bounced/complained) vs everything else (active, inactive,
    or an unrecognized state). Returns (suppressed, stats)."""
    api_secret = os.getenv("KIT_API_SECRET", "")
    if not api_secret:
        raise RuntimeError("KIT_API_SECRET not set in .env")

    suppressed: list[dict] = []
    by_state: dict[str, int] = {}
    page = 1
    total_pages = 1
    total_seen = 0

    while page <= total_pages:
        resp = requests.get(
            _KIT_SUBSCRIBERS_URL,
            params={"api_secret": api_secret, "page": page},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        total_pages = data.get("total_pages", 1)

        for sub in data.get("subscribers", []):
            total_seen += 1
            state = sub.get("state", "unknown")
            by_state[state] = by_state.get(state, 0) + 1
            if state in _SUPPRESSED_STATES:
                suppressed.append({
                    "email": sub.get("email_address"),
                    "first_name": sub.get("first_name"),
                    "kit_subscriber_id": sub.get("id"),
                    "kit_state": state,
                    "kit_created_at": sub.get("created_at"),
                })

        page += 1

    stats = {
        "total_seen": total_seen,
        "active_count": by_state.get(_ACTIVE_STATE, 0),
        "inactive_count": by_state.get("inactive", 0),
        "suppressed_count": len(suppressed),
        "by_state": by_state,
    }
    return suppressed, stats


def write_snapshot(suppressed: list[dict]) -> str:
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(_OUTPUT_DIR, f"kit_suppression_{timestamp}.json")
    with open(path, "w") as f:
        json.dump({
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": "kit_v3_subscribers",
            "count": len(suppressed),
            "suppressed": suppressed,
        }, f, indent=2)
    return path


def run(live: bool = False) -> dict:
    suppressed, stats = fetch_all_subscribers()

    if not live:
        return {"live": False, "would_export": len(suppressed), **stats}

    path = write_snapshot(suppressed)
    return {"live": True, "exported": len(suppressed), "path": path, **stats}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export Kit's genuinely suppressed (cancelled/bounced/complained) subscribers to a local JSON snapshot."
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Write the snapshot file to data/exports/. Without this flag, only a summary is printed and nothing is written."
    )
    args = parser.parse_args()

    result = run(live=args.live)

    print(f"Kit subscribers seen: {result['total_seen']}")
    print(f"Active: {result['active_count']}")
    print(f"Inactive (engagement-based, NOT a suppression, excluded): {result['inactive_count']}")
    print(f"Suppressed (cancelled/bounced/complained): {result['suppressed_count']}")
    print(f"By state: {result['by_state']}")
    if result["live"]:
        print(f"Exported {result['exported']} suppressed addresses to {result['path']}")
    else:
        print(
            f"[DRY RUN] Would export {result['would_export']} suppressed addresses. "
            "Re-run with --live to write the snapshot file. No files written, no Brevo calls made."
        )
