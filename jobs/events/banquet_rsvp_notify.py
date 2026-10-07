"""jobs/events/banquet_rsvp_notify.py -- Telegram notification when someone
RSVPs to the Servant Leaders Banquet (Bill's 2026-10-07 request).

Recipients: Dr. Bill (seating) and Bill Crook (food). Each message gives
the respondent, their guest count, childcare count if any, and updated
running totals. Gated by system_settings `banquet_rsvp_notify_live`
('on' to go live; off/absent = nothing is sent from the intake trigger).

Quiet hours: per Bill's standing rule, nothing goes to anyone but Dr. Bill
after 8pm. Dr. Bill's copy is always immediate. Bill Crook's copy is sent
only 9am-8pm local; outside that window the RSVP is queued in
`banquet_rsvp_notify_queue` and `flush_queue()` (cron, 9am daily) sends
one summary with the then-current totals.
"""
import logging
import sqlite3
from datetime import datetime

from config.settings import DB_PATH
from jobs.telegram.send_to_person import send_to_person

log = logging.getLogger(__name__)

DR_BILL_ID = 7
BILL_CROOK_ID = 78
WINDOW_START_HOUR, WINDOW_END_HOUR = 9, 20
SIGNOFF = " - Watson"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def is_live() -> bool:
    with _conn() as c:
        r = c.execute("SELECT value FROM system_settings WHERE key = 'banquet_rsvp_notify_live'").fetchone()
    return bool(r) and str(r["value"]).lower() == "on"


def _in_window(now: datetime | None = None) -> bool:
    h = (now or datetime.now()).hour
    return WINDOW_START_HOUR <= h < WINDOW_END_HOUR


def sentence(name: str, attending: bool, guests: int, kids: int, updated: bool = False) -> str:
    """Plain-English one-liner, e.g. "Bob Jones RSVPd 4 guests and 2 kids for the SLB."
    Guests counts everyone attending in that RSVP, the respondent included."""
    if not attending:
        return f"{name} will not be attending the SLB." if not updated else f"{name} changed their RSVP: will not be attending the SLB."
    g = f"{guests} guest{'' if guests == 1 else 's'}"
    k = f" and {kids} kid{'' if kids == 1 else 's'}" if kids else ""
    return f"{name} " + ("updated their RSVP to " if updated else "RSVPd ") + f"{g}{k} for the SLB."


def _totals_block(totals: dict) -> str:
    return (
        f"Totals so far: {totals['guests']} guests and {totals['childcare']} kids for childcare. "
        f"{totals['responded']} of {totals['invited']} invited have responded "
        f"({totals['yes']} yes, {totals['no']} no)."
    )


def format_message(name: str, attending: bool, party: list[str], child_count: int,
                   totals: dict, updated: bool = False, test: bool = False) -> str:
    text = sentence(name, attending, len(party), child_count, updated) + "\n\n" + _totals_block(totals) + "\n" + SIGNOFF.strip()
    return ("TEST: " + text) if test else text


def current_totals(event_id: int) -> dict:
    from jobs.congregation.banquet_report import rsvp_status_report
    r = rsvp_status_report(event_id)
    return {
        "guests": r["total_adults"], "childcare": r["total_children"],
        "yes": len(r["yes"]), "no": len(r["no"]),
        "responded": len(r["yes"]) + len(r["no"]), "invited": r["invited_count"],
    }


def _ensure_queue(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS banquet_rsvp_notify_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
            summary TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now','localtime')))"""
    )


def notify_new_rsvp(event_id: int, detection: dict, updated: bool) -> None:
    """Called by the intake handler after an RSVP is recorded. Never raises."""
    try:
        if not is_live():
            return
        first = (detection.get("first_name") or "").strip()
        last = (detection.get("last_name") or "").strip()
        name = f"{first} {last}".strip() or "Someone"
        attending = bool(detection.get("attending"))
        party = [name] + [
            f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip()
            for p in (detection.get("additional_attendees") or []) if isinstance(p, dict)
        ]
        kids = len([c for c in (detection.get("children") or []) if str(c).strip()])
        msg = format_message(name, attending, party, kids, current_totals(event_id), updated)
        send_to_person(DR_BILL_ID, msg)
        if _in_window():
            send_to_person(BILL_CROOK_ID, msg)
        else:
            with _conn() as c:
                _ensure_queue(c)
                c.execute("INSERT INTO banquet_rsvp_notify_queue (event_id, summary) VALUES (?, ?)",
                          (event_id, sentence(name, attending, len(party), kids, updated)))
    except Exception:
        log.exception("banquet RSVP notification failed (RSVP itself was recorded)")


def flush_queue() -> int:
    """9am cron: send Bill Crook one summary of RSVPs that came in overnight."""
    if not is_live() or not _in_window():
        return 0
    with _conn() as c:
        _ensure_queue(c)
        rows = c.execute("SELECT id, event_id, summary FROM banquet_rsvp_notify_queue ORDER BY id").fetchall()
        if not rows:
            return 0
        totals = current_totals(rows[0]["event_id"])
        body = "SLB RSVPs since yesterday:\n" + "\n".join(r["summary"] for r in rows) + "\n\n" + _totals_block(totals) + "\n" + SIGNOFF.strip()
        if send_to_person(BILL_CROOK_ID, body):
            c.execute("DELETE FROM banquet_rsvp_notify_queue")
            return len(rows)
    return 0


if __name__ == "__main__":
    print(flush_queue())
