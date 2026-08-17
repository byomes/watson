#!/usr/bin/env python3
"""
brevo_sync.py — Sync Brevo contacts (attributes + list membership) →
watson.db, so Comms Desk and other comms-prep work reads local data
instead of calling Brevo's API live on every request.

Same pattern as jobs/givebutter/sync.py (mirror an external system's data
locally rather than hitting it live, so comms-prep work can join against
other local tables without live-API cost) — but this job is READ-ONLY
against Brevo. It pulls contacts/attributes/list membership; it never
writes anything to Brevo. That's a hard structural difference from
jobs/migration/brevo_suppression_import.py and brevo_import.py, which DO
write to Brevo — don't conflate the two.

Two triggers, same underlying sync_once():
1. Scheduled — hourly cron:
   0 * * * * PYTHONPATH=/home/billyomes/watson \
     /home/billyomes/watson/venv/bin/python -m jobs.campaigns.brevo_sync \
     >> /home/billyomes/watson/logs/brevo_sync.log 2>&1
2. On-demand — jobs/comms/api.py's POST /api/comms/brevo/refresh route
   calls sync_once() directly for an immediate refresh before a send.

Mirrors 3 tables in watson.db (shape follows what Brevo's own API
returns — GET /v3/contacts includes attributes + listIds per contact in
one response, no need for kit_export.py's per-tag-subscription approach):

- brevo_contacts — one row per Brevo contact. The approved attribute set
  (ARC_READER, WRITING_ROOM_PARTNER, COMPANION_GUIDE_READER,
  DONOR/DONOR_SEGMENT, TWJ_LAUNCH_SIGNUP — see
  ~/watson/memory/kit_brevo_audit.md) as columns, plus FIRSTNAME/LASTNAME.
- brevo_lists — one row per Brevo list (id is Brevo's own list ID, not
  autoincremented — lists are natively unique there). Covers both the
  lead-magnet lists ("Lead Magnet: {slug}") and the 4 signup-source lists
  ("Signup Source: ..."), same as everything else Brevo returns from
  GET /v3/contacts/lists — this table doesn't special-case which list is
  which, it just mirrors whatever exists.
- brevo_list_membership — join table, (contact_id, list_id), mirroring
  each contact's listIds array directly.

last_synced_at on every row in all three tables (not just a single
per-run counter) — staleness is visible per-record, not silently global.

This is a full-refresh sync, not incremental: every run re-pulls every
contact and every list and upserts. A contact's list_membership rows are
replaced wholesale on each sync (delete-then-reinsert for that contact),
so a contact removed from a list in Brevo is correctly reflected as
removed locally too. Contacts/lists no longer present in Brevo at all are
NOT deleted from the local mirror by this job — out of scope here, and
last_synced_at makes that staleness visible rather than hiding it via
silent deletion.
"""
import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.database import get_connection

load_dotenv(os.path.expanduser("~/watson/.env"))

BASE_DIR = Path(__file__).resolve().parents[2]
LOG_PATH = BASE_DIR / "logs" / "brevo_sync.log"

_BASE = "https://api.brevo.com/v3"
_TIMEOUT = 15
_CONTACTS_PAGE_SIZE = 500  # Brevo's max page size for /contacts
_LISTS_PAGE_SIZE = 50      # Brevo's max page size for /contacts/lists


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
    )


log = logging.getLogger(__name__)


def _headers() -> dict:
    return {"accept": "application/json", "api-key": os.getenv("BREVO_API_KEY", "")}


# ── DB ────────────────────────────────────────────────────────────────────────

def init_db(conn=None) -> None:
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS brevo_contacts (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                brevo_contact_id        INTEGER NOT NULL UNIQUE,
                email                   TEXT    NOT NULL UNIQUE,
                first_name              TEXT,
                last_name               TEXT,
                arc_reader              INTEGER NOT NULL DEFAULT 0,
                writing_room_partner    INTEGER NOT NULL DEFAULT 0,
                companion_guide_reader  INTEGER NOT NULL DEFAULT 0,
                donor                   INTEGER NOT NULL DEFAULT 0,
                donor_segment           TEXT,
                twj_launch_signup       INTEGER NOT NULL DEFAULT 0,
                last_synced_at          TEXT    NOT NULL DEFAULT (datetime('now')),
                created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS brevo_lists (
                id              INTEGER PRIMARY KEY,
                name            TEXT NOT NULL,
                last_synced_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS brevo_list_membership (
                contact_id      INTEGER NOT NULL REFERENCES brevo_contacts(id),
                list_id         INTEGER NOT NULL REFERENCES brevo_lists(id),
                last_synced_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (contact_id, list_id)
            );
        """)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


# ── Brevo fetch ───────────────────────────────────────────────────────────────

def fetch_all_lists() -> list[dict]:
    """[{id, name}, ...] for every Brevo contact list, paginated."""
    out, offset = [], 0
    while True:
        resp = requests.get(
            f"{_BASE}/contacts/lists",
            params={"limit": _LISTS_PAGE_SIZE, "offset": offset},
            headers=_headers(), timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        lists = resp.json().get("lists", [])
        out.extend({"id": l["id"], "name": l["name"]} for l in lists)
        if len(lists) < _LISTS_PAGE_SIZE:
            break
        offset += _LISTS_PAGE_SIZE
    return out


def fetch_all_contacts() -> list[dict]:
    """[{brevo_contact_id, email, attributes, listIds}, ...] for every
    Brevo contact, paginated. GET /v3/contacts includes attributes and
    listIds per contact by default — no separate per-list query needed."""
    out, offset = [], 0
    while True:
        resp = requests.get(
            f"{_BASE}/contacts",
            params={"limit": _CONTACTS_PAGE_SIZE, "offset": offset},
            headers=_headers(), timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        contacts = resp.json().get("contacts", [])
        for c in contacts:
            out.append({
                "brevo_contact_id": c["id"],
                "email": c["email"],
                "attributes": c.get("attributes") or {},
                "listIds": c.get("listIds") or [],
            })
        if len(contacts) < _CONTACTS_PAGE_SIZE:
            break
        offset += _CONTACTS_PAGE_SIZE
    return out


# ── Upsert ────────────────────────────────────────────────────────────────────

def _upsert_list(conn, list_row: dict) -> None:
    conn.execute(
        """INSERT INTO brevo_lists (id, name, last_synced_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(id) DO UPDATE SET name = excluded.name, last_synced_at = excluded.last_synced_at""",
        (list_row["id"], list_row["name"]),
    )


def _upsert_contact(conn, contact: dict) -> int:
    attrs = contact["attributes"]
    row = (
        contact["brevo_contact_id"],
        contact["email"],
        attrs.get("FIRSTNAME"),
        attrs.get("LASTNAME"),
        bool(attrs.get("ARC_READER")),
        bool(attrs.get("WRITING_ROOM_PARTNER")),
        bool(attrs.get("COMPANION_GUIDE_READER")),
        bool(attrs.get("DONOR")),
        attrs.get("DONOR_SEGMENT"),
        bool(attrs.get("TWJ_LAUNCH_SIGNUP")),
    )
    conn.execute(
        """INSERT INTO brevo_contacts
               (brevo_contact_id, email, first_name, last_name, arc_reader,
                writing_room_partner, companion_guide_reader, donor,
                donor_segment, twj_launch_signup, last_synced_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(brevo_contact_id) DO UPDATE SET
               email = excluded.email, first_name = excluded.first_name,
               last_name = excluded.last_name, arc_reader = excluded.arc_reader,
               writing_room_partner = excluded.writing_room_partner,
               companion_guide_reader = excluded.companion_guide_reader,
               donor = excluded.donor, donor_segment = excluded.donor_segment,
               twj_launch_signup = excluded.twj_launch_signup,
               last_synced_at = excluded.last_synced_at""",
        row,
    )
    return conn.execute(
        "SELECT id FROM brevo_contacts WHERE brevo_contact_id = ?",
        (contact["brevo_contact_id"],),
    ).fetchone()["id"]


def _sync_list_membership(conn, contact_id: int, list_ids: list[int]) -> None:
    conn.execute("DELETE FROM brevo_list_membership WHERE contact_id = ?", (contact_id,))
    for list_id in list_ids:
        conn.execute(
            """INSERT INTO brevo_list_membership (contact_id, list_id, last_synced_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(contact_id, list_id) DO UPDATE SET last_synced_at = excluded.last_synced_at""",
            (contact_id, list_id),
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def sync_once() -> dict:
    """Full pull + upsert. Returns stats. Safe to call directly (on-demand
    refresh) or via __main__ (cron)."""
    conn = get_connection()
    init_db(conn)

    lists = fetch_all_lists()
    for list_row in lists:
        _upsert_list(conn, list_row)

    contacts = fetch_all_contacts()
    for contact in contacts:
        contact_id = _upsert_contact(conn, contact)
        _sync_list_membership(conn, contact_id, contact["listIds"])

    conn.commit()
    conn.close()

    return {"lists_synced": len(lists), "contacts_synced": len(contacts)}


if __name__ == "__main__":
    _setup_logging()
    log.info("Brevo sync starting…")
    try:
        stats = sync_once()
        log.info("Brevo sync complete: %d lists, %d contacts.", stats["lists_synced"], stats["contacts_synced"])
    except Exception:
        log.exception("Brevo sync failed.")
        raise
