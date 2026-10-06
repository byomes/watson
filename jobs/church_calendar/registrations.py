"""jobs/church_calendar/registrations.py -- copies every Subsplash event
registration into Watson's own database (watson.db) so questions like "how
many are signed up for Men's Fraternity?" are answered instantly from local
data instead of by driving the dashboard.

How: Subsplash has no public API on this plan, so this reads the same
internal calls the web dashboard makes (core.subsplash.com events/v2 and
forms/v1/responses), from inside the work phone's already-logged-in dashboard
page over Chrome's debug port -- same technique as
jobs/congregation/kids_checkin_client.py (the auth header is captured inside
the page and never brought into Python). Read-only: nothing is written to
Subsplash.

Tables (watson.db):
  subsplash_event_regs    one row per event instance with a registration form
                          (short_code = the "+xxxx" id in church_calendar_events,
                          registered = number of registrations)
  subsplash_registrations one row per registration (response); name/email/phone,
                          tickets, submitted_at, matched member_id when found

Window: events from 60 days ago to 180 days ahead. Cron hourly (see crontab).
If the phone's dashboard login has lapsed (Subsplash texts a 6-digit code on a
fresh login), the pull fails; after 3 failed runs in a row Bill gets ONE Telegram.

Usage: python -m jobs.church_calendar.registrations
"""
import asyncio
import json
import logging
import time

import requests
import websockets

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_connection
from jobs.congregation import kids_checkin_client as kc
from jobs.events.matching import find_member_id, find_member_id_by_name

log = logging.getLogger(__name__)

# Subsplash org key (the forms service scopes responses by it; seen in the dashboard's own calls).
ORG_KEY = "8RZCZS57"
PAST_DAYS = 60
FUTURE_DAYS = 180
_FAIL_KEY = "subsplash_registrations_fail_streak"
_FAIL_ALERT_AT = 3

_PULL_JS = """
(async () => {
  const APP_KEY = "%(app_key)s", ORG_KEY = "%(org_key)s", PAST = %(past)d, FUTURE = %(future)d;
  const h = {Authorization: window.__capturedAuth};
  const g = async u => {
    for (let a = 0; a < 3; a++) {
      try {
        const r = await fetch(u, {headers: h});
        if (r.ok) return await r.json();
        if (r.status !== 429 && r.status < 500) return {__error: r.status};
      } catch (e) {}
      await new Promise(res => setTimeout(res, 500 * (a + 1)));
    }
    return {__error: 'retries_exhausted'};
  };
  const lo = new Date(Date.now() - PAST * 864e5).toISOString();
  const hi = new Date(Date.now() + FUTURE * 864e5).toISOString();
  let events = [];
  for (let page = 1; page <= 15; page++) {
    const j = await g(`https://core.subsplash.com/events/v2/events?filter[app_key]=${APP_KEY}&filter[ical]=false&sort=-start_at&page[size]=100&page[number]=${page}`);
    if (j.__error) return JSON.stringify({error: 'events_list', detail: j.__error});
    const batch = (j._embedded && j._embedded.events) || [];
    events = events.concat(batch);
    if (!(j._links && j._links.next) || (batch.length && batch[batch.length - 1].start_at < lo)) break;
  }
  events = events.filter(e => e.start_at >= lo && e.start_at <= hi);
  const out = [];
  for (const e of events) {
    const form = e._embedded && e._embedded.form;
    const rec = {id: e.id, short_code: e.short_code, title: e.title, start_at: e.start_at,
                 form_id: form ? form.id : null, reg_type: form ? form.registration_type : null,
                 responses: null};
    if (form && form.id) {
      let rows = [], ok = true;
      for (let p = 1; p <= 20; p++) {
        const j = await g(`https://core.subsplash.com/forms/v1/responses?filter[form.id]=${form.id}&filter[org_key]=${ORG_KEY}&page[size]=100&page[number]=${p}&sort=-created_at`);
        if (j.__error) { ok = false; break; }
        const batch = (j._embedded && j._embedded.responses) || [];
        rows = rows.concat(batch.map(r => ({id: r.id, submitted_at: r.submitted_at || r.created_at, answers: r.answers,
          fields: (r.definition || []).map(f => ({id: f.id, purpose: f.properties && f.properties.purpose}))})));
        if (!(j._links && j._links.next)) break;
      }
      rec.responses = ok ? rows : null;
      await new Promise(res => setTimeout(res, 120));
    }
    out.push(rec);
  }
  return JSON.stringify({events: out});
})()
""" % {"app_key": kc.APP_KEY, "org_key": ORG_KEY, "past": PAST_DAYS, "future": FUTURE_DAYS}


def _bootstrap() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subsplash_event_regs (
                short_code TEXT PRIMARY KEY,
                event_uuid TEXT NOT NULL,
                form_id    TEXT,
                title      TEXT NOT NULL,
                start_at   TEXT NOT NULL,
                reg_type   TEXT,
                registered INTEGER,
                checked_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subsplash_registrations (
                response_id  TEXT PRIMARY KEY,
                short_code   TEXT NOT NULL,
                event_title  TEXT NOT NULL,
                event_start  TEXT NOT NULL,
                first_name   TEXT,
                last_name    TEXT,
                email        TEXT,
                phone        TEXT,
                tickets      INTEGER,
                submitted_at TEXT,
                member_id    INTEGER,
                extra_json   TEXT,
                first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sreg_event ON subsplash_registrations(short_code)")


_bootstrap()


async def _pull_async() -> dict:
    await kc.ensure_phone_connected()
    tab_ws = kc._find_dashboard_tab()
    async with websockets.connect(tab_ws, max_size=100_000_000) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Page.enable"})); await ws.recv()
        await ws.send(json.dumps({"id": 2, "method": "Page.addScriptToEvaluateOnNewDocument",
                                  "params": {"source": kc._PATCH_JS}})); await ws.recv()
        await ws.send(json.dumps({"id": 3, "method": "Page.reload"}))
        ready = False
        end = time.time() + 20
        while time.time() < end:
            try:
                if await kc._ws_eval(ws, kc._CHECK_JS, timeout=3) == "ready":
                    ready = True
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
        if not ready:
            raise kc.KidsCheckinClientError("no dashboard auth captured (phone Chrome logged out? needs a verification code)")
        await asyncio.sleep(3)
        return json.loads(await kc._ws_eval(ws, _PULL_JS, await_promise=True, timeout=300))


def pull() -> dict:
    return asyncio.run(_pull_async())


def _answer(answers: list[dict], purpose_field: dict[str, str], purpose: str) -> str | None:
    fid = purpose_field.get(purpose)
    for a in answers or []:
        if a.get("field_id") == fid and a.get("value") not in (None, ""):
            return str(a["value"]).strip()
    return None


def store(data: dict) -> dict:
    """Upsert a pull into watson.db. Returns counts."""
    n_events = n_regs = n_new = 0
    with get_connection() as conn:
        for e in data.get("events", []):
            if not e.get("form_id"):
                continue
            responses = e.get("responses")
            conn.execute("""
                INSERT INTO subsplash_event_regs (short_code, event_uuid, form_id, title, start_at, reg_type, registered, checked_at)
                VALUES (?,?,?,?,?,?,?,datetime('now'))
                ON CONFLICT(short_code) DO UPDATE SET event_uuid=excluded.event_uuid, form_id=excluded.form_id,
                    title=excluded.title, start_at=excluded.start_at, reg_type=excluded.reg_type,
                    registered=COALESCE(excluded.registered, subsplash_event_regs.registered),
                    checked_at=CASE WHEN excluded.registered IS NULL THEN subsplash_event_regs.checked_at ELSE excluded.checked_at END
            """, (e["short_code"], e["id"], e["form_id"], e["title"], e["start_at"], e.get("reg_type"),
                  len(responses) if responses is not None else None))
            n_events += 1
            if responses is None:
                continue
            seen_ids = set()
            for r in responses:
                # Each response carries the form definition it was submitted against.
                pf = {f["purpose"]: f["id"] for f in r.get("fields", []) if f.get("purpose")}
                ans = r.get("answers") or []
                first = _answer(ans, pf, "registration_first_name")
                last = _answer(ans, pf, "registration_last_name")
                email = _answer(ans, pf, "registration_email")
                phone = _answer(ans, pf, "registration_phone_number")
                tix = _answer(ans, pf, "registration_head_count")
                known = set(pf.values())
                extra = {a["field_id"]: a.get("value") for a in ans if a.get("field_id") not in known and a.get("value") not in (None, "")}
                member_id = find_member_id(email or "", phone or "") or find_member_id_by_name(first or "", last or "")
                seen_ids.add(r["id"])
                cur = conn.execute("SELECT 1 FROM subsplash_registrations WHERE response_id=?", (r["id"],)).fetchone()
                if not cur:
                    n_new += 1
                conn.execute("""
                    INSERT INTO subsplash_registrations (response_id, short_code, event_title, event_start, first_name, last_name,
                        email, phone, tickets, submitted_at, member_id, extra_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(response_id) DO UPDATE SET first_name=excluded.first_name, last_name=excluded.last_name,
                        email=excluded.email, phone=excluded.phone, tickets=excluded.tickets, member_id=excluded.member_id,
                        extra_json=excluded.extra_json, event_title=excluded.event_title, event_start=excluded.event_start
                """, (r["id"], e["short_code"], e["title"], e["start_at"], first, last, email, phone,
                      int(tix) if tix and tix.isdigit() else 1, r.get("submitted_at"), member_id,
                      json.dumps(extra) if extra else None))
                n_regs += 1
            # Registrations cancelled/removed in Subsplash disappear from the list; mirror that.
            gone = [x[0] for x in conn.execute("SELECT response_id FROM subsplash_registrations WHERE short_code=?", (e["short_code"],))
                    if x[0] not in seen_ids]
            for rid in gone:
                conn.execute("DELETE FROM subsplash_registrations WHERE response_id=?", (rid,))
    return {"events_with_forms": n_events, "registrations": n_regs, "new": n_new}


def _streak(delta: int | None) -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key=?", (_FAIL_KEY,)).fetchone()
        cur = int(row["value"]) if row else 0
        new = 0 if delta is None else cur + delta
        conn.execute("""INSERT INTO system_settings (key, value, updated_at) VALUES (?,?,datetime('now'))
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""", (_FAIL_KEY, str(new)))
    return new


def run() -> None:
    try:
        result = store(pull())
    except Exception as exc:
        streak = _streak(1)
        log.error("registrations pull failed (%d in a row): %s", streak, exc)
        if streak == _FAIL_ALERT_AT:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", timeout=15, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": "Watson could not read Subsplash event registrations 3 times in a row. The work phone's dashboard "
                        "login probably expired and needs a verification code. - Watson"})
        return
    _streak(None)
    log.info("registrations pull ok: %s", result)
    print(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
