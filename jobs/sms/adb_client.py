"""jobs/sms/adb_client.py — shared adb connect + content-provider query
helper for jobs/sms/adb_inbound.py (and jobs/sms/heartbeat.py's ADB
reachability check). Mirrors the connect pattern already duplicated in
jobs/sms/sabbath_digest.py and jobs/sms/call_forwarding_toggle.py (left
untouched -- not worth the blast radius of refactoring working, unrelated
features just to share this one call).

Known fragility (see project_sms_gateway_app.md): `adb tcpip 5555` does not
survive a phone reboot -- needs re-running over USB each time. The phone
stays physically wired to this host via USB though, which has no such
fragility (survives reboot as long as USB debugging is still authorized) --
2026-09-30: connect_device() was blind to that working USB connection and
only ever tried the wireless candidates below, so every phone reboot caused
a false "adb unreachable" alert despite the phone being fully reachable the
whole time over the cable sitting right next to it. Now checks for any
already-connected/authorized device (USB included) before falling back to
wireless. See bug_tracker for this fix.
"""
import logging
import os
import re
import subprocess

log = logging.getLogger(__name__)

ADB = os.path.expanduser("~/platform-tools/adb")
_DEVICE_CANDIDATES = ["100.87.200.90:5555", "192.168.1.174:5555"]

# Splits a `Row: N col=val, col=val, ...` line only at ", " boundaries that
# are immediately followed by another `word=` field -- a plain
# `line.split(", ")` breaks on any comma inside a message body (e.g.
# "Thank you, I'll resend the invite").
_FIELD_SPLIT_RE = re.compile(r", (?=[A-Za-z_][A-Za-z0-9_]*=)")


def connect_device() -> str | None:
    """Checks for any device adb already sees in "device" state first --
    the gateway phone's permanent USB connection to this host normally
    answers here, instantly, without ever touching the network candidates
    below. Only falls back to the wireless candidates (Tailscale first, LAN
    fallback -- same order/behavior as sabbath_digest.py's
    _connect_device()) if nothing is already connected, e.g. the phone is
    momentarily unplugged.

    `adb connect` can itself hang past its timeout when the target host is
    up on the network (responds to ping) but nothing is listening on 5555
    -- discovered 2026-09-29 when this raised subprocess.TimeoutExpired
    uncaught, crashing every caller (heartbeat.py's 5-minute cron included,
    silently, for hours -- its debounced Telegram alert never got a chance
    to fire since the crash happened before reaching that logic). Both
    subprocess calls are now guarded so an unreachable/slow candidate is
    treated as a failed candidate, not an unhandled exception."""
    try:
        existing = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        existing = None
    if existing is not None:
        for line in existing.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) == 2 and parts[1] == "device":
                return parts[0]

    for device in _DEVICE_CANDIDATES:
        try:
            subprocess.run([ADB, "connect", device], capture_output=True, text=True, timeout=15)
            check = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            log.warning("adb_client.connect_device: %s timed out, trying next candidate", device)
            continue
        if device in check.stdout and "device" in check.stdout.split(device, 1)[1].split("\n", 1)[0]:
            return device
    return None


_ROW_START_RE = re.compile(r"^Row: \d+ ")


def _parse_row(line: str) -> dict:
    # "Row: 0 col=val, col=val, ..." -- drop the "Row: N " prefix first.
    _, _, rest = line.partition(" ")
    _, _, rest = rest.partition(" ")
    fields = {}
    for part in _FIELD_SPLIT_RE.split(rest):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        fields[k.strip()] = None if v == "NULL" else v
    return fields


def _parse_rows(stdout: str) -> list[dict]:
    """A field value (e.g. an MMS part's `text` column) can itself contain
    embedded newlines -- confirmed 2026-09-29 with a 3-line inbound message
    whose `content query` output spanned 3 physical lines. splitlines() then
    filtering on a literal "Row:" prefix silently dropped every continuation
    line, truncating the body at its first newline. Instead, only a line
    matching "Row: N " starts a new row; every other line is a continuation
    of the previous row's last field and gets rejoined with the "\\n"
    splitlines() stripped."""
    rows = []
    buffer = None
    for line in stdout.splitlines():
        if _ROW_START_RE.match(line):
            if buffer is not None:
                rows.append(_parse_row(buffer))
            buffer = line
        elif buffer is not None:
            buffer += "\n" + line
    if buffer is not None:
        rows.append(_parse_row(buffer))
    return rows


def query(device: str, uri: str, where: str | None = None, sort: str | None = None, timeout: int = 20) -> list[dict]:
    """Runs `content query` as ONE quoted shell string, not argv-split --
    confirmed during investigation that passing --where/--sort as separate
    adb argv elements breaks and prints usage help instead of results."""
    cmd = f'content query --uri "{uri}"'
    if where:
        cmd += f' --where "{where}"'
    if sort:
        cmd += f' --sort "{sort}"'

    result = subprocess.run(
        [ADB, "-s", device, "shell", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0 or "usage:" in result.stdout.lower():
        log.warning("adb_client.query: failed for uri=%s where=%s: %s", uri, where, result.stdout[:300] or result.stderr[:300])
        return []
    return _parse_rows(result.stdout)
