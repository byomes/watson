"""jobs/twj/api.py — Flask Blueprint for The Wrong Jesus launch-page email
signup (wcky /thewrongjesus -> Watson -> Brevo).

Closes the last remaining Kit -> Brevo migration architecture violation
(Phase 1 touchpoint #13, ~/watson/memory/kit_brevo_audit.md): wcky's
/api/thewrongjesus/signup used to call Kit v4 directly from Vercel — the
only signup form on the site that didn't route through Watson first,
unlike ARC (jobs/arc/api.py), lead magnets (jobs/lead_magnet/api.py), and
Writing Room, which all call a Watson route first. This brings it in
line with the rest.

Writes directly to Brevo, not Kit — POST /v3/contacts with
updateEnabled=true, the same upsert shape jobs/migration/brevo_import.py
already validated: sets TWJ_LAUNCH_SIGNUP=true and adds the contact to
the "Signup Source: TWJ Launch Page" Brevo list (id 5, folder id 1 —
confirmed live via the jobs/campaigns/brevo_sync.py local mirror after
Bill's real Phase 3 import, 2026-08-17: `SELECT id FROM brevo_lists WHERE
name = 'Signup Source: TWJ Launch Page'` -> 5. Not guessed.)

Deliberately minimal, matching only what Phase 4 asked for: no local DB
record, no confirmation email, no Telegram notification — unlike
ARC/lead-magnet, which do all three. New signups are visible in Brevo
directly (contact list + the list above); flagged as a follow-up if Bill
wants notification/visibility beyond that, not built here.
"""
import os
import sys
from functools import wraps
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

twj_bp = Blueprint("twj", __name__)

_API_KEY = lambda: os.getenv("WRITING_ROOM_API_KEY", "")
_BREVO_CONTACTS_URL = "https://api.brevo.com/v3/contacts"
_TIMEOUT = 15

_TWJ_LIST_ID = 5  # "Signup Source: TWJ Launch Page" — see module docstring


def _require_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Watson-Key") != _API_KEY() or not _API_KEY():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _headers() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": os.getenv("BREVO_API_KEY", ""),
    }


@twj_bp.route("/api/twj/signup", methods=["POST"])
@_require_key
def twj_signup():
    data = request.get_json(force=True) or {}
    first_name = (data.get("firstName") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"error": "Email is required."}), 400

    if not os.getenv("BREVO_API_KEY"):
        return jsonify({"error": "server configuration error"}), 500

    attributes = {"TWJ_LAUNCH_SIGNUP": True}
    if first_name:
        attributes["FIRSTNAME"] = first_name

    resp = requests.post(
        _BREVO_CONTACTS_URL,
        json={
            "email": email,
            "updateEnabled": True,
            "attributes": attributes,
            "listIds": [_TWJ_LIST_ID],
        },
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    if resp.status_code not in (200, 201, 204):
        return jsonify({"error": "Brevo API error"}), 502

    return jsonify({"ok": True}), 200
