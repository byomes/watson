"""jobs/congregation/fluro_client.py -- live client for Subsplash's Fluro
church-management API (api.fluro.io), authenticated by riding the real
Chrome session already signed into fluro.subsplash.com on Watson's
dedicated Android gateway phone (the same device jobs/sms/gateway_client.py
talks to -- see that module's docstring for the phone's Tailscale hostname/
role). This is NOT a headless-browser scrape and NOT a stored API key --
the phone is a real device with Chrome signed in via watson.wcky@gmail.com
and Fluro credentials saved in Chrome's own password manager (set up
2026-09-26, see project_subsplash_fluro_admin_pull memory). That real-device
session is what gets past whatever blocked the old headless-login attempt.

How it works:
  1. `adb connect <phone>:5555` (adb binary vendored at ~/watson/bin/adb,
     no system install / apt / sudo needed -- see that binary's own
     provenance: official Google platform-tools, downloaded once).
  2. `adb forward tcp:<port> localabstract:chrome_devtools_remote` exposes
     Chrome's DevTools Protocol (already available because the phone has
     USB/wireless debugging enabled for Watson's dev access -- that's the
     SAME channel, not a second debug surface).
  3. Find (or open) a tab on fluro.subsplash.com, then use
     `Runtime.evaluate` to read `localStorage['_fluro_user']` from that
     page's own JS context -- this returns the live session token Fluro's
     own SPA is already using, so the job never needs its own stored
     credential and can't go stale independently of the real login.
  4. From there on, plain `requests` calls straight to api.fluro.io (NOT
     routed through the browser) -- confirmed live 2026-09-26 that
     api.fluro.io's `/content/<type>/filter` + `/content/<type>/multiple`
     pair accepts this bearer token directly with no additional
     browser-only cookie/CSRF requirement, and that `filter` does not
     truncate at its default page size -- passing a `limit` >= the real
     row count (691 contacts, confirmed) returns everything in one call,
     and `multiple` accepts the full id list in one POST (691 ids, ~4s).
     No pagination loop needed for a church this size; if the roster ever
     grows large enough for this to become a real payload/timeout concern,
     chunk `multiple`'s ids client-side -- the API itself imposed no limit
     during testing.

Never call the write-side of this API (anything but GET/filter/multiple)
from an automated job without a fresh, explicit decision from Bill --
this client only ever reads. See fluro_pull.py's docstring for why.
"""
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import requests
import websockets
import asyncio
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

log = logging.getLogger(__name__)

ADB_BIN = str(Path.home() / "watson" / "bin" / "adb")
CDP_LOCAL_PORT = 9223  # deliberately not 9222 -- avoid colliding with a human's own manual debug session on the same box
FLURO_API_VERSION = "2.2.30"
API_BASE = "https://api.fluro.io"
FLURO_ORIGIN = "https://fluro.subsplash.com"


class FluroClientError(RuntimeError):
    pass


def _phone_host() -> str:
    host = os.getenv("FLURO_PHONE_ADB_HOST", "").strip()
    if not host:
        raise FluroClientError(
            "FLURO_PHONE_ADB_HOST not set in .env (expected '<tailscale-ip>:5555')"
        )
    return host


def _adb(*args: str, timeout: int = 20) -> str:
    result = subprocess.run(
        [ADB_BIN, *args], capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0 and "already connected" not in (result.stdout + result.stderr):
        raise FluroClientError(f"adb {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def ensure_phone_connected() -> None:
    host = _phone_host()
    _adb("connect", host)
    _adb("-s", host, "forward", f"tcp:{CDP_LOCAL_PORT}", "localabstract:chrome_devtools_remote")


def _list_cdp_targets() -> list[dict]:
    resp = requests.get(f"http://localhost:{CDP_LOCAL_PORT}/json", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _find_or_open_fluro_tab() -> str:
    """Returns a page-target websocket debugger URL for a tab already on
    fluro.subsplash.com, opening one if none exists. Never navigates an
    EXISTING tab away from whatever it's showing -- Bill or Donna may be
    looking at it."""
    for target in _list_cdp_targets():
        if target.get("type") == "page" and target.get("url", "").startswith(FLURO_ORIGIN):
            return target["webSocketDebuggerUrl"]

    version_resp = requests.get(f"http://localhost:{CDP_LOCAL_PORT}/json/version", timeout=10)
    version_resp.raise_for_status()
    browser_ws = version_resp.json()["webSocketDebuggerUrl"]

    async def _open() -> str:
        async with websockets.connect(browser_ws, max_size=50_000_000) as ws:
            await ws.send(json.dumps({
                "id": 1, "method": "Target.createTarget",
                "params": {"url": f"{FLURO_ORIGIN}/people/contacts"},
            }))
            while True:
                raw = await ws.recv()
                data = json.loads(raw)
                if data.get("id") == 1:
                    return data["result"]["targetId"]

    target_id = asyncio.run(_open())
    time.sleep(4)  # let the SPA finish its initial load before reading localStorage
    for target in _list_cdp_targets():
        if target.get("id") == target_id:
            return target["webSocketDebuggerUrl"]
    raise FluroClientError("opened a Fluro tab but couldn't find its debugger target")


def get_session_token() -> dict:
    """Returns the live _fluro_user object (token/refreshToken/expires/...)
    read straight out of the real Chrome session's localStorage. Never
    cached to disk -- fetched fresh every call so a re-login on the phone
    is picked up automatically next run with no code change."""
    ensure_phone_connected()
    tab_ws = _find_or_open_fluro_tab()

    async def _read() -> dict:
        async with websockets.connect(tab_ws, max_size=50_000_000) as ws:
            await ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": "localStorage.getItem('_fluro_user')", "returnByValue": True},
            }))
            while True:
                raw = await ws.recv()
                data = json.loads(raw)
                if data.get("id") == 1:
                    value = data["result"]["result"].get("value")
                    if not value:
                        raise FluroClientError(
                            "no _fluro_user in localStorage -- the phone's Chrome session isn't logged into Fluro"
                        )
                    return json.loads(value)

    return asyncio.run(_read())


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json",
        "fluro-api-version": FLURO_API_VERSION,
        "fluro-request-timezone": "America/New_York",
        "fluro-request-date": str(int(time.time() * 1000)),
    }


def filter_content(token: str, content_type: str, filter_body: dict) -> list[str]:
    """POST /content/<type>/filter -- returns matching ids only (not full
    records). Confirmed live 2026-09-26: a limit >= the real row count
    returns everything in one call, no skip/offset pagination needed at
    this church's roster size."""
    resp = requests.post(
        f"{API_BASE}/content/{content_type}/filter",
        headers=_headers(token), json=filter_body, timeout=30,
    )
    resp.raise_for_status()
    return [row["_id"] for row in resp.json()]


def multiple_content(token: str, content_type: str, ids: list[str]) -> list[dict]:
    """POST /content/<type>/multiple -- full records for a list of ids.
    Confirmed live: 691 ids in one call, ~4s, no server-side rejection.
    Chunk client-side (e.g. 250 ids/call) if a future roster size makes
    that stop being true."""
    if not ids:
        return []
    resp = requests.post(
        f"{API_BASE}/content/{content_type}/multiple",
        headers=_headers(token), json={"ids": ids, "allDefinitions": True}, timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_all(token: str, content_type: str, extra_filters: list[dict] | None = None,
              sort_key: str = "updated", sort_direction: str = "desc",
              sort_type: str = "date") -> list[dict]:
    """Generic active+draft pull for any Fluro content type -- filter then
    multiple, both confirmed unpaginated at this church's data size."""
    filters = [{"key": "status", "comparator": "in", "values": ["active", "draft"]}]
    if extra_filters:
        filters.extend(extra_filters)
    body = {
        "sort": {"sortKey": sort_key, "sortDirection": sort_direction, "sortType": sort_type},
        "filter": {"operator": "and", "filters": [{"operator": "and", "filters": filters}]},
        "search": "",
        "includeArchived": False,
        "allDefinitions": True,
        "searchInheritable": False,
        "includeUnmatched": True,
        "limit": 5000,
        "timezone": "America/New_York",
    }
    ids = filter_content(token, content_type, body)
    return multiple_content(token, content_type, ids)


def fetch_all_contacts(token: str) -> list[dict]:
    return fetch_all(token, "contact", sort_key="lastName", sort_direction="asc", sort_type="string")


def fetch_all_service_teams(token: str) -> list[dict]:
    return fetch_all(token, "serviceTeam", sort_key="updated", sort_direction="desc", sort_type="date")
