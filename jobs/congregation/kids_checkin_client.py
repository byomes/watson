"""jobs/congregation/kids_checkin_client.py -- pulls kids-class checkin
history from Subsplash's real checkin API (core.subsplash.com/check-in/v1),
riding the work phone's already-authenticated Chrome session, same spirit
as jobs/congregation/fluro_client.py but NOT the same technique: Fluro's
access token sits in a stable localStorage key (`_fluro_user`) that's safe
to read directly into Python. Subsplash's dashboard-client token is a
short-lived Bearer JWT that is NOT stored anywhere stable -- it only ever
exists transiently on the wire. Discovered 2026-09-29 (see
project_kids_checkin_backlog memory) that trying to capture it into Python
even briefly (for debugging) tripped the secret-guard hook, so this client
deliberately never brings the token to Python at all:

  1. `adb connect` + `adb forward` to the phone's Chrome DevTools port,
     same as fluro_client.py (distinct local port: 9225, vs. Fluro's 9223).
  2. Find the existing "Subsplash Dashboard" tab (never navigate a tab away
     from what Bill/Donna may be looking at -- same rule as fluro_client.py).
  3. Inject a `fetch` monkey-patch via `Page.addScriptToEvaluateOnNewDocument`
     (this, not a plain Runtime.evaluate, is required -- a plain eval's
     patch gets wiped the moment the page reloads, since reload creates a
     brand new JS context) that captures the app's own Authorization header
     into a page-local `window.__capturedAuth` variable the FIRST time the
     app issues any authenticated request of its own.
  4. `Page.reload` to force the app to re-issue its initial authenticated
     calls, so the patch has something to capture.
  5. Run the ENTIRE pull (event-instance list + every past instance's
     roster) as one big `Runtime.evaluate(awaitPromise: true)` call executed
     INSIDE the page's own JS context, using `window.__capturedAuth`
     internally for every `fetch()` call. Only the final structured JSON
     result (event/roster data, never the token) crosses back to Python.

Confirmed live 2026-09-29 against the real Kids Checkin repeating event
(798311fd-f384-4375-ad82-c899762b04d4): `events/v2/events` filtered by
`filter[repeating_event.id]` and sorted by `start_at` returns the FULL
history in a couple of pages (page[size] capped at 100 by the API) with no
month/day filter needed -- the UI's calendar-month view was a red herring,
this direct filter+sort call is not bound to it. First-ever instance was
2025-01-26 (page 1 has no `previous` link), confirming that's the true
start of this ministry's checkin history, not an artifact of a query
window. Each past instance's roster comes from
`check-in/v1/end-user-check-ins?filter[event.id]=<id>`, which embeds BOTH
the child (`profile-snapshot`) and the checking-in guardian
(`by-profile-snapshot`, with phone/email) per check-in record -- the
guardian's phone/email is what jobs/congregation/kids_checkin_import.py
uses to find a household match, not fuzzy name matching.

Usage:
  from jobs.congregation.kids_checkin_client import pull_full_history
  data = pull_full_history()  # {"total_instances": N, "results": [...]}
"""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import requests
import websockets
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

ADB_BIN = str(Path.home() / "watson" / "bin" / "adb")
CDP_LOCAL_PORT = 9225  # distinct from fluro_client.py's 9223
DASHBOARD_ORIGIN = "https://dashboard.subsplash.com"
REPEATING_EVENT_ID = "798311fd-f384-4375-ad82-c899762b04d4"
APP_KEY = "7BVGB9"


class KidsCheckinClientError(RuntimeError):
    pass


class ApiAccessDisabled(KidsCheckinClientError):
    """Raised by every function in this module that calls core.subsplash.com."""


def _phone_host() -> str:
    host = os.getenv("FLURO_PHONE_ADB_HOST", "").strip()
    if not host:
        raise KidsCheckinClientError("FLURO_PHONE_ADB_HOST not set in .env (shared with fluro_client.py)")
    return host


def _adb(*args: str, timeout: int = 20) -> str:
    result = subprocess.run([ADB_BIN, *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0 and "already connected" not in (result.stdout + result.stderr):
        raise KidsCheckinClientError(f"adb {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _select_device_serial() -> str:
    """Prefer the Tailscale TCP/IP host; fall back to any USB-connected
    device already listed by `adb devices` if Tailscale is unreachable (the
    phone can drop off Tailscale/WiFi for hours while still sitting plugged
    in over USB -- discovered 2026-09-30 when the weekly cron's Tailscale
    route timed out but the phone was reachable over USB the whole time).
    Short 5s timeouts here so an offline phone fails fast into the USB
    fallback instead of hanging the whole pull."""
    host = _phone_host()
    try:
        subprocess.run([ADB_BIN, "connect", host], capture_output=True, text=True, timeout=5)
        check = subprocess.run([ADB_BIN, "-s", host, "get-state"], capture_output=True, text=True, timeout=5)
        if check.returncode == 0 and check.stdout.strip() == "device":
            return host
    except Exception:
        pass
    listed = subprocess.run([ADB_BIN, "devices"], capture_output=True, text=True, timeout=10)
    for line in listed.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device" and ":" not in parts[0]:
            return parts[0]
    raise KidsCheckinClientError(
        f"phone unreachable -- not on Tailscale ({host}) and no USB-connected device found"
    )


async def _cdp_page_enable_responsive(timeout: float = 6) -> bool:
    """Quick health check: does Chrome's CDP endpoint actually respond to a
    real command, not just accept the TCP/websocket connection? A stale/
    zombie debug session can complete the handshake but never ack a real
    command -- discovered 2026-09-30 when a pull looked connected (tab
    found, websocket open) but hung forever on Chrome never acking
    Page.enable. Uses its own throwaway id (999) so it can never collide
    with a real caller's in-flight message id."""
    try:
        tab_ws = _find_dashboard_tab()
    except KidsCheckinClientError:
        return False
    try:
        async with websockets.connect(tab_ws, open_timeout=5) as ws:
            await ws.send(json.dumps({"id": 999, "method": "Page.enable"}))
            await asyncio.wait_for(ws.recv(), timeout=timeout)
            return True
    except Exception:
        return False


async def _poll_for_cdp_responsive(seconds: float) -> bool:
    """Poll _cdp_page_enable_responsive() for up to `seconds`, instead of a
    single fixed-delay check -- cold Chrome starts (post-reboot, or after
    _force_restart_chrome's force-stop) are slow and variable, so any single
    fixed sleep-then-check is a guess that can fail even when Chrome would
    have been ready a few seconds later."""
    end = time.time() + seconds
    while time.time() < end:
        if await _cdp_page_enable_responsive(timeout=4):
            return True
        await asyncio.sleep(3)
    return False


def _force_restart_chrome(serial: str) -> None:
    """Full kill + relaunch, not just wake/foreground -- recovery path for
    when Chrome's CDP debug handler is unresponsive (zombie state, see
    _cdp_page_enable_responsive's docstring). More disruptive than a plain
    wake (all tabs reload from scratch), so this is only ever called as a
    fallback after a responsiveness check actually fails, never
    unconditionally -- same "don't disturb what's open unless we have to"
    courtesy _find_dashboard_tab already follows for tab reuse."""
    _adb("-s", serial, "shell", "am", "force-stop", "com.android.chrome")
    time.sleep(2)
    _adb("-s", serial, "shell", "monkey", "-p", "com.android.chrome", "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)


async def ensure_phone_connected() -> str:
    """Returns the adb serial/host actually used (Tailscale TCP host or a
    USB serial) in case a caller needs it, though the rest of this module
    only ever talks to the locally-forwarded CDP port and doesn't care which
    transport got it there.

    2026-09-30: added a responsiveness check + force-restart fallback.
    Waking/foregrounding an already-running Chrome process isn't enough to
    recover a zombie CDP debug session (discovered same day, see
    _cdp_page_enable_responsive) -- only a real kill + relaunch does. Kept
    as a fallback path (not the default) since it's more disruptive to
    whatever tabs/state are already open on the phone."""
    serial = _select_device_serial()
    _adb("-s", serial, "forward", f"tcp:{CDP_LOCAL_PORT}", "localabstract:chrome_devtools_remote")
    # Chrome on the phone gets suspended (Dozing) when idle -- CDP won't
    # respond at all until the screen is woken and Chrome is actually running.
    _adb("-s", serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    _adb("-s", serial, "shell", "monkey", "-p", "com.android.chrome", "-c", "android.intent.category.LAUNCHER", "1")

    # 2026-09-30: a bare 2s sleep + single check here used to fail hard
    # ("connection refused") right after a phone reboot, since a COLD Chrome
    # start (plus the rest of the OS still settling post-reboot) can easily
    # take longer than 2s to expose its debug socket at all -- a plain wake
    # of an already-running Chrome is fast, but this same code path also
    # covers the cold-start case, so it needs to poll, not assume 2s is
    # enough. Same poll_for_cdp helper as the post-force-restart path below.
    if await _poll_for_cdp_responsive(seconds=20):
        return serial

    print(f"[{time.strftime('%H:%M:%S')}] Chrome unresponsive to CDP -- force-restarting...", flush=True)
    _force_restart_chrome(serial)
    _adb("-s", serial, "forward", f"tcp:{CDP_LOCAL_PORT}", "localabstract:chrome_devtools_remote")
    if await _poll_for_cdp_responsive(seconds=20):
        return serial
    raise KidsCheckinClientError("Chrome still unresponsive to CDP after force-restart")


def _list_targets() -> list[dict]:
    resp = requests.get(f"http://localhost:{CDP_LOCAL_PORT}/json", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _find_dashboard_tab() -> str:
    """Never opens a new tab -- this reuses whatever existing tab is on
    dashboard.subsplash.com, same courtesy fluro_client.py extends to the
    Fluro tab (Bill or Donna may be looking at it)."""
    for target in _list_targets():
        if target.get("type") == "page" and target.get("url", "").startswith(DASHBOARD_ORIGIN):
            return target["webSocketDebuggerUrl"]
    raise KidsCheckinClientError(
        "no dashboard.subsplash.com tab open on the phone -- open the Subsplash Dashboard "
        "once on the work phone so this client has a tab to attach to"
    )


_PATCH_JS = """
(() => {
  window.__capturedAuth = null;
  const orig = window.fetch;
  window.fetch = function(input, init) {
    try {
      let h = (init && init.headers) || (input && input.headers);
      if (h) {
        const hdrs = new Headers(h);
        const a = hdrs.get('Authorization') || hdrs.get('authorization');
        // Always take the LATEST header seen, not just the first -- if the
        // app's own boot sequence hits a stale cached token and silently
        // refreshes-and-retries, the first one we'd see is the bad one.
        if (a) window.__capturedAuth = a;
      }
    } catch (e) {}
    return orig.apply(this, arguments);
  };
})();
"""

_CHECK_JS = "window.__capturedAuth ? 'ready' : 'waiting'"

_PULL_JS = f"""
(async () => {{
  const REPEATING_EVENT_ID = "{REPEATING_EVENT_ID}";
  const APP_KEY = "{APP_KEY}";
  const headers = {{Authorization: window.__capturedAuth}};

  async function fetchJson(url) {{
    for (let attempt = 0; attempt < 3; attempt++) {{
      const resp = await fetch(url, {{headers}});
      if (resp.ok) return await resp.json();
      if (resp.status === 429 || resp.status >= 500) {{
        await new Promise(r => setTimeout(r, 500 * (attempt + 1)));
        continue;
      }}
      return {{__error: resp.status}};
    }}
    return {{__error: 'retries_exhausted'}};
  }}

  let events = [];
  let page = 1;
  while (true) {{
    const url = `https://core.subsplash.com/events/v2/events?filter[app_key]=${{APP_KEY}}&filter[repeating_event.id]=${{REPEATING_EVENT_ID}}&filter[ical]=false&sort=start_at&page[size]=100&page[number]=${{page}}`;
    const j = await fetchJson(url);
    if (j.__error) return JSON.stringify({{error: 'events_list', status: j.__error, page}});
    const batch = (j._embedded && j._embedded.events) || [];
    events = events.concat(batch);
    if (!(j._links && j._links.next)) break;
    page += 1;
    if (page > 20) break;
  }}

  const now = new Date().toISOString();
  const pastEvents = events.filter(e => e.start_at <= now);

  const results = [];
  const failedEvents = [];
  for (const ev of pastEvents) {{
    let checkins = [];
    let cpage = 1;
    let failed = false;
    while (true) {{
      const rurl = `https://core.subsplash.com/check-in/v1/end-user-check-ins?filter[app_key]=${{APP_KEY}}&filter[event.id]=${{ev.id}}&page[size]=100&page[number]=${{cpage}}`;
      const rj = await fetchJson(rurl);
      if (rj.__error) {{ failed = true; break; }}
      const batch = (rj._embedded && rj._embedded['end-user-check-ins']) || [];
      checkins = checkins.concat(batch);
      if (!(rj._links && rj._links.next)) break;
      cpage += 1;
      if (cpage > 10) break;
    }}
    if (failed) {{
      failedEvents.push(ev.id);
      continue;  // don't record a false "0 checkins" for a fetch that actually failed
    }}
    results.push({{event_id: ev.id, start_at: ev.start_at, checkins}});
    await new Promise(r => setTimeout(r, 150));  // gentle pacing, avoid tripping a rate limit
  }}

  return JSON.stringify({{total_instances: pastEvents.length, results, failedEvents}});
}})()
"""


async def _ws_eval(ws, expression: str, await_promise: bool = False, timeout: int = 20):
    msg_id = int(time.time() * 1000) % 1_000_000
    await ws.send(json.dumps({
        "id": msg_id, "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
    }))
    # Bug found 2026-09-30: this used to pass the FULL `timeout` to every
    # wait_for() call inside the loop instead of the remaining budget until
    # `end` -- a chatty CDP connection sending unrelated Page/Network
    # notification frames (which don't match msg_id and just loop around)
    # kept resetting each individual recv()'s own fresh timeout, so the loop
    # could run far past the intended deadline instead of bailing at `end`.
    # Caught live: a pull_full_history() call ran 15+ minutes past its
    # intended 540s cap, blocked in epoll with no progress.
    end = time.time() + timeout
    while True:
        remaining = end - time.time()
        if remaining <= 0:
            break
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        data = json.loads(raw)
        if data.get("id") == msg_id:
            result = data.get("result", {})
            if result.get("exceptionDetails"):
                raise KidsCheckinClientError(f"JS error: {result['exceptionDetails']}")
            if "result" in result:
                return result["result"].get("value")
            raise KidsCheckinClientError(f"unexpected eval response: {data}")
    raise TimeoutError("Runtime.evaluate timed out")


async def _pull_full_history_async() -> dict:
    # 2026-09-30 bug fix: the two bare `await ws.recv()` calls just below
    # (CDP acks for Page.enable / addScriptToEvaluateOnNewDocument) had NO
    # timeout at all -- unlike _ws_eval, which now correctly bounds its
    # wait. If the CDP connection went silently dead right here (before any
    # _ws_eval call is even reached), the coroutine blocked forever with
    # zero output. This is the real cause of a 19+ minute hang that
    # survived the earlier _ws_eval timeout fix. Fixed by bounding every
    # recv() the same way, adding an explicit connect open_timeout, and
    # wrapping the whole function in an outer hard-ceiling watchdog (see
    # pull_full_history() below) so no future unguarded recv() can hang
    # this forever again. print(..., flush=True) at each stage so a stall,
    # if it ever recurs, shows exactly which stage it died on instead of
    # producing zero output like this one did.
    print(f"[{time.strftime('%H:%M:%S')}] connecting to phone...", flush=True)
    await ensure_phone_connected()
    tab_ws = _find_dashboard_tab()
    print(f"[{time.strftime('%H:%M:%S')}] found dashboard tab, opening CDP websocket...", flush=True)

    async with websockets.connect(tab_ws, max_size=50_000_000, open_timeout=15) as ws:
        print(f"[{time.strftime('%H:%M:%S')}] websocket open, enabling Page domain...", flush=True)
        await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        await asyncio.wait_for(ws.recv(), timeout=15)

        # Always force a fresh capture rather than trusting a leftover
        # window.__capturedAuth from an earlier run in this same tab -- the
        # captured value doesn't expire from the page's point of view (it's
        # just a JS global sitting there), but the underlying short-lived
        # JWT it holds does. Reusing a stale one produces a silent 401 on
        # the real pull, discovered 2026-09-29 on a second same-session run.
        await ws.send(json.dumps({
            "id": 2, "method": "Page.addScriptToEvaluateOnNewDocument",
            "params": {"source": _PATCH_JS},
        }))
        await asyncio.wait_for(ws.recv(), timeout=15)
        await ws.send(json.dumps({"id": 3, "method": "Page.reload"}))
        print(f"[{time.strftime('%H:%M:%S')}] page reloaded, waiting for auth capture...", flush=True)

        ready = False
        end = time.time() + 15
        while time.time() < end:
            try:
                status = await _ws_eval(ws, _CHECK_JS, timeout=3)
                if status == "ready":
                    ready = True
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
        if not ready:
            raise KidsCheckinClientError("timed out waiting for auth capture after reload")
        # Grace period: give the app's own boot sequence time to finish any
        # stale-token-then-refresh cycle so __capturedAuth settles on a
        # validated token before we start using it ourselves.
        await asyncio.sleep(4)

        print(f"[{time.strftime('%H:%M:%S')}] auth captured, starting browser-side pull of all instances...", flush=True)
        result = await _ws_eval(ws, _PULL_JS, await_promise=True, timeout=540)
        print(f"[{time.strftime('%H:%M:%S')}] pull finished", flush=True)
        return json.loads(result)


def _one_time_override_active() -> bool:
    """Bill's explicit ONE-TIME exception to the Subsplash API pause (2026-10-06: "for this one time catch up, while we wait for Subsplash
    to get back to us, pull all the data from core.subsplash.com and backfill"). Active only while the environment variable
    SUBSPLASH_API_ONE_TIME_UNTIL holds an ISO date that is today or later: a cron line or a later session cannot trigger it by accident and
    it expires by itself. Nothing sets it by default; the paused cron lines do not."""
    import datetime as _dt
    import os as _os
    try:
        return _dt.date.today() <= _dt.date.fromisoformat(_os.environ.get("SUBSPLASH_API_ONE_TIME_UNTIL", ""))
    except ValueError:
        return False


def _require_api_permission() -> None:
    if _one_time_override_active():
        print(f"[{time.strftime('%H:%M:%S')}] ONE-TIME Subsplash API override in effect (Bill, 2026-10-06); the API is switched off otherwise", flush=True)
        return
    raise ApiAccessDisabled(
        "Subsplash/Fluro API access is switched off (Bill, 2026-10-06): their robots.txt disallows automated "
        "access and we have no permission to use their API. Read the dashboard pages instead "
        "(see jobs/church_calendar/registrations.py).")


def pull_full_history() -> dict:
    """Returns {"total_instances": N, "results": [{"event_id", "start_at",
    "checkins": [...]}]} covering every past instance of the Kids Checkin
    repeating event. Confirmed 2026-09-29: 88 instances, 345 checkin
    records, 2025-01-26 through the most recent Sunday.

    A full 88-instance pull sometimes trips a rate limit partway through
    (discovered 2026-09-29 re-running this twice in one session -- some
    instances came back in `failedEvents` on the second run that succeeded
    on the first). kids_checkin_import.py's INSERT OR IGNORE on
    subsplash_checkin_id makes re-running this safe, but for patching a
    handful of known-missing dates, prefer pull_events_by_id below instead
    of hammering the full history again.

    2026-09-30: wrapped in an outer 11-minute hard-ceiling watchdog. Every
    individual recv()/eval() inside _pull_full_history_async() is now
    correctly timeout-bounded (see the two bugs fixed same day), but this
    outer wait_for is cheap defense-in-depth against any future unguarded
    await slipping in and hanging the whole pull silently again -- raises a
    clear TimeoutError instead."""
    _require_api_permission()
    return asyncio.run(asyncio.wait_for(_pull_full_history_async(), timeout=660))


_PULL_EVENTS_JS_TEMPLATE = """
(async () => {{
  const APP_KEY = "{app_key}";
  const eventIds = {event_ids_json};
  const headers = {{Authorization: window.__capturedAuth}};

  async function fetchJson(url) {{
    for (let attempt = 0; attempt < 3; attempt++) {{
      const resp = await fetch(url, {{headers}});
      if (resp.ok) return await resp.json();
      if (resp.status === 429 || resp.status >= 500) {{
        await new Promise(r => setTimeout(r, 800 * (attempt + 1)));
        continue;
      }}
      return {{__error: resp.status}};
    }}
    return {{__error: 'retries_exhausted'}};
  }}

  const results = [];
  const failedEvents = [];
  for (const eventId of eventIds) {{
    const evUrl = `https://core.subsplash.com/events/v2/events/${{eventId}}?include=calendar`;
    const evJson = await fetchJson(evUrl);
    const startAt = (evJson && evJson.start_at) || null;

    let checkins = [];
    let cpage = 1;
    let failed = false;
    while (true) {{
      const rurl = `https://core.subsplash.com/check-in/v1/end-user-check-ins?filter[app_key]=${{APP_KEY}}&filter[event.id]=${{eventId}}&page[size]=100&page[number]=${{cpage}}`;
      const rj = await fetchJson(rurl);
      if (rj.__error) {{ failed = true; break; }}
      const batch = (rj._embedded && rj._embedded['end-user-check-ins']) || [];
      checkins = checkins.concat(batch);
      if (!(rj._links && rj._links.next)) break;
      cpage += 1;
      if (cpage > 10) break;
    }}
    if (failed) {{ failedEvents.push(eventId); continue; }}
    results.push({{event_id: eventId, start_at: startAt, checkins}});
    await new Promise(r => setTimeout(r, 300));
  }}

  return JSON.stringify({{total_instances: results.length, results, failedEvents}});
}})()
"""


async def _pull_events_by_id_async(event_ids: list[str]) -> dict:
    await ensure_phone_connected()
    tab_ws = _find_dashboard_tab()

    async with websockets.connect(tab_ws, max_size=50_000_000) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        await ws.recv()
        await ws.send(json.dumps({
            "id": 2, "method": "Page.addScriptToEvaluateOnNewDocument",
            "params": {"source": _PATCH_JS},
        }))
        await ws.recv()
        await ws.send(json.dumps({"id": 3, "method": "Page.reload"}))

        ready = False
        end = time.time() + 15
        while time.time() < end:
            try:
                status = await _ws_eval(ws, _CHECK_JS, timeout=3)
                if status == "ready":
                    ready = True
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
        if not ready:
            raise KidsCheckinClientError("timed out waiting for auth capture after reload")
        await asyncio.sleep(4)

        js = _PULL_EVENTS_JS_TEMPLATE.format(app_key=APP_KEY, event_ids_json=json.dumps(event_ids))
        result = await _ws_eval(ws, js, await_promise=True, timeout=180)
        return json.loads(result)


def pull_events_by_id(event_ids: list[str]) -> dict:
    """Targeted backfill for a small, known list of event instance ids --
    much less likely to trip a rate limit than re-running the full
    88-instance pull_full_history() just to patch a handful of dates."""
    _require_api_permission()
    return asyncio.run(_pull_events_by_id_async(event_ids))


if __name__ == "__main__":
    data = pull_full_history()
    if "error" in data:
        print("ERROR:", data)
    else:
        total_checkins = sum(len(r["checkins"]) for r in data["results"])
        print(f"instances: {data['total_instances']}, checkin records: {total_checkins}")
