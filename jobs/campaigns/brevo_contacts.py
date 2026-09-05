"""jobs/campaigns/brevo_contacts.py — Read-only Brevo contacts/lists client.

Used by Comms Desk's email composer (via jobs/comms/api.py) to let Kaci pick a
live Brevo list or specific individual contacts as a send's audience, and by
dispatch.py at send time to resolve a 'brevo_list' recipient_mode row into
actual recipients. Never sends anything — jobs/email_job/brevo_send.py owns
that.
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/watson/.env"))

_BASE = "https://api.brevo.com/v3"
_TIMEOUT = 15
_CONTACTS_PAGE_SIZE = 500  # Brevo's max page size for /contacts and /contacts/lists/{id}/contacts
_LISTS_PAGE_SIZE = 50      # Brevo's max page size for /contacts/lists — a separate, smaller cap


def _headers():
    return {"accept": "application/json", "api-key": os.getenv("BREVO_API_KEY", "")}


def _contact_name(attrs: dict) -> str:
    first = (attrs.get("FIRSTNAME") or "").strip()
    last = (attrs.get("LASTNAME") or "").strip()
    return f"{first} {last}".strip()


def list_lists() -> list[dict]:
    """[{id, name, count}, ...] for every Brevo contact list, newest-imported
    first isn't guaranteed by the API — sorted by name here for a stable,
    predictable dropdown order."""
    if not os.getenv("BREVO_API_KEY"):
        return []
    out, offset = [], 0
    while True:
        resp = requests.get(
            f"{_BASE}/contacts/lists",
            params={"limit": _LISTS_PAGE_SIZE, "offset": offset, "sort": "desc"},
            headers=_headers(), timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        lists = data.get("lists", [])
        out.extend({"id": l["id"], "name": l["name"], "count": l.get("totalSubscribers", 0)} for l in lists)
        if len(lists) < _LISTS_PAGE_SIZE:
            break
        offset += _LISTS_PAGE_SIZE
    out.sort(key=lambda l: l["name"].lower())
    return out


def list_contacts(list_id: int | None = None) -> list[dict]:
    """[{email, name}, ...] — every contact in one Brevo list, or every
    contact in the account when list_id is None (the "choose specific
    people" picker's full roster)."""
    if not os.getenv("BREVO_API_KEY"):
        return []
    path = f"/contacts/lists/{list_id}/contacts" if list_id is not None else "/contacts"
    out, offset = [], 0
    while True:
        resp = requests.get(
            f"{_BASE}{path}",
            params={"limit": _CONTACTS_PAGE_SIZE, "offset": offset},
            headers=_headers(), timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        contacts = data.get("contacts", [])
        out.extend(
            {"email": c["email"], "name": _contact_name(c.get("attributes", {}))}
            for c in contacts
        )
        if len(contacts) < _CONTACTS_PAGE_SIZE:
            break
        offset += _CONTACTS_PAGE_SIZE
    return out
