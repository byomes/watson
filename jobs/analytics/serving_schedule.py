"""jobs/analytics/serving_schedule.py -- LLM-free team-chat answers about the volunteer
schedule ("who is serving this coming Sunday?"), from the tables that
jobs/congregation/fluro_schedule.py keeps in watson.db (fluro_schedule*).

Entry point: answer(question) -> str | None. None means "not a who-is-serving question",
and data_chat carries on with its other routes.

A question is recognised from three independent pieces, so many phrasings work:
  WHEN   a Sunday/week/date reference (this coming Sunday, next week, tomorrow, 10/18, in two weeks,
         last Sunday for a look back). Weekday names other than Sunday work for rostered weekday events.
  INTENT a serving word (serving, volunteering, who's on the schedule/roster/lineup, who's working...)
         OR a role/team word asked with "who" (who's running sound, who is greeting).
  SHAPE  who / list / show / what / which / how many / any / do we need ...
Weak serving words (scheduled, working, helping, covering, "who's on") also need "who", so
"what's scheduled this Sunday" (the event calendar) and "is the office working Sunday" fall through.
Past wording without a clear look-back date ("how long has Pat served") falls through.

Extras: a team/role filter ("who is on the worship team Sunday", "who's running sound"),
an open-positions view ("what positions are open Sunday?", "do we need volunteers"),
and a service filter ("pre-service", "the 10am service").

Celebrate Recovery is never named. Names only: no contact details are stored or shown.
"""
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config.settings import DB_PATH

_TZ = ZoneInfo("America/New_York")
_STALE_DAYS = 2

_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "a": 1, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# ---- INTENT -------------------------------------------------------------------------------------------------------
_STRONG_RE = re.compile(
    r"\b(serving|servin|volunteer(?:s|ing|ed)?|roster(?:ed|s)?|rota|line-?ups?|serving\s+(?:team|schedule|list)|service\s+(?:team|positions?|schedule)"
    r"|serves?\s+(?:this|on|next|the|sunday|today|tomorrow)|(?:on|from|in|off)\s+(?:the\s+)?(?:schedule|roster|rota|line-?up|serving\s+list))\b", re.I)
_WEAK_RE = re.compile(
    r"\b(scheduled|working|helping|helps|covering|doing|on\s+duty|on\s+deck|on\s+for|on\s+(?:this|next|the|sunday|today|tomorrow)|has\s+(?:this|next|the|sunday))\b", re.I)
_WHO_RE = re.compile(r"\b(who|whos|who's|who’s|whom|whose|anyone|anybody|everyone|everybody)\b", re.I)
_SHAPE_RE = re.compile(r"\b(list|show|tell\s+me|give\s+me|what|which|how\s+many|any|is\s+there|are\s+there|do\s+we|did\s+we|have\s+we|need|lineup|line-up)\b", re.I)
_OPEN_RE = re.compile(r"\b(open|opening|openings|unfilled|vacan\w*|empty|missing|short|understaffed|uncovered|need(?:s|ed)?|still\s+need|to\s+fill|fill(?:ed)?|gaps?)\b", re.I)
_OPEN_CTX_RE = re.compile(r"\b(position|positions|spot|spots|slot|slots|role|roles|volunteer|volunteers|serving|serve|help|people|anyone|someone|openings?|staff\w*)\b", re.I)

# team/role words -> substrings of "<team> <role>" (lowercase). Order matters: first matching entries all apply.
_ROLE_WORDS = [
    (r"worship\s+leader|leading\s+worship|lead(?:s)?\s+worship", ["worship leader"]),
    (r"worship\s+team|worship|music|musicians?|band", ["worship team"]),
    (r"sound|audio|soundboard|mixing|mixer|\bpa\b", ["sound tech"]),
    (r"\btech\b|av\s+team|a/v", ["sound tech", "camera operator", "slides"]),
    (r"camera|video|filming", ["camera"]),
    (r"slides?|projection|projector|propresenter|lyrics", ["slides"]),
    (r"sing(?:ers?|ing)?|vocal(?:s|ists?)?", ["vocalist"]),
    (r"piano|keys|keyboard", ["piano"]),
    (r"guitar\w*", ["guitarist"]),
    (r"synth\w*", ["synth"]),
    (r"count(?:ers?|ing)|offering|money\s+counters?", ["counting team"]),
    (r"hospitality|welcome\s+team", ["hospitality"]),
    (r"ushers?|ushering", ["usher"]),
    (r"greet\w*", ["greeter"]),
    (r"coffee|bar\b|barista", ["coffee bar"]),
    (r"parking|lot\s+attendant", ["parking"]),
    (r"check-?\s?in", ["check-in attendant"]),
    (r"security|safety|lock-?\s?up|locking\s+up", ["security", "lock-up"]),
    (r"nursery|babies|baby", ["nursery"]),
    (r"toddlers?", ["toddler"]),
    (r"kids?|children|childcare|child\s+care|kidmin|elementary|sunday\s+school|small\s+group\s+childcare", ["nursery", "toddler", "elementary", "childcare"]),
]
_ROLE_RES = [(re.compile(r"\b(?:" + p + r")", re.I), needles) for p, needles in _ROLE_WORDS]

# ---- WHEN ---------------------------------------------------------------------------------------------------------
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_NUM_DATE_RE = re.compile(r"(?<![\d/])(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?(?![\d/:])")
_MONTH_DATE_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.I)
_DAY_MONTH_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_WEEKDAY_RE = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|sundays|mon|tues?|wed|thurs?|fri|sat|sun)\b", re.I)
_WHEN_WORDS_RE = re.compile(
    r"\b(sundays?|this\s+week|next\s+week|this\s+weekend|the\s+weekend|weekend|coming\s+week|upcoming|today|tomorrow|tonight|"
    r"this\s+coming|week\s+after|following\s+week|in\s+\w+\s+weeks?|\w+\s+weeks?\s+from\s+now|after\s+next|church\s+this|service)\b", re.I)
_PAST_RE = re.compile(r"\b(last|past|previous|prior|yesterday|ago|served|did\s+serve|was\s+serving|were\s+serving|worked)\b", re.I)
_LOOKBACK_RE = re.compile(r"\b(last|past|previous|prior)\s+(?:sunday|week|weekend)|\bsunday\s+before\b|\byesterday\b|\bweek\s+ago\b|\b(?:this|the)\s+past\s+sunday\b", re.I)
_NEXT_RE = re.compile(r"\bnext\s+(?:sunday|week|weekend)\b|\bthe\s+(?:following|sunday\s+after)\b|\bweek\s+after\b|\bfollowing\s+week\b", re.I)
_AFTER_NEXT_RE = re.compile(r"\bafter\s+next\b|\bnext\s+next\b|\bsunday\s+after\s+next\b", re.I)
_IN_WEEKS_RE = re.compile(r"\b(?:in\s+|within\s+)?(one|two|three|four|five|six|a|\d)\s+weeks?(?:\s+from\s+(?:now|today|this\s+sunday))?\b", re.I)


def _target_date(q: str, today: date | None = None) -> date | None:
    """The date the question means, or None if it names no time. Plain 'Sunday', 'this week', 'this coming Sunday' = the next
    Sunday on or after today; 'next Sunday/week' = the one after; 'in two weeks' = that many Sundays on; weekday names = next such day."""
    today = today or datetime.now(_TZ).date()
    q = q.lower()
    sunday = today + timedelta(days=(6 - today.weekday()) % 7)
    if re.search(r"\byesterday\b", q):
        return today - timedelta(days=1)
    if _LOOKBACK_RE.search(q):
        back = (today.weekday() + 1) % 7 or 7  # days since the most recent past Sunday (a full week if today is Sunday)
        d = today - timedelta(days=back)
        if re.search(r"\b(?:two|2)\s+(?:sundays|weeks)\s+ago\b", q):
            d -= timedelta(days=7)
        return d
    m = _NUM_DATE_RE.search(q)
    if m:
        try:
            y = int(m.group(3)) if m.group(3) else today.year
            return date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    m = _MONTH_DATE_RE.search(q)
    if m:
        try:
            return date(int(m.group(3)) if m.group(3) else today.year, _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
        except ValueError:
            pass
    m = _DAY_MONTH_RE.search(q)
    if m:
        try:
            return date(today.year, _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        except ValueError:
            pass
    if re.search(r"\btomorrow\b", q):
        return today + timedelta(days=1)
    if re.search(r"\b(today|tonight)\b", q):
        return today
    if _AFTER_NEXT_RE.search(q):
        return sunday + timedelta(days=14)
    m = _IN_WEEKS_RE.search(q)
    if m and ("in " in m.group(0).lower() or "from" in m.group(0).lower()):
        return sunday + timedelta(days=7 * (_NUMS.get(m.group(1).lower(), 1) - 1) if _NUMS.get(m.group(1).lower(), 1) > 1 else 0)
    if _NEXT_RE.search(q):
        return sunday + timedelta(days=7)
    m = _WEEKDAY_RE.search(q)
    if m and not m.group(1).lower().startswith("sun"):
        names = {w[:3]: i for i, w in enumerate(_WEEKDAYS)}
        wd = names[m.group(1).lower()[:3]]
        return today + timedelta(days=(wd - today.weekday()) % 7)
    if re.search(r"\b(sundays?|sun|this\s+week|this\s+weekend|weekend|coming\s+week|upcoming|this\s+coming|church\s+this|service)\b", q):
        return sunday
    return None


# ---- formatting helpers ---------------------------------------------------------------------------------------------
def _local_day(start_utc: str) -> date:
    return datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone(_TZ).date()


def _local_time(start_utc: str) -> str:
    t = datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone(_TZ)
    return t.strftime("%I:%M %p").lstrip("0")


def _fmt_day(d: date) -> str:
    return f"{d.strftime('%A, %B')} {d.day}"


def _norm(question: str) -> str:
    return question.replace("’", "'").replace("‘", "'").strip()


def _role_needles(q: str) -> list[str]:
    out: list[str] = []
    for rx, needles in _ROLE_RES:
        if rx.search(q):
            out += [n for n in needles if n not in out]
    return out


def _service_filter(q: str):
    """(label, predicate on event title/start) for 'pre-service' / 'the 10am service', else None."""
    if re.search(r"pre-?\s?service|before\s+service|early\s+service|8:?30|9\s?am|nine", q, re.I):
        return lambda title, start: "pre-service" in title.lower() or _local_time(start).startswith(("8:", "9:"))
    if re.search(r"\b10\s?(?::00)?\s?(?:am|a\.m\.)\b|main\s+service|worship\s+service|late\s+service|10:00", q, re.I):
        return lambda title, start: _local_time(start).startswith("10:")
    return None


def _is_question(q: str) -> tuple[bool, bool, list[str]]:
    """(matches, open_view, needles). Pieces: INTENT + SHAPE (+ WHEN checked by the caller)."""
    who = bool(_WHO_RE.search(q))
    shaped = who or bool(_SHAPE_RE.search(q))
    needles = _role_needles(q)
    strong = bool(_STRONG_RE.search(q))
    weak = bool(_WEAK_RE.search(q)) and who
    role_only = bool(needles) and bool(re.match(r"^\W*(?:hey\W+|please\W+|can\s+you\W+|could\s+you\W+|tell\s+me\W+|let\s+me\s+know\W+|do\s+you\s+know\W+)*(?:who|whos|who's)\b", q, re.I))
    open_view = bool(_OPEN_RE.search(q) and _OPEN_CTX_RE.search(q))
    ok = (strong and shaped) or weak or role_only or open_view
    return ok, open_view, needles


def answer(question: str, today: date | None = None) -> str | None:
    q = _norm(question)
    ok, open_view, needles = _is_question(q)
    if not ok:
        return None
    day = _target_date(q, today)
    if day is None:
        return None
    looking_back = bool(_LOOKBACK_RE.search(q))
    if _PAST_RE.search(q) and not looking_back:
        return None  # "how long has Pat served", "who served communion": not a schedule lookup
    if open_view and not (_STRONG_RE.search(q) or re.search(r"\b(position|positions|spot|spots|slot|slots|role|roles|volunteers?)\b", q, re.I)):
        return None  # "who needs prayer Sunday" etc.
    svc = _service_filter(q)
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5) as c:
        c.row_factory = sqlite3.Row
        try:
            events = [e for e in c.execute("SELECT event_id, title, start_utc, pulled_at FROM fluro_schedule_events ORDER BY start_utc")
                      if _local_day(e["start_utc"]) == day and (svc is None or svc(e["title"], e["start_utc"]))]
        except sqlite3.OperationalError:
            return None  # table not created yet: nothing to answer from
        word = "Scheduled" if looking_back else "Serving"
        if not events:
            return f"I don't have a volunteer schedule for {_fmt_day(day)}" + (" (I only keep schedules from when I started saving them)." if looking_back else " yet. It may not be built in Fluro.")
        blocks, unconfirmed, total, shown = [], 0, 0, 0
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
                hay = f"{s['team']} {s['role']}".lower()
                if "recovery" in hay or (needles and not any(n in hay for n in needles)):
                    continue
                who_in = by_role.get((s["team"], s["role"]), [])
                short = max((s["minimum"] or 0) - len(who_in), 0)
                if open_view and not short:
                    continue
                if s["team"] != team:
                    team = s["team"]
                    lines.append(f"\n{team}")
                total += len(who_in)
                unconfirmed += sum(1 for p in who_in if p["confirmation"] != "confirmed")
                names = ", ".join(p["volunteer_name"] for p in who_in)
                if not who_in:
                    names = "open"
                elif short:
                    names += f" (needs {short} more)"
                lines.append(f"  {s['role']}: {names}")
                shown += 1
            if lines:
                blocks.append(f"{e['title']}, {_local_time(e['start_utc'])}" + "".join(f"\n{ln}" if ln.startswith("  ") else ln for ln in lines))
        if not blocks:
            if open_view:
                return f"Every position is filled for {_fmt_day(day)}." + ("" if not needles else " (for the team/role you asked about)")
            return f"I don't see that position on the schedule for {_fmt_day(day)}."
        head = f"Open positions {_fmt_day(day)}:" if open_view else f"{word} {_fmt_day(day)}:"
        out = f"{head}\n\n" + "\n\n".join(blocks)
        if total and not open_view:
            out += f"\n\n{total - unconfirmed} of {total} confirmed."
        try:
            pulled = datetime.strptime(events[0]["pulled_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if not looking_back and datetime.now(timezone.utc) - pulled > timedelta(days=_STALE_DAYS):
                out += f" (Schedule last refreshed {pulled.astimezone(_TZ).strftime('%b')} {pulled.astimezone(_TZ).day}.)"
        except Exception:
            pass
        return out
