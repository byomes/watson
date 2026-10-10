"""jobs/analytics/serving_schedule.py -- LLM-free team-chat answer to "who is serving this
coming Sunday?" / "who is serving this week?", from the volunteer schedule that
jobs/congregation/fluro_schedule.py keeps in watson.db (fluro_schedule* tables).

Entry point: answer(question) -> str | None. None means "not a who-is-serving question",
and data_chat carries on with its other routes.

Needs BOTH a serving word (serving, serves, volunteering, scheduled, rostered, on the
schedule) AND a Sunday/week reference, so ordinary questions that merely contain "serve"
or "Sunday" (attendance, "how long has X served", sermon questions) fall through.
Past-tense and "last/past" wording never matches. Celebrate Recovery is never named.
Names only: no contact details are stored or shown.
"""
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config.settings import DB_PATH

_TZ = ZoneInfo("America/New_York")

_SERVING_RE = re.compile(
    r"\b(serving|serves?\s+(?:this|on|next|the|sunday)|volunteer(?:s|ing)?|scheduled|rostered|on the (?:schedule|roster)|service positions?)\b", re.I)
_SHAPE_RE = re.compile(r"\b(who|whos|who's|list|show|tell me|give me|what)\b", re.I)
_WHEN_RE = re.compile(r"\b(sunday|this week|next week|this weekend|coming week|upcoming|today|tomorrow)\b", re.I)
_PAST_RE = re.compile(r"\b(last|past|previous|served|was serving|were serving|yesterday|ago)\b", re.I)
_NEXT_RE = re.compile(r"\bnext\s+(?:sunday|week)\b", re.I)
_STALE_DAYS = 2


def _target_date(question: str, today: date | None = None) -> date:
    """The Sunday the question means. 'this week' / 'coming' / plain 'Sunday' = the next Sunday on or after today;
    'next Sunday' / 'next week' = the one after that."""
    today = today or datetime.now(_TZ).date()
    sunday = today + timedelta(days=(6 - today.weekday()) % 7)
    if _NEXT_RE.search(question):
        sunday += timedelta(days=7)
    return sunday


def _local_day(start_utc: str) -> date:
    return datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone(_TZ).date()


def _local_time(start_utc: str) -> str:
    t = datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone(_TZ)
    return t.strftime("%I:%M %p").lstrip("0")


def _fmt_day(d: date) -> str:
    return f"{d.strftime('%A, %B')} {d.day}"


def _is_question(question: str) -> bool:
    return bool(_SERVING_RE.search(question) and _SHAPE_RE.search(question) and _WHEN_RE.search(question)
                and not _PAST_RE.search(question))


def answer(question: str, today: date | None = None) -> str | None:
    if not _is_question(question):
        return None
    day = _target_date(question, today)
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5) as c:
        c.row_factory = sqlite3.Row
        try:
            events = [e for e in c.execute("SELECT event_id, title, start_utc, pulled_at FROM fluro_schedule_events ORDER BY start_utc")
                      if _local_day(e["start_utc"]) == day]
        except sqlite3.OperationalError:
            return None  # table not created yet: nothing to answer from
        if not events:
            return f"I don't have a volunteer schedule for {_fmt_day(day)} yet. It may not be built in Fluro."
        blocks, unconfirmed, total = [], 0, 0
        for e in events:
            slots = c.execute("SELECT id, team, role, minimum, maximum, filled FROM fluro_schedule_slots WHERE event_id=? ORDER BY id",
                              (e["event_id"],)).fetchall()
            people = c.execute("SELECT team, role, volunteer_name, confirmation FROM fluro_schedule WHERE event_id=? ORDER BY rowid",
                               (e["event_id"],)).fetchall()
            by_role: dict[tuple, list] = {}
            for p in people:
                by_role.setdefault((p["team"], p["role"]), []).append(p)
            lines, team = [], None
            for s in slots:
                if "recovery" in (s["team"] or "").lower():
                    continue
                if s["team"] != team:
                    team = s["team"]
                    lines.append(f"\n{team}")
                who = by_role.get((s["team"], s["role"]), [])
                total += len(who)
                unconfirmed += sum(1 for p in who if p["confirmation"] != "confirmed")
                names = ", ".join(p["volunteer_name"] for p in who)
                short = max((s["minimum"] or 0) - len(who), 0)
                if not who:
                    names = "open"
                elif short:
                    names += f" (needs {short} more)"
                lines.append(f"  {s['role']}: {names}")
            blocks.append(f"{e['title']}, {_local_time(e['start_utc'])}" + "".join(f"\n{ln}" if ln.startswith("  ") else ln for ln in lines))
        out = f"Serving {_fmt_day(day)}:\n\n" + "\n\n".join(blocks)
        if total:
            out += f"\n\n{total - unconfirmed} of {total} confirmed."
        try:
            pulled = datetime.strptime(events[0]["pulled_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - pulled > timedelta(days=_STALE_DAYS):
                out += f" (Schedule last refreshed {pulled.astimezone(_TZ).strftime('%b')} {pulled.astimezone(_TZ).day}.)"
        except Exception:
            pass
        return out
