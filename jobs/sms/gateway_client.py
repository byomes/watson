"""jobs/sms/gateway_client.py — thin client for the android-sms-gateway
(capcom6/android-sms-gateway, self-hosted local-server mode, port 8080)
running on Watson's dedicated Android phone.

GATEWAY_MODE controls which backend is used:
  - 'mock' (default): no real HTTP calls. fetch_inbound() drains a small
    JSON queue file that POST /api/sms/mock/inject (jobs/sms/api.py) writes
    to, so the whole pipeline can be exercised end to end before the phone
    exists. send_message()/get_vitals() just log and return canned success.
  - 'live': calls the real gateway over Tailscale. Endpoints/auth below were
    confirmed 2026-09-26 against the app's published OpenAPI spec
    (capcom6.github.io/android-sms-gateway/swagger.json) ahead of the real
    phone arriving: local-server mode uses HTTP Basic auth (not Bearer) and
    the send/receive paths are /messages and /inbox (not /message and
    /message/inbox). The refresh-then-poll timing for /inbox and the exact
    response shape still need confirming against the actual device.

    Note: this API has no battery/vitals field at all (Device schema is just
    id/name/simCards/timestamps) — get_vitals() below can only report
    reachability, not battery. Battery-level reporting for the SMS app's
    indicator has to come from Headwind MDM's device status API instead,
    once a device is enrolled there.
"""
import json
import logging
import os
import sqlite3
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

log = logging.getLogger(__name__)

_MOCK_QUEUE_PATH = Path(__file__).resolve().parents[2] / "data" / "sms_mock_inbound_queue.json"
_LAST_POLL_PATH = Path(__file__).resolve().parents[2] / "data" / "sms_gateway_last_poll.txt"
_ACTIVE_HOST_PATH = Path(__file__).resolve().parents[2] / "data" / "sms_gateway_active_host.json"

# jobs/network_monitor labels the phone's device row with this exact string
# (set 2026-09-28) so its current home-LAN IP can be looked up here.
_LAN_GATEWAY_LABEL = "SMS Gateway Phone"
_LAN_GATEWAY_PORT = 8080


def _gateway_mode() -> str:
    return os.getenv("GATEWAY_MODE", "mock").strip().lower()


def _gateway_url() -> str:
    return os.getenv("SMS_GATEWAY_URL", "").rstrip("/")


def _gateway_auth() -> HTTPBasicAuth | None:
    """Local-server mode uses HTTP Basic auth with the username/password
    shown on the phone's gateway app screen — not a Bearer token."""
    user = os.getenv("SMS_GATEWAY_USER", "")
    password = os.getenv("SMS_GATEWAY_PASS", "")
    if not user or not password:
        return None
    return HTTPBasicAuth(user, password)


def _lan_fallback_url() -> str | None:
    """Looks up the gateway phone's current home-LAN IP from
    jobs/network_monitor's device table. The phone can be fully healthy on
    wifi while only its Tailscale connection has dropped (confirmed
    2026-09-28: an overnight "unreachable" outage turned out to be exactly
    this), so a same-network retry is worth it before treating the gateway
    as actually down."""
    from config.settings import DB_PATH

    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT ip FROM network_devices WHERE label = ? AND ip IS NOT NULL "
            "ORDER BY last_seen DESC LIMIT 1",
            (_LAN_GATEWAY_LABEL,),
        ).fetchone()
        conn.close()
    except sqlite3.Error as exc:
        log.warning("gateway_client: LAN fallback lookup failed: %s", exc)
        return None
    if not row or not row[0]:
        return None
    return f"http://{row[0]}:{_LAN_GATEWAY_PORT}"


def _read_cached_host() -> str | None:
    if not _ACTIVE_HOST_PATH.exists():
        return None
    try:
        return json.loads(_ACTIVE_HOST_PATH.read_text()).get("base_url")
    except (json.JSONDecodeError, OSError):
        return None


def _write_cached_host(base_url: str) -> None:
    _ACTIVE_HOST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_HOST_PATH.write_text(json.dumps({"base_url": base_url}))


def _candidate_urls() -> list[str]:
    """Whichever host last worked goes first (so a live outage doesn't pay
    Tailscale's connect-timeout on every single call), then the configured
    Tailscale URL, then the LAN fallback — de-duplicated, order preserved."""
    ordered = [_read_cached_host(), _gateway_url(), _lan_fallback_url()]
    seen: set[str] = set()
    candidates = []
    for url in ordered:
        if url and url not in seen:
            seen.add(url)
            candidates.append(url)
    return candidates


def _request(method: str, path: str, **kwargs) -> requests.Response:
    """Tries each candidate gateway host in turn, falling back from
    Tailscale to the phone's current LAN IP on a connection-level failure
    (timeout, refused, DNS) — not on an HTTP error from a host that *did*
    answer, since that's a real API error, not a reachability problem.
    Raises the last connection error if every candidate fails."""
    last_exc: Exception | None = None
    for base in _candidate_urls():
        try:
            resp = requests.request(method, f"{base}{path}", **kwargs)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            log.warning("gateway_client: %s unreachable (%s)", base, exc)
            continue
        _write_cached_host(base)
        return resp
    raise last_exc or RuntimeError("no gateway URL configured")


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


def _read_last_poll() -> str | None:
    if not _LAST_POLL_PATH.exists():
        return None
    return _LAST_POLL_PATH.read_text().strip() or None


def _write_last_poll(iso_ts: str) -> None:
    _LAST_POLL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LAST_POLL_PATH.write_text(iso_ts)


def fetch_inbound() -> list[dict]:
    """Returns a list of {"phone": str, "text": str, "name": str|None,
    "gateway_message_id": str|None} for messages not yet ingested.

    Confirmed endpoint (2026-09-26, from the app's OpenAPI spec): GET /inbox,
    HTTP Basic auth, query param `from` (RFC3339 timestamp) to bound the
    window — this is a live query against the device's SMS content
    provider, not a drain/queue, so Watson tracks its own last-poll
    timestamp in data/sms_gateway_last_poll.txt to avoid re-ingesting the
    same messages every cron tick. POST /inbox/refresh exists to nudge the
    device to re-scan first; calling it is best-effort since local-server
    mode may already reflect the live inbox without it — NOT YET VERIFIED
    against real hardware."""
    if _gateway_mode() == "mock":
        return _mock_queue_read_and_clear()

    url = _gateway_url()
    auth = _gateway_auth()
    if not url or not auth:
        log.error("fetch_inbound: GATEWAY_MODE=live but SMS_GATEWAY_URL/SMS_GATEWAY_USER/SMS_GATEWAY_PASS not set")
        return []

    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    since = _read_last_poll()

    try:
        _request("POST", "/inbox/refresh", auth=auth, json={}, timeout=10)
    except Exception as exc:
        log.warning("fetch_inbound: /inbox/refresh call failed (continuing): %s", exc)

    try:
        # No `type` filter -- confirmed 2026-09-26 that real replies from
        # some phones (Emily's, Micah's) arrive as MMS_DOWNLOADED rather
        # than SMS (RCS/chat-features fallback), and an earlier type=SMS
        # filter here silently dropped every one of them. contentPreview
        # is populated the same way for both types, so no other change
        # is needed to ingest them correctly.
        params = {}
        if since:
            params["from"] = since
        resp = _request("GET", "/inbox", auth=auth, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("fetch_inbound: live gateway request failed: %s", exc)
        return []

    _write_last_poll(now_iso)

    return [
        {
            "phone": m.get("sender", ""),
            "text": m.get("contentPreview", ""),
            "name": None,
            "gateway_message_id": str(m.get("id")) if m.get("id") is not None else None,
        }
        for m in (data if isinstance(data, list) else [])
    ]


def _as_phone_list(phones: str | list[str]) -> list[str]:
    return phones if isinstance(phones, list) else [phones]


def send_message(phones: str | list[str], body: str) -> dict:
    """Returns {"success": bool, "gateway_message_id": str|None, "error": str|None}.

    `phones` a single string sends a normal 1:1 SMS, unchanged. A list of
    more than one number is accepted for API-shape consistency but plain
    SMS has no multi-recipient concept at the carrier level -- see
    send_mms below, which is what a real group text needs."""
    phone_list = _as_phone_list(phones)
    if _gateway_mode() == "mock":
        log.info("gateway_client (mock): send_message to %s: %s", phone_list, body)
        return {"success": True, "gateway_message_id": None, "error": None}

    url = _gateway_url()
    auth = _gateway_auth()
    if not url or not auth:
        return {"success": False, "gateway_message_id": None, "error": "gateway not configured"}

    try:
        resp = _request(
            "POST",
            "/messages",
            auth=auth,
            json={"phoneNumbers": phone_list, "textMessage": {"text": body}},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"success": True, "gateway_message_id": str(data.get("id", "")), "error": None}
    except Exception as exc:
        log.error("send_message: live gateway send failed for %s: %s", phone_list, exc)
        return {"success": False, "gateway_message_id": None, "error": str(exc)}


def send_mms(phones: str | list[str], body: str, media_path: str | None = None, media_type: str | None = None) -> dict:
    """Returns {"success": bool, "gateway_message_id": str|None, "error": str|None}.

    Confirmed endpoint/shape (2026-09-26, from the app's OpenAPI spec):
    POST /messages with an `mmsMessage` object ({text, attachments: [{data,
    contentType}]}) — attachments use `contentType`, not `mimeType`. The
    spec notes MMS "requires the app to be the default SMS app for reliable
    delivery on most carriers" — confirmed working end-to-end 2026-09-28
    against real hardware, including a real image attachment.

    `media_path`/`media_type` are optional -- omit both for a text-only
    send. This matters for group threads (jobs/sms/send_core.py): a plain
    SMS is inherently point-to-point and can't carry more than one
    recipient in its own PDU, so *any* group-thread send -- even a plain
    text reply with no photo -- goes out as a (possibly attachment-less)
    MMS instead, addressed to every participant in one `phoneNumbers`
    list, so it actually arrives as one shared group conversation on their
    phones (confirmed 2026-09-28: sending each participant a separate 1:1
    SMS, which the fan-out loop this replaced was doing, delivers to each
    of them individually with no group envelope at all -- that's the bug
    this fixes)."""
    phone_list = _as_phone_list(phones)
    if _gateway_mode() == "mock":
        log.info("gateway_client (mock): send_mms to %s: %s (media=%s)", phone_list, body, media_path)
        return {"success": True, "gateway_message_id": None, "error": None}

    url = _gateway_url()
    auth = _gateway_auth()
    if not url or not auth:
        return {"success": False, "gateway_message_id": None, "error": "gateway not configured"}

    attachments = []
    if media_path and media_type:
        import base64

        with open(media_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        attachments = [{"data": encoded, "contentType": media_type}]

    try:
        resp = _request(
            "POST",
            "/messages",
            auth=auth,
            json={
                "phoneNumbers": phone_list,
                "mmsMessage": {"text": body, "attachments": attachments},
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"success": True, "gateway_message_id": str(data.get("id", "")), "error": None}
    except Exception as exc:
        log.error("send_mms: live gateway send failed for %s: %s", phone_list, exc)
        return {"success": False, "gateway_message_id": None, "error": str(exc)}


def get_vitals() -> dict:
    """Returns {"ok": bool, "battery_pct": int|None, "detail": str}.

    Confirmed 2026-09-26 against real hardware: GET /health (no auth
    required) returns a dynamic `checks` map that DOES include
    `battery:level` (observedValue, percent) and `battery:charging` among
    other checks — the app's static OpenAPI schema doesn't enumerate these
    since HealthChecks is an open map, which is why an earlier read of the
    schema alone missed this. Overall `status` is "pass"/"warn"/"fail"."""
    if _gateway_mode() == "mock":
        return {"ok": True, "battery_pct": 100, "detail": "mock gateway — always healthy"}

    if not _gateway_url() and not _lan_fallback_url():
        return {"ok": False, "battery_pct": None, "detail": "gateway not configured"}

    try:
        resp = _request("GET", "/health", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        checks = data.get("checks", {})
        battery = checks.get("battery:level", {})
        # battery:charging observedValue is 0 unplugged, non-zero (5 seen
        # on USB power, 2026-10-06) while charging.
        charging = checks.get("battery:charging", {}).get("observedValue")
        return {
            "ok": data.get("status") == "pass",
            "battery_pct": battery.get("observedValue"),
            "charging": bool(charging) if charging is not None else None,
            "detail": data.get("status", "unknown"),
        }
    except Exception as exc:
        return {"ok": False, "battery_pct": None, "detail": str(exc)}
