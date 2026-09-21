"""jobs/network_monitor/db.py — DB helpers for the home LAN device log.

jobs/network_monitor/scan.py runs every 5 minutes, ping-sweeps the LAN to
populate the kernel ARP cache, then reads `ip neigh show` for every
MAC/IP it can see. network_devices holds one row per MAC ever seen
(label/assigned_to are Bill's own identification of the device, set from
the dashboard — scan.py never touches them once a device exists).
network_sightings is an append-only log, one row per scan per MAC seen,
which is what "active when" answers come from; at a 5-minute cadence this
is small enough (~2000 rows/device/week) to never need pruning.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from config.settings import DB_PATH

# A device is considered "online" if seen within this many scan cycles
# (5-minute cadence) — generous enough to not flap offline on one missed
# ARP reply.
ONLINE_WINDOW_MINUTES = 12


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS network_devices (
                mac         TEXT PRIMARY KEY,
                ip          TEXT,
                hostname    TEXT,
                label       TEXT,
                assigned_to TEXT,
                known       INTEGER NOT NULL DEFAULT 0,
                first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
                last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS network_sightings (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                mac     TEXT NOT NULL,
                ip      TEXT,
                seen_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_network_sightings_mac_seen "
            "ON network_sightings(mac, seen_at)"
        )


def record_sighting(mac: str, ip: str, hostname: str | None) -> bool:
    """Upserts network_devices and logs a sighting row. Returns True if this
    MAC had never been seen before (i.e. a brand-new device on the LAN)."""
    with conn() as c:
        existing = c.execute(
            "SELECT mac FROM network_devices WHERE mac = ?", (mac,)
        ).fetchone()
        if existing:
            # hostname can flicker (DHCP re-lease, device renamed) — only
            # overwrite when the scan actually resolved one this pass.
            if hostname:
                c.execute(
                    "UPDATE network_devices SET ip = ?, hostname = ?, last_seen = datetime('now') WHERE mac = ?",
                    (ip, hostname, mac),
                )
            else:
                c.execute(
                    "UPDATE network_devices SET ip = ?, last_seen = datetime('now') WHERE mac = ?",
                    (ip, mac),
                )
        else:
            c.execute(
                "INSERT INTO network_devices (mac, ip, hostname) VALUES (?, ?, ?)",
                (mac, ip, hostname),
            )
        c.execute(
            "INSERT INTO network_sightings (mac, ip) VALUES (?, ?)", (mac, ip)
        )
        return existing is None


def device_count() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM network_devices").fetchone()[0]


def all_devices() -> list[sqlite3.Row]:
    """Dashboard listing order: assigned devices grouped together, then most
    recently active first within each group."""
    with conn() as c:
        return c.execute(
            "SELECT mac, ip, hostname, label, assigned_to, known, first_seen, last_seen "
            "FROM network_devices "
            "ORDER BY (assigned_to IS NULL), assigned_to, last_seen DESC"
        ).fetchall()


def update_device(mac: str, label: str | None, assigned_to: str | None) -> None:
    """Bill identifying a device from the dashboard edit form, which always
    submits both fields together (an empty one clears it — this isn't a
    partial patch). Saving either as non-empty marks the device known, so
    it drops out of any "needs a look" filtering."""
    known = 1 if (label or assigned_to) else 0
    with conn() as c:
        c.execute(
            "UPDATE network_devices SET label = ?, assigned_to = ?, known = ? WHERE mac = ?",
            (label, assigned_to, known, mac),
        )


def delete_device(mac: str) -> None:
    """Drop a device that should never have been tracked (a one-off guest
    phone, a stale entry). Sighting history for it goes too — unlike
    house_calls' soft-delete-by-status, there's no billing/audit reason to
    keep it."""
    with conn() as c:
        c.execute("DELETE FROM network_devices WHERE mac = ?", (mac,))
        c.execute("DELETE FROM network_sightings WHERE mac = ?", (mac,))


def recent_sightings(mac: str, limit: int = 200) -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT ip, seen_at FROM network_sightings WHERE mac = ? ORDER BY seen_at DESC LIMIT ?",
            (mac, limit),
        ).fetchall()


def online_cutoff() -> str:
    """UTC, to match sqlite's `datetime('now')` default that last_seen uses."""
    return (datetime.now(timezone.utc) - timedelta(minutes=ONLINE_WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
