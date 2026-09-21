"""jobs/network_monitor/scan.py — logs which devices are active on Bill's
home LAN and when.

The Beelink is a plain client on this network (consumer router, no
SSH/API/SNMP access), so there's no router log to read. Instead this
active-pings every host in the subnet (populating the kernel's ARP cache
as a side effect), then reads that cache with `ip neigh show`. Both steps
run fine unprivileged: /usr/bin/ping already carries cap_net_raw on this
box, so no sudo/arp-scan/nmap install is needed (Claude Code's only sudo
here is service restarts, never package installs — see CLAUDE.md).

Every MAC ever seen gets a network_devices row Bill can label/assign to a
family member from the dashboard; a MAC that's never been seen before
gets a one-time Telegram alert (jobs/network_monitor/db.py.record_sighting
returns whether it was new). Cron: every 5 minutes.
"""
import ipaddress
import logging
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.vacation import vacation_gate
from jobs.network_monitor.db import device_count, init_db, record_sighting

log = logging.getLogger(__name__)

# Bill's LAN, from `ip route` (default via 192.168.1.1, /24 on enp171s0).
SUBNET = "192.168.1.0/24"
PING_TIMEOUT_S = 1
SWEEP_WORKERS = 64


def _ping(ip: str) -> None:
    subprocess.run(
        ["ping", "-c", "1", "-W", str(PING_TIMEOUT_S), ip],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _sweep() -> None:
    hosts = [str(ip) for ip in ipaddress.ip_network(SUBNET).hosts()]
    with ThreadPoolExecutor(max_workers=SWEEP_WORKERS) as pool:
        list(pool.map(_ping, hosts))


def _read_arp_table() -> dict[str, str]:
    """Returns {mac: ip} for every reachable/recently-reachable neighbor
    `ip neigh` knows about on our subnet. FAILED/INCOMPLETE entries (a host
    that didn't answer this sweep) are excluded."""
    net = ipaddress.ip_network(SUBNET)
    out = subprocess.run(
        ["ip", "neigh", "show"], capture_output=True, text=True, timeout=10
    ).stdout
    devices: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or "lladdr" not in parts:
            continue
        ip = parts[0]
        try:
            if ipaddress.ip_address(ip) not in net:
                continue
        except ValueError:
            continue
        state = parts[-1]
        if state in ("FAILED", "INCOMPLETE"):
            continue
        mac = parts[parts.index("lladdr") + 1]
        devices[mac] = ip
    return devices


def _resolve_hostname(ip: str) -> str | None:
    try:
        name, _, _ = socket.gethostbyaddr(ip)
        return name.split(".")[0]
    except (socket.herror, socket.gaierror, OSError):
        pass
    try:
        result = subprocess.run(
            ["avahi-resolve-address", ip], capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and result.stdout.strip():
            # "192.168.1.42\tsome-device.local"
            parts = result.stdout.strip().split()
            if len(parts) == 2:
                return parts[1].removesuffix(".local")
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _alert_new_device(mac: str, ip: str, hostname: str | None) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    desc = hostname or ip
    text = (
        f"New device joined the home network: {desc} ({mac}). "
        "Label it from the dashboard's Network Devices card if you know "
        "what it is.\n\n- Watson"
    )
    if vacation_gate("normal", "jobs.network_monitor.scan", text):
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
        timeout=10,
    )


def run() -> None:
    init_db()
    # First run ever: seed whatever's already on the network as the known
    # baseline instead of firing one "new device" alert per device (which
    # would just spam every phone/laptop/TV already sitting on the LAN).
    seeding = device_count() == 0
    _sweep()
    devices = _read_arp_table()
    log.info("network_monitor: %d devices seen this pass%s", len(devices), " (seeding baseline)" if seeding else "")
    for mac, ip in devices.items():
        hostname = _resolve_hostname(ip)
        is_new = record_sighting(mac, ip, hostname)
        if is_new and not seeding:
            log.info("network_monitor: new device %s (%s) %s", mac, ip, hostname or "")
            _alert_new_device(mac, ip, hostname)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
