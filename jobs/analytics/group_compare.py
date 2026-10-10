"""jobs/analytics/group_compare.py -- LLM-free team-chat answers that set who REGISTERED
for a small group / event session (Subsplash sign-ups) against who ATTENDED it
(group_attendance, checked off at /cat/tracker). Added 2026-10-09 so the two are never
blurred: registered-but-absent, walk-ins and no-shows are all answered from the data.

Entry point: answer(question) -> str | None. None means "not a registered-vs-attended
question about a known group", and data_chat carries on with its other routes.

Only compares when Subsplash has a sign-up form for that session; groups without one
(and sessions while registration reading is paused) get an honest "can't compare"
plus the attendance side. Celebrate Recovery is never named, so it is never answered.
"""
import re
import sqlite3
from datetime import date

from core.database import get_connection as _watson_conn
from jobs.congregation.groups_web import CONGREGATION_DB, _registrations, _series_list, _session_dates

_REG_RE = re.compile(r"\b(regist\w*|signed[- ]?up|sign[- ]?ups?|rsvp\w*)\b", re.I)
_ATT_RE = re.compile(r"\b(attend\w*|came|come|show(?:ed)?(?:\s+up)?|no[- ]?shows?|walk[- ]?ins?|present|made it|turned? out|here)\b", re.I)
_STOP = {"small", "group", "groups", "special", "events", "event", "study", "bible", "the", "of", "and", "a"}
_MIN_PREFIX = 4


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower().replace("'", ""))


def _matches_word(key: str, words: list[str]) -> bool:
    """A distinctive title word matches a question word if one is a prefix of the other ("frat" ~ "fraternity")."""
    return any(w == key or (len(w) >= _MIN_PREFIX and (key.startswith(w) or w.startswith(key))) for w in words)


def _pick_series(question: str) -> tuple[dict | None, list[dict]]:
    """(series, candidates). Several candidates and no single winner -> (None, candidates)."""
    words = _tokens(question)
    series = [s for s in _series_list() if not s["counts_only"]]
    scored = []
    for s in series:
        keys = [t for t in _tokens(s["title"]) if t not in _STOP]
        hits = sum(1 for k in keys if _matches_word(k, words))
        if hits:
            # Generic words ("bible study") only break ties between titles sharing a distinctive word
            # ("Men's Fraternity" Bible Study vs Billiards Outing).
            tie = sum(1 for t in _tokens(s["title"]) if t in _STOP and t not in {"the", "of", "and", "a"} and t in words)
            scored.append(((hits, tie), s))
    if not scored and "bible" in words and "study" in words:
        scored = [(1, s) for s in series if "bible study" in s["title"].lower()]
    if not scored:
        return None, []
    top = max(h for h, _ in scored)
    best = [s for h, s in scored if h == top]
    return (best[0], best) if len(best) == 1 else (None, best)


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_NUM_DATE_RE = re.compile(r"(?<![\d/])(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?(?![\d/])")
_MONTH_DATE_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.I)
_DAY_MONTH_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_LAST_WEEKDAY_RE = re.compile(r"\b(?:last|this past)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)


def _asked_date(question: str, today=None) -> str | None:
    """A specific date named in the question ("the 10/7 session", "October 7th", "last Wednesday") as YYYY-MM-DD,
    else None. A date with no year means this year; answer() steps back a year if that date is still ahead and last year's has records."""
    from datetime import timedelta
    today = today or date.today()
    month = day = year = None
    m = _NUM_DATE_RE.search(question)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else None
    else:
        m = _MONTH_DATE_RE.search(question)
        if m:
            month, day, year = _MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3)) if m.group(3) else None
        else:
            m = _DAY_MONTH_RE.search(question)
            if m:
                month, day = _MONTHS[m.group(2).lower()], int(m.group(1))
    if month:
        if year is not None and year < 100:
            year += 2000
        try:
            return date(year or today.year, month, day).isoformat()
        except ValueError:
            return None
    m = _LAST_WEEKDAY_RE.search(question)
    if m:
        back = (today.weekday() - _WEEKDAYS.index(m.group(1).lower())) % 7 or 7
        return (today - timedelta(days=back)).isoformat()
    return None


def _has_records(series: str, event_date: str) -> bool:
    """Attendance or sign-ups exist for that date (covers sessions older than the calendar window)."""
    with sqlite3.connect(CONGREGATION_DB) as c:
        if c.execute("SELECT 1 FROM group_attendance WHERE series=? AND event_date=? LIMIT 1", (series, event_date)).fetchone():
            return True
    return bool(_registrations(series, event_date)[1])


def _latest_session(series: str) -> str | None:
    """Newest past session that has any attendance recorded or any sign-up, else the newest past session."""
    dates = _session_dates(series)
    with sqlite3.connect(CONGREGATION_DB) as c:
        for d in dates:
            if c.execute("SELECT 1 FROM group_attendance WHERE series=? AND event_date=? LIMIT 1", (series, d)).fetchone():
                return d
    for d in dates:
        if _registrations(series, d)[1]:
            return d
    return dates[0] if dates else None


def _fmt_date(iso: str) -> str:
    y, m, d = map(int, iso.split("-"))
    return date(y, m, d).strftime("%A, %B %-d")


def _names(items) -> str:
    return ", ".join(sorted(items)) if items else "none"


def _unmatched_registrants(title: str, event_date: str) -> list[str]:
    with _watson_conn() as w:
        rows = w.execute(
            "SELECT DISTINCT trim(coalesce(first_name,'') || ' ' || coalesce(last_name,'')) AS n FROM subsplash_registrations "
            "WHERE event_title=? AND date(event_start)=? AND member_id IS NULL", (title, event_date)).fetchall()
    return [r["n"] for r in rows if r["n"]]


def answer(question: str) -> str | None:
    if not (_REG_RE.search(question) and _ATT_RE.search(question)):
        return None
    series, cands = _pick_series(question)
    if not cands:
        return None
    if series is None:
        return "Which one do you mean: " + " or ".join(c["title"] for c in cands) + "?"
    asked = _asked_date(question)
    if asked:
        event_date = asked
        if event_date > date.today().isoformat():
            y, m, d = event_date.split("-")
            last_year = f"{int(y) - 1}-{m}-{d}"
            if _has_records(series["series"], last_year) and not re.search(r"\b20\d\d\b", question):
                event_date = last_year
            else:
                return f"{series['title']} on {_fmt_date(event_date)} has not happened yet."
        if event_date not in _session_dates(series["series"]) and not _has_records(series["series"], event_date):
            recent = _session_dates(series["series"])[:4]
            tail = (" Recent sessions: " + ", ".join(_fmt_date(d) for d in recent) + ".") if recent else ""
            return f"I don't see a {series['title']} session on {_fmt_date(event_date)}.{tail}"
    else:
        event_date = _latest_session(series["series"])
    if not event_date:
        return f"{series['title']} has no recent sessions on the calendar yet."

    with sqlite3.connect(CONGREGATION_DB) as c:
        c.row_factory = sqlite3.Row
        attended = {r["member_id"]: r["name"] for r in c.execute(
            "SELECT m.id AS member_id, m.name FROM group_attendance a JOIN members m ON m.id=a.member_id "
            "WHERE a.series=? AND a.event_date=?", (series["series"], event_date))}
        g = c.execute("SELECT guests FROM group_counts WHERE series=? AND event_date=?", (series["series"], event_date)).fetchone()
        guests = g["guests"] if g else 0
        known, reg_ids = _registrations(series["series"], event_date)
        registered = {}
        if reg_ids:
            registered = {r["id"]: r["name"] for r in c.execute(
                f"SELECT id, name FROM members WHERE id IN ({','.join('?' * len(reg_ids))})", sorted(reg_ids))}

    head = f"{series['title']}, {_fmt_date(event_date)}"
    guest_line = f"Guests (not in the church database): {guests}." if attended else ""
    if not known:
        if not attended:
            return f"{head}: Subsplash has no sign-up information I can use for that session, and no attendance has been entered yet."
        return (f"{head}: I can't compare registered to attended because I don't have Subsplash sign-ups for that session "
                f"(there may be no sign-up form, or registration reading is paused). {len(attended)} attended: {_names(attended.values())}. {guest_line}")

    unmatched = _unmatched_registrants(series["title"], event_date)
    total_reg = len(registered) + len(unmatched)
    lines = [f"{head}: {total_reg} registered, {len(attended)} attended."]
    if not attended:
        lines.append("Attendance has not been entered for that session yet, so nobody counts as attended.")
        lines.append(f"Registered ({total_reg}): {_names(list(registered.values()) + unmatched)}.")
        return "\n".join(lines)
    came = [n for i, n in registered.items() if i in attended]
    absent = [n for i, n in registered.items() if i not in attended]
    walk = [n for i, n in attended.items() if i not in registered]
    lines.append(f"Registered and came ({len(came)}): {_names(came)}.")
    lines.append(f"Registered but did not come ({len(absent)}): {_names(absent)}.")
    if unmatched:
        lines.append(f"Registered but not matched to anyone in the church database ({len(unmatched)}): {_names(unmatched)}. "
                     "I can't tell whether they came.")
    lines.append(f"Came without registering ({len(walk)}): {_names(walk)}.")
    lines.append(guest_line)
    return "\n".join(lines)
