"""jobs/privacy/captcha_assist.py — Option C: human-in-the-loop CAPTCHA
solving for brokers whose form_selectors carries "captcha_assist": true
(currently MyLife; Radaris has the same shape blocker but is not wired into
this yet -- see its notes column in schema.py).

Design, chosen over 2Captcha-style paid solving or a VNC/Xvfb desktop:
Watson's browser stays headless=True (jobs/browser/browser_service.py's rule
is untouched) but is launched with Chromium's own --remote-debugging-port.
Chrome DevTools' own chrome://inspect already mirrors a live, fully
interactive view of a remote target and forwards real mouse/keyboard input
over the debugging protocol -- no VNC/Xvfb needed, and it works against a
headless target exactly as well as a headed one. Bill opens chrome://inspect
on his own device (already on the tailnet), adds the printed <ip>:<port>
once, clicks "inspect", solves the challenge and clicks the real submit
button himself in that mirror -- this module (and remove.py) never click
submit after handoff, so there's no race between Watson and Bill for the
same action.

IMPORTANT, found by live-testing this module before wiring it into any real
broker (2026-09-05): --remote-debugging-address does NOT work on current
Chromium -- it's a known, intentional security hardening (unauthenticated
CDP access is equivalent to full remote code execution, so Chromium refuses
to bind the debugging port to anything but 127.0.0.1 regardless of that
flag). So this module runs its own tiny asyncio TCP proxy
(TAILSCALE_IP:port -> 127.0.0.1:port) instead of trying to make Chromium
bind non-loopback directly -- confirmed live that this is sufficient: Chrome
DevTools' HTTP endpoint rewrites the `webSocketDebuggerUrl` it returns using
the incoming request's Host header (the same mechanism that makes e.g. `adb
forward`-based remote debugging work), and a raw byte-level TCP proxy passes
that header through untouched. Never bind the proxy's listen socket to
0.0.0.0 -- same convention as TAILSCALE_IP in jobs/dev/sandbox_session.py.

privacy_captcha_waits (schema.py) is the coordination point: this module
holds the browser + page open and polls that table; bot.py's
priv_captcha_ready/priv_captcha_cancel callback handlers just write to it on
a button tap. Same "SQLite as the shared coordination point" pattern the
rest of jobs/privacy/* already uses -- not a new IPC mechanism.
"""
import asyncio
import logging
import socket
from datetime import datetime
from pathlib import Path

from core.database import get_connection
from jobs.privacy import send_telegram
from jobs.privacy.verify import check_success

log = logging.getLogger(__name__)

# Never bind to 0.0.0.0 -- a port bound to the Tailscale interface is
# unreachable from the LAN and the wider internet regardless of what's
# proxied, not just "not currently exposed". Same IP, same rationale, as
# jobs/dev/sandbox_session.py's TAILSCALE_IP; duplicated here rather than
# imported since these are two independent, unrelated subsystems sharing one
# small safety constant, not a shared dependency.
TAILSCALE_IP = "100.117.237.96"
PORT_RANGE = range(7800, 7821)  # distinct from sandbox_session.py's 7700-7750

POLL_INTERVAL_S = 5
MAX_WAIT_S = 15 * 60  # 15 minutes -- if nobody's there, don't hold the browser open forever

LOG_DIR = Path.home() / "watson" / "logs" / "privacy"


def pick_port() -> int:
    """Binds and immediately releases each candidate port to find one that's
    actually free on TAILSCALE_IP right now -- Chromium's own launch failure
    on an in-use port is a much worse failure mode to debug live."""
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((TAILSCALE_IP, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in {PORT_RANGE.start}-{PORT_RANGE.stop - 1} on {TAILSCALE_IP}")


def launch_args(port: int) -> list[str]:
    # --remote-debugging-address is deliberately NOT passed -- Chromium
    # ignores it and binds 127.0.0.1 regardless (see module docstring); the
    # TCP proxy below is what actually makes this reachable over Tailscale.
    return [f"--remote-debugging-port={port}"]


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()


async def _handle_proxy_conn(port: int, client_reader, client_writer) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        client_writer.close()
        return
    await asyncio.gather(
        _pipe(client_reader, upstream_writer),
        _pipe(upstream_reader, client_writer),
        return_exceptions=True,
    )


async def start_tailscale_proxy(port: int):
    """Raw byte-level TCP forwarder, TAILSCALE_IP:port -> 127.0.0.1:port --
    see module docstring for why this exists instead of a Chromium launch
    flag. Returns the asyncio.Server; caller must .close() +
    await .wait_closed() it when done (wait_for_human() does this in a
    finally block)."""
    return await asyncio.start_server(
        lambda r, w: _handle_proxy_conn(port, r, w), host=TAILSCALE_IP, port=port
    )


def _insert_waiting(conn, removal_id: int) -> None:
    conn.execute(
        """INSERT INTO privacy_captcha_waits (removal_id, status)
           VALUES (?, 'waiting')
           ON CONFLICT(removal_id) DO UPDATE SET status='waiting', created_at=datetime('now'), resolved_at=NULL""",
        (removal_id,),
    )
    conn.commit()


def _poll_status(conn, removal_id: int) -> str | None:
    row = conn.execute(
        "SELECT status FROM privacy_captcha_waits WHERE removal_id=?", (removal_id,)
    ).fetchone()
    return row["status"] if row else None


async def wait_for_human(
    removal_id: int, person_name: str, broker_name: str, port: int, success_check: dict | None
) -> tuple[bool, str | None, bool]:
    """Sends the assist request, then polls privacy_captcha_waits until Bill
    resolves it (or MAX_WAIT_S elapses). Returns the same (ok, reason,
    confirmed) shape _submit_form()/_submit_wizard() already use.

    Caller is responsible for having already filled every field it has real
    data for and navigated to the final step BEFORE calling this -- Bill's
    mirror should show a form that's ready to solve-and-submit, not a blank
    one."""
    conn = get_connection()
    try:
        _insert_waiting(conn, removal_id)
    finally:
        conn.close()

    proxy = await start_tailscale_proxy(port)

    send_telegram(
        f"🧩 Privacy Guard needs a hand — {broker_name} ({person_name}) is filled in and waiting on a "
        f"CAPTCHA only you can solve.\n\n"
        f"1. On a device on the Tailscale network, open Chrome and go to chrome://inspect\n"
        f"2. Click \"Configure...\" and add: {TAILSCALE_IP}:{port}\n"
        f"3. Under \"Remote Target\", click \"inspect\" on the page that appears\n"
        f"4. Solve the CAPTCHA and click the real submit button yourself in that window — "
        f"Watson won't touch it after this message\n"
        f"5. Tap a button below once you're done (times out in 15 min otherwise)",
        priority="normal",
        reply_markup={"inline_keyboard": [[
            {"text": "✅ Done, verify", "callback_data": f"priv_captcha_ready:{removal_id}"},
            {"text": "❌ Cancel", "callback_data": f"priv_captcha_cancel:{removal_id}"},
        ]]},
    )

    elapsed = 0
    conn = get_connection()
    try:
        while elapsed < MAX_WAIT_S:
            status = _poll_status(conn, removal_id)
            if status == "ready":
                return True, None, True  # confirmed below only if success_check actually passes -- see caller
            if status == "cancelled":
                return False, "cancelled by Bill via the CAPTCHA-assist Telegram button", False
            await asyncio.sleep(POLL_INTERVAL_S)
            elapsed += POLL_INTERVAL_S
    finally:
        conn.close()
        # The proxy is only needed while Bill might still be looking at the
        # mirror -- torn down here regardless of outcome, same "always clean
        # up" rule as the browser itself in remove.py's callers.
        proxy.close()
        await proxy.wait_closed()

    return False, f"CAPTCHA-assist timed out after {MAX_WAIT_S // 60} min waiting for a response", False


async def resolve_after_human(page, removal: dict, success_check: dict | None) -> tuple[bool, str | None, bool]:
    """Called once wait_for_human() reports 'ready' -- Bill has (by his own
    hand) already submitted the form in the mirrored session, which is the
    same underlying page/browser this function still holds open. Screenshots
    + records the final page state regardless of outcome, since that's
    exactly the "captured live, never guessed" evidence a future
    success_check for this broker would need if none exists yet (see
    MyLife's form_selectors note in schema.py)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    shot_path = LOG_DIR / f"captcha-assist-{removal['id']}-{datetime.now():%Y%m%d%H%M%S}.png"
    try:
        await page.screenshot(path=str(shot_path), full_page=True)
    except Exception:
        pass
    final_url = page.url

    if not success_check:
        return (
            True,
            f"submitted by Bill via CAPTCHA-assist, but no success_check captured for this broker yet "
            f"(final url: {final_url}, screenshot: {shot_path})",
            False,
        )
    success, reason = await check_success(page, success_check)
    if success:
        return True, None, True
    return True, f"CAPTCHA-assist submit completed but success check failed ({reason}, screenshot: {shot_path})", False
