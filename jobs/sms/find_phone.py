"""Locate the gateway phone's wireless-adb endpoint without assuming any IP or port.

Order: cached host -> gateway /health sweep of the LAN (matches the phone by what
it answers, not by MAC/IP) -> adb mDNS -> port scan of 30000-50000 on that host.
Wireless-debugging ports rotate, and mDNS entries go stale, so the port scan is the
authoritative fallback. Usage: python -m jobs.sms.find_phone [--run "<adb shell cmd>"]
"""
import concurrent.futures as cf
import ipaddress
import json
import os
import socket
import subprocess
import sys
import urllib.request
import base64

ADB = os.path.expanduser("~/platform-tools/adb")
SUBNET = "192.168.1.0/24"
CACHE = os.path.expanduser("~/watson/data/phone_host.json")
TAILSCALE_IP = "100.69.92.36"


def _env(name):
    for line in open(os.path.expanduser("~/watson/.env")):
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    return ""


def _is_gateway(ip, timeout=1.0):
    req = urllib.request.Request(f"http://{ip}:8080/health")
    tok = base64.b64encode(f"{_env('SMS_GATEWAY_USER')}:{_env('SMS_GATEWAY_PASS')}".encode()).decode()
    req.add_header("Authorization", "Basic " + tok)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return "battery:level" in json.load(r).get("checks", {})
    except Exception:
        return False


def find_host():
    try:
        cached = json.load(open(CACHE))["ip"]
        if _is_gateway(cached):
            return cached
    except Exception:
        pass
    ips = [str(i) for i in ipaddress.ip_network(SUBNET).hosts()]
    with cf.ThreadPoolExecutor(64) as ex:
        for ip, ok in zip(ips, ex.map(_is_gateway, ips)):
            if ok:
                os.makedirs(os.path.dirname(CACHE), exist_ok=True)
                json.dump({"ip": ip}, open(CACHE, "w"))
                return ip
    return None


def _open(ip, port):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex((ip, port)) == 0
    finally:
        s.close()


def candidate_ports(ip):
    out = subprocess.run([ADB, "mdns", "services"], capture_output=True, text=True).stdout
    ports = [int(l.split(":")[-1]) for l in out.splitlines() if ip + ":" in l]
    with cf.ThreadPoolExecutor(400) as ex:
        scanned = [p for p, ok in zip(range(30000, 50000), ex.map(lambda p: _open(ip, p), range(30000, 50000))) if ok]
    return [p for p in ports + scanned if _open(ip, p)] or scanned


def connect():
    """Return an adb serial ('ip:port') for the phone, or None."""
    ip = find_host()
    if not ip:
        return None
    for port in dict.fromkeys(candidate_ports(ip)):
        serial = f"{ip}:{port}"
        subprocess.run([ADB, "connect", serial], capture_output=True, timeout=15)
        state = subprocess.run([ADB, "-s", serial, "get-state"], capture_output=True, text=True, timeout=15).stdout.strip()
        if state == "device":
            return serial
        subprocess.run([ADB, "disconnect", serial], capture_output=True)
    return None


if __name__ == "__main__":
    serial = connect()
    print(serial or "phone not found")
    if serial and "--run" in sys.argv:
        cmd = sys.argv[sys.argv.index("--run") + 1]
        r = subprocess.run([ADB, "-s", serial, "shell", cmd], capture_output=True, text=True, timeout=120)
        print(r.stdout, r.stderr)
    sys.exit(0 if serial else 1)
