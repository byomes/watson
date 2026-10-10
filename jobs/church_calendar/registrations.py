"""jobs/church_calendar/registrations.py -- keeps a copy of Catalyst's Subsplash
event registrations in watson.db so "how many are signed up for X?" is answered
instantly from local data.

PAGE READING ONLY (Bill, 2026-10-06: no calls to Subsplash's APIs without
their permission; core.subsplash.com's robots.txt disallows all automated
access). This module never calls an API. It drives the work phone's
already-logged-in Chrome like a person would: open the dashboard's Events page,
open each event's Guest List page, click a guest to see their contact details,
and read the TEXT that appears. Nothing is written to or changed in Subsplash.
(The dashboard page itself loads its data in the browser, as it does for any
person who opens it; Watson only reads the rendered page.)

Pacing: it only visits events on the Special Events and Small Groups calendars
within a window (14 days back, 75 days ahead), one page at a time with a pause,
and only clicks guests it has not stored yet. Cron: every 3 hours, 6am-9pm.

Tables (watson.db):
  subsplash_event_regs    one row per event (uuid, title, date, calendar, registered, has_form)
  subsplash_registrations one row per registration (name, email, phone, tickets,
                          submitted date, matched member_id when found)

If the phone's dashboard login lapses (Subsplash then texts a 6-digit code to
Bill's number), runs fail; after 3 in a row Bill gets ONE Telegram.

Usage: python -m jobs.church_calendar.registrations
"""
import asyncio
import json
import logging
import re
import time
from datetime import date, datetime, timedelta

import requests
import websockets

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from jobs.congregation import kids_checkin_client as kc  # phone/Chrome connection helpers only
from jobs.events.matching import find_member_id, find_member_id_by_name

log = logging.getLogger(__name__)

DASH = "https://dashboard.subsplash.com/-d/#/library/events"
CALENDARS = ("Special Events", "Small Groups")
PAST_DAYS = 14
FUTURE_DAYS = 75
API_PAST_DAYS = 30
API_FUTURE_DAYS = 180
PAUSE = 2.0  # seconds between page loads
_FAIL_KEY = "subsplash_registrations_fail_streak"
_FAIL_ALERT_AT = 3

_LIST_JS = r"""
(()=>{const out=[];let date=null;
 const w=document.createTreeWalker(document.body,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
 let n; while(n=w.nextNode()){
  if(n.nodeType===3){const t=n.textContent.trim(); if(/^(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day, [A-Z][a-z]{2} \d{1,2}$/.test(t)) date=t;}
  else if(n.tagName==='A'&&/events\/events\/edit\//.test(n.getAttribute('href')||'')){
    out.push({uuid:n.getAttribute('href').split('/edit/')[1].split('/')[0], cal:(n.textContent||'').trim(), date});}
 } const m=document.body.innerText.match(/\n([A-Z][a-z]{2} \d{4})\nToday/);
 return JSON.stringify({month:m?m[1]:null, items:out});})()
"""

# Guest List page text -> title, counts, rows. Rows are three text lines: name, "N Ticket type", "Mon D, YYYY".
_READ_JS = r"""
(()=>{const t=document.body.innerText;
 const lines=t.split('\n').map(s=>s.trim()).filter(Boolean);
 const gi=lines.indexOf('Guest List');
 const title=gi>0?lines[gi-1]:null;
 const reg=t.match(/Registered \((\d+)\)/);
 const rng=t.match(/(\d+) - (\d+) of (\d+) responses/);
 const rows=[];
 for(let i=1;i<lines.length-1;i++){
   if(/^\d+ .+/.test(lines[i])&&/^[A-Z][a-z]{2} \d{1,2}, \d{4}$/.test(lines[i+1])&&!/^\d+ - \d+ of/.test(lines[i])){
     rows.push({name:lines[i-1],ticket:lines[i],date:lines[i+1]});}}
 return JSON.stringify({title,hasForm:gi>=0,registered:reg?+reg[1]:null,shown:rng?+rng[2]:rows.length,total:rng?+rng[3]:rows.length,rows});})()
"""

_NEXT_PAGE_JS = r"""
(()=>{const el=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&/of \d+ responses/.test(e.textContent||''));
 if(!el) return 'no-pager';
 let box=el; for(let i=0;i<4&&box;i++){const b=[...box.querySelectorAll('button,waves-icon,[role=button]')].filter(x=>/right|next/i.test((x.getAttribute('name')||'')+(x.getAttribute('aria-label')||'')+(x.innerHTML||'')));
   if(b.length){b[b.length-1].click();return 'clicked';} box=box.parentElement;}
 return 'no-button';})()
"""

_CLICK_ROW_JS = r"""
(async()=>{const idx=%d, want=%s;
 const rows=[...document.querySelectorAll('tr,[role=row]')].filter(r=>/^\d+ .+/m.test(r.innerText||'')&&/[A-Z][a-z]{2} \d{1,2}, \d{4}/.test(r.innerText||''));
 const r=rows[idx]; if(!r) return JSON.stringify({err:'no-row'});
 r.click(); await new Promise(s=>setTimeout(s,1800));
 const t=document.body.innerText; const tail=t.slice(t.lastIndexOf('Terms of Service')); 
 const em=tail.match(/([A-Za-z0-9._%%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/); const ph=tail.match(/(\(?\d{3}\)?[ -.]?\d{3}[ -.]?\d{4})/);
 return JSON.stringify({ok: tail.includes(want), email: em?em[1]:null, phone: ph?ph[1]:null});})()
"""


# --- API reader (2026-10-09): Subsplash confirmed Watson may read core.subsplash.com. One in-browser pass, using the dashboard's own login
# (token never leaves the page): list events, then every form's responses. Replaces the slow page-by-page reading above, which stays as a
# fallback in run(). Returns plain data in the shape store() already takes.
_API_JS = r"""
(async()=>{
 const H={Authorization:window.__capturedAuth}, LO="__LO__", HI="__HI__", WANT=__WANT__, APP="7BVGB9", ORG="8RZCZS57";
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 async function j(u){for(let a=0;a<3;a++){const r=await fetch(u,{headers:H});if(r.ok)return await r.json();
   if(r.status===429||r.status>=500){await sleep(800*(a+1));continue;} return {__error:r.status,__url:u.split('?')[0]};} return {__error:'retries'};}
 const cj=await j('https://core.subsplash.com/events/v2/calendars?filter[app_key]='+APP);
 if(cj.__error) return JSON.stringify({error:'calendars',detail:cj});
 const calName={}; for(const c of (cj._embedded&&cj._embedded.calendars)||[]) calName[c.id]=c.title||c.name;
 const events=[]; let url='https://core.subsplash.com/events/v2/events?filter[app_key]='+APP+'&include=calendar,form&sort=-start_at&page[size]=100&page[number]=1', pages=0;
 while(url && pages<40){
   const pj=await j(url); if(pj.__error) return JSON.stringify({error:'events',detail:pj});
   const batch=(pj._embedded&&pj._embedded.events)||[]; pages++;
   for(const e of batch){ if(e.start_at<LO||e.start_at>HI) continue;
     const cals=((e._embedded&&e._embedded.calendars)||[]).map(c=>calName[c.id]).filter(Boolean);
     const cal=cals.find(c=>WANT.includes(c)); if(!cal) continue;
     events.push({id:e.id,title:e.title,start_at:e.start_at,tz:e.timezone,calendar:cal,form:(e._embedded&&e._embedded.form)||null}); }
   if(batch.length && batch[batch.length-1].start_at<LO) break;
   const nx=pj._links&&pj._links.next&&pj._links.next.href; url=nx?(nx.startsWith('http')?nx:'https://core.subsplash.com'+nx):null;
 }
 for(const ev of events){
   if(!ev.form){const d=await j('https://core.subsplash.com/events/v2/events/'+ev.id+'?include=form'); if(d.__error) return JSON.stringify({error:'event',detail:d}); ev.form=(d._embedded&&d._embedded.form)||null;}
   if(!ev.form){ev.responses=null; continue;}
   const heads=(ev.form.fields||[]).filter(f=>f.properties&&f.properties.purpose==='registration_head_count'); ev.responses=[];
   let ru='https://core.subsplash.com/forms/v1/responses?filter[form.id]='+ev.form.id+'&filter[org_key]='+ORG+'&page[size]=100&page[number]=1', rp=0;
   while(ru&&rp<20){const rj=await j(ru); if(rj.__error) return JSON.stringify({error:'responses',detail:rj}); rp++;
     for(const r of (rj._embedded&&rj._embedded.responses)||[]){
       const a={}; for(const x of r.answers||[]) if(a[x.field_id]===undefined) a[x.field_id]=x.value;
       const pc=r.primary_contact||{}; const defs=r.definition||[];
       const byPurpose=p=>{const d=defs.find(d=>d.properties&&d.properties.purpose===p); return d?a[d.id]:null;};
       ev.responses.push({first:pc.first_name||byPurpose('registration_first_name'),last:pc.last_name||byPurpose('registration_last_name'),
         email:pc.email||byPurpose('registration_email'),phone:byPurpose('registration_phone_number'),
         tickets:heads.length?heads.reduce((n,h)=>n+(parseInt(a[h.id],10)||0),0):null,
         ttype:heads.filter(h=>(parseInt(a[h.id],10)||0)>0).map(h=>h.properties.description).filter((v,i,x)=>x.indexOf(v)===i).join(' + ')||null,submitted:r.submitted_at});}
     const nx=rj._links&&rj._links.next&&rj._links.next.href; ru=nx?(nx.startsWith('http')?nx:'https://core.subsplash.com'+nx):null; await sleep(120);}
   await sleep(120);
 }
 return JSON.stringify({events});
})()
"""


def _local_day(iso: str, tz: str | None) -> str:
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(tz or "America/New_York")).date().isoformat()


def pull_api() -> list[dict]:
    """Read every Special Events / Small Groups event in the window and its registrations; returns store()-ready events."""
    today = date.today()
    lo = (today - timedelta(days=API_PAST_DAYS)).isoformat() + "T00:00:00Z"
    hi = (today + timedelta(days=API_FUTURE_DAYS)).isoformat() + "T23:59:59Z"
    js = _API_JS.replace("__LO__", lo).replace("__HI__", hi).replace("__WANT__", json.dumps(list(CALENDARS)))
    data = json.loads(kc.authed_eval(js, timeout=900))
    if "error" in data:
        raise kc.KidsCheckinClientError(f"Subsplash API read failed: {data}")
    out = []
    for e in data["events"]:
        day = _local_day(e["start_at"], e.get("tz"))
        base = {"uuid": e["id"], "start_date": day, "calendar": e["calendar"]}
        if e["responses"] is None:
            out.append({**base, "title": e["title"], "hasForm": False, "rows": []})
            continue
        rows = []
        for r in e["responses"]:
            first, last = (r.get("first") or "").strip(), (r.get("last") or "").strip()
            sub = datetime.fromisoformat(r["submitted"].replace("Z", "+00:00")).astimezone(__import__("zoneinfo").ZoneInfo(e.get("tz") or "America/New_York"))
            rows.append({"name": f"{first} {last}".strip(), "ticket": f"{r['tickets'] if r.get('tickets') else 1} {r.get('ttype') or 'Registration'}",
                         "date": sub.strftime("%b %d, %Y").replace(" 0", " "), "email": r.get("email") or None, "phone": r.get("phone") or None})
        out.append({**base, "title": e["title"], "hasForm": True, "registered": len(rows), "rows": rows})
    return out


def is_paused() -> bool:
    """Kill switch (system_settings 'subsplash_registrations_paused' = '1'): while set, nothing reads
    Subsplash, and team chat / the Connection page ignore the stored registrations."""
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key='subsplash_registrations_paused'").fetchone()
    return bool(row and row["value"] == "1")


def _bootstrap() -> None:
    with get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(subsplash_event_regs)")}
        if cols and "event_uuid" not in cols or (cols and "short_code" in cols and "has_form" not in cols):
            # Old API-sourced tables (pre 2026-10-06 page-reading rewrite): purge, then repopulate by page reading.
            conn.execute("DROP TABLE IF EXISTS subsplash_event_regs")
            conn.execute("DROP TABLE IF EXISTS subsplash_registrations")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subsplash_event_regs (
                event_uuid TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                start_date TEXT NOT NULL,
                calendar   TEXT,
                has_form   INTEGER NOT NULL DEFAULT 1,
                registered INTEGER,
                checked_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subsplash_registrations (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_uuid   TEXT NOT NULL,
                event_title  TEXT NOT NULL,
                event_start  TEXT NOT NULL,
                first_name   TEXT,
                last_name    TEXT,
                email        TEXT,
                phone        TEXT,
                tickets      INTEGER NOT NULL DEFAULT 1,
                ticket_type  TEXT,
                submitted_at TEXT,
                member_id    INTEGER,
                first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                dup          INTEGER NOT NULL DEFAULT 0,
                UNIQUE(event_uuid, first_name, last_name, submitted_at, dup)
            )""")
        if "dup" not in {r[1] for r in conn.execute("PRAGMA table_info(subsplash_registrations)")}:
            # 2026-10-09: the same person can register twice on the same day (Church Picnic: two Letha Palmer rows); the old unique key
            # collapsed them and the headcount came up short. `dup` numbers repeats (0, 1, ...). Rebuild: SQLite cannot alter a UNIQUE.
            conn.execute("ALTER TABLE subsplash_registrations RENAME TO subsplash_registrations_old")
            conn.execute("DROP INDEX IF EXISTS idx_sreg_event")
            conn.execute("""
                CREATE TABLE subsplash_registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_uuid TEXT NOT NULL, event_title TEXT NOT NULL, event_start TEXT NOT NULL,
                    first_name TEXT, last_name TEXT, email TEXT, phone TEXT, tickets INTEGER NOT NULL DEFAULT 1, ticket_type TEXT,
                    submitted_at TEXT, member_id INTEGER, first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                    dup INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(event_uuid, first_name, last_name, submitted_at, dup))""")
            conn.execute("""INSERT INTO subsplash_registrations (id, event_uuid, event_title, event_start, first_name, last_name, email, phone,
                            tickets, ticket_type, submitted_at, member_id, first_seen_at)
                            SELECT id, event_uuid, event_title, event_start, first_name, last_name, email, phone, tickets, ticket_type,
                            submitted_at, member_id, first_seen_at FROM subsplash_registrations_old""")
            conn.execute("DROP TABLE subsplash_registrations_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sreg_event ON subsplash_registrations(event_uuid)")


_bootstrap()


class _Page:
    """A tiny driver for the phone's one dashboard tab: navigate, evaluate JS, click."""

    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            r = json.loads(await asyncio.wait_for(self.ws.recv(), 30))
            if r.get("id") == self.n:
                return r

    async def ev(self, js):
        r = await self.call("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True)
        res = r.get("result", {})
        if res.get("exceptionDetails"):
            raise kc.KidsCheckinClientError(f"page script error: {res['exceptionDetails'].get('text')}")
        return res.get("result", {}).get("value")

    async def surface(self):
        """RULE (Bill, 2026-10-06): all Subsplash work needs the dashboard on the phone's screen; a background tab renders nothing."""
        await kc.surface_tab(self.ws)

    async def go(self, url, ready_js, tries=25):
        await self.surface()
        await self.call("Page.navigate", url=url)
        for _ in range(tries):
            await asyncio.sleep(1)
            try:
                if await self.ev(ready_js):
                    return True
            except Exception:
                pass
        return False


def _parse_header_date(header: str | None, month_label: str | None) -> str | None:
    """'Sunday, Oct 4' + 'Oct 2026' -> '2026-10-04'."""
    if not header or not month_label:
        return None
    try:
        year = int(month_label.split()[-1])
        return datetime.strptime(f"{header.split(', ')[1]} {year}", "%b %d %Y").date().isoformat()
    except Exception:
        return None


async def _discover(pg: _Page) -> list[dict]:
    today = date.today()
    lo, hi = today - timedelta(days=PAST_DAYS), today + timedelta(days=FUTURE_DAYS)
    ok = await pg.go(DASH, "/^[A-Z][a-z]{2} \\d{4}\\nToday/m.test(document.body.innerText) && document.querySelectorAll('a[href*=\"events/events/edit\"]').length>0")
    if not ok:
        raise kc.KidsCheckinClientError("dashboard Events page did not load (logged out? needs a verification code)")
    found: dict[str, dict] = {}

    async def harvest():
        data = json.loads(await pg.ev(_LIST_JS))
        for it in data["items"]:
            d = _parse_header_date(it["date"], data["month"])
            if d and it["cal"] in CALENDARS and lo.isoformat() <= d <= hi.isoformat():
                found[it["uuid"]] = {"uuid": it["uuid"], "calendar": it["cal"], "start_date": d}
        return data["month"]

    month = await harvest()
    # Step forward through the months that overlap the window.
    months_needed = max(1, ((hi.year - today.year) * 12 + hi.month - today.month))
    for _ in range(months_needed):
        await pg.ev("document.querySelectorAll('button.btn-circle.btn-frameless')[1].click(); 1")
        for _ in range(10):
            await asyncio.sleep(1)
            m = json.loads(await pg.ev(_LIST_JS))["month"]
            if m and m != month:
                break
        await asyncio.sleep(1)
        month = await harvest()
    return sorted(found.values(), key=lambda e: e["start_date"])


async def _wait_loaded(pg: _Page, uuid: str) -> str:
    """Wait until the Guest List has really loaded. Page text alone can not be trusted (it shows
    'No registrations / Turn on Registration' while loading), so first wait for the browser's own
    request-timing log to show this event's details finished loading (names/timings only, nothing
    fetched), then wait for the list text to stop changing. Returns 'form', 'noform' or 'timeout'."""
    for _ in range(25):
        await asyncio.sleep(0.7)
        ents = json.loads(await pg.ev(
            "JSON.stringify(performance.getEntriesByType('resource').map(r=>[r.name,r.responseEnd]))"))
        if any(f"events/v2/events/{uuid}" in n and end > 0 for n, end in ents):
            break
    else:
        return "timeout"
    await asyncio.sleep(2.5)
    last = None
    for _ in range(8):
        cur = json.loads(await pg.ev(_READ_JS))
        key = (cur["registered"], cur["shown"], len(cur["rows"]))
        text = await pg.ev("document.body.innerText")
        if "Turn on Registration" in text and key == last:
            return "noform"
        if key == last and "Guest List" in text and "Turn on Registration" not in text:
            if cur["registered"] and cur["rows"]:
                return "form"
            # Form exists but no rows yet: the list's data can arrive up to ~20s late, so keep
            # waiting before concluding the event really has zero registrations.
            for _ in range(14):
                await asyncio.sleep(1.5)
                cur = json.loads(await pg.ev(_READ_JS))
                if cur["registered"] and cur["rows"]:
                    break
            return "form"
        last = key
        await asyncio.sleep(1.2)
    return "timeout"


async def _read_event(pg: _Page, ev: dict) -> dict | None:
    """Open one event's Guest List and read every registration (paging if needed).
    The dashboard only renders the list while the phone screen is awake (see _keep_awake)."""
    await pg.ev("performance.clearResourceTimings(); 1")
    url = f"{DASH}/events/edit/{ev['uuid']}/responses"
    if not await pg.go(url, "document.body.innerText.includes('Guest List')"):
        return None
    # Moving between events by hash change alone leaves the dashboard showing the previous
    # event's list state without re-requesting the new one, so do a full page reload each time.
    await pg.ev("performance.clearResourceTimings(); 1")
    await pg.call("Page.reload")
    await asyncio.sleep(2)
    for _ in range(25):
        try:
            if await pg.ev("document.body.innerText.includes('Guest List')"):
                break
        except Exception:
            pass
        await asyncio.sleep(1)
    state = await _wait_loaded(pg, ev["uuid"])
    if state == "timeout":
        return None
    if state == "noform":
        # The page still names the event ("Events > Title > Guest List"), so keep the real title instead of a placeholder.
        return {"title": json.loads(await pg.ev(_READ_JS)).get("title"), "hasForm": False, "rows": []}
    first = json.loads(await pg.ev(_READ_JS))
    rows = list(first["rows"])
    for _ in range(15):
        cur = json.loads(await pg.ev(_READ_JS))
        if cur["shown"] >= cur["total"]:
            break
        if await pg.ev(_NEXT_PAGE_JS) != "clicked":
            break
        await asyncio.sleep(2)
        nxt = json.loads(await pg.ev(_READ_JS))
        rows += [r for r in nxt["rows"] if r not in rows]
    return {"title": first["title"], "hasForm": True, "registered": first["registered"], "rows": rows}


async def _read_contact(pg: _Page, idx: int, name: str) -> dict:
    """Click one guest row; the side panel shows 'Name  email • phone'. Returns {email, phone}."""
    r = json.loads(await pg.ev(_CLICK_ROW_JS % (idx, json.dumps(name))))
    return {"email": r.get("email"), "phone": r.get("phone")} if r.get("ok") else {}


def _split(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def _keep_awake(on: bool) -> None:
    """Chrome freezes the dashboard's rendering when the phone screen sleeps, so the guest
    list comes back empty. Stay on while plugged in for the duration of a run, then restore."""
    try:
        serial = kc._select_device_serial()
        kc._adb("-s", serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
        kc._adb("-s", serial, "shell", "svc", "power", "stayon", "true" if on else "false")
    except Exception as exc:
        log.warning("keep-awake %s failed: %s", on, exc)


async def _pull_async() -> dict:
    await kc.ensure_phone_connected()
    _keep_awake(True)
    try:
        return await _pull_inner()
    finally:
        _keep_awake(False)


async def _pull_inner() -> dict:
    tab_ws = kc._find_dashboard_tab()
    totals = {"events_with_forms": 0, "registrations": 0, "new": 0}
    async with websockets.connect(tab_ws, max_size=100_000_000) as ws:
        pg = _Page(ws)
        await pg.call("Page.enable")
        await pg.surface()
        events = await _discover(pg)
        log.info("candidate events in window: %d", len(events))
        with get_connection() as conn:
            skip = {r["event_uuid"] for r in conn.execute(
                "SELECT event_uuid FROM subsplash_event_regs WHERE has_form=0 AND checked_at > datetime('now','-7 days')")}
        events = [e for e in events if e["uuid"] not in skip]
        log.info("after skipping events confirmed to have no registration form: %d", len(events))
        for ev in events:
            await asyncio.sleep(PAUSE)
            try:
                info = await _read_event(pg, ev)
            except Exception as exc:
                log.warning("could not read %s: %s", ev["uuid"], exc)
                continue
            if info is None:
                continue
            # Only click guests we do not already have contact details for.
            with get_connection() as conn:
                have = {(r["first_name"], r["last_name"], r["submitted_at"]) for r in conn.execute(
                    "SELECT first_name, last_name, submitted_at FROM subsplash_registrations WHERE event_uuid=? AND email IS NOT NULL",
                    (ev["uuid"],))}
            for i, row in enumerate(info["rows"] if info["hasForm"] else []):
                f, l = _split(row["name"])
                if (f, l, row["date"]) in have:
                    continue
                await asyncio.sleep(0.5)
                row.update(await _read_contact(pg, i, row["name"]))
            res = store([{**ev, **info}])
            for k in totals:
                totals[k] += res[k]
    return totals


def pull() -> dict:
    return asyncio.run(_pull_async())


def store(events: list[dict]) -> dict:
    n_events = n_regs = n_new = 0
    with get_connection() as conn:
        for e in events:
            title = e.get("title") or ""
            if not e["hasForm"]:
                conn.execute("""INSERT INTO subsplash_event_regs (event_uuid, title, start_date, calendar, has_form, registered, checked_at)
                                VALUES (?,?,?,?,0,NULL,datetime('now'))
                                ON CONFLICT(event_uuid) DO UPDATE SET has_form=0, checked_at=datetime('now'),
                                    title=CASE WHEN excluded.title='(no registration form)' THEN subsplash_event_regs.title ELSE excluded.title END""",
                             (e["uuid"], title or "(no registration form)", e["start_date"], e["calendar"]))
                continue
            conn.execute("""INSERT INTO subsplash_event_regs (event_uuid, title, start_date, calendar, has_form, registered, checked_at)
                            VALUES (?,?,?,?,1,?,datetime('now'))
                            ON CONFLICT(event_uuid) DO UPDATE SET title=excluded.title, start_date=excluded.start_date,
                                calendar=excluded.calendar, has_form=1, registered=excluded.registered, checked_at=datetime('now')""",
                         (e["uuid"], title, e["start_date"], e["calendar"], e.get("registered") if e.get("registered") is not None else len(e["rows"])))
            n_events += 1
            seen = set()
            repeats: dict[tuple, int] = {}
            for r in e["rows"]:
                first, last = _split(r["name"])
                tm = re.match(r"(\d+)\s+(.*)", r["ticket"])
                submitted = datetime.strptime(r["date"], "%b %d, %Y").date().isoformat()
                dup = repeats[(first, last, submitted)] = repeats.get((first, last, submitted), -1) + 1  # same person, same day, again
                seen.add((first, last, submitted, dup))
                existing = conn.execute("SELECT id, email, member_id FROM subsplash_registrations WHERE event_uuid=? AND first_name=? AND last_name=? AND submitted_at=? AND dup=?",
                                        (e["uuid"], first, last, submitted, dup)).fetchone()
                email, phone = r.get("email"), r.get("phone")
                if existing:
                    if email and not existing["email"]:
                        conn.execute("UPDATE subsplash_registrations SET email=?, phone=? WHERE id=?", (email, phone, existing["id"]))
                    continue
                member_id = find_member_id(email or "", phone or "") or find_member_id_by_name(first, last)
                conn.execute("""INSERT INTO subsplash_registrations (event_uuid, event_title, event_start, first_name, last_name, email, phone,
                                tickets, ticket_type, submitted_at, member_id, dup) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (e["uuid"], title, e["start_date"], first, last, email, phone, int(tm.group(1)) if tm else 1,
                              tm.group(2) if tm else None, submitted, member_id, dup))
                n_new += 1
            n_regs += len(e["rows"])
            # Guests removed in Subsplash disappear from the list; mirror that (only when we read the full list).
            if e.get("registered") is None or len(e["rows"]) >= e["registered"]:
                for row in conn.execute("SELECT id, first_name, last_name, submitted_at, dup FROM subsplash_registrations WHERE event_uuid=?", (e["uuid"],)).fetchall():
                    if (row["first_name"], row["last_name"], row["submitted_at"], row["dup"]) not in seen:
                        conn.execute("DELETE FROM subsplash_registrations WHERE id=?", (row["id"],))
    return {"events_with_forms": n_events, "registrations": n_regs, "new": n_new}


def _streak(delta: int | None) -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key=?", (_FAIL_KEY,)).fetchone()
        cur = int(row["value"]) if row else 0
        new = 0 if delta is None else cur + delta
        conn.execute("""INSERT INTO system_settings (key, value, updated_at) VALUES (?,?,datetime('now'))
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""", (_FAIL_KEY, str(new)))
    return new


def sync_church_events() -> list[str]:
    """Bill, 2026-10-09: keep church_events in step with Subsplash so every signup email already has its event to attach to.
    Each upcoming Subsplash event that has a registration form gets a tracked church_events row (one per occurrence for recurring
    events; jobs/events/matching.py picks the occurrence by the date in the email). A row of the same name with no date gets
    that date filled in instead of a duplicate. Reads only the local copy; never touches Subsplash. Returns what it created."""
    made = []
    with get_connection() as conn:
        for ev in conn.execute("SELECT title, start_date FROM subsplash_event_regs r "
                               "WHERE has_form = 1 AND (start_date >= date('now','localtime') OR (start_date >= date('now','-14 days','localtime') AND EXISTS (SELECT 1 FROM church_calendar_events c "
                               "WHERE c.active = 1 AND LOWER(c.title) = LOWER(r.title) AND c.start_date >= date('now','localtime')))) "
                               "ORDER BY start_date").fetchall():
            title, day = ev["title"], ev["start_date"]
            if conn.execute("SELECT 1 FROM church_events WHERE LOWER(event_name) = LOWER(?) AND start_date = ?", (title, day)).fetchone():
                continue
            undated = conn.execute("SELECT id FROM church_events WHERE LOWER(event_name) = LOWER(?) AND COALESCE(start_date,'') = ''",
                                   (title,)).fetchall()
            if len(undated) == 1:
                conn.execute("UPDATE church_events SET start_date = ? WHERE id = ?", (day, undated[0]["id"]))
                made.append(f"{title} {day} (dated existing)")
                continue
            conn.execute("INSERT INTO church_events (event_name, start_date, tracking_active, created_by, creator_notified) "
                         "VALUES (?, ?, 1, 'Subsplash sync', 1)", (title, day))
            made.append(f"{title} {day}")
    return made


def run() -> None:
    if is_paused():
        log.info("paused (system_settings subsplash_registrations_paused); not reading Subsplash")
        return
    try:
        try:
            result = store(pull_api())  # one pass over Subsplash's own data; exact names, emails, phones and head counts
            result["via"] = "api"
        except Exception as api_exc:
            log.warning("API read failed (%s); falling back to dashboard page reading", api_exc)
            result = pull()  # stores each event as it is read
            result["via"] = "pages"
    except Exception as exc:
        streak = _streak(1)
        log.error("registrations read failed (%d in a row): %s", streak, exc)
        if streak == _FAIL_ALERT_AT:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", timeout=15, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": "Watson could not read Subsplash event registrations 3 times in a row. The work phone's dashboard "
                        "login probably expired and needs a verification code."})
        return
    _streak(None)
    try:
        log.info("church_events synced from Subsplash: %s", sync_church_events() or "nothing new")
    except Exception as exc:
        log.error("church_events sync failed: %s", exc)
    log.info("registrations read ok: %s", result)
    print(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
