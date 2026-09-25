"""jobs/sms/gateway_client.py — thin client for the android-sms-gateway
(capcom6/android-sms-gateway, self-hosted local-server mode) running on
Watson's dedicated Android phone.

GATEWAY_MODE controls which backend is used:
  - 'mock' (default): no real HTTP calls. fetch_inbound() drains a small
    JSON queue file that POST /api/sms/mock/inject (jobs/sms/api.py) writes
    to, so the whole pipeline can be exercised end to end before the phone
    exists. send_message()/get_vitals() just log and return canned success.
  - 'live': calls the real gateway over Tailscale. NOT YET VERIFIED against
    real hardware (no phone purchased as of 2026-09-25) — the request
    shapes below follow android-sms-gateway's documented REST API, but
    confirm against the actual device once it's set up, before relying on
    this in production.
"""
import json
import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

log = logging.getLogger(__name__)

_MOCK_QUEUE_PATH = Path(__file__).resolve().parents[2] / "data" / "sms_mock_inbound_queue.json"


def _gateway_mode() -> str:
    return os.getenv("GATEWAY_MODE", "mock").strip().lower()


def _gateway_url() -> str:
    return os.getenv("SMS_GATEWAY_URL", "").rstrip("/")


def _gateway_token() -> str:
    return os.getenv("SMS_GATEWAY_TOKEN", "")


def _mock_queue_read_and_clear() -> list[dict]:
    if not _MOCK_QUEUE_PATH.exists():
        return []
    try:
        items = json.loads(_MOCK_QUEUE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    _MOCK_QUEUE_PATH.write_text("[]")
    return items if isinstance(items, list) else []


def mock_queue_push(phone: str, text: str, name: str | None = None) -> None:
    """Used only by POST /api/sms/mock/inject (jobs/sms/api.py) — appends a
    fake inbound message that the next fetch_inbound() call will pick up."""
    _MOCK_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    items = []
    if _MOCK_QUEUE_PATH.exists():
        try:
            items = json.loads(_MOCK_QUEUE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            items = []
    items.append({"phone": phone, "text": text, "name": name})
    _MOCK_QUEUE_PATH.write_text(json.dumps(items))


def fetch_inbound() -> list[dict]:
    """Returns a list of {"phone": str, "text": str, "name": str|None,
    "gateway_message_id": str|None} for messages not yet ingested. The
    gateway itself is the source of truth for "not yet ingested" — this is
    a drain, not a re-readable log, matching android-sms-gateway's own
    inbox-consumption model."""
    if _gateway_mode() == "mock":
        return _mock_queue_read_and_clear()

    url = _gateway_url()
    token = _gateway_token()
    if not url or not token:
        log.error("fetch_inbound: GATEWAY_MODE=live but SMS_GATEWAY_URL/SMS_GATEWAY_TOKEN not set")
        return []

    try:
        # android-sms-gateway exposes received messages via GET /message/inbox
        # (or a webhook push, depending on install mode) — poll-mode assumed
        # here since Watson's job architecture is cron-driven, not
        # webhook-receiving. Verify this path against the real device.
        resp = requests.get(
            f"{url}/message/inbox",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("fetch_inbound: live gateway request failed: %s", exc)
        return []

    return [
        {
            "phone": m.get("phoneNumber") or m.get("phone_number") or m.get("phone", ""),
            "text": m.get("message") or m.get("text", ""),
            "name": None,
            "gateway_message_id": str(m.get("id")) if m.get("id") is not None else None,
        }
        for m in (data if isinstance(data, list) else data.get("messages", []))
    ]


def send_message(phone: str, body: str) -> dict:
    """Returns {"success": bool, "gateway_message_id": str|None, "error": str|None}."""
    if _gateway_mode() == "mock":
        log.info("gateway_client (mock): send_message to %s: %s", phone, body)
        return {"success": True, "gateway_message_id": None, "error": None}

    url = _gateway_url()
    token = _gateway_token()
    if not url or not token:
        return {"success": False, "gateway_message_id": None, "error": "gateway not configured"}

    try:
        resp = requests.post(
            f"{url}/message",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"phoneNumbers": [phone], "message": body},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"success": True, "gateway_message_id": str(data.get("id", "")), "error": None}
    except Exception as exc:
        log.error("send_message: live gateway send failed for %s: %s", phone, exc)
        return {"success": False, "gateway_message_id": None, "error": str(exc)}


def get_vitals() -> dict:
    """Returns {"ok": bool, "battery_pct": int|None, "detail": str}."""
    if _gateway_mode() == "mock":
        return {"ok": True, "battery_pct": 100, "detail": "mock gateway — always healthy"}

    url = _gateway_url()
    token = _gateway_token()
    if not url or not token:
        return {"ok": False, "battery_pct": None, "detail": "gateway not configured"}

    try:
        # android-sms-gateway's /health or /device endpoint (name TBD against
        # the real device) — assumed shape, verify once hardware exists.
        resp = requests.get(
            f"{url}/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "ok": True,
            "battery_pct": data.get("battery") or data.get("battery_pct"),
            "detail": "reachable",
        }
    except Exception as exc:
        return {"ok": False, "battery_pct": None, "detail": str(exc)}
