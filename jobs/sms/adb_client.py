"""jobs/sms/adb_client.py — shared adb connect + content-provider query
helper for jobs/sms/adb_inbound.py (and jobs/sms/heartbeat.py's ADB
reachability check). Mirrors the connect pattern already duplicated in
jobs/sms/sabbath_digest.py and jobs/sms/call_forwarding_toggle.py (left
untouched -- not worth the blast radius of refactoring working, unrelated
features just to share this one call).

Known fragility (see project_sms_gateway_app.md): `adb tcpip 5555` does not
survive a phone reboot -- needs re-running over USB each time. This module
does not attempt to recover from that; jobs/sms/heartbeat.py's ADB check is
what alerts Bill when the phone is unreachable this way.
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
    """Tries each candidate host in turn (Tailscale first, LAN fallback),
    same order/behavior as sabbath_digest.py's _connect_device()."""
    for device in _DEVICE_CANDIDATES:
        subprocess.run([ADB, "connect", device], capture_output=True, text=True, timeout=15)
        check = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=10)
        if device in check.stdout and "device" in check.stdout.split(device, 1)[1].split("\n", 1)[0]:
            return device
    return None


def _parse_rows(stdout: str) -> list[dict]:
    rows = []
    for line in stdout.splitlines():
        if not line.startswith("Row:"):
            continue
        # "Row: 0 col=val, col=val, ..." -- drop the "Row: N " prefix first.
        _, _, rest = line.partition(" ", )
        _, _, rest = rest.partition(" ")
        fields = {}
        for part in _FIELD_SPLIT_RE.split(rest):
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            fields[k.strip()] = None if v == "NULL" else v
        rows.append(fields)
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
