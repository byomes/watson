"""jobs/congregation/fluro_schedule.py -- keeps a copy of the upcoming Sunday
volunteer schedule (who is rostered to serve, on which team and role) in
watson.db.

SCOPE (Bill, 2026-10-09): Fluro is authorized for VOLUNTEER SCHEDULING DATA
ONLY. Anything else (contacts, giving, people records, the Watson-SMS list)
needs a fresh decision from Bill, so this module deliberately:
  - calls only POST /content/event/filter (event ids for a date window) and
    GET /content/get/<id>?type=event&appendAssignments=all (the roster);
  - never asks for appendContactDetail, so no emails/phones come back;
  - keeps only team, role, slot min/max, assignment id/status/confirmation,
    the volunteer's display name and Fluro contact id;
  - reads the session token inside the page (never returned to Python, never
    logged or stored), and is read-only (no writes to Fluro).
fluro_client.py and its ApiAccessDisabled guard are left untouched on purpose.

Transport: same as the other Subsplash readers -- the work phone's real
Chrome session via adb/CDP (kids_checkin_client helpers). A NEW tab is opened
on fluro.subsplash.com, brought to the front (hidden tabs render nothing),
used once, and closed. If the Fluro login has lapsed the run fails and
nothing is changed.

Tables (watson.db):
  fluro_schedule_events  one row per event with a roster (id, title, start/end UTC)
  fluro_schedule_slots   one row per team role (min/max, filled count)
  fluro_schedule         one row per assignment (team, role, name, confirmation, member_id)

Each run replaces the stored schedule for the window it read.

Usage: python -m jobs.congregation.fluro_schedule [--days 35] [--show]
"""
import argparse
import asyncio
import json
import logging
import time
from datetime import date, timedelta

import requests
import websockets

from core.database import get_connection
from jobs.congregation import kids_checkin_client as kc  # phone/Chrome connection helpers only
from jobs.events.matching import find_member_id_by_name

log = logging.getLogger(__name__)

FLURO_ORIGIN = "https://fluro.subsplash.com"
DEFAULT_DAYS = 35

_PULL_JS = r"""
(async () => {
  const u = JSON.parse(localStorage.getItem('_fluro_user') || 'null');
  if (!u || !u.token) return JSON.stringify({error: 'not logged in'});
  const H = {Authorization: 'Bearer ' + u.token, Accept: 'application/json'};
  const body = {
    sort: {sortKey: 'startDate', sortDirection: 'asc', sortType: 'date'},
    filter: {operator: 'and', filters: [{operator: 'and', filters: [{key: 'status', comparator: 'in', values: ['active']}]}]},
    search: '', includeArchived: false, allDefinitions: true, searchInheritable: false, includeUnmatched: true,
    limit: 200, startDate: '__LO__', endDate: '__HI__', timezone: 'America/New_York'
  };
  const fr = await fetch('https://api.fluro.io/content/event/filter', {method: 'POST', headers: {...H, 'Content-Type': 'application/json;charset=UTF-8'}, body: JSON.stringify(body)});
  if (!fr.ok) return JSON.stringify({error: 'filter ' + fr.status});
  const found = await fr.json();
  const ids = found.map(x => (typeof x === 'string' ? x : x._id));
  const events = [];
  for (const id of ids) {
    let r = null;
    for (let a = 0; a < 3; a++) {
      r = await fetch('https://api.fluro.io/content/get/' + id + '?type=event&appendAssignments=all', {headers: H});
      if (r.ok || (r.status < 500 && r.status !== 429)) break;
      await new Promise(s => setTimeout(s, 500 * (a + 1)));
    }
    if (!r.ok) { events.push({id, error: r.status}); continue; }
    const e = await r.json();
    const teams = (e.rostered || []).map(t => ({
      title: t.title, definition: t.definition,
      slots: (t.slots || []).map(s => ({
        title: s.title, minimum: s.minimum, maximum: s.maximum,
        assignments: (s.assignments || []).map(a => ({
          id: a._id, status: a.status, confirmation: a.confirmationStatus,
          name: a.contactName || (a.contact && a.contact.title) || '',
          first: a.contact && a.contact.firstName, last: a.contact && a.contact.lastName,
          contact_id: a.contact && a.contact._id
        }))
      }))
    }));
    events.push({id, title: e.title, start: e.startDate, end: e.endDate, teams});
    await new Promise(s => setTimeout(s, 150));
  }
  return JSON.stringify({events});
})()
"""


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fluro_schedule_events (
                event_id   TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                start_utc  TEXT NOT NULL,
                end_utc    TEXT,
                pulled_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fluro_schedule_slots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id   TEXT NOT NULL,
                team       TEXT NOT NULL,
                role       TEXT NOT NULL,
                minimum    INTEGER,
                maximum    INTEGER,
                filled     INTEGER NOT NULL DEFAULT 0
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fluro_schedule (
                assignment_id   TEXT PRIMARY KEY,
                event_id        TEXT NOT NULL,
                team            TEXT NOT NULL,
                team_definition TEXT,
                role            TEXT NOT NULL,
                volunteer_name  TEXT NOT NULL,
                fluro_contact_id TEXT,
                confirmation    TEXT,
                member_id       INTEGER
            )""")
        # Manual links (a Fluro contact -> the right members row) for names the matcher can't settle. Checked before any name matching.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fluro_member_links (
                fluro_contact_id TEXT PRIMARY KEY,
                member_id        INTEGER NOT NULL,
                note             TEXT,
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fluro_schedule_event ON fluro_schedule(event_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fluro_schedule_slots_event ON fluro_schedule_slots(event_id)")


async def _read_schedule(lo: str, hi: str) -> dict:
    """Open a fresh Fluro tab on the phone, run the read in-page, close the tab."""
    await kc.ensure_phone_connected()
    ver = requests.get(f"http://localhost:{kc.CDP_LOCAL_PORT}/json/version", timeout=10).json()
    async with websockets.connect(ver["webSocketDebuggerUrl"], max_size=50_000_000) as bws:
        await bws.send(json.dumps({"id": 1, "method": "Target.createTarget",
                                   "params": {"url": f"{FLURO_ORIGIN}/events/list"}}))
        while True:
            d = json.loads(await bws.recv())
            if d.get("id") == 1:
                tid = d["result"]["targetId"]
                break
    try:
        ws_url = None
        for _ in range(10):
            await asyncio.sleep(1)
            ws_url = next((t["webSocketDebuggerUrl"] for t in kc._list_targets() if t.get("id") == tid), None)
            if ws_url:
                break
        if not ws_url:
            raise kc.KidsCheckinClientError("opened a Fluro tab but could not find its debugger target")
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            await kc.surface_tab(ws)
            for _ in range(20):  # let the SPA finish loading and write its session
                if await kc._ws_eval(ws, "!!localStorage.getItem('_fluro_user')"):
                    break
                await asyncio.sleep(1)
            js = _PULL_JS.replace("__LO__", lo).replace("__HI__", hi)
            return json.loads(await kc._ws_eval(ws, js, await_promise=True, timeout=600))
    finally:
        try:
            requests.get(f"http://localhost:{kc.CDP_LOCAL_PORT}/json/close/{tid}", timeout=10)
        except Exception:
            pass


def pull(days: int = DEFAULT_DAYS) -> list[dict]:
    """Returns the rostered events in [today, today+days]; raises on any read failure."""
    today = date.today()
    lo = (today - timedelta(days=1)).isoformat() + "T04:00:00.000Z"
    hi = (today + timedelta(days=days)).isoformat() + "T04:59:59.999Z"
    data = asyncio.run(_read_schedule(lo, hi))
    if "error" in data:
        raise kc.KidsCheckinClientError(f"Fluro schedule read failed: {data['error']}")
    failed = [e for e in data["events"] if "error" in e]
    if failed:
        raise kc.KidsCheckinClientError(f"Fluro schedule read incomplete: {len(failed)} event(s) errored; nothing stored")
    return [e for e in data["events"] if any(s["assignments"] or s["title"] for t in e["teams"] for s in t["slots"])]


def store(events: list[dict]) -> dict:
    """Replace the stored schedule for the pulled window with `events`."""
    _bootstrap()
    n_assign = 0
    with get_connection() as conn:
        # Window is the whole upcoming set: drop anything not in this pull that is not yet past.
        keep = [e["id"] for e in events]
        cutoff = (date.today() - timedelta(days=1)).isoformat()
        stale = [r[0] for r in conn.execute("SELECT event_id FROM fluro_schedule_events WHERE substr(start_utc,1,10) >= ?", (cutoff,))
                 if r[0] not in keep]
        for eid in set(keep) | set(stale):
            conn.execute("DELETE FROM fluro_schedule WHERE event_id=?", (eid,))
            conn.execute("DELETE FROM fluro_schedule_slots WHERE event_id=?", (eid,))
            conn.execute("DELETE FROM fluro_schedule_events WHERE event_id=?", (eid,))
        for e in events:
            conn.execute("INSERT INTO fluro_schedule_events (event_id, title, start_utc, end_utc) VALUES (?,?,?,?)",
                         (e["id"], e["title"], e["start"], e.get("end")))
            for t in e["teams"]:
                for s in t["slots"]:
                    active = [a for a in s["assignments"] if a.get("status") == "active"]
                    conn.execute("INSERT INTO fluro_schedule_slots (event_id, team, role, minimum, maximum, filled) VALUES (?,?,?,?,?,?)",
                                 (e["id"], t["title"], s["title"], s.get("minimum"), s.get("maximum"), len(active)))
                    for a in active:
                        name = (a.get("name") or "").strip()
                        if not name:
                            continue
                        link = conn.execute("SELECT member_id FROM fluro_member_links WHERE fluro_contact_id = ?",
                                            (a.get("contact_id"),)).fetchone()
                        member_id = link[0] if link else find_member_id_by_name(a.get("first") or "", a.get("last") or "")
                        conn.execute("""INSERT OR REPLACE INTO fluro_schedule
                            (assignment_id, event_id, team, team_definition, role, volunteer_name, fluro_contact_id, confirmation, member_id)
                            VALUES (?,?,?,?,?,?,?,?,?)""",
                                     (a["id"], e["id"], t["title"], t.get("definition"), s["title"], name,
                                      a.get("contact_id"), a.get("confirmation"), member_id))
                        n_assign += 1
    return {"events": len(events), "assignments": n_assign, "dropped_stale": len(stale)}


def show() -> None:
    _bootstrap()
    with get_connection() as conn:
        for ev in conn.execute("SELECT event_id, title, start_utc FROM fluro_schedule_events ORDER BY start_utc"):
            print(f"\n{ev['start_utc'][:16]}Z  {ev['title']}")
            for r in conn.execute("""SELECT team, role, minimum, maximum, filled FROM fluro_schedule_slots
                                     WHERE event_id=? ORDER BY id""", (ev["event_id"],)):
                print(f"   {r['team']} / {r['role']}: {r['filled']}/{r['maximum']}")


def run(days: int = DEFAULT_DAYS) -> dict:
    t0 = time.time()
    summary = store(pull(days))
    summary["seconds"] = round(time.time() - t0, 1)
    log.info("fluro_schedule: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--show", action="store_true", help="print the stored schedule instead of pulling")
    args = ap.parse_args()
    if args.show:
        show()
    else:
        print(run(args.days))
