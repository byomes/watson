"""jobs/network_monitor/remap.py — carry device identities across a Wi-Fi
SSID/password rotation.

Phones and tablets use a per-SSID randomized MAC (iOS "Private Wi-Fi
Address", Android's default), so renaming a network makes each of them
show up as a brand-new MAC with no label. This tool:

  1. `on`       snapshots network_devices/network_sightings to JSON and turns
                on migration mode. While on, scan.py sends ONE batched
                Telegram summary per scan instead of one alert per new MAC.
  2. `propose`  lists labeled devices not seen since `on` ("old") against
                unlabeled MACs first seen since `on` ("new"), grouped by
                hostname. A group with exactly one old and one new is a
                unique match; anything else is ambiguous and needs
                `apply` by hand (hostnames like "iPhone" are shared by
                every iPhone, so rejoin one person's device at a time and
                match by first_seen).
  3. `apply OLD NEW`   copies label/assigned_to/known onto NEW, re-points
                OLD's sighting history at NEW (so online/offline sessions
                stay continuous), and deletes OLD's row.
     `apply-unique`    does that for every unique match, only with --yes.
  4. `off`      ends migration mode.

Usage (from /home/billyomes/watson):
  PYTHONPATH=. venv/bin/python -m jobs.network_monitor.remap status|on|off|propose|snapshot
  PYTHONPATH=. venv/bin/python -m jobs.network_monitor.remap apply OLD_MAC NEW_MAC
  PYTHONPATH=. venv/bin/python -m jobs.network_monitor.remap apply-unique --yes
  PYTHONPATH=. venv/bin/python -m jobs.network_monitor.remap restore SNAPSHOT.json
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

from config.settings import BASE_DIR
from jobs.network_monitor.db import conn, init_db

FLAG_PATH = BASE_DIR / "data" / "network_migration.json"
SNAPSHOT_DIR = BASE_DIR / "data" / "network_snapshots"


def _now() -> str:
    # Same format as sqlite datetime('now'), so string comparison against
    # first_seen/last_seen is valid.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def migration_started_at() -> str | None:
    """UTC start time of the active migration, or None when it's off."""
    try:
        return json.loads(FLAG_PATH.read_text()).get("started_at")
    except (OSError, ValueError):
        return None


def snapshot() -> str:
    init_db()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"network_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with conn() as c:
        data = {
            "network_devices": [dict(r) for r in c.execute("SELECT * FROM network_devices")],
            "network_sightings": [dict(r) for r in c.execute("SELECT * FROM network_sightings")],
        }
    path.write_text(json.dumps(data))
    return str(path)


def restore(path: str) -> None:
    data = json.loads(open(path).read())
    with conn() as c:
        c.execute("DELETE FROM network_devices")
        c.execute("DELETE FROM network_sightings")
        for table in ("network_devices", "network_sightings"):
            for row in data[table]:
                cols = list(row)
                c.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [row[k] for k in cols],
                )


def migration_on() -> None:
    snap = snapshot()
    FLAG_PATH.write_text(json.dumps({"started_at": _now(), "snapshot": snap}))
    print(f"Migration mode ON. Snapshot: {snap}")


def migration_off() -> None:
    FLAG_PATH.unlink(missing_ok=True)
    print("Migration mode OFF.")


def _host_key(hostname: str | None) -> str:
    return (hostname or "").strip().lower()


def candidates() -> tuple[list, list]:
    """(old, new): labeled devices not seen since migration start, and
    unlabeled devices first seen since migration start."""
    started = migration_started_at()
    if not started:
        raise SystemExit("Migration mode is off. Run `on` first.")
    with conn() as c:
        old = c.execute(
            "SELECT * FROM network_devices WHERE (label IS NOT NULL OR assigned_to IS NOT NULL) "
            "AND last_seen < ? ORDER BY assigned_to, label",
            (started,),
        ).fetchall()
        new = c.execute(
            "SELECT * FROM network_devices WHERE label IS NULL AND assigned_to IS NULL "
            "AND first_seen >= ? ORDER BY first_seen",
            (started,),
        ).fetchall()
    return old, new


def propose() -> list[tuple[str, str]]:
    old, new = candidates()
    groups_old, groups_new = defaultdict(list), defaultdict(list)
    for r in old:
        groups_old[_host_key(r["hostname"])].append(r)
    for r in new:
        groups_new[_host_key(r["hostname"])].append(r)

    unique: list[tuple[str, str]] = []
    waiting = 0
    print(f"{len(old)} labeled device(s) not yet back, {len(new)} new unlabeled MAC(s).\n")
    for key in sorted(set(groups_old) | set(groups_new)):
        o, n = groups_old.get(key, []), groups_new.get(key, [])
        name = key or "(no hostname)"
        if not n:
            waiting += len(o)
            continue
        if len(o) == 1 and len(n) == 1 and key:
            print(f"UNIQUE  {name}: {o[0]['mac']} ({o[0]['label'] or o[0]['assigned_to']}) -> {n[0]['mac']}")
            unique.append((o[0]["mac"], n[0]["mac"]))
            continue
        print(f"AMBIGUOUS  {name}: {len(o)} old, {len(n)} new")
        for r in o:
            print(f"    old {r['mac']}  {r['label'] or ''} / {r['assigned_to'] or ''}  last_seen {r['last_seen']}")
        for r in n:
            print(f"    new {r['mac']}  ip {r['ip']}  first_seen {r['first_seen']}  vendor {r['vendor'] or ''}")
    print(f"\n{waiting} labeled device(s) have no same-hostname newcomer yet (still offline or not rejoined).")
    if unique:
        print("Apply unique matches with: apply-unique --yes   (or `apply OLD NEW` one at a time)")
    return unique


def apply(old_mac: str, new_mac: str) -> None:
    old_mac, new_mac = old_mac.lower(), new_mac.lower()
    with conn() as c:
        old = c.execute("SELECT * FROM network_devices WHERE mac = ?", (old_mac,)).fetchone()
        new = c.execute("SELECT * FROM network_devices WHERE mac = ?", (new_mac,)).fetchone()
        if not old or not new:
            raise SystemExit(f"Unknown MAC: {'old' if not old else 'new'}")
        c.execute(
            "UPDATE network_devices SET label = ?, assigned_to = ?, known = ?, first_seen = MIN(first_seen, ?) "
            "WHERE mac = ?",
            (old["label"], old["assigned_to"], old["known"], old["first_seen"], new_mac),
        )
        moved = c.execute(
            "UPDATE network_sightings SET mac = ? WHERE mac = ?", (new_mac, old_mac)
        ).rowcount
        c.execute("DELETE FROM network_devices WHERE mac = ?", (old_mac,))
    print(f"{old_mac} -> {new_mac}: label={old['label']!r} assigned_to={old['assigned_to']!r}, {moved} sighting(s) moved")


def main(argv: list[str]) -> None:
    init_db()
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        s = migration_started_at()
        print(f"Migration mode: {'ON since ' + s + ' UTC' if s else 'off'}")
    elif cmd == "on":
        migration_on()
    elif cmd == "off":
        migration_off()
    elif cmd == "snapshot":
        print(snapshot())
    elif cmd == "restore" and len(argv) == 2:
        restore(argv[1])
        print("Restored.")
    elif cmd == "propose":
        propose()
    elif cmd == "apply" and len(argv) == 3:
        snapshot()
        apply(argv[1], argv[2])
    elif cmd == "apply-unique":
        if "--yes" not in argv:
            raise SystemExit("Review `propose` first, then re-run with --yes.")
        snapshot()
        for o, n in propose():
            apply(o, n)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
