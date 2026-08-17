"""jobs/migration/brevo_suppression_import.py — One-off: apply a Kit
suppression snapshot (produced by kit_suppression_export.py --live) to
Brevo.

Part of the Kit -> Brevo migration (Phase 2) — see
~/watson/memory/kit_brevo_audit.md.

Sets TWO fields per contact, and both matter for a different reason:

- `smtpBlacklistSender: [DEFAULT_FROM_EMAIL]` — the field that actually
  takes effect against Watson's real send path. Confirmed via Brevo's own
  API reference (developers.brevo.com/reference/updatecontact,
  /reference/createcontact): a list-of-string field, "transactional email
  forbidden sender for contact." Scoped to
  jobs.email_job.brevo_send.DEFAULT_FROM_EMAIL
  (watson@williamckyomes.com) — Watson's existing, already-verified
  sender. Per Brevo's docs this requires `updateEnabled: true` on
  POST /v3/contacts (upsert) to take effect for a newly-created contact.

  KNOWN, ACCEPTED LIMITATION: Watson sends everything (newsletters and
  transactional mail alike — password resets, welcome emails) from this
  one sender address, and smtpBlacklistSender blocks per-sender, not
  per-message-type. There is no dedicated newsletter sender to scope this
  to. Practical effect: a contact suppressed here because they opted out
  of / bounced on a newsletter will ALSO stop receiving transactional
  mail from Watson. Accepted as low-risk because the suppression list is
  built from bounced/complained Kit addresses, which are unlikely to also
  need transactional mail from Watson — but this is a real tradeoff, not
  an oversight, and is documented here plus in
  ~/watson/memory/kit_brevo_audit.md so it isn't quietly rediscovered
  later. A dedicated newsletter sender was considered and deliberately
  rejected in favor of this simpler approach.
- `emailBlacklisted: true` — Brevo's native Campaigns/Automation blocklist
  flag. Confirmed (via Brevo's docs + an official staff community reply)
  that this does NOT affect transactional sends at all, so it's a no-op
  against Watson's current send path — but it's harmless to set (zero
  effect on brevo_send.py) and it accurately records that these contacts
  are genuinely opted out, which matters if Comms Desk or any future work
  ever sends through Brevo's native Campaigns feature instead of the
  transactional API. Set alongside smtpBlacklistSender for that reason,
  not as the enforcement mechanism.

Known gap in Brevo's docs, flagged rather than guessed around: neither
reference page documents whether smtpBlacklistSender REPLACES or MERGES
with a contact's existing forbidden-sender list on a repeat write, and the
standard GET /v3/contacts/{id} response doesn't expose the current array
back for a read-before-write check (confirmed via
developers.brevo.com/reference/get-contact-info — it only returns the
emailBlacklisted/smsBlacklisted/whatsappBlacklisted booleans, no
per-sender detail). Since DEFAULT_FROM_EMAIL is Watson's one and only
sender, a repeat run against a contact that already has it blocked is
expected to be idempotent in practice (same single-element list written
again) — but the REPLACES-vs-MERGES behavior itself is still unconfirmed
by Brevo's docs, worth confirming with Brevo support before relying on it
for any future sender added to this list.

Uses Brevo's POST /v3/contacts with updateEnabled=true, which upserts:
creates the contact (already suppressed) if Brevo has never seen that
address, or updates the existing contact. One endpoint covers both cases,
matching how jobs/campaigns/brevo_contacts.py and
jobs/email_job/brevo_send.py already talk to Brevo (api-key header,
requests, no SDK).

Safe by default: dry run only reads the local snapshot file and prints
what it would do — it makes ZERO Brevo API calls. Only --live actually
calls Brevo.
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

_BREVO_CONTACTS_URL = "https://api.brevo.com/v3/contacts"
_EXPORT_DIR = os.path.expanduser("~/watson/data/exports")
_TIMEOUT = 15
_DELAY_SECONDS = 0.2  # same politeness delay as jobs/campaigns/dispatch.py's brevo send loop


def _latest_snapshot_path() -> str | None:
    candidates = sorted(glob.glob(os.path.join(_EXPORT_DIR, "kit_suppression_*.json")))
    return candidates[-1] if candidates else None


def load_snapshot(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    return data.get("suppressed", [])


def _headers() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": os.getenv("BREVO_API_KEY", ""),
    }


def apply_suppression(email: str) -> tuple[bool, str]:
    """Upsert the contact in Brevo: block DEFAULT_FROM_EMAIL (Watson's one
    sender) via smtpBlacklistSender (the field that actually affects
    Watson's real send path) and set emailBlacklisted=true (accurate
    status, no effect on transactional sends — see module docstring).
    Returns (ok, detail)."""
    resp = requests.post(
        _BREVO_CONTACTS_URL,
        json={
            "email": email,
            "updateEnabled": True,
            "smtpBlacklistSender": [DEFAULT_FROM_EMAIL],
            "emailBlacklisted": True,
        },
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    if resp.status_code in (200, 201, 204):
        return True, str(resp.status_code)
    return False, f"{resp.status_code}: {resp.text[:200]}"


def run(snapshot_path: str, live: bool = False) -> dict:
    suppressed = load_snapshot(snapshot_path)

    if not live:
        return {
            "live": False,
            "snapshot": snapshot_path,
            "would_suppress": len(suppressed),
            "sample": [s["email"] for s in suppressed[:10] if s.get("email")],
        }

    if not os.getenv("BREVO_API_KEY"):
        raise RuntimeError("BREVO_API_KEY not set in .env")

    applied, failed = [], []
    for sub in suppressed:
        email = sub.get("email")
        if not email:
            continue
        ok, detail = apply_suppression(email)
        (applied if ok else failed).append({"email": email, "detail": detail})
        time.sleep(_DELAY_SECONDS)

    return {
        "live": True,
        "snapshot": snapshot_path,
        "applied_count": len(applied),
        "failed_count": len(failed),
        "failed": failed,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Apply a Kit suppression snapshot to Brevo — blocks DEFAULT_FROM_EMAIL "
            "via smtpBlacklistSender and sets emailBlacklisted=true on each contact."
        )
    )
    parser.add_argument(
        "--snapshot", default=None,
        help="Path to a kit_suppression_*.json file. Defaults to the most recent one in data/exports/."
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Actually call Brevo's API. Without this flag, nothing is sent to Brevo."
    )
    args = parser.parse_args()

    snapshot_path = args.snapshot or _latest_snapshot_path()
    if not snapshot_path:
        raise SystemExit(
            "No kit_suppression_*.json snapshot found in data/exports/ — "
            "run kit_suppression_export.py --live first."
        )

    result = run(snapshot_path, live=args.live)

    if not result["live"]:
        print(f"[DRY RUN] Snapshot: {result['snapshot']}")
        print(
            f"[DRY RUN] Would block {DEFAULT_FROM_EMAIL} (smtpBlacklistSender) and set "
            f"emailBlacklisted=true for {result['would_suppress']} Brevo contacts."
        )
        if result["sample"]:
            print(f"[DRY RUN] Sample: {', '.join(result['sample'])}")
        print("[DRY RUN] No Brevo API calls made. Re-run with --live to apply.")
    else:
        print(f"Applied suppression to {result['applied_count']} contacts.")
        if result["failed_count"]:
            print(f"Failed: {result['failed_count']}")
            for f in result["failed"][:20]:
                print(f"  {f['email']}: {f['detail']}")
