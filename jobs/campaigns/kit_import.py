"""jobs/campaigns/kit_import.py — Pull the Kit (ConvertKit) account-wide
subscriber list and import it into book_launch_contacts as the 'general'
segment for a given campaign.

Uses the same v3 API pattern already established elsewhere in this codebase
(api_secret in query params — see jobs/writing_room/onboard.py, jobs/givebutter/
sync.py). Kit's v4 API/credential is not used anywhere in this codebase yet.

Safe by default: --dry-run defaults to true. Only --dry-run=false inserts rows.
"""
import argparse
import os

import requests
from dotenv import load_dotenv

from core.database import get_connection

load_dotenv(os.path.expanduser("~/watson/.env"))

_KIT_SUBSCRIBERS_URL = "https://api.convertkit.com/v3/subscribers"
_ACTIVE_STATE = "active"


def fetch_all_subscribers() -> tuple[list[dict], dict]:
    """Page through every Kit subscriber, filter to state == 'active', and
    return (active_subscribers, stats) where stats reports total seen and
    exclusions by state."""
    api_secret = os.getenv("KIT_API_SECRET", "")
    if not api_secret:
        raise RuntimeError("KIT_API_SECRET not set in .env")

    active = []
    excluded_by_state: dict[str, int] = {}
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
            if state == _ACTIVE_STATE:
                active.append(sub)
            else:
                excluded_by_state[state] = excluded_by_state.get(state, 0) + 1

        page += 1

    stats = {
        "total_seen": total_seen,
        "active_count": len(active),
        "excluded_count": total_seen - len(active),
        "excluded_by_state": excluded_by_state,
    }
    return active, stats


def insert_contacts(campaign_id: str, subscribers: list[dict], conn=None) -> int:
    """Insert active subscribers into book_launch_contacts as segment='general',
    source='kit_export'. Returns the number of rows inserted."""
    owns_conn = conn is None
    conn = conn or get_connection()
    inserted = 0
    try:
        for sub in subscribers:
            conn.execute(
                """INSERT INTO book_launch_contacts
                   (campaign_id, email, name, segment, source)
                   VALUES (?, ?, ?, 'general', 'kit_export')""",
                (campaign_id, sub["email_address"], sub.get("first_name") or None),
            )
            inserted += 1
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
    return inserted


def run(campaign_id: str, dry_run: bool = True) -> dict:
    subscribers, stats = fetch_all_subscribers()

    if dry_run:
        return {"dry_run": True, "would_insert": len(subscribers), **stats}

    inserted = insert_contacts(campaign_id, subscribers)
    return {"dry_run": False, "inserted": inserted, **stats}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import Kit subscribers into book_launch_contacts.")
    parser.add_argument("--campaign-id", default="twj-2026")
    parser.add_argument("--dry-run", type=str, default="true")
    args = parser.parse_args()
    dry_run = args.dry_run.strip().lower() != "false"

    result = run(args.campaign_id, dry_run=dry_run)

    print(f"Kit subscribers seen: {result['total_seen']}")
    print(f"Active: {result['active_count']}")
    print(f"Excluded: {result['excluded_count']} ({result['excluded_by_state'] or 'none'})")
    if result["dry_run"]:
        print(f"[DRY RUN] Would insert {result['would_insert']} contacts for campaign_id={args.campaign_id}")
    else:
        print(f"Inserted {result['inserted']} contacts for campaign_id={args.campaign_id}")
